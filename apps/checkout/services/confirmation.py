"""Owner-only purchase snapshots with optional current catalog imagery."""
from decimal import Decimal

from babel.numbers import get_currency_precision
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404

from apps.checkout.models import Checkout
from apps.ecommerce.models import Order, OrderItem, ProductImage


# Stored decimals have three places; display their original checkout/source currency precision.
def _money(amount, currency):
    quantum = Decimal(1).scaleb(-get_currency_precision(currency))
    return {'amount': format(amount.quantize(quantum), 'f'), 'currency': currency}


def checkout_confirmation(user, reference):
    """Keep purchase details historical; current images are presentation-only."""
    items_query = OrderItem.objects.order_by('pk').select_related('product').prefetch_related(
        Prefetch('product__images', queryset=ProductImage.objects.order_by('-is_default', 'order', 'pk')))
    orders = Order.objects.order_by('store_id').select_related('store', 'checkout').prefetch_related(
        Prefetch('items', queryset=items_query))
    checkout = get_object_or_404(Checkout.objects.filter(user=user).prefetch_related(
        Prefetch('orders', queryset=orders)), reference=reference)
    groups = []
    for order in checkout.orders.all():
        groups.append(order_snapshot(order))
    return {'reference': checkout.reference, 'status': checkout.status, 'payment_method': checkout.payment_method,
            'created_at': checkout.created_at, 'completed_at': checkout.completed_at,
            'total': _money(checkout.total_amount, checkout.currency), 'orders': groups}


def order_snapshot(order):
    """Serialize an eagerly loaded store order using immutable purchase prices and names."""
    items = list(order.items.all())
    subtotal = sum((item.price_at_purchase * item.quantity for item in items), Decimal(0))
    return {
        'id': order.pk, 'store': {'id': order.store_id, 'name': order.store_name_at_purchase,
                                'profile_image': order.store.profile_image or None},
        'status': order.status, 'payment_status': order.payment_status,
        'recipient': order.recipient_snapshot, 'subtotal': _money(subtotal, order.checkout.currency),
        'exchange_rate': format(order.exchange_rate, 'f'), 'exchange_rate_date': order.exchange_rate_date,
        'items': [{
            'id': item.pk, 'product_id': item.product_id, 'name': item.product_name_at_purchase,
            'image': next(iter(item.product.images.all()), None) if item.product else None,
            'quantity': item.quantity, 'unit_price': _money(item.price_at_purchase, order.checkout.currency),
            'original_unit_price': _money(item.original_unit_price, order.source_currency),
            'subtotal': _money(item.price_at_purchase * item.quantity, order.checkout.currency),
        } for item in items],
    }
