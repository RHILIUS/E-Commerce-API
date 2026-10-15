"""Credential binding shared by challenge issuance and asynchronous delivery."""
from django.utils.crypto import salted_hmac


def credential_state(user):
    """Bind a challenge to the current password without persisting another reusable hash."""
    return salted_hmac("password-change-state", user.password).hexdigest()
