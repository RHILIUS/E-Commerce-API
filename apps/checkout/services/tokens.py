"""Customer-bound review snapshots for later placement; signing is not encryption."""
from django.core import signing
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

PREVIEW_MAX_AGE = 300
PREVIEW_SALT = 'checkout.preview.v1'


class PreviewExpired(APIException):
    status_code = 409
    default_detail = 'Checkout preview expired. Request and review a fresh preview.'
    default_code = 'preview_expired'


def sign_preview(user, snapshot):
    """Sign only server-calculated purchase terms and return their absolute expiry."""
    expires = int(timezone.now().timestamp()) + PREVIEW_MAX_AGE
    payload = {'version': 1, 'user_id': user.pk, 'expires_at': expires, **snapshot}
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True), expires


def read_preview(token, user):
    """Verify signature, lifetime and purchaser before a future placement revalidates stock."""
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise PreviewExpired() from exc
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise ValidationError({'preview_token': ['Invalid checkout preview.']}) from exc
    if payload.get('version') != 1 or payload.get('user_id') != user.pk:
        raise ValidationError({'preview_token': ['Invalid checkout preview.']})
    if payload['expires_at'] <= timezone.now().timestamp():
        raise PreviewExpired()
    return payload
