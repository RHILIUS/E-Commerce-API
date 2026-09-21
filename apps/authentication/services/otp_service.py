import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from apps.authentication.models import OTPVerificationRecord


class OTPRateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("OTP request rate limit exceeded.")


# Helper that calculates how many seconds remaining until a certain time
def _remaining_seconds(until):
    return max(1, int((until - timezone.now()).total_seconds()) + 1)


def _generate_otp():
    otp = f"{secrets.randbelow(1_000_000):06d}"
    return otp, make_password(otp)


@transaction.atomic
def issue_otp(email: str) -> str:
    """Persist a hashed OTP and return the plaintext value for delivery."""
    email = email.strip().lower()
    now = timezone.now()

    record = (
        OTPVerificationRecord.objects
        .select_for_update()
        .filter(email=email)
        .first()
    )

    # get_or_create handles two simultaneous first requests safely against the
    # model's unique email constraint. Chaining select_for_update ensures that
    # the request that did not create the row locks it before checking limits.
    if record is None:
        otp, otp_hash = _generate_otp()
        record, created = (
            OTPVerificationRecord.objects
            .select_for_update()
            .get_or_create(
                email=email,
                defaults={
                    "otp_hash": otp_hash,
                    "last_sent_at": now,
                    "expires_at": now + timedelta(
                        seconds=settings.OTP_EXPIRATION_SECONDS
                    ),
                    "failed_attempts": 0,
                    "send_count": 1,
                    "send_window_started_at": now,
                    "is_verified": False,
                    "verified_at": None,
                },
            )
        )

        if created:
            return otp

    # Rate Limit check
    cooldown_ends_at = record.last_sent_at + timedelta(
        seconds=settings.OTP_RESEND_COOLDOWN_SECONDS
    )

    # Don't allow the request to proceed if there is still a cooldown
    if now < cooldown_ends_at:
        raise OTPRateLimitExceeded(
            retry_after=_remaining_seconds(cooldown_ends_at)
        )

    window_ends_at = record.send_window_started_at + timedelta(
        seconds=settings.OTP_SEND_WINDOW_SECONDS
    )

    # Reset the window related fields if the window duration already ended
    if now >= window_ends_at:
        record.send_count = 0
        record.send_window_started_at = now
    elif record.send_count >= settings.OTP_MAX_SENDS_PER_WINDOW:
        raise OTPRateLimitExceeded(
            retry_after=_remaining_seconds(window_ends_at)
        )

    otp, otp_hash = _generate_otp()
    expires_at = now + timedelta(
        seconds=settings.OTP_EXPIRATION_SECONDS
    )

    record.otp_hash = otp_hash
    record.last_sent_at = now
    record.expires_at = expires_at
    record.failed_attempts = 0
    record.send_count += 1
    record.is_verified = False
    record.verified_at = None

    record.save(
        update_fields=[
            "otp_hash",
            "last_sent_at",
            "expires_at",
            "failed_attempts",
            "send_count",
            "send_window_started_at",
            "is_verified",
            "verified_at",
        ]
    )

    return otp
