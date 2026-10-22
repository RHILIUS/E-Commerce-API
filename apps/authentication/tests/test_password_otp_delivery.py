"""Password OTP worker eligibility and post-commit publishing regression checks."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.account.services.password import change_password
from apps.authentication.models import PasswordChangeState
from apps.authentication.services.email_service import OTPDeliveryError
from apps.authentication.tasks import deliver_password_change_otp, deliver_password_change_notice


class PasswordOTPDeliveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="delivery", email="delivery@example.com", password="OriginalPassword1",
            phone_number="+639171234567",
        )
        self.payload = {}
        with patch.object(deliver_password_change_otp, "apply_async") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                change_password(self.user.pk, "request", self.payload)
        self.args = publish.call_args.kwargs["args"]
        sender = patch("apps.authentication.tasks.send_password_change_code")
        self.send = sender.start()
        self.addCleanup(sender.stop)

    def test_current_challenge_delivers_to_registered_email(self):
        deliver_password_change_otp.run(*self.args)
        self.send.assert_called_once_with(self.user.email, self.args[2])

    def test_invalid_challenge_states_are_skipped(self):
        for changes in ({"consumed": True}, {"challenge_id": uuid.uuid4()},
                        {"expires_at": timezone.now() - timedelta(seconds=1)},
                        {"email": "changed@example.com"}, {"credential_state": "replaced"},
                        {"otp_hash": "replaced"}):
            with self.subTest(changes=changes):
                with transaction.atomic():
                    PasswordChangeState.objects.filter(user=self.user).update(**changes)
                    deliver_password_change_otp.run(*self.args)
                    transaction.set_rollback(True)
        self.send.assert_not_called()

    def test_changed_password_email_inactive_or_deleted_owner_is_skipped(self):
        for changes in ({"password": "replaced"}, {"email": "changed@example.com"},
                        {"is_active": False}):
            with self.subTest(changes=changes):
                with transaction.atomic():
                    get_user_model().objects.filter(pk=self.user.pk).update(**changes)
                    deliver_password_change_otp.run(*self.args)
                    transaction.set_rollback(True)
        self.user.delete()
        deliver_password_change_otp.run(*self.args)
        self.send.assert_not_called()

    def test_transient_failure_retries_without_exposing_or_reissuing_code(self):
        error = OTPDeliveryError("Delivery failed")
        error.__cause__ = OSError("Offline")
        self.send.side_effect = error
        with patch.object(deliver_password_change_otp, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                deliver_password_change_otp.run(*self.args)
        self.assertNotIn(self.args[2], retry.call_args.kwargs["argsrepr"])
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).send_count, 1)
        PasswordChangeState.objects.filter(user=self.user).update(consumed=True)
        self.send.reset_mock()
        deliver_password_change_otp.run(*self.args)
        self.send.assert_not_called()

    def test_commit_queues_and_rollback_discards_delivery(self):
        PasswordChangeState.objects.filter(user=self.user).delete()
        with patch.object(deliver_password_change_otp, "apply_async") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                change_password(self.user.pk, "request", self.payload)
                publish.assert_not_called()
            publish.assert_called_once()
            publish.reset_mock()
            PasswordChangeState.objects.filter(user=self.user).delete()
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(ValueError):
                    with transaction.atomic():
                        change_password(self.user.pk, "request", self.payload)
                        raise ValueError("Rollback")
            publish.assert_not_called()
            self.assertFalse(PasswordChangeState.objects.filter(user=self.user).exists())


    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_change_notice_uses_account_change_wording_and_skips_changed_destination(self):
        deliver_password_change_notice.run(self.user.pk, self.user.email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password was changed", mail.outbox[-1].subject)
        self.assertNotIn("OriginalPassword1", mail.outbox[-1].body)
        self.assertNotIn(self.args[2], mail.outbox[-1].body)
        deliver_password_change_notice.run(self.user.pk, "old@example.com")
        self.assertEqual(len(mail.outbox), 1)
