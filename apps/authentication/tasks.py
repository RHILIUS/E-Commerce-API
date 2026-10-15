"""Background delivery for registration verification emails."""
import logging
import smtplib

from celery import shared_task
from django.contrib.auth.hashers import check_password
from django.utils import timezone

from apps.authentication.models import OTPVerificationRecord
from apps.authentication.services.email_service import OTPDeliveryError, send_otp_email

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, ignore_result=True)
def deliver_registration_otp(self, email, otp):
    """Deliver the current code, retrying temporary failures without issuing another OTP."""
    record = OTPVerificationRecord.objects.filter(email=email).first()
    if (record is None or record.is_verified or timezone.now() >= record.expires_at
            or not check_password(otp, record.otp_hash)):
        return

    try:
        send_otp_email(email, otp)
    except OTPDeliveryError as error:
        cause = error.__cause__
        if isinstance(cause, smtplib.SMTPRecipientsRefused):
            temporary = bool(cause.recipients) and all(
                400 <= response[0] < 500 for response in cause.recipients.values()
            )
        elif isinstance(cause, smtplib.SMTPResponseException):
            temporary = 400 <= cause.smtp_code < 500
        else:
            temporary = isinstance(cause, (OSError, smtplib.SMTPServerDisconnected))
        delay = 5 * (2 ** self.request.retries)
        if (temporary and self.request.retries < self.max_retries
                and (record.expires_at - timezone.now()).total_seconds() > delay):
            # Never attach transport errors, addresses or codes to Celery retry logs.
            raise self.retry(exc=OTPDeliveryError("Temporary OTP delivery failure."),
                             countdown=delay, argsrepr="[<redacted>, <redacted>]")
        logger.warning("Registration OTP delivery failed; task_id=%s", self.request.id)
