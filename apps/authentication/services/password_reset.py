"""Public, email-bound password recovery with durable budgets and single-use grants."""
import hashlib
import math
import re
import secrets
import uuid
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from kombu.exceptions import OperationalError
from rest_framework.exceptions import APIException, ValidationError

from apps.authentication.models import PasswordResetState

COOKIE_NAME = "password_reset"
COOKIE_PATH = "/api/auth/forgot-password/"


class ResetError(APIException):
    """Expose recovery information without distinguishing eligible accounts."""
    def __init__(self, code, detail, status=400, retry_after=None):
        self.status_code = status
        data = {"code": code, "detail": detail}
        if retry_after is not None:
            data["retry_after"] = retry_after
        super().__init__(data)
        if retry_after is not None:
            self.detail["retry_after"] = retry_after


def credential_binding(user):
    """Invalidate issued codes and grants after any encoded-password change."""
    return salted_hmac("password-reset-credential", user.password).hexdigest()


def _window_end(state, now):
    """Keep resend/restart from resetting the shared email budget or attempt counter."""
    end = state.window_started_at + timedelta(seconds=settings.PASSWORD_RESET_WINDOW_SECONDS)
    if now >= end:
        state.window_started_at = now
        state.request_count = state.send_count = state.verification_count = state.failed_attempts = 0
        end = now + timedelta(seconds=settings.PASSWORD_RESET_WINDOW_SECONDS)
    return max(1, math.ceil((end - now).total_seconds()))


def _charge(state, field, maximum, wait):
    """Charge rejected operations too; callers persist expected failures before raising."""
    if getattr(state, field) >= maximum:
        raise ResetError("rate_limited", "Too many requests. Please wait and try again.", 429, wait)
    setattr(state, field, getattr(state, field) + 1)


def _eligible(user, state):
    """Require the original active account, email and credential state at every use."""
    return (user is not None and user.is_active and user.has_usable_password()
            and state.user_id == user.pk and state.email == user.email.strip().lower()
            and constant_time_compare(state.credential_state, credential_binding(user)))


def _publish_code(state_id, challenge_id, otp, expires_at):
    """Publish the same task shape for every email; the worker decides delivery eligibility."""
    from apps.authentication.tasks import deliver_password_reset_otp
    try:
        deliver_password_reset_otp.apply_async(
            args=[state_id, str(challenge_id), otp], argsrepr="[<redacted>]",
            expires=expires_at, retry=False,
        )
    except (OperationalError, OSError) as error:
        raise ResetError("delivery_unavailable", "We couldn't queue the request. Please try again later.",
                         503, settings.PASSWORD_RESET_COOLDOWN_SECONDS) from error


def request_code(email, challenge_id=None):
    """Issue/resend uniformly for known and unknown emails; preserve attempts across restarts."""
    error = None
    now = timezone.now()
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(email__iexact=email).first()
        state, _ = PasswordResetState.objects.get_or_create(email=email, defaults={"window_started_at": now})
        state = PasswordResetState.objects.select_for_update().get(pk=state.pk)
        try:
            wait = _window_end(state, now)
            _charge(state, "request_count", settings.PASSWORD_RESET_MAX_REQUESTS, wait)
            if challenge_id is not None and state.challenge_id != challenge_id:
                raise ResetError("challenge_unavailable", "This request is unavailable. Start again.")
            if state.failed_attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
                raise ResetError("rate_limited", "Too many incorrect codes. Please wait and start again.", 429, wait)
            if state.last_sent_at:
                cooldown = math.ceil((state.last_sent_at + timedelta(seconds=settings.PASSWORD_RESET_COOLDOWN_SECONDS) - now).total_seconds())
                if cooldown > 0:
                    raise ResetError("rate_limited", "Please wait before requesting another code.", 429, cooldown)
            _charge(state, "send_count", settings.PASSWORD_RESET_MAX_SENDS, wait)
            state.challenge_id = uuid.uuid4()
            state.user = user if user and user.is_active and user.has_usable_password() else None
            state.credential_state = credential_binding(user) if state.user_id else ""
            otp = f"{secrets.randbelow(1_000_000):06d}"
            state.otp_hash = make_password(otp)
            state.otp_consumed = False
            state.expires_at = now + timedelta(seconds=settings.PASSWORD_RESET_EXPIRY_SECONDS)
            state.last_sent_at = now
            state.grant_hash = ""
            state.grant_expires_at = None
        except ResetError as exc:
            error = exc
        state.save()
        if error is None:
            transaction.on_commit(partial(_publish_code, state.pk, state.challenge_id, otp, state.expires_at))
    if error:
        raise error
    return {"challenge_id": state.challenge_id, "expires_at": state.expires_at,
            "retry_after": settings.PASSWORD_RESET_COOLDOWN_SECONDS,
            "message": "If an account exists for this email, we'll send a verification code."}


