import smtplib

from django.conf import settings
from django.core.mail import send_mail


class OTPDeliveryError(Exception):
    """Raised when an OTP email cannot be handed to the email backend."""


def send_otp_email(email: str, otp: str) -> None:
    """Send a plain-text email containing the one-time verification code."""
    expiration_minutes = max(1, settings.OTP_EXPIRATION_SECONDS // 60)

    try:
        sent_count = send_mail(
            subject="Your E-Commerce verification code",
            message=(
                f"Your verification code is: {otp}\n\n"
                f"This code expires in {expiration_minutes} minutes.\n\n"
                "If you did not request this code, you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except (smtplib.SMTPException, OSError, ValueError) as error:
        raise OTPDeliveryError("Unable to send the OTP email.") from error

    if sent_count != 1:
        raise OTPDeliveryError("The email backend did not accept the OTP email.")


def _send_password_email(email, subject, message):
    """Send account-security email using the configured transport without exposing secrets."""
    try:
        count = send_mail(subject=subject, message=message,
                          from_email=settings.DEFAULT_FROM_EMAIL,
                          recipient_list=[email], fail_silently=False)
    except (smtplib.SMTPException, OSError, ValueError) as error:
        raise OTPDeliveryError("Unable to send account-security email.") from error
    if count != 1:
        raise OTPDeliveryError("Email provider did not accept the message.")


def send_password_change_code(email, otp):
    """Deliver an OTP dedicated to the signed-in password-change workflow."""
    _send_password_email(email, "Verify your E-Commerce password change",
        f"Your password-change verification code is: {otp}\n\n"
        f"This code expires in {settings.PASSWORD_CHANGE_EXPIRY_SECONDS // 60} minutes.\n\n"
        "If you did not request this change, secure your account. Your password has not changed.")
