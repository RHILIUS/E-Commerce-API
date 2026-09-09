from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import mail, signing
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from authentication.models import OTPVerificationRecord
from authentication.services.otp_service import (
    OTPRateLimitExceeded,
    issue_otp,
)
from authentication.services.registration_token_service import (
    REGISTRATION_TOKEN_COOKIE_NAME,
    REGISTRATION_TOKEN_SALT,
    InvalidRegistrationToken,
    issue_registration_token,
    validate_registration_token,
)


@override_settings(REGISTRATION_TOKEN_EXPIRATION_SECONDS=900)
class RegistrationTokenServiceTests(SimpleTestCase):
    def test_issued_token_round_trip_returns_normalized_email(self):
        token = issue_registration_token(" Person@Example.com ")

        self.assertEqual(
            validate_registration_token(token),
            "person@example.com",
        )

    def test_tampered_token_is_rejected(self):
        token = issue_registration_token("person@example.com")
        tampered_token = f"{token}tampered"

        with self.assertRaises(InvalidRegistrationToken):
            validate_registration_token(tampered_token)

    @patch("django.core.signing.time.time", side_effect=[1000, 1901])
    def test_expired_token_is_rejected(self, _):
        token = issue_registration_token("person@example.com")

        with self.assertRaises(InvalidRegistrationToken):
            validate_registration_token(token)

    def test_invalid_purpose_is_rejected(self):
        token = signing.dumps(
            {"email": "person@example.com", "purpose": "password-reset"},
            salt=REGISTRATION_TOKEN_SALT,
        )

        with self.assertRaises(InvalidRegistrationToken):
            validate_registration_token(token)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="E-Commerce <noreply@example.com>",
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
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["person@example.com"])
        self.assertEqual(
            mail.outbox[0].subject,
            "Your E-Commerce verification code",
        )

        record = OTPVerificationRecord.objects.get(email="person@example.com")
        otp = mail.outbox[0].body.split(
            "Your verification code is: ", 1)[1][:6]
        self.assertTrue(check_password(otp, record.otp_hash))

    def test_throttled_response_includes_retry_after_header(self):
        self.client.post(
            self.url, {"email": "person@example.com"}, format="json")

        response = self.client.post(
            self.url,
            {"email": "person@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code,
                         status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.headers["Retry-After"],
            str(response.data["retry_after"]),
        )

    @patch(
        "authentication.services.email_service.send_mail",
        side_effect=OSError("SMTP unavailable"),
    )
    def test_delivery_failure_returns_service_unavailable_without_exposing_otp(
        self,
        _,
    ):
        response = self.client.post(
            self.url,
            {"email": "person@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code,
                         status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["retry_after"], 60)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertNotIn("otp", response.data)


@override_settings(
    OTP_MAX_VERIFICATION_ATTEMPTS=3,
    REGISTRATION_TOKEN_EXPIRATION_SECONDS=900,
)
class VerifyOTPViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/auth/verify-otp/"

    def create_record(self, *, expires_at, otp="123456", failed_attempts=0):
        now = timezone.now()
        return OTPVerificationRecord.objects.create(
            email="person@example.com",
            otp_hash=make_password(otp),
            last_sent_at=now,
            expires_at=expires_at,
            send_window_started_at=now,
            failed_attempts=failed_attempts,
        )

    def test_correct_otp_marks_record_verified_and_consumes_otp(self):
        record = self.create_record(
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = self.client.post(
            self.url,
            {"email": " Person@Example.com ", "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"message": "Email verified successfully."},
        )

        cookie = response.cookies[REGISTRATION_TOKEN_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(bool(cookie["secure"]), not settings.DEBUG)
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/api/auth/register/")
        self.assertEqual(int(cookie["max-age"]), 900)
        self.assertEqual(
            validate_registration_token(cookie.value),
            "person@example.com",
        )
        self.assertNotIn(REGISTRATION_TOKEN_COOKIE_NAME, response.data)

        record.refresh_from_db()
        self.assertTrue(record.is_verified)
        self.assertIsNotNone(record.verified_at)
        self.assertFalse(check_password("123456", record.otp_hash))

    def test_consumed_otp_cannot_be_verified_again(self):
        record = self.create_record(
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        payload = {"email": "person@example.com", "otp": "123456"}

        first_response = self.client.post(self.url, payload, format="json")
        second_response = self.client.post(self.url, payload, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(second_response.data["otp"], ["Invalid OTP"])
        record.refresh_from_db()
        self.assertTrue(record.is_verified)

    def test_expired_record_is_deleted_and_returns_gone(self):
        record = self.create_record(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.post(
            self.url,
            {"email": "person@example.com", "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertEqual(response.data["email"], ["Expired or invalid OTP"])
        self.assertFalse(
            OTPVerificationRecord.objects.filter(pk=record.pk).exists()
        )

    def test_missing_record_returns_bad_request(self):
        response = self.client.post(
            self.url,
            {"email": "missing@example.com", "otp": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["email"], ["Invalid OTP or email."])

    def test_invalid_otp_increments_failed_attempts(self):
        record = self.create_record(
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = self.client.post(
            self.url,
            {"email": "person@example.com", "otp": "000000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["otp"], ["Invalid OTP"])
        record.refresh_from_db()
        self.assertEqual(record.failed_attempts, 1)

    def test_maximum_failed_attempt_deletes_record_and_returns_too_many_requests(self):
        record = self.create_record(
            expires_at=timezone.now() + timedelta(minutes=10),
            failed_attempts=2,
        )

        response = self.client.post(
            self.url,
            {"email": "person@example.com", "otp": "000000"},
            format="json",
        )

        self.assertEqual(response.status_code,
                         status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.data["otp"],
            ["You have exceeded the maximum attempts. Please request a new OTP"],
        )
        self.assertFalse(
            OTPVerificationRecord.objects.filter(pk=record.pk).exists()
        )
