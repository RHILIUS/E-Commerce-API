from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers.currencies import DisplayCurrencyQuerySerializer, read_display_currency
from ..serializers.carts import CartAddSerializer, CartQuantitySerializer, CartRemoveSerializer, CartResponseSerializer
from ..services.carts import add_item, read_carts, remove_items, update_item


class CartListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Cart'], parameters=[DisplayCurrencyQuerySerializer], responses={200: CartResponseSerializer, 400: dict, 401: dict, 503: dict}, description='Read the signed-in customer’s carts, one per store. Current prices and stock; no reservation. Unpublished products are redacted. Empty carts are omitted.')
    def get(self, request):
        display_currency = read_display_currency(request)
        return Response(read_carts(request, display_currency=display_currency))


class CartItemsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Cart'], parameters=[DisplayCurrencyQuerySerializer], request=CartAddSerializer, responses={200: CartResponseSerializer, 400: dict, 401: dict, 404: dict, 409: dict, 503: dict}, description='Add quantity to a product in its store cart. Existing quantities are incremented. Reject quantities above current stock. Does not reserve or reduce stock.')
    def post(self, request):
        display_currency = read_display_currency(request)
        data = CartAddSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        add_item(request.user, **data.validated_data)
        return Response(read_carts(request, display_currency=display_currency))

    @extend_schema(tags=['Cart'], parameters=[DisplayCurrencyQuerySerializer], request=CartRemoveSerializer, responses={200: CartResponseSerializer, 400: dict, 401: dict, 404: dict, 409: dict, 503: dict}, description='Remove selected owned items across store carts. The whole request fails if any ID is missing or foreign; remove empty carts. Pending online payment items cannot be edited or removed.')
    def delete(self, request):
        display_currency = read_display_currency(request)
        data = CartRemoveSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        remove_items(request.user, **data.validated_data)
        return Response(read_carts(request, display_currency=display_currency))


class CartItemView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Cart'], parameters=[DisplayCurrencyQuerySerializer], request=CartQuantitySerializer, responses={200: CartResponseSerializer, 400: dict, 401: dict, 404: dict, 409: dict, 503: dict}, description='Replace an owned item’s quantity after validating current stock. Return the full cart collection.')
    def patch(self, request, pk):
        display_currency = read_display_currency(request)
        data = CartQuantitySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        update_item(request.user, pk, data.validated_data['quantity'])
        return Response(read_carts(request, display_currency=display_currency))
