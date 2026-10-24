from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.checkout.services.refunds import reconcile_refund
from ..serializers.orders import OrderFilterSerializer, UserOrderSerializer, UserOrderListSerializer, OrderHistorySerializer
from ..models import Order
from ..services.orders import TAB_STATUSES, cancel_order, customer_orders, serialize_order


class UserOrderPagination(PageNumberPagination):
    page_size = 20


class UserOrderListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Orders'], parameters=[OrderFilterSerializer, OpenApiParameter('page', int, description='Positive page number; 20 orders per page')], responses={200: UserOrderListSerializer, 400: dict, 401: dict, 404: dict}, description='Owned store orders, newest creation first (ID tie-break). All includes every fulfillment status. Twenty orders per page; page is a positive integer. Prices/names are purchase snapshots.')
    def get(self, request):
        query = OrderFilterSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        orders = customer_orders(request.user)
        tab = query.validated_data['tab']
        if tab in TAB_STATUSES:
            orders = orders.filter(status__in=TAB_STATUSES[tab])
        pagination = UserOrderPagination()
        page = pagination.paginate_queryset(orders, request)
        data = UserOrderSerializer([serialize_order(order) for order in page], many=True, context={'request': request}).data
        return pagination.get_paginated_response(data)


class UserOrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Orders'], request=None, responses={200: UserOrderSerializer, 401: dict, 404: dict, 409: dict}, description='Cancel an owned Pending/Accepted/Packed order. Restore stock exactly once; paid Stripe test orders receive a persisted partial-charge refund for this store order. Retry safely to reconcile an unresolved refund. No wallet credit. Pending checkout payment must be resolved through the payment endpoint.')
    def post(self, request, pk):
        refund = cancel_order(request.user, pk)
        if refund:
            reconcile_refund(refund.pk)
        order = get_object_or_404(customer_orders(request.user), pk=pk)
        return Response(UserOrderSerializer(serialize_order(order), context={'request': request}).data)


class UserOrderHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Orders'], responses={200: OrderHistorySerializer, 401: dict, 404: dict},
                   description='Read the owning customer’s complete recorded timeline, oldest first. Historical gaps are flagged. Actor roles only; no identities, provider IDs or internal errors. This GET never creates events or contacts Stripe.')
    def get(self, request, pk):
        order = get_object_or_404(Order.objects.filter(user=request.user), pk=pk)
        events = list(order.events.all())
        return Response(OrderHistorySerializer({
            'order_id': order.pk, 'current_status': order.status,
            'is_partial': any(event.is_backfilled for event in events), 'events': events,
        }).data)