def verify_code(email, challenge_id, otp):
    """Consume a valid OTP and return a secret only for the backend's HttpOnly cookie."""
    error = None
    now = timezone.now()
    secret = None
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(email__iexact=email).first()
        state = PasswordResetState.objects.select_for_update().filter(email=email).first()
        if state is None:
            # Match the cost of a failed code check without creating a verification-only record.
            make_password(otp)
            raise ResetError("invalid_code", "Incorrect or unavailable code. Check the code or start again.")
        try:
            wait = _window_end(state, now)
            _charge(state, "verification_count", settings.PASSWORD_RESET_MAX_VERIFICATIONS, wait)
            if state.failed_attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
                raise ResetError("challenge_exhausted", "Too many incorrect codes. Wait and start again.", 429, wait)
            if state.challenge_id != challenge_id or state.otp_consumed:
                raise ResetError("challenge_unavailable", "This request is unavailable. Start again.")
            if state.expires_at is None or now >= state.expires_at:
                raise ResetError("code_expired", "The code has expired. Request a new code.")
            matches = check_password(otp, state.otp_hash)
            if not matches or not _eligible(user, state):
                state.failed_attempts += 1
                if state.failed_attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
                    state.otp_consumed = True
                    raise ResetError("challenge_exhausted", "Too many incorrect codes. Wait and start again.", 429, wait)
                raise ResetError("invalid_code", "Incorrect or unavailable code. Check the code or start again.")
            secret = secrets.token_urlsafe(32)
            state.otp_consumed = True
            state.otp_hash = make_password(None)
            state.grant_hash = hashlib.sha256(secret.encode()).hexdigest()
            state.grant_expires_at = now + timedelta(seconds=settings.PASSWORD_RESET_GRANT_SECONDS)
        except ResetError as exc:
            error = exc
        state.save()
    if error:
        raise error
    return f"{state.challenge_id}:{secret}", state.grant_expires_at


def _notify(user_id, email):
    """A notification publication failure must never turn a committed reset into a failed save."""
    import logging
    from apps.authentication.tasks import deliver_password_reset_notice
    try:
        deliver_password_reset_notice.apply_async(args=[user_id, email], argsrepr="[<redacted>]", retry=False)
    except (OperationalError, OSError):
        logging.getLogger(__name__).warning("Password reset notification could not be queued.")


def confirm_reset(cookie, new_password):
    """Serialize competing resets and commit password, session revocation and grant consumption together."""
    try:
        identifier, secret = cookie.split(":", 1)
        identifier = uuid.UUID(identifier)
        if len(secret) != 43:
            raise ValueError
    except (ValueError, AttributeError):
        raise ResetError("reset_unavailable", "Reset permission is invalid or expired. Verify your email again.")
    # Resolve ownership before acquiring locks, then re-read the state under those locks.
    owner_id = PasswordResetState.objects.filter(challenge_id=identifier).values_list("user_id", flat=True).first()
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(pk=owner_id).first()
        state = PasswordResetState.objects.select_for_update().filter(challenge_id=identifier).first()
        if (state is None or not _eligible(user, state) or not state.grant_hash
                or state.grant_expires_at is None or timezone.now() >= state.grant_expires_at
                or not constant_time_compare(state.grant_hash, hashlib.sha256(secret.encode()).hexdigest())):
            raise ResetError("reset_unavailable", "Reset permission is invalid or expired. Verify your email again.")
        if not (8 <= len(new_password) <= 64 and re.search(r"[A-Z]", new_password)
                and re.search(r"[a-z]", new_password) and re.search(r"[0-9]", new_password)):
            raise ValidationError({"new_password": ["Use 8–64 characters with an uppercase letter, a lowercase letter and a number."]})
        if user.check_password(new_password):
            raise ValidationError({"new_password": ["Choose a password different from your current password."]})
        user.set_password(new_password)
        user.authentication_version += 1
        user.save(update_fields=["password", "authentication_version"])
        state.grant_hash = ""
        state.grant_expires_at = None
        state.save(update_fields=["grant_hash", "grant_expires_at", "updated_at"])
        transaction.on_commit(partial(_notify, user.pk, user.email))
