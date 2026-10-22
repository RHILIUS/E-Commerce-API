"""Background delivery for registration and password-change verification emails."""
import logging
import smtplib

from celery import shared_task
from django.contrib.auth.hashers import check_password
from django.utils import timezone

from apps.authentication.models import OTPVerificationRecord, PasswordChangeState
from apps.account.services.password_challenge import credential_state
from apps.authentication.services.email_service import OTPDeliveryError, send_otp_email, send_password_change_code

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, ignore_result=True)
def deliver_registration_otp(self, email, otp):
    """Deliver the current code, retrying temporary failures without issuing another OTP."""
    record = OTPVerificationRecord.objects.filter(email=email).first()
    if (record is None or record.is_verified or timezone.now() >= record.expires_at
            or not check_password(otp, record.otp_hash)):
        return

    _deliver_code(self, email, otp, record.expires_at, send_otp_email)


@shared_task(bind=True, max_retries=3, ignore_result=True)
def deliver_password_change_otp(self, user_id, challenge_id, otp):
    """Deliver only a current, owned challenge bound to unchanged account credentials."""
    state = PasswordChangeState.objects.select_related("user").filter(
        user_id=user_id, challenge_id=challenge_id,
    ).first()
    if (state is None or state.consumed or not state.user.is_active
            or state.expires_at is None or timezone.now() >= state.expires_at
            or state.email != state.user.email
            or state.credential_state != credential_state(state.user)
            or not check_password(otp, state.otp_hash)):
        return
    _deliver_code(self, state.email, otp, state.expires_at, send_password_change_code)


def _deliver_code(task, email, otp, expires_at, send_code):
    """Share bounded transport retries while each task owns its challenge validity checks."""
    try:
        send_code(email, otp)
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
        delay = 5 * (2 ** task.request.retries)
        if (temporary and task.request.retries < task.max_retries
                and (expires_at - timezone.now()).total_seconds() > delay):
            # Never attach transport errors, addresses or codes to Celery retry logs.
            raise task.retry(exc=OTPDeliveryError("Temporary OTP delivery failure."),
                             countdown=delay, argsrepr="[<redacted>]")
        logger.warning("OTP delivery failed; task_id=%s", task.request.id)


@shared_task(bind=True, max_retries=3, ignore_result=True)
def deliver_password_reset_otp(self, state_id, challenge_id, otp):
    """Discard unknown accounts and obsolete reset tasks before attempting transport."""
    from apps.authentication.models import PasswordResetState
    from apps.authentication.services.password_reset import _eligible
    from apps.authentication.services.email_service import send_password_reset_code
    state = PasswordResetState.objects.select_related("user").filter(pk=state_id, challenge_id=challenge_id).first()
    if (state is None or state.otp_consumed or state.expires_at is None
            or timezone.now() >= state.expires_at or not _eligible(state.user, state)
            or not check_password(otp, state.otp_hash)):
        return
    _deliver_code(self, state.user.email, otp, state.expires_at, send_password_reset_code)


@shared_task(bind=True, max_retries=3, ignore_result=True)
def deliver_password_reset_notice(self, user_id, email):
    """Notify the reset recipient, with bounded transport retries and no credential payload."""
    from datetime import timedelta
    from django.contrib.auth import get_user_model
    from apps.authentication.services.email_service import send_password_reset_notice
    if not get_user_model().objects.filter(pk=user_id, email=email).exists():
        return
    _deliver_code(self, email, "", timezone.now() + timedelta(seconds=60), send_password_reset_notice)
