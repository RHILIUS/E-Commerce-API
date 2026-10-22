"""Authenticated email verification and single-use permission for password changes."""
import hashlib
import logging
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
from django.utils.crypto import constant_time_compare
from kombu.exceptions import OperationalError
from rest_framework.exceptions import APIException, ValidationError

from apps.authentication.models import PasswordChangeState
from apps.authentication.tasks import deliver_password_change_notice, deliver_password_change_otp
from .password_challenge import credential_state

COOKIE_NAME = "password_change"
COOKIE_PATH = "/api/account/change-password/"


class PasswordChangeUnavailable(APIException):
    """Return a stable workflow code and optional server-authoritative retry time."""
    def __init__(self, code="challenge_unavailable", detail="This verification request is unavailable. Start again.",
                 status=400, retry_after=None):
        self.status_code = status
        data = {"detail": detail, "code": code}
        if retry_after is not None:
            data["retry_after"] = retry_after
        super().__init__(data)
        if retry_after is not None:
            self.detail["retry_after"] = retry_after


def masked_email(email):
    """Show the fixed destination without returning the complete email local part."""
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _check_password(user, value):
    """Enforce the existing password policy at the final authorized save."""
    if not (8 <= len(value) <= 64 and re.search(r"[A-Z]", value)
            and re.search(r"[a-z]", value) and re.search(r"[0-9]", value)):
        raise ValidationError({"new_password": ["Use 8–64 characters with an uppercase letter, a lowercase letter and a number."]})
    if user.check_password(value):
        raise ValidationError({"new_password": ["Choose a password different from your current password."]})


def _window(state, now):
    """Reset a shared window only on expiry, never on resend or form restart."""
    end = state.window_started_at + timedelta(seconds=settings.PASSWORD_CHANGE_WINDOW_SECONDS)
    if now >= end:
        state.window_started_at = now
        state.request_count = state.send_count = state.verification_count = state.failed_attempts = 0
        end = now + timedelta(seconds=settings.PASSWORD_CHANGE_WINDOW_SECONDS)
    return max(1, math.ceil((end - now).total_seconds()))


def _charge(state, field, maximum, wait):
    """Charge expected failures too; the caller commits counters before raising."""
    if getattr(state, field) >= maximum:
        raise PasswordChangeUnavailable("rate_limited", "Too many requests. Please wait and try again.", 429, wait)
    setattr(state, field, getattr(state, field) + 1)


def _bound(user, state):
    """Reject account changes even if an old challenge or cookie is still present."""
    return (user.is_active and user.has_usable_password() and state.email == user.email
            and constant_time_compare(state.credential_state, credential_state(user)))


def _eligible(user, state, challenge_id):
    """Authorize only the current unconsumed code for this account."""
    if state.challenge_id != challenge_id or state.consumed:
        raise PasswordChangeUnavailable()
    if not _bound(user, state):
        state.consumed = True
        state.grant_hash = ""
        state.grant_expires_at = None
        raise PasswordChangeUnavailable()


def _publish_password_code(user_id, challenge_id, code, expires_at):
    """Publish after commit; failure retains issuance, invalidation and charged budgets."""
    try:
        deliver_password_change_otp.apply_async(
            args=[user_id, str(challenge_id), code], argsrepr="[<redacted>]",
            expires=expires_at, retry=False,
        )
    except (OperationalError, OSError) as error:
        raise PasswordChangeUnavailable("delivery_unavailable", "We couldn't queue the verification email. Please try again later.",
                                        503, settings.PASSWORD_CHANGE_COOLDOWN_SECONDS) from error


def _notify(user_id, email):
    """Do not report a failed password save when post-commit notification queuing fails."""
    try:
        deliver_password_change_notice.apply_async(args=[user_id, email], argsrepr="[<redacted>]", retry=False)
    except (OperationalError, OSError):
        logging.getLogger(__name__).warning("Password change notification could not be queued.")


