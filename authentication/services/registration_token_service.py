from django.conf import settings
from django.core import signing


REGISTRATION_TOKEN_SALT = "authentication.registration"
REGISTRATION_TOKEN_PURPOSE = "registration"
REGISTRATION_TOKEN_COOKIE_NAME = "registration_token"


class InvalidRegistrationToken(Exception):
    """Raised when a registration token cannot authorize registration."""


def issue_registration_token(email: str) -> str:
    """Return a signed, timestamped token for a verified email address."""
    payload = {
        "email": email.strip().lower(),
        "purpose": REGISTRATION_TOKEN_PURPOSE,
    }
    return signing.dumps(payload, salt=REGISTRATION_TOKEN_SALT)


def validate_registration_token(token: str) -> str:
    """Return the verified email carried by a valid registration token."""
    try:
        payload = signing.loads(
            token,
            salt=REGISTRATION_TOKEN_SALT,
            max_age=settings.REGISTRATION_TOKEN_EXPIRATION_SECONDS,
        )
    except (signing.BadSignature, signing.SignatureExpired) as error:
        raise InvalidRegistrationToken(
            "Registration token is invalid or expired."
        ) from error

    if not isinstance(payload, dict):
        raise InvalidRegistrationToken("Registration token payload is invalid.")

    email = payload.get("email")
    purpose = payload.get("purpose")
    if (
        not isinstance(email, str)
        or not email
        or purpose != REGISTRATION_TOKEN_PURPOSE
    ):
        raise InvalidRegistrationToken("Registration token payload is invalid.")

    return email.strip().lower()
