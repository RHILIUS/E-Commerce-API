"""Account-bound verification, durable budgets, atomic saves and session revocation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import close_old_connections, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.account.services.password import COOKIE_NAME, change_password, confirm_password, PasswordChangeUnavailable
from apps.authentication.models import PasswordChangeState, OTPVerificationRecord
from apps.authentication.tasks import deliver_password_change_otp
from apps.authentication.serializers import CustomTokenObtainPairSerializer

ROOT = "/api/account/change-password/"
OLD = "OriginalPassword1"
NEW = "UpdatedPassword2"


def user(name="buyer"):
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.com", password=OLD,
        phone_number=f"+63917123456{1 if name == 'buyer' else 2}")


class PasswordChangeFixtures:
    """Exercise actual CSRF checks while mocking only external task publication."""
    def setUp(self):
        cache.clear()
        publisher = patch('apps.account.services.password.deliver_password_change_otp.apply_async',
                          side_effect=lambda **options: deliver_password_change_otp.run(*options['args']))
        self.publish = publisher.start()
        self.addCleanup(publisher.stop)
        notice = patch('apps.account.services.password.deliver_password_change_notice.apply_async')
        self.notice = notice.start()
        self.addCleanup(notice.stop)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_authenticate(self.user)
        response = self.client.get(ROOT)
        self.csrf = response.data['csrf_token']

    def post(self, path, data, **kwargs):
        return self.client.post(path, data, format='json', HTTP_X_CSRFTOKEN=self.csrf, **kwargs)

    def issue(self):
        response = self.post(ROOT+'request/', {})
        self.assertEqual(response.status_code, 200, response.data)
        code = mail.outbox[-1].body.split('code is: ')[1].splitlines()[0]
        return response.data['challenge_id'], code

    def verify(self, challenge, code):
        return self.post(ROOT+'verify/', {'challenge_id': str(challenge), 'otp': code})

    def grant(self):
        challenge, code = self.issue()
        response = self.verify(challenge, code)
        self.assertEqual(response.status_code, 200, response.data)
        return self.client.cookies[COOKIE_NAME].value

    def confirm(self, password=NEW):
        return self.post(ROOT+'confirm/', {'new_password': password})

    def ready_to_resend(self):
        PasswordChangeState.objects.filter(user=self.user).update(last_sent_at=timezone.now()-timedelta(seconds=61))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordChangeTests(PasswordChangeFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = user()

    def post(self, path, data, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return super().post(path, data, **kwargs)

    def test_bootstrap_shows_fixed_email_without_issuing_or_clearing_permission(self):
        self.grant()
        cookie = self.client.cookies[COOKIE_NAME].value
        response = self.client.get(ROOT)
        self.assertEqual(response.data['masked_email'], 'b***@example.com')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.cookies[COOKIE_NAME].value, cookie)
        self.assertEqual(self.publish.call_count, 1)
        self.assertTrue(PasswordChangeState.objects.get(user=self.user).grant_hash)

    def test_all_routes_require_authentication_and_posts_require_csrf(self):
        for suffix in ('request/', 'resend/', 'verify/', 'confirm/'):
            self.assertEqual(self.client.post(ROOT+suffix, {}, format='json').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(ROOT).status_code, 401)
        for suffix in ('request/', 'resend/', 'verify/', 'confirm/'):
            self.assertEqual(self.post(ROOT+suffix, {}).status_code, 401)
        self.assertFalse(PasswordChangeState.objects.exists())

    def test_ip_guard_includes_bootstrap_and_ignores_forwarded_headers(self):
        cache.clear()
        for index in range(20):
            self.assertEqual(self.client.get(ROOT, HTTP_X_FORWARDED_FOR=f'198.51.100.{index}').status_code, 200)
        response = self.client.get(ROOT, HTTP_X_FORWARDED_FOR='203.0.113.1')
        self.assertEqual(response.status_code, 429)
        self.assertIsInstance(response.data['retry_after'], int)
        self.assertEqual(response['Retry-After'], str(response.data['retry_after']))

    def test_issuance_uses_owned_email_and_never_requires_passwords(self):
        response = self.post(ROOT+'request/', {'email': 'attacker@example.com', 'user_id': 999})
        self.assertEqual(response.status_code, 200)
        state = PasswordChangeState.objects.get(user=self.user)
        code = mail.outbox[-1].body.split('code is: ')[1].splitlines()[0]
        self.assertNotEqual(state.otp_hash, code)
        self.assertEqual(mail.outbox[-1].to, [self.user.email])
        self.assertEqual(state.email, self.user.email)
        self.assertLessEqual((state.expires_at-timezone.now()).total_seconds(), 300)
        self.assertFalse(OTPVerificationRecord.objects.exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))

    def test_verify_consumes_code_without_changing_password_and_sets_isolated_cookie(self):
        challenge, code = self.issue()
        response = self.verify(challenge, code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'message', 'expires_at'})
        cookie = response.cookies[COOKIE_NAME]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['path'], ROOT)
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['max-age'], 600)
        self.assertEqual(bool(cookie['secure']), not settings.DEBUG)
        state = PasswordChangeState.objects.get(user=self.user)
        self.assertTrue(state.consumed)
        self.assertNotIn(cookie.value.split(':')[1], state.grant_hash)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))
        self.assertEqual(self.user.authentication_version, 0)
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')

    def test_success_clears_permission_revokes_sessions_and_notifies_without_credentials(self):
        refresh = CustomTokenObtainPairSerializer.get_token(self.user)
        access = str(refresh.access_token)
        self.grant()
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        for name in (COOKIE_NAME, 'refresh_token'):
            self.assertEqual(response.cookies[name]['max-age'], 0)
        self.notice.assert_called_once()
        self.assertNotIn(NEW, str(self.notice.call_args))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        self.assertEqual(self.user.authentication_version, 1)
        self.assertFalse(PasswordChangeState.objects.get(user=self.user).grant_hash)
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')
        device = APIClient()
        device.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(device.get('/api/account/profile/').status_code, 401)
        device.cookies['refresh_token'] = str(refresh)
        self.assertEqual(device.post('/api/auth/refresh/', {}).status_code, 401)
        self.assertEqual(device.post('/api/auth/verify/', {'token': access}).status_code, 401)
        device.credentials()
        self.assertEqual(device.post('/api/auth/login/', {'email': self.user.email, 'password': OLD}).status_code, 401)
        self.assertEqual(device.post('/api/auth/login/', {'email': self.user.email, 'password': NEW}).status_code, 200)

    def test_legacy_tokens_are_invalidated_by_save(self):
        legacy = RefreshToken.for_user(self.user)
        self.assertNotIn('auth_version', legacy)
        device = APIClient()
        device.credentials(HTTP_AUTHORIZATION=f'Bearer {legacy.access_token}')
        self.assertEqual(device.get('/api/account/profile/').status_code, 200)
        self.grant()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(device.get('/api/account/profile/').status_code, 401)

    def test_ownership_cookie_isolation_and_malformed_permission(self):
        challenge, code = self.issue()
        other = user('other')
        self.client.force_authenticate(other)
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')
        self.client.force_authenticate(self.user)
        self.assertEqual(self.verify(challenge, code).status_code, 200)
        cookie = self.client.cookies[COOKIE_NAME].value
        self.client.force_authenticate(other)
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')
        self.client.force_authenticate(self.user)
        for invalid in ('', 'not-a-cookie', str(uuid.uuid4())+':'+('a'*43)):
            self.client.cookies[COOKIE_NAME] = invalid
            self.client.cookies['password_reset'] = cookie
            response = self.confirm()
            self.assertEqual(response.data['code'], 'change_unavailable')
            self.assertEqual(response.cookies[COOKIE_NAME]['max-age'], 0)

    def test_invalid_code_shapes_do_not_charge_verification_budget(self):
        challenge, _ = self.issue()
        for invalid in ('12345', '1234567', '123456\n', '１２３４５６', 'abcdef'):
            self.assertEqual(self.verify(challenge, invalid).status_code, 400)
        state = PasswordChangeState.objects.get(user=self.user)
        self.assertEqual(state.failed_attempts, 0)
        self.assertEqual(state.verification_count, 0)

    def test_resend_rotates_identifier_and_preserves_attempts(self):
        challenge, code = self.issue()
        wrong = '000000' if code != '000000' else '000001'
        self.assertEqual(self.verify(challenge, wrong).data['code'], 'invalid_code')
        self.ready_to_resend()
        with patch('apps.account.services.password.secrets.randbelow', return_value=123456):
            response = self.post(ROOT+'resend/', {'challenge_id': str(challenge)})
        replacement = response.data['challenge_id']
        self.assertNotEqual(str(replacement), str(challenge))
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).failed_attempts, 1)
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')
        self.assertEqual(self.verify(replacement, '123456').status_code, 200)

    def test_restart_preserves_attempts_and_exhaustion_blocks_issuance_until_window_ends(self):
        challenge, code = self.issue()
        wrong = '000000' if code != '000000' else '000001'
        self.verify(challenge, wrong)
        self.ready_to_resend()
        challenge, code = self.issue()
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(4):
            response = self.verify(challenge, wrong)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['code'], 'challenge_exhausted')
        self.ready_to_resend()
        self.assertEqual(self.post(ROOT+'request/', {}).status_code, 429)
        self.assertEqual(self.post(ROOT+'resend/', {'challenge_id': str(challenge)}).status_code, 429)
        self.assertEqual(self.verify(challenge, code).status_code, 429)
        PasswordChangeState.objects.filter(user=self.user).update(window_started_at=timezone.now()-timedelta(seconds=901))
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')
        self.issue()
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).failed_attempts, 0)

    def test_expired_code_can_be_replaced(self):
        challenge, code = self.issue()
        PasswordChangeState.objects.filter(user=self.user).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.verify(challenge, code).data['code'], 'code_expired')
        self.ready_to_resend()
        self.assertEqual(self.post(ROOT+'resend/', {'challenge_id': str(challenge)}).status_code, 200)

    def test_expired_permission_and_reissue_invalidate_prior_grant(self):
        cookie = self.grant()
        PasswordChangeState.objects.filter(user=self.user).update(grant_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')
        self.ready_to_resend()
        self.issue()
        self.client.cookies[COOKIE_NAME] = cookie
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')

    def test_account_changes_invalidate_code_and_permission(self):
        challenge, code = self.issue()
        self.user.email = 'changed@example.com'
        self.user.save(update_fields=['email'])
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')
        self.ready_to_resend()
        self.grant()
        self.user.set_password('AnotherPassword3')
        self.user.save(update_fields=['password'])
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')

    def test_password_policy_error_preserves_grant_for_correction(self):
        self.grant()
        for invalid in (OLD, 'weakpassword'):
            response = self.confirm(invalid)
            self.assertIn('new_password', response.data)
            self.assertNotIn(COOKIE_NAME, response.cookies)
            self.assertTrue(PasswordChangeState.objects.get(user=self.user).grant_hash)
        self.assertEqual(self.confirm().status_code, 200)

    def test_database_failure_rolls_back_password_version_and_grant(self):
        self.grant()
        with patch.object(get_user_model(), 'save', side_effect=IntegrityError('simulated')):
            self.assertEqual(self.confirm().status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))
        self.assertEqual(self.user.authentication_version, 0)
        self.assertTrue(PasswordChangeState.objects.get(user=self.user).grant_hash)
        self.notice.assert_not_called()

    @override_settings(PASSWORD_CHANGE_MAX_REQUESTS=2)
    def test_rejected_cooldown_requests_charge_durable_budget(self):
        self.issue()
        self.assertEqual(self.post(ROOT+'request/', {}).status_code, 429)
        self.ready_to_resend()
        self.assertEqual(self.post(ROOT+'request/', {}).status_code, 429)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).request_count, 2)

    @override_settings(PASSWORD_CHANGE_MAX_VERIFICATIONS=2)
    def test_verification_budget_survives_restart(self):
        _, code = self.issue()
        for _ in range(2):
            self.verify(uuid.uuid4(), code)
        self.ready_to_resend()
        challenge, code = self.issue()
        self.assertEqual(self.verify(challenge, code).status_code, 429)

    def test_send_budget_survives_restarts(self):
        self.issue()
        for _ in range(4):
            self.ready_to_resend()
            self.issue()
        self.ready_to_resend()
        self.assertEqual(self.post(ROOT+'request/', {}).status_code, 429)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).send_count, 5)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordChangeCommitTests(PasswordChangeFixtures, TransactionTestCase):
    def setUp(self):
        self.user = user()
        super().setUp()

    def test_publish_failure_retains_issuance_cooldown_and_limits(self):
        with patch('apps.account.services.password.deliver_password_change_otp.apply_async', side_effect=OSError('Offline')):
            response = self.post(ROOT+'request/', {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['retry_after'], 60)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).send_count, 1)
        self.assertEqual(self.post(ROOT+'request/', {}).status_code, 429)

    def test_failed_replacement_publication_keeps_old_code_and_grant_invalid(self):
        cookie = self.grant()
        self.ready_to_resend()
        with patch('apps.account.services.password.deliver_password_change_otp.apply_async', side_effect=OSError('Offline')):
            self.assertEqual(self.post(ROOT+'request/', {}).status_code, 503)
        self.client.cookies[COOKIE_NAME] = cookie
        self.assertEqual(self.confirm().data['code'], 'change_unavailable')

    def test_failed_resend_publication_still_replaces_the_code_identifier(self):
        challenge, code = self.issue()
        self.ready_to_resend()
        with patch('apps.account.services.password.deliver_password_change_otp.apply_async', side_effect=OSError('Offline')):
            self.assertEqual(self.post(ROOT+'resend/', {'challenge_id': str(challenge)}).status_code, 503)
        state = PasswordChangeState.objects.get(user=self.user)
        self.assertNotEqual(str(state.challenge_id), str(challenge))
        self.assertEqual(self.verify(challenge, code).data['code'], 'challenge_unavailable')

    def test_request_queues_without_synchronous_delivery_and_redacts_payload(self):
        with patch('apps.account.services.password.deliver_password_change_otp.apply_async') as publish:
            self.assertEqual(self.post(ROOT+'request/', {}).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        options = publish.call_args.kwargs
        self.assertEqual(options['args'][0], self.user.pk)
        self.assertNotIn(options['args'][2], options['argsrepr'])
        self.assertFalse(options['retry'])

    def test_notification_failure_cannot_fail_committed_password_change(self):
        self.grant()
        with patch('apps.account.services.password.deliver_password_change_notice.apply_async', side_effect=OSError('Offline')):
            self.assertEqual(self.confirm().status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        self.assertFalse(PasswordChangeState.objects.get(user=self.user).grant_hash)

    def test_simultaneous_confirmations_change_password_once(self):
        cookie = self.grant()
        def confirm():
            close_old_connections()
            try:
                try:
                    confirm_password(self.user.pk, cookie, NEW)
                    return 200
                except PasswordChangeUnavailable:
                    return 400
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: confirm(), range(2)))
        self.assertEqual(sorted(results), [200, 400])
        self.user.refresh_from_db()
        self.assertEqual(self.user.authentication_version, 1)
        self.assertEqual(self.notice.call_count, 1)
