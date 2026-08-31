from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from authentication.models import OTPVerificationRecord
from authentication.services.otp_service import (
    OTPRateLimitExceeded,
    issue_otp,
)


@override_settings(
    OTP_RESEND_COOLDOWN_SECONDS=60,
    OTP_SEND_WINDOW_SECONDS=900,
    OTP_MAX_SENDS_PER_WINDOW=5,
    OTP_EXPIRATION_SECONDS=600,
)
class IssueOTPServiceTests(TestCase):
    email = "person@example.com"

    @patch("authentication.services.otp_service.secrets.randbelow", return_value=42)
    def test_first_request_normalizes_email_and_stores_only_hash(self, _):
        otp = issue_otp(" Person@Example.com ")

        record = OTPVerificationRecord.objects.get(email=self.email)
        self.assertEqual(otp, "000042")
        self.assertNotEqual(record.otp_hash, otp)
        self.assertTrue(check_password(otp, record.otp_hash))
        self.assertEqual(record.send_count, 1)

    def test_request_during_cooldown_is_rejected(self):
        issue_otp(self.email)

        with self.assertRaises(OTPRateLimitExceeded) as context:
            issue_otp(self.email)

        self.assertGreater(context.exception.retry_after, 0)
        self.assertLessEqual(context.exception.retry_after, 60)

    def test_allowed_resend_replaces_otp_and_increments_count(self):
        first_otp = issue_otp(self.email)
        record = OTPVerificationRecord.objects.get(email=self.email)
        record.last_sent_at = timezone.now() - timedelta(seconds=61)
        record.save(update_fields=["last_sent_at"])

        second_otp = issue_otp(self.email)

        record.refresh_from_db()
        self.assertEqual(record.send_count, 2)
        self.assertFalse(check_password(first_otp, record.otp_hash))
        self.assertTrue(check_password(second_otp, record.otp_hash))

    def test_maximum_requests_in_active_window_are_rejected(self):
        issue_otp(self.email)
        record = OTPVerificationRecord.objects.get(email=self.email)
        record.last_sent_at = timezone.now() - timedelta(seconds=61)
        record.send_count = 5
        record.save(update_fields=["last_sent_at", "send_count"])

        with self.assertRaises(OTPRateLimitExceeded) as context:
            issue_otp(self.email)

        self.assertGreater(context.exception.retry_after, 0)
        record.refresh_from_db()
        self.assertEqual(record.send_count, 5)

    def test_expired_window_resets_send_count(self):
        issue_otp(self.email)
        record = OTPVerificationRecord.objects.get(email=self.email)
        record.last_sent_at = timezone.now() - timedelta(seconds=61)
        record.send_window_started_at = timezone.now() - timedelta(seconds=901)
        record.send_count = 5
        record.save(
            update_fields=[
                "last_sent_at",
                "send_window_started_at",
                "send_count",
            ]
        )

        issue_otp(self.email)

        record.refresh_from_db()
        self.assertEqual(record.send_count, 1)


@override_settings(
    OTP_RESEND_COOLDOWN_SECONDS=60,
    OTP_SEND_WINDOW_SECONDS=900,
    OTP_MAX_SENDS_PER_WINDOW=5,
    OTP_EXPIRATION_SECONDS=600,
)
class SendOTPViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/auth/send-otp/"

    def test_success_response_preserves_email_contract_without_exposing_otp(self):
        response = self.client.post(
            self.url,
            {"email": " Person@Example.com "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "person@example.com")
        self.assertEqual(response.data["retry_after"], 60)
        self.assertNotIn("otp", response.data)

    def test_throttled_response_includes_retry_after_header(self):
        self.client.post(self.url, {"email": "person@example.com"}, format="json")

        response = self.client.post(
            self.url,
            {"email": "person@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.headers["Retry-After"],
            str(response.data["retry_after"]),
        )
