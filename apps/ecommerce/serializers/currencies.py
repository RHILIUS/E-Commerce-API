"""Display-currency query validation and public option responses."""
from rest_framework import serializers


class DisplayCurrencyQuerySerializer(serializers.Serializer):
    display_currency = serializers.RegexField(
        regex=r"^[A-Z]{3}$", required=False, trim_whitespace=False,
        help_text="Optional provider-supported uppercase browsing currency. Omit for address/verified-country/USD default; unavailable conversion retains source money. Does not change checkout.",
    )

    def validate_display_currency(self, value):
        """Accept only enabled ISO codes; never silently replace an explicit choice."""
        from ..services.currency_options import display_currency_codes
        if value not in display_currency_codes():
            raise serializers.ValidationError("Choose a supported display currency.")
        return value


def read_display_currency(request):
    """Validate browsing query input before reads or cart mutations use it."""
    query = DisplayCurrencyQuerySerializer(data=request.query_params.dict())
    query.is_valid(raise_exception=True)
    explicit = query.validated_data.get("display_currency")
    if explicit:
        return explicit
    from ..services.currency_preference import default_display_currency
    return default_display_currency(request)[0]


class CurrencyOptionSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    minor_unit = serializers.IntegerField()
    display_supported = serializers.BooleanField()
    stripe_supported = serializers.BooleanField()


class CurrencyOptionsSerializer(serializers.Serializer):
    currencies = CurrencyOptionSerializer(many=True)
    default_currency = serializers.CharField()
    default_source = serializers.ChoiceField(choices=["address", "country", "fallback"])
