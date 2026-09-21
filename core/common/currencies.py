from babel.numbers import get_territory_currencies


def currency_for_country(country_code):
    """Return a country's sole current tender currency, or None if ambiguous/unknown."""
    currencies = get_territory_currencies((country_code or "").upper())
    return currencies[0] if len(currencies) == 1 else None
