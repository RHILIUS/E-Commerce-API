"""Shared purchase validation for COD placement and online inventory reservation."""
import hashlib
import json
from decimal import Decimal
from datetime import timedelta
from hmac import compare_digest

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.account.models import RecipientInfo
from apps.account.serializers import RecipientSerializer
from apps.checkout.models import Checkout, PaymentAttempt
from apps.ecommerce.models import Cart, CartItem, Order, OrderItem, Product
from apps.inventory.models import StockReservation
from apps.store.models import Store
from .pricing import rounded_unit_price
from .snapshots import RECIPIENT_FIELDS, recipient_snapshot
from .tokens import read_preview


class PlacementConflict(APIException):
    status_code = 409
    default_detail = 'Purchase details changed. Request and review a fresh checkout preview.'
    default_code = 'checkout_conflict'


# Hash normalized request fields, including the exact token, without retaining the token itself.
def _fingerprint(data):
    terms = {field: data[field] for field in ('preview_token', 'recipient_id', 'payment_method')}
    return hashlib.sha256(json.dumps(terms, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# Existing records without a fingerprint cannot safely acknowledge a matching request.
def _replay(checkout, fingerprint):
    if (not checkout.request_fingerprint or not compare_digest(checkout.request_fingerprint, fingerprint)
            or checkout.status != Checkout.Status.COMPLETED):
        raise PlacementConflict('This idempotency key has already been used for a different or incomplete purchase.')
    return checkout, False


@transaction.atomic
def _place(user, data):
    """Commit a COD purchase or online reservation, replaying matching requests before token checks.

    Lock order is customer, recipient, products by ID, stores by ID, cart lines by ID.
    Cart APIs already lock the customer first. Future reservation writers must lock
    the same product rows before changing holds to preserve available-stock checks.
    """
    online = data['payment_method'] == 'stripe'
    fingerprint = _fingerprint(data)
    # Serializing on the customer also covers two first submissions with the same key.
    get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
    existing = Checkout.objects.filter(user=user, idempotency_key=data['idempotency_key']).first()
    if existing is not None:
        if online:
            if existing.payment_method != 'stripe' or not compare_digest(existing.request_fingerprint, fingerprint):
                raise PlacementConflict('This idempotency key has already been used for a different or incomplete purchase.')
            return existing, False
        return _replay(existing, fingerprint)

    preview = read_preview(data['preview_token'], user)
    if preview.get('payment_method', 'cod') != data['payment_method']:
        raise PlacementConflict('Review prices for the selected payment method first.')
    if online and (preview['currency'] != 'USD' or not Decimal('0.50') <= Decimal(preview['total_amount']) <= Decimal('999999.99')):
        raise PlacementConflict('Stripe checkout requires a valid USD total.')
    recipient = get_object_or_404(RecipientInfo.objects.select_for_update(), pk=data['recipient_id'], user=user)
    recipient_data = RecipientSerializer(data=recipient_snapshot(recipient))
    if not recipient_data.is_valid():
        raise ValidationError({'recipient_id': ['The selected recipient has incomplete or invalid contact/address details.']})
    recipient_details = {field: recipient_data.validated_data[field] for field in RECIPIENT_FIELDS}

    lines = preview['items']
    product_ids = [line['product_id'] for line in lines]
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')}
    if len(products) != len(lines):
        raise PlacementConflict('A selected product is no longer available. Request a fresh preview.')
    stores = {s.pk: s for s in Store.objects.select_for_update().filter(
        pk__in={p.store_id for p in products.values()}).order_by('pk')}
    cart_ids = [line['cart_item_id'] for line in lines] if preview['source'] == 'cart' else []
    cart_items = {item.pk: item for item in CartItem.objects.select_for_update(of=('self',)).filter(
        pk__in=cart_ids, cart__user=user).order_by('pk')}
    if len(cart_items) != len(cart_ids):
        raise PlacementConflict('Selected cart items changed. Request a fresh preview.')
    from apps.ecommerce.services.carts import reject_frozen_items
    reject_frozen_items(cart_ids)
    # Lock waits can outlive the original review window. Recheck after acquiring all rows.
    read_preview(data['preview_token'], user)
    now = timezone.now()
    holds = dict(StockReservation.objects.filter(
        product_id__in=product_ids, status=StockReservation.Status.HELD,
    ).values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total'))
    calculated_total = Decimal(0)
    for line in lines:
        product = products[line['product_id']]
        store = stores[product.store_id]
        if (product.store_id != line['store_id'] or store.currency != line['source_currency']
                or product.price != Decimal(line['original_unit_price'])
                or product.name != line['product_name'] or store.name != line['store_name']):
            raise PlacementConflict('Product details or prices changed. Request a fresh preview.')
        if product.status != Product.Status.ACTIVE or product.stock - holds.get(product.pk, 0) < line['quantity']:
            raise PlacementConflict('Available stock changed. Adjust quantities and request a fresh preview.')
        if cart_ids:
            item = cart_items[line['cart_item_id']]
            if item.product_id != product.pk or item.quantity != line['quantity']:
                raise PlacementConflict('Selected cart quantities changed. Request a fresh preview.')
        unit = rounded_unit_price(product.price, preview['currency'], Decimal(line['exchange_rate']))
        if unit != Decimal(line['unit_price']):
            raise PlacementConflict('Purchase prices changed. Request a fresh preview.')
        calculated_total += unit * line['quantity']
    if calculated_total != Decimal(preview['total_amount']):
        raise PlacementConflict('Purchase total changed. Request a fresh preview.')

    checkout = Checkout.objects.create(
        user=user, idempotency_key=data['idempotency_key'], request_fingerprint=fingerprint,
        payment_method=data['payment_method'], status=Checkout.Status.PENDING if online else Checkout.Status.COMPLETED,
        currency=preview['currency'], total_amount=calculated_total, completed_at=None if online else now,
        expires_at=now + timedelta(hours=1) if online else None,
    )
    orders = {}
    for line in lines:
        store_id = line['store_id']
        if store_id not in orders:
            orders[store_id] = Order.objects.create(
                checkout=checkout, user=user, recipient_info=recipient,
                recipient_snapshot=recipient_details, store_id=store_id,
                store_name_at_purchase=line['store_name'], source_currency=line['source_currency'],
                exchange_rate=Decimal(line['exchange_rate']), exchange_rate_date=line['exchange_rate_date'],
                status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PENDING if online else Order.PaymentStatus.UNPAID,
            )
        item = OrderItem.objects.create(
            order=orders[store_id], product_id=line['product_id'], quantity=line['quantity'],
            product_name_at_purchase=line['product_name'], original_unit_price=Decimal(line['original_unit_price']),
            price_at_purchase=Decimal(line['unit_price']), source_cart_item_id=line['cart_item_id'],
        )
        product = products[line['product_id']]
        if online:
            StockReservation.objects.create(order_item=item, product=product, quantity=item.quantity, expires_at=checkout.expires_at)
        else:
            Product.objects.filter(pk=product.pk).update(stock=product.stock - line['quantity'], updated_at=now)
    if online:
        attempt = PaymentAttempt.objects.create(checkout=checkout, amount=calculated_total, currency="USD", expires_at=checkout.expires_at)
        from .stripe_provider import session_parameters
        attempt.session_parameters = session_parameters(attempt)
        attempt.save(update_fields=["session_parameters", "updated_at"])
    if cart_ids and not online:
        # Quantities were checked under lock, so removing these lines removes exactly the purchase.
        CartItem.objects.filter(pk__in=cart_ids).delete()
        Cart.objects.filter(user=user, items__isnull=True).delete()
    return checkout, True


def place_cod(user, data):
    """Place or replay a COD purchase using the shared authoritative review checks."""
    return _place(user, data)


def reserve_online(user, data):
    """Persist all online orders and holds before any external provider request."""
    from .stripe_provider import ensure_configured
    ensure_configured()
    return _place(user, data)
