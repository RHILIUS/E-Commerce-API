"""Strict checkout quotes: one target currency, dated multipliers, no price relabelling."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from urllib.error import URLError
from urllib.request import Request, urlopen

from babel.numbers import get_currency_precision, is_currency
from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import APIException

from core.common.currencies import currency_for_country


class ConversionUnavailable(APIException):
    status_code = 503
    default_detail = 'Currency conversion is unavailable. Please retry checkout preview later.'
    default_code = 'conversion_unavailable'


# External provider input must be bounded and validated before caching or signing.
def _fetch_json(path):
    with urlopen(Request('https://api.frankfurter.dev/v2/' + path,
                         headers={'Accept': 'application/json', 'User-Agent': 'E-Commerce-Checkout/1.0'}),
                 timeout=settings.CHECKOUT_FX_TIMEOUT_SECONDS) as response:
        body = response.read(131073)
    if len(body) > 131072:
        raise ValueError('Provider response is too large')
    data = json.loads(body, parse_float=Decimal)
    if not isinstance(data, list):
        raise ValueError('Expected provider rows')
    return data


# Cache provider capability separately so an outage is never mistaken for unsupported currency.
def _supported_currencies():
    key = 'checkout-fx:v1:currencies'
    codes = cache.get(key)
    if codes is None:
        rows = _fetch_json('currencies')
        codes = {row['iso_code'] for row in rows
                 if isinstance(row, dict) and isinstance(row.get('iso_code'), str)
                 and re.fullmatch(r'[A-Z]{3}', row['iso_code'])}
        if 'USD' not in codes or len(codes) != len(rows):
            raise ValueError('Invalid currency capabilities')
        cache.set(key, codes, timeout=86400)
    return codes


# Use the provider's target-based table, inverted once to the model's source multiplier.
def _rate_table(target, now):
    key = f'checkout-fx:v1:rates:{target}'
    table = cache.get(key)
    if not table or now.timestamp() - table['fetched_at'] >= 86400:
        if cache.add(key + ':refresh', True, timeout=300):
            try:
                rates = {}
                for row in _fetch_json(f'rates?base={target.lower()}'):
                    try:
                        quoted = Decimal(str(row['rate']))
                        rate_date = date.fromisoformat(row['date'])
                        expiry = datetime.combine(rate_date + timedelta(days=7), time(), timezone.utc)
                        source = row['quote']
                        if (row['base'] != target or not isinstance(source, str)
                                or not re.fullmatch(r'[A-Z]{3}', source)
                                or not quoted.is_finite() or not Decimal('0.000000001') <= quoted <= Decimal('1000000000')
                                or rate_date > now.date() or expiry <= now + timedelta(seconds=300)):
                            continue
                        with localcontext() as ctx:
                            ctx.prec = 50
                            multiplier = (Decimal(1) / quoted).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_UP)
                        rates[source] = {'rate': multiplier, 'date': rate_date.isoformat(), 'expires_at': expiry.timestamp()}
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        continue
                if not rates:
                    raise ValueError('No valid exchange rates')
                table = {'rates': rates, 'fetched_at': now.timestamp()}
                cache.set(key, table, timeout=7 * 86400)
            except (URLError, OSError, ValueError, InvalidOperation):
                # A refresh failure may use previously validated, still-current rows.
                pass
    return table['rates'] if table else {}


def checkout_rates(user, source_currencies, *, target_currency=None):
    """Return one currency and source multipliers; fail if required conversion is missing."""
    target = target_currency or currency_for_country(user.country_code) or 'USD'
    if any(not is_currency(source) for source in source_currencies):
        raise ConversionUnavailable()
    # Same-currency purchases require no exchange service or capability lookup.
    if source_currencies == {target}:
        return target, {target: {'rate': Decimal(1), 'date': None}}
    try:
        if target != 'USD' and target not in _supported_currencies():
            target = 'USD'
        rates = {target: {'rate': Decimal(1), 'date': None}}
        if source_currencies - {target}:
            now = datetime.now(timezone.utc)
            table = _rate_table(target, now)
            for source in source_currencies - {target}:
                row = table.get(source)
                if not row or row['expires_at'] <= now.timestamp() + 300:
                    raise ConversionUnavailable()
                rates[source] = row
        return target, rates
    except (URLError, OSError, ValueError, InvalidOperation) as exc:
        raise ConversionUnavailable() from exc


def rounded_unit_price(price, currency, multiplier):
    """Round the signed multiplier's result before quantity multiplication, matching order items."""
    with localcontext() as ctx:
        ctx.prec = 50
        return (price * multiplier).quantize(Decimal(1).scaleb(-get_currency_precision(currency)), rounding=ROUND_HALF_UP)
