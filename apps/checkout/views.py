from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.preview import preview_checkout
from .services.placement import place_cod
from .services.confirmation import checkout_confirmation
from .serializers import (PreviewRequestSerializer, PreviewResponseSerializer,
                          PlaceCODSerializer, CheckoutConfirmationSerializer)


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


class CheckoutPlacementView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Checkout'], request=PlaceCODSerializer,
        responses={201: CheckoutConfirmationSerializer, 200: CheckoutConfirmationSerializer,
                   400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT,
                   409: OpenApiResponse(OpenApiTypes.OBJECT, description='Expired/stale preview or conflicting idempotency key.')},
        description=('Atomically place a COD purchase. Creates one order per store, deducts stock and removes only '
                     'purchased cart lines. Returns 201 for creation or 200 for an identical successful replay. '
                     'Replays are checked before preview expiry and cart revalidation. Orders start pending/unpaid.'),
    )
    def post(self, request):
        serializer = PlaceCODSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checkout, created = place_cod(request.user, serializer.validated_data)
        result = checkout_confirmation(request.user, checkout.reference)
        return Response(CheckoutConfirmationSerializer(result, context={'request': request}).data,
                        status=201 if created else 200)


class CheckoutConfirmationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Checkout'], responses={200: CheckoutConfirmationSerializer, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
        description='Read the owning customer’s persisted checkout/order snapshots. Never recomputes current prices or FX.',
    )
    def get(self, request, reference):
        return Response(CheckoutConfirmationSerializer(
            checkout_confirmation(request.user, reference), context={'request': request}).data)
