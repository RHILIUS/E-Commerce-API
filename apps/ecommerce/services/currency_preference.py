"""Resolve automatic browsing defaults without changing checkout currency policy."""
from core.common.currencies import currency_for_country

from core.common.country_hint import verified_country
from .currency_options import CurrencyOptionsUnavailable, display_currency_codes


def default_display_currency(request, *, supported_codes=None):
    """Prefer a personal address, then a supported verified country, then USD.

    Existing address defaults remain valid without provider discovery. Country
    suggestions require supported metadata; discovery failures never block an
    automatic browsing request. Explicit choices are validated by the serializer.
    """
    address = getattr(request.user, "address", None) if request.user.is_authenticated else None
    address_currency = currency_for_country(address.country_code) if address else None
    if address_currency:
        return address_currency, "address"
    country_currency = currency_for_country(verified_country(request))
    if country_currency:
        try:
            codes = supported_codes if supported_codes is not None else display_currency_codes()
        except CurrencyOptionsUnavailable:
            return "USD", "fallback"
        if country_currency in codes:
            return country_currency, "country"
    return "USD", "fallback"
