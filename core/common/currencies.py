from babel.numbers import get_territory_currencies


def currency_for_country(country_code):
    """Return a country's sole current tender currency, or None if ambiguous/unknown."""
    currencies = get_territory_currencies((country_code or "").upper())
    return currencies[0] if len(currencies) == 1 else None


def default_currency_for_user(user):
    """Resolve the personal-address currency or USD, without checking FX availability.

    Callers control viewer eligibility; guests have no personal address. Payment
    methods may override this default before applying their own conversion rules.
    """
    address = getattr(user, "address", None)
    return (currency_for_country(address.country_code) if address else None) or "USD"
