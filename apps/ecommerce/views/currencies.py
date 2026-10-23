"""Public display-currency metadata HTTP handler."""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers.currencies import CurrencyOptionsSerializer


class CurrencyOptionsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Catalog"], responses={200: CurrencyOptionsSerializer, 401: dict, 503: dict},
        description="Provider-supported browsing currencies and viewer default. Daily cached metadata, up to seven days on refresh failure; 503 when no usable list is available. stripe_supported marks only the existing USD test-checkout implementation, not live account eligibility. Defaults use personal address or USD; country detection is not enabled.",
    )
    def get(self, request):
        from babel.numbers import get_currency_name, get_currency_precision
        from core.common.currencies import currency_for_country
        from ..services.currency_options import display_currency_codes
        address = getattr(request.user, "address", None) if request.user.is_authenticated else None
        address_currency = currency_for_country(address.country_code) if address else None
        return Response({
            "currencies": [{
                "code": code, "name": get_currency_name(code, locale="en"),
                "minor_unit": get_currency_precision(code), "display_supported": True,
                "stripe_supported": code == "USD",
            } for code in display_currency_codes()],
            "default_currency": address_currency or "USD",
            "default_source": "address" if address_currency else "fallback",
        })
