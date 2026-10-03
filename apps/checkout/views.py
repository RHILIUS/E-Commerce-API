from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.preview import preview_checkout
from .serializers import PreviewRequestSerializer, PreviewResponseSerializer


class CheckoutPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Checkout'], request=PreviewRequestSerializer,
        responses={
            200: PreviewResponseSerializer,
            400: OpenApiResponse(OpenApiTypes.OBJECT, description='Invalid source shape or fields.'),
            401: OpenApiTypes.OBJECT,
            404: OpenApiResponse(OpenApiTypes.OBJECT, description='Product or selected owned cart item not found.'),
            503: OpenApiResponse(OpenApiTypes.OBJECT, description='Required currency conversion unavailable.'),
        },
        description=(
            'Preview one product and quantity, or 1–500 distinct owned cart item IDs using saved quantities. '
            'Returns store groups in one currency. Item issues return 200 with can_place_order=false '
            'and null token/expiry. Valid previews are signed for this customer for five minutes. '
            'No order creation, stock reservation or cart mutation. Placement must revalidate current data.'
        ),
    )
    def post(self, request):
        serializer = PreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = preview_checkout(request, serializer.validated_data)
        return Response(PreviewResponseSerializer(result, context={'request': request}).data)
