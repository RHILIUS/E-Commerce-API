"""Display conversions from each store's pricing currency, without modifying prices."""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.error import URLError
from urllib.request import Request, urlopen

from babel.numbers import get_currency_precision
from django.conf import settings
from django.core.cache import cache

from core.common.currencies import default_currency_for_user

logger = logging.getLogger(__name__)


def _fetch_rates(target, now):
    """Fetch one table for the viewer currency; retain only valid, recent rows."""
    with urlopen(
        Request(
            f"https://api.frankfurter.dev/v2/rates?base={target.lower()}",
            headers={"Accept": "application/json", "User-Agent": "E-Commerce-Catalog/1.0"},
        ),
        timeout=settings.CATALOG_FX_TIMEOUT_SECONDS,
    ) as response:
        payload = json.loads(response.read(65536), parse_float=Decimal)
    if not isinstance(payload, list):
        raise ValueError("Invalid exchange-rate table")
    rates = {}
    for row in payload:
        try:
            rate = Decimal(str(row["rate"]))
            rate_date = date.fromisoformat(row["date"])
            expires = datetime.combine(rate_date + timedelta(days=7), time(), timezone.utc)
            source = row["quote"]
            if (row["base"] != target or not isinstance(source, str)
                    or len(source) != 3 or not source.isascii() or not source.isupper()
                    or not source.isalpha() or not rate.is_finite()
                    or not Decimal("0.000000001") <= rate <= Decimal("1000000000")
                    or rate_date > now.date() or expires <= now):
                continue
            # Provider returns source units per target unit; prices divide by this rate.
            rates[source] = {"rate": rate, "expires_at": expires.timestamp()}
        except (ValueError, InvalidOperation, KeyError, TypeError):
            continue
    if not rates:
        raise ValueError("No usable exchange rates")
    return {"rates": rates, "fetched_at": now.timestamp()}


def get_display_rates(user, source_currencies, *, target_currency=None):
    """Resolve a page's conversions in at most one HTTP call, falling back to source."""
    target = target_currency or default_currency_for_user(user if user.is_authenticated else None)
    conversions = {source: (source, Decimal(1)) for source in source_currencies}
    if not source_currencies or set(source_currencies) == {target}:
        return conversions

    key = f"catalog-fx:v2:{target}"
    cached = cache.get(key)
    now = datetime.now(timezone.utc)
    if (not cached or now.timestamp() - cached["fetched_at"] >= 86400):
        # A short gate bounds concurrent refreshes and retries during outages.
        if cache.add(f"{key}:refresh", True, timeout=300):
            try:
                cached = _fetch_rates(target, now)
                cache.set(key, cached, timeout=7 * 86400)
            except (URLError, OSError, ValueError, InvalidOperation, KeyError, TypeError):
                logger.warning("Catalog rates unavailable for %s; using cached rates or store currencies", target)
    for source in source_currencies:
        if source == target:
            continue
        row = cached["rates"].get(source) if cached else None
        if row and row["expires_at"] > now.timestamp():
            conversions[source] = (target, row["rate"])
    return conversions


def convert_display_price(price, currency, divisor):
    """Convert Decimal prices and round half up to the display currency's minor unit."""
    quantum = Decimal(1).scaleb(-get_currency_precision(currency))
    amount = (price / divisor).quantize(quantum, rounding=ROUND_HALF_UP)
    return {"amount": format(amount, "f"), "currency": currency}
