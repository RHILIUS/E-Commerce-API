"""Coordinate committed OTP issuance with background delivery."""
from functools import partial

from django.conf import settings
from django.db import transaction

from apps.authentication.services.otp_service import issue_otp
from apps.authentication.tasks import deliver_registration_otp


def _publish(email, otp):
    """Keep secrets out of task representations; the broker payload still contains the code."""
    deliver_registration_otp.apply_async(
        args=[email, otp], argsrepr="[<redacted>, <redacted>]",
        expires=settings.OTP_EXPIRATION_SECONDS, retry=False,
    )


@transaction.atomic
def queue_registration_otp(email):
    """Persist issuance before publishing; publish failure retains code and send budgets."""
    otp = issue_otp(email)
    transaction.on_commit(partial(_publish, email, otp))
