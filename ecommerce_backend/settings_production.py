"""Runtime settings for the private API behind the Compose HTTPS proxy."""

from .settings import *  # noqa: F403
from .settings import BASE_DIR, REST_FRAMEWORK, env

DEBUG = False
# ALLOW_DEV_RESET inherits the explicit .env opt-in for disposable demo deployments.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Nginx overwrites this header; Gunicorn has no published host port.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": 1}
