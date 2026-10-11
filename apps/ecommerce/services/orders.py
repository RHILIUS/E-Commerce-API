"""Owned purchase history and atomic, whole-store-order cancellation."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.checkout.models import Checkout, OrderRefund, PaymentAttempt
from apps.checkout.services.confirmation import order_snapshot, _money
from apps.ecommerce.models import Order, OrderEvent, OrderItem, Product, ProductImage

CANCELLABLE = ('pending', 'accepted', 'packed')
TAB_STATUSES = {
    'to_ship': (*CANCELLABLE, 'shipment_arranged'), 'to_receive': ('shipped',),
    'cancelled': ('cancelled',), 'delivered': ('delivered',), 'completed': ('completed',),
}


class OrderConflict(APIException):
    status_code = 409
    default_detail = 'This order can no longer be cancelled. Refresh your orders.'


def customer_orders(user):
    """Eagerly load only this customer's orders, with deterministic newest-first order."""
    items = OrderItem.objects.select_related('product').prefetch_related(Prefetch(
        'product__images', queryset=ProductImage.objects.order_by('-is_default', 'order', 'pk'))).order_by('pk')
    return Order.objects.filter(user=user).select_related('checkout', 'store', 'refund').prefetch_related(
        Prefetch('items', queryset=items)).order_by('-created_at', '-pk')


def serialize_order(order):
    """Expose saved purchase values and refund state without inferring payment from fulfillment."""
    result = order_snapshot(order)
    refund = getattr(order, 'refund', None)
    result.update(created_at=order.created_at, concluded_at=order.concluded_at,
                  checkout_reference=order.checkout.reference,
                  payment_pending=order.checkout.status == 'pending',
                  can_cancel=(order.status in CANCELLABLE and order.checkout.status == 'completed'
                              and (order.checkout.payment_method == 'cod' and order.payment_status == 'unpaid'
                                   or order.checkout.payment_method == 'stripe' and order.payment_status == 'paid')),
                  refund=({'status': refund.status, 'amount': _money(Decimal(refund.amount_cents) / 100, 'USD'),
                           'needs_review': bool(refund.failure_code)} if refund else None))
    return result


@transaction.atomic
def cancel_order(user, pk):
    """Restock once and persist refund intent before any network call; lock like checkout writers."""
    get_user_model().objects.select_for_update().get(pk=user.pk)
    initial = get_object_or_404(Order.objects.filter(user=user), pk=pk)
    checkout = Checkout.objects.select_for_update().get(pk=initial.checkout_id)
    order = Order.objects.select_for_update().get(pk=pk)
    if order.status == 'cancelled':
        return getattr(order, 'refund', None)
    if order.status not in CANCELLABLE or checkout.status != 'completed':
        raise OrderConflict()
    items = list(order.items.order_by('product_id'))
    list(Product.objects.select_for_update().filter(pk__in=[i.product_id for i in items]).order_by('pk'))
    refund = None
    if checkout.payment_method == 'stripe':
        attempt = PaymentAttempt.objects.filter(checkout=checkout, status='succeeded', receipt__isnull=False).first()
        amount = sum((i.price_at_purchase * i.quantity for i in items), Decimal(0)) * 100
        if (order.payment_status != 'paid' or checkout.currency != 'USD' or not attempt
                or not attempt.stripe_payment_intent_id or amount != amount.to_integral_value()
                or not 0 <= amount <= attempt.amount * 100):
            raise OrderConflict('The payment must be verified before this order can be refunded.')
        if amount:
            refund = OrderRefund.objects.create(order=order, payment_attempt=attempt, amount_cents=int(amount))
    elif checkout.payment_method != 'cod' or order.payment_status != 'unpaid':
        raise OrderConflict('This payment requires review before cancellation.')
    now = timezone.now()
    for item in items:
        if item.product_id:
            Product.objects.filter(pk=item.product_id).update(stock=F('stock') + item.quantity, updated_at=now)
    order.status, order.concluded_at = 'cancelled', now
    if checkout.payment_method == 'cod':
        order.payment_status = 'cancelled'
    order.save(update_fields=['status', 'concluded_at', 'payment_status', 'updated_at'],
               event_actor=user, event_actor_role='customer', event_at=now)
    if refund:
        OrderEvent.objects.create(order=order, event_type='refund_requested', occurred_at=now,
                                  actor=user, actor_role='customer')
    return refund
