"""Registration email validity, retry and transaction tests."""
import smtplib
from datetime import timedelta
from unittest.mock import patch

from celery.exceptions import Retry
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.authentication.models import OTPVerificationRecord
from apps.authentication.services.email_service import OTPDeliveryError
from apps.authentication.services.otp_delivery import queue_registration_otp
from apps.authentication.services.otp_service import issue_otp
from apps.authentication.tasks import deliver_registration_otp


class OTPDeliveryTests(TestCase):
    def setUp(self):
        self.email = "person@example.com"
        self.code = issue_otp(self.email)
        sender = patch("apps.authentication.tasks.send_otp_email")
        self.send = sender.start()
        self.addCleanup(sender.stop)

    def test_current_code_is_delivered(self):
        deliver_registration_otp.run(self.email, self.code)
        self.send.assert_called_once_with(self.email, self.code)

    def test_obsolete_codes_are_skipped(self):
        for changes in ({"is_verified": True},
                        {"expires_at": timezone.now() - timedelta(seconds=1)},
                        {"otp_hash": "replaced"}):
            with self.subTest(changes=changes):
                record = OTPVerificationRecord.objects.get(email=self.email)
                original = {key: getattr(record, key) for key in changes}
                OTPVerificationRecord.objects.filter(pk=record.pk).update(**changes)
                deliver_registration_otp.run(self.email, self.code)
                OTPVerificationRecord.objects.filter(pk=record.pk).update(**original)
        OTPVerificationRecord.objects.all().delete()
        deliver_registration_otp.run(self.email, self.code)
        self.send.assert_not_called()

    def test_temporary_failure_retries_same_task_without_changing_code(self):
        error = OTPDeliveryError("Delivery failed")
        error.__cause__ = OSError("Transport failed")
        self.send.side_effect = error
        with patch.object(deliver_registration_otp, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                deliver_registration_otp.run(self.email, self.code)
        self.assertEqual(retry.call_args.kwargs["countdown"], 5)
        self.assertNotIn(self.code, retry.call_args.kwargs["argsrepr"])
        self.assertEqual(OTPVerificationRecord.objects.get(email=self.email).send_count, 1)

    def test_smtp_temporary_response_retries(self):
        for cause in (smtplib.SMTPDataError(451, b"Try later"),
                      smtplib.SMTPRecipientsRefused({self.email: (450, b"Try later")})):
            error = OTPDeliveryError("Delivery failed")
            error.__cause__ = cause
            self.send.side_effect = error
            with patch.object(deliver_registration_otp, "retry", side_effect=Retry()) as retry:
                with self.assertRaises(Retry):
                    deliver_registration_otp.run(self.email, self.code)
            retry.assert_called_once()

    def test_permanent_failure_or_exhausted_retry_budget_does_not_retry(self):
        for cause, retries in ((smtplib.SMTPDataError(550, b"Rejected"), 0),
                               (OSError("Offline"), 3)):
            error = OTPDeliveryError("Delivery failed")
            error.__cause__ = cause
            self.send.side_effect = error
            deliver_registration_otp.push_request(retries=retries)
            try:
                with self.assertLogs("apps.authentication.tasks", level="WARNING"), \
                        patch.object(deliver_registration_otp, "retry") as retry:
                    deliver_registration_otp.run(self.email, self.code)
                    retry.assert_not_called()
            finally:
                deliver_registration_otp.pop_request()

    def test_retry_that_would_pass_expiry_is_not_scheduled(self):
        OTPVerificationRecord.objects.filter(email=self.email).update(
            expires_at=timezone.now() + timedelta(seconds=2))
        error = OTPDeliveryError("Delivery failed")
        error.__cause__ = OSError("Offline")
        self.send.side_effect = error
        with self.assertLogs("apps.authentication.tasks", level="WARNING"), \
                patch.object(deliver_registration_otp, "retry") as retry:
            deliver_registration_otp.run(self.email, self.code)
            retry.assert_not_called()

    @patch("apps.authentication.services.otp_delivery.deliver_registration_otp.apply_async")
    def test_publish_waits_for_commit_and_rollback_discards_callback(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            queue_registration_otp("other@example.com")
            publish.assert_not_called()
        publish.assert_called_once()
        publish.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    queue_registration_otp("rollback@example.com")
                    raise ValueError("Rollback")
        publish.assert_not_called()
        self.assertFalse(OTPVerificationRecord.objects.filter(email="rollback@example.com").exists())