def change_password(user_id, action, data):
    """Issue/resend or verify a code under account/state locks, preserving rejected-operation budgets."""
    error = None
    code = None
    secret = None
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        now = timezone.now()
        state, _ = PasswordChangeState.objects.get_or_create(user=user, defaults={"window_started_at": now})
        state = PasswordChangeState.objects.select_for_update().get(pk=state.pk)
        try:
            wait = _window(state, now)
            field = "verification_count" if action == "verify" else "request_count"
            maximum = settings.PASSWORD_CHANGE_MAX_VERIFICATIONS if action == "verify" else settings.PASSWORD_CHANGE_MAX_REQUESTS
            _charge(state, field, maximum, wait)
            if state.failed_attempts >= settings.PASSWORD_CHANGE_MAX_ATTEMPTS:
                raise PasswordChangeUnavailable("challenge_exhausted", "Too many incorrect codes. Wait and start again.", 429, wait)
            if action == "verify":
                _eligible(user, state, data["challenge_id"])
                if state.expires_at is None or now >= state.expires_at:
                    raise PasswordChangeUnavailable("code_expired", "The code has expired. Request a new code.")
                if not check_password(data["otp"], state.otp_hash):
                    state.failed_attempts += 1
                    if state.failed_attempts >= settings.PASSWORD_CHANGE_MAX_ATTEMPTS:
                        state.consumed = True
                        raise PasswordChangeUnavailable("challenge_exhausted", "Too many incorrect codes. Wait and start again.", 429, wait)
                    raise PasswordChangeUnavailable("invalid_code", "Incorrect verification code.")
                secret = secrets.token_urlsafe(32)
                state.consumed = True
                state.otp_hash = make_password(None)
                state.grant_hash = hashlib.sha256(secret.encode()).hexdigest()
                state.grant_expires_at = now + timedelta(seconds=settings.PASSWORD_CHANGE_GRANT_SECONDS)
            else:
                if not user.is_active or not user.has_usable_password():
                    raise PasswordChangeUnavailable()
                if action == "resend":
                    _eligible(user, state, data["challenge_id"])
                if state.last_sent_at:
                    cooldown = math.ceil((state.last_sent_at + timedelta(seconds=settings.PASSWORD_CHANGE_COOLDOWN_SECONDS) - now).total_seconds())
                    if cooldown > 0:
                        raise PasswordChangeUnavailable("rate_limited", "Please wait before requesting another code.", 429, cooldown)
                _charge(state, "send_count", settings.PASSWORD_CHANGE_MAX_SENDS, wait)
                state.challenge_id = uuid.uuid4()
                state.consumed = False
                state.email = user.email
                state.credential_state = credential_state(user)
                code = f"{secrets.randbelow(1_000_000):06d}"
                state.otp_hash = make_password(code)
                state.expires_at = now + timedelta(seconds=settings.PASSWORD_CHANGE_EXPIRY_SECONDS)
                state.last_sent_at = now
                state.grant_hash = ""
                state.grant_expires_at = None
        except (ValidationError, PasswordChangeUnavailable) as exc:
            error = exc
        state.save()
        if code is not None and error is None:
            transaction.on_commit(partial(_publish_password_code, user.pk, state.challenge_id, code, state.expires_at))
    if error:
        raise error
    if action == "verify":
        return f"{state.challenge_id}:{secret}", state.grant_expires_at
    return {"challenge_id": state.challenge_id, "masked_email": masked_email(state.email),
            "expires_at": state.expires_at, "retry_after": settings.PASSWORD_CHANGE_COOLDOWN_SECONDS}


def confirm_password(user_id, cookie, new_password):
    """Consume only this authenticated account's permission and atomically revoke its sessions."""
    unavailable = PasswordChangeUnavailable("change_unavailable", "Change permission is invalid or expired. Verify your email again.")
    try:
        identifier, secret = cookie.split(":", 1)
        identifier = uuid.UUID(identifier)
        if len(secret) != 43:
            raise ValueError
    except (ValueError, AttributeError):
        raise unavailable
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        state = PasswordChangeState.objects.select_for_update().filter(user_id=user_id).first()
        if (state is None or state.challenge_id != identifier or not _bound(user, state)
                or not state.grant_hash or state.grant_expires_at is None
                or timezone.now() >= state.grant_expires_at
                or not constant_time_compare(state.grant_hash, hashlib.sha256(secret.encode()).hexdigest())):
            raise unavailable
        _check_password(user, new_password)
        user.set_password(new_password)
        user.authentication_version += 1
        user.save(update_fields=["password", "authentication_version"])
        state.grant_hash = ""
        state.grant_expires_at = None
        state.save(update_fields=["grant_hash", "grant_expires_at"])
        transaction.on_commit(partial(_notify, user.pk, user.email))
