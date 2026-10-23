"""Discover browsing currencies without coupling display choices to payment support."""
import json
import re
from time import time
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import APIException

CACHE_KEY = "display-currencies:v1"
FRESH_SECONDS = 86400
STALE_SECONDS = 7 * 86400
REFRESH_SECONDS = 300


class CurrencyOptionsUnavailable(APIException):
    status_code = 503
    default_detail = "Currency options are temporarily unavailable. Please try again."
    default_code = "currency_options_unavailable"


def _fetch_codes():
    """Read bounded provider metadata; reject malformed or incomplete capability lists."""
    with urlopen(Request(
        "https://api.frankfurter.dev/v2/currencies",
        headers={"Accept": "application/json", "User-Agent": "E-Commerce-Catalog/1.0"},
    ), timeout=settings.CATALOG_FX_TIMEOUT_SECONDS) as response:
        body = response.read(131073)
    if len(body) > 131072:
        raise ValueError("Currency response is too large")
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError("Expected currency rows")
    codes = []
    for row in rows:
        code = row.get("iso_code") if isinstance(row, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("Invalid currency code")
        codes.append(code)
    if "USD" not in codes or len(set(codes)) != len(codes):
        raise ValueError("Incomplete or duplicate currency capabilities")
    return tuple(sorted(codes))


def display_currency_codes():
    """Use daily provider choices, retaining validated stale metadata during outages.

    A refresh gate limits concurrent and failed calls. A cold failure is unavailable,
    not evidence that a requested currency is unsupported. Restrictions apply after
    caching so configuration changes do not require another provider request.
    """
    now = time()
    saved = cache.get(CACHE_KEY)
    if saved and now - saved["fetched_at"] >= STALE_SECONDS:
        saved = None
    if not saved or now - saved["fetched_at"] >= FRESH_SECONDS:
        if cache.add(CACHE_KEY + ":refresh", True, timeout=REFRESH_SECONDS):
            try:
                saved = {"codes": _fetch_codes(), "fetched_at": now}
                cache.set(CACHE_KEY, saved, timeout=STALE_SECONDS)
            except (OSError, ValueError, TypeError):
                pass
    if saved is None:
        raise CurrencyOptionsUnavailable()
    allowed = settings.DISPLAY_CURRENCY_ALLOWLIST
    return tuple(code for code in saved["codes"] if allowed is None or code in allowed)
