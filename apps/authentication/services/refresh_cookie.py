from time import time

from rest_framework_simplejwt.tokens import RefreshToken


def set_refresh_token_cookie(response, token, *, secure):
    """Persist an issued refresh token only for its remaining validity."""
    token_value = str(token)
    expires_at = int(RefreshToken(token_value)["exp"])
    remaining_seconds = max(0, expires_at - int(time()))
    response.set_cookie(
        key="refresh_token",
        value=token_value,
        max_age=remaining_seconds,
        httponly=True,
        secure=secure,
        samesite="Lax",
        path="/",
    )
