"""Serialized, user-bound password challenges with durable account limits."""
import logging
import math
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import APIException, ValidationError

from apps.authentication.models import PasswordChangeState
from apps.authentication.services.email_service import OTPDeliveryError, send_password_change_code, send_password_changed_email

logger = logging.getLogger(__name__)


class PasswordChangeUnavailable(APIException):
    status_code = 400
    default_detail = "This verification request is unavailable. Start again."

    def __init__(self, code="challenge_unavailable", detail=None, status=400, retry_after=None):
        self.status_code = status
        data = {"detail": detail or self.default_detail, "code": code}
        if retry_after is not None:
            data["retry_after"] = retry_after
        super().__init__(data)
        if retry_after is not None:
            self.detail["retry_after"] = retry_after


def _credential_state(user):
    """Bind challenges to the password without duplicating its reusable hash."""
    return salted_hmac("password-change-state", user.password).hexdigest()


def _check_passwords(user, data):
    """Apply the registration policy and reject a reused current password."""
    if not user.check_password(data["current_password"]):
        raise ValidationError({"current_password": ["Current password is incorrect."]})
    value = data["new_password"]
    if not (8 <= len(value) <= 64 and re.search(r"[A-Z]", value) and re.search(r"[a-z]", value) and re.search(r"[0-9]", value)):
        raise ValidationError({"new_password": ["Use 8–64 characters with an uppercase letter, a lowercase letter and a number."]})
    if user.check_password(value):
        raise ValidationError({"new_password": ["Choose a password different from your current password."]})


def _limit(state, now, count_field, window_field, maximum):
    """Persist account budgets independently of challenge replacement and failure."""
    window = getattr(state, window_field)
    end = window + timedelta(seconds=settings.PASSWORD_CHANGE_WINDOW_SECONDS)
    if now >= end:
        setattr(state, window_field, now)
        setattr(state, count_field, 0)
        end = now + timedelta(seconds=settings.PASSWORD_CHANGE_WINDOW_SECONDS)
    if getattr(state, count_field) >= maximum:
        raise PasswordChangeUnavailable("rate_limited", "Too many requests. Please wait and try again.", 429, max(1, math.ceil((end-now).total_seconds())))
    setattr(state, count_field, getattr(state, count_field)+1)


def _eligible(user, state, challenge_id):
    """Reject stale identifiers, consumed challenges and changed account credentials."""
    if state.challenge_id != challenge_id or state.consumed:
        raise PasswordChangeUnavailable()
    if state.email != user.email or state.credential_state != _credential_state(user):
        state.consumed = True
        raise PasswordChangeUnavailable()


def _notify(email):
    """Notification failures never undo a committed password change or expose secrets."""
    try:
        send_password_changed_email(email)
    except OTPDeliveryError:
        logger.warning("Password-change notification delivery failed")


def change_password(user_id, action, data):
    """Run one operation under the user lock; commit limit/attempt errors before raising."""
    error = None
    code = None
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        now = timezone.now()
        state, _ = PasswordChangeState.objects.get_or_create(user=user, defaults={
            "send_window_started_at": now, "request_window_started_at": now,
            "verification_window_started_at": now,
        })
        try:
            if action == "confirm":
                _limit(state, now, "verification_count", "verification_window_started_at", settings.PASSWORD_CHANGE_MAX_VERIFICATIONS)
                _eligible(user, state, data["challenge_id"])
                if now >= state.expires_at:
                    raise PasswordChangeUnavailable("code_expired", "The code has expired. Request a new code.")
                if not check_password(data["otp"], state.otp_hash):
                    state.failed_attempts += 1
                    if state.failed_attempts >= settings.PASSWORD_CHANGE_MAX_ATTEMPTS:
                        state.consumed = True
                        raise PasswordChangeUnavailable("challenge_exhausted", "Too many incorrect codes. Start again.")
                    raise ValidationError({"otp": ["Incorrect verification code."]})
                _check_passwords(user, data)
                user.set_password(data["new_password"])
                user.authentication_version += 1
                user.save(update_fields=["password", "authentication_version"])
                state.consumed = True
                state.otp_hash = make_password(None)
                transaction.on_commit(lambda: _notify(user.email))
            else:
                _limit(state, now, "request_count", "request_window_started_at", settings.PASSWORD_CHANGE_MAX_REQUESTS)
                if action == "request":
                    _check_passwords(user, data)
                else:
                    _eligible(user, state, data["challenge_id"])
                if state.last_sent_at:
                    wait = math.ceil((state.last_sent_at + timedelta(seconds=settings.PASSWORD_CHANGE_COOLDOWN_SECONDS)-now).total_seconds())
                    if wait > 0:
                        raise PasswordChangeUnavailable("rate_limited", "Please wait before requesting another code.", 429, wait)
                _limit(state, now, "send_count", "send_window_started_at", settings.PASSWORD_CHANGE_MAX_SENDS)
                if action == "request":
                    state.challenge_id = uuid.uuid4()
                    state.failed_attempts = 0
                    state.consumed = False
                    state.email = user.email
                    state.credential_state = _credential_state(user)
                code = f"{secrets.randbelow(1_000_000):06d}"
                state.otp_hash = make_password(code)
                state.expires_at = now + timedelta(seconds=settings.PASSWORD_CHANGE_EXPIRY_SECONDS)
                state.last_sent_at = now
        except (ValidationError, PasswordChangeUnavailable) as exc:
            error = exc
        state.save()
    if error:
        raise error
    if action == "confirm":
        return {"message": "Password changed successfully. Sign in with your new password."}
    try:
        send_password_change_code(state.email, code)
    except OTPDeliveryError as exc:
        raise PasswordChangeUnavailable("delivery_unavailable", "We couldn't send the verification code. Please try again later.", 503, settings.PASSWORD_CHANGE_COOLDOWN_SECONDS) from exc
    local, domain = state.email.rsplit("@", 1)
    return {"challenge_id": state.challenge_id, "masked_email": f"{local[:1]}***@{domain}",
            "expires_at": state.expires_at, "retry_after": settings.PASSWORD_CHANGE_COOLDOWN_SECONDS}
