"""Public recovery authorization, abuse limits, delivery and transaction regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.db import DatabaseError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authentication.models import PasswordResetState
from apps.authentication.services.password_reset import (
    COOKIE_NAME, COOKIE_PATH, ResetError, confirm_reset, request_code, verify_code,
)
from apps.authentication.tasks import deliver_password_reset_otp

ROOT = "/api/auth/forgot-password/"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PasswordResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="reset", email="reset@example.com", phone_number="+639171234560", password="OriginalPassword1")

    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.client.get(ROOT).data["csrf_token"]
        code = patch("apps.authentication.services.password_reset.secrets.randbelow", return_value=123456)
        code.start(); self.addCleanup(code.stop)
        publisher = patch("apps.authentication.tasks.deliver_password_reset_otp.apply_async")
        self.publish = publisher.start(); self.addCleanup(publisher.stop)
        notice = patch("apps.authentication.tasks.deliver_password_reset_notice.apply_async")
        self.notice = notice.start(); self.addCleanup(notice.stop)

    def post(self, operation, data):
        """Execute delivery callbacks for functional assertions; real commits are tested below."""
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(ROOT + operation + "/", data, format="json", HTTP_X_CSRFTOKEN=self.csrf)

    def request(self, email=None):
        return self.post("request", {"email": email or self.user.email})

    def verify(self, challenge=None, otp="123456"):
        challenge = challenge or self.request().data
        return self.post("verify", {"email": self.user.email, "challenge_id": challenge["challenge_id"], "otp": otp})

    def test_success_cookie_single_use_and_all_sessions_revoked(self):
        refresh = RefreshToken.for_user(self.user)
        refresh["auth_version"] = 0
        access = str(refresh.access_token)
        challenge = self.request()
        self.assertEqual(challenge.status_code, 200)
        state = PasswordResetState.objects.get(email=self.user.email)
        self.assertTrue(check_password("123456", state.otp_hash))
        self.assertNotIn("123456", self.publish.call_args.kwargs["argsrepr"])
        verified = self.verify(challenge.data)
        self.assertEqual(verified.status_code, 200)
        cookie = verified.cookies[COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], COOKIE_PATH)
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(int(cookie["max-age"]), 600)
        self.assertNotIn("reset_token", verified.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OriginalPassword1"))
        self.assertEqual(self.verify(challenge.data).status_code, 400)
        saved_cookie = cookie.value
        response = self.post("confirm", {"new_password": "UpdatedPassword2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies[COOKIE_NAME]["max-age"], 0)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 0)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("UpdatedPassword2"))
        self.assertEqual(self.user.authentication_version, 1)
        self.assertEqual(self.client.post("/api/auth/verify/", {"token": access}).status_code, 401)
        self.client.cookies["refresh_token"] = str(refresh)
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)
        self.client.cookies[COOKIE_NAME] = saved_cookie
        self.assertEqual(self.post("confirm", {"new_password": "AnotherPassword3"}).status_code, 400)
        self.notice.assert_called_once()

    def test_unknown_inactive_and_known_emails_share_public_contract_and_limits(self):
        get_user_model().objects.create_user(username="disabled", email="disabled@example.com", phone_number="+639171234561", password="OriginalPassword1", is_active=False)
        for email in (self.user.email, "unknown@example.com", "disabled@example.com"):
            with self.subTest(email=email):
                first = self.request(email)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(set(first.data), {"challenge_id", "expires_at", "retry_after", "message"})
                self.assertEqual(first.data["retry_after"], 60)
                self.assertIn("If an account exists", first.data["message"])
                again = self.request(email)
                self.assertEqual(again.status_code, 429)
                self.assertEqual(again.data["code"], "rate_limited")
                self.assertIn("Retry-After", again)
        self.assertEqual(self.publish.call_count, 3)

    def test_csrf_is_required_even_for_anonymous_requests_and_trusted_origin_checked(self):
        for operation, data in (("request", {"email": self.user.email}), ("resend", {}), ("verify", {}), ("confirm", {"new_password": "UpdatedPassword2"})):
            self.assertEqual(self.client.post(ROOT+operation+"/", data).status_code, 403)
        self.assertFalse(PasswordResetState.objects.exists())
        response = self.client.post(ROOT+"request/", {"email": self.user.email}, HTTP_X_CSRFTOKEN=self.csrf, HTTP_ORIGIN="https://attacker.example")
        self.assertEqual(response.status_code, 403)
        with override_settings(CSRF_TRUSTED_ORIGINS=["http://localhost:5173"]):
            self.assertEqual(self.client.post(ROOT+"request/", {"email": self.user.email}, HTTP_X_CSRFTOKEN=self.csrf, HTTP_ORIGIN="http://localhost:5173").status_code, 200)

    def test_resend_replaces_code_and_preserves_attempts(self):
        challenge = self.request().data
        self.assertEqual(self.verify(challenge, "000000").status_code, 400)
        PasswordResetState.objects.update(last_sent_at=timezone.now()-timedelta(seconds=61))
        with patch("apps.authentication.services.password_reset.secrets.randbelow", return_value=654321):
            replaced = self.post("resend", {"email": self.user.email, "challenge_id": challenge["challenge_id"]})
        self.assertEqual(replaced.status_code, 200)
        state = PasswordResetState.objects.get(email=self.user.email)
        self.assertEqual(state.failed_attempts, 1)
        self.assertNotEqual(str(replaced.data["challenge_id"]), str(challenge["challenge_id"]))
        self.assertEqual(self.verify(replaced.data, "123456").status_code, 400)
        self.assertEqual(self.verify(replaced.data, "654321").status_code, 200)

    def test_restarts_cannot_bypass_attempt_or_send_budgets(self):
        challenge = self.request().data
        for _ in range(5):
            last = self.verify(challenge, "000000")
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.data["code"], "challenge_exhausted")
        PasswordResetState.objects.update(last_sent_at=timezone.now()-timedelta(seconds=61))
        self.assertEqual(self.request().status_code, 429)
        state = PasswordResetState.objects.get()
        self.assertEqual(state.failed_attempts, 5)
        PasswordResetState.objects.update(window_started_at=timezone.now()-timedelta(seconds=901))
        self.assertEqual(self.request().status_code, 200)
        for _ in range(4):
            PasswordResetState.objects.update(last_sent_at=timezone.now()-timedelta(seconds=61))
            self.assertEqual(self.request().status_code, 200)
        PasswordResetState.objects.update(last_sent_at=timezone.now()-timedelta(seconds=61))
        self.assertEqual(self.request().status_code, 429)

    def test_expired_code_and_grant_cannot_reset_password(self):
        challenge = self.request().data
        PasswordResetState.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.verify(challenge).data["code"], "code_expired")
        PasswordResetState.objects.update(expires_at=timezone.now()+timedelta(seconds=60))
        self.assertEqual(self.verify(challenge).status_code, 200)
        PasswordResetState.objects.update(grant_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post("confirm", {"new_password": "UpdatedPassword2"}).data["code"], "reset_unavailable")

    def test_changed_email_password_or_inactive_account_invalidates_grant(self):
        self.assertEqual(self.verify().status_code, 200)
        for changes in ({"email": "changed@example.com"}, {"password": "changed"}, {"is_active": False}):
            with self.subTest(changes=changes), transaction.atomic():
                get_user_model().objects.filter(pk=self.user.pk).update(**changes)
                self.assertEqual(self.post("confirm", {"new_password": "UpdatedPassword2"}).status_code, 400)
                transaction.set_rollback(True)

    def test_password_policy_errors_preserve_authorization_and_registration_code_cannot_authorize(self):
        self.assertEqual(self.post("confirm", {"new_password": "UpdatedPassword2"}).status_code, 400)
        self.assertEqual(self.verify().status_code, 200)
        for password in ("weakpassword", "OriginalPassword1"):
            response = self.post("confirm", {"new_password": password})
            self.assertEqual(response.status_code, 400)
            self.assertIn("new_password", response.data)
        self.assertEqual(self.post("confirm", {"new_password": "UpdatedPassword2"}).status_code, 200)

    def test_database_failure_rolls_back_password_version_and_grant(self):
        self.verify()
        with patch.object(PasswordResetState, "save", side_effect=DatabaseError("unavailable")):
            self.assertEqual(self.post("confirm", {"new_password": "UpdatedPassword2"}).status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OriginalPassword1"))
        self.assertEqual(self.user.authentication_version, 0)
        self.assertTrue(PasswordResetState.objects.get().grant_hash)
        self.notice.assert_not_called()

    def test_queued_worker_sends_only_current_owned_eligible_codes(self):
        self.request()
        args = self.publish.call_args.kwargs["args"]
        with patch("apps.authentication.services.email_service.send_password_reset_code") as sender:
            deliver_password_reset_otp.run(*args)
            sender.assert_called_once_with(self.user.email, "123456")
            sender.reset_mock()
            for changes in ({"otp_consumed": True}, {"credential_state": "old"}, {"user_id": None}, {"expires_at": timezone.now()-timedelta(seconds=1)}):
                with self.subTest(changes=changes), transaction.atomic():
                    PasswordResetState.objects.update(**changes)
                    deliver_password_reset_otp.run(*args)
                    transaction.set_rollback(True)
            sender.assert_not_called()
            self.request("unknown@example.com")
            deliver_password_reset_otp.run(*self.publish.call_args.kwargs["args"])
            sender.assert_not_called()


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PasswordResetTransactionTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="atomicreset", email="atomic@example.com", phone_number="+639171234569", password="OriginalPassword1")

    def issue(self):
        with patch("apps.authentication.tasks.deliver_password_reset_otp.apply_async") as publisher:
            challenge = request_code(self.user.email)
        return challenge, publisher.call_args.kwargs["args"][2]

    def test_real_commit_publication_and_rollback_discard(self):
        with patch("apps.authentication.tasks.deliver_password_reset_otp.apply_async") as publisher:
            with transaction.atomic():
                request_code(self.user.email)
                publisher.assert_not_called()
            publisher.assert_called_once()
        PasswordResetState.objects.all().delete()
        with patch("apps.authentication.tasks.deliver_password_reset_otp.apply_async") as publisher:
            with self.assertRaises(ValueError), transaction.atomic():
                request_code(self.user.email)
                raise ValueError("abort")
            publisher.assert_not_called()
            self.assertFalse(PasswordResetState.objects.exists())

    def test_broker_failure_retains_state_and_budgets_for_all_emails(self):
        with patch("apps.authentication.tasks.deliver_password_reset_otp.apply_async", side_effect=OSError("offline")):
            for email in (self.user.email, "unknown@example.com"):
                with self.assertRaises(ResetError) as raised:
                    request_code(email)
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(PasswordResetState.objects.get(email=email).send_count, 1)

    def test_notification_failure_does_not_fail_a_committed_reset(self):
        challenge, otp = self.issue()
        cookie, _ = verify_code(self.user.email, challenge["challenge_id"], otp)
        with patch("apps.authentication.tasks.deliver_password_reset_notice.apply_async", side_effect=OSError("offline")):
            confirm_reset(cookie, "UpdatedPassword2")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("UpdatedPassword2"))
        self.assertFalse(PasswordResetState.objects.get().grant_hash)

    def test_concurrent_confirmations_consume_grant_only_once(self):
        challenge, otp = self.issue()
        cookie, _ = verify_code(self.user.email, challenge["challenge_id"], otp)
        barrier = Barrier(2)

        def attempt():
            """Exercise real independent PostgreSQL connections after a shared start."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                confirm_reset(cookie, "UpdatedPassword2")
                return "success"
            except ResetError:
                return "rejected"
            finally:
                close_old_connections()

        with patch("apps.authentication.tasks.deliver_password_reset_notice.apply_async"), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["success", "rejected"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.authentication_version, 1)
