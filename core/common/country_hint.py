"""Verify optional edge country hints; these hints never authorize an operation."""
import hashlib
import hmac
import json
import re
from time import time

from django.conf import settings


def verified_country(request):
    """Accept a signed hint for this request for five minutes, with 30s clock skew.

    The versioned JSON tuple matches Pages' UTF-8 JSON.stringify encoding. Request
    URLs use the backend origin and full path/query, not the browser Pages origin.
    Missing configuration, direct requests and invalid hints simply have no country.
    """
    secret = settings.COUNTRY_HINT_SECRET
    if not secret or len(secret.encode("utf-8")) < 32:
        return None
    country = request.headers.get("X-Shop-Country", "")
    timestamp = request.headers.get("X-Shop-Country-Time", "")
    signature = request.headers.get("X-Shop-Country-Signature", "")
    if (not re.fullmatch(r"[A-Z]{2}", country) or country == "XX"
            or not re.fullmatch(r"[0-9]{10,12}", timestamp)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        return None
    age = time() - int(timestamp)
    if age < -30 or age > 300:
        return None
    message = json.dumps([
        "country-hint-v1", timestamp, country, request.method,
        f"{request.scheme}://{request.get_host()}", request.get_full_path(),
    ], separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return country if hmac.compare_digest(expected, signature) else None
