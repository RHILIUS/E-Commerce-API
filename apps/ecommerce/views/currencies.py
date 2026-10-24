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
        description="Provider-supported browsing currencies and viewer default. Daily cached metadata, up to seven days on refresh failure; 503 when no usable list is available. stripe_supported marks only the existing USD test-checkout implementation, not live account eligibility. Defaults use personal address, a supported signed edge-country hint, or USD. Invalid/missing hints are ignored; explicit browsing selection still takes priority.",
    )
    def get(self, request):
        from babel.numbers import get_currency_name, get_currency_precision
        from ..services.currency_preference import default_display_currency
        from ..services.currency_options import display_currency_codes
        codes = display_currency_codes()
        currency, source = default_display_currency(request, supported_codes=codes)
        return Response({
            "currencies": [{
                "code": code, "name": get_currency_name(code, locale="en"),
                "minor_unit": get_currency_precision(code), "display_supported": True,
                "stripe_supported": code == "USD",
            } for code in codes],
            "default_currency": currency,
            "default_source": source,
        })
