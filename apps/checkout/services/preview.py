"""Read-only purchase review from authoritative product/cart data."""
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal

from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.ecommerce.models import CartItem, Product, ProductImage
from apps.inventory.models import StockReservation
from .pricing import checkout_rates, rounded_unit_price
from .tokens import sign_preview

MAX_AMOUNT = Decimal('999999999999999.999')


# Keep money strings at the currency's actual precision instead of JSON floating point.
def _money(amount, currency):
    return {'amount': format(amount, 'f'), 'currency': currency}


def preview_checkout(request, data):
    """Return grouped purchase terms and issues without writing carts, orders or inventory."""
    images = Prefetch('images', queryset=ProductImage.objects.order_by('order', 'pk'))
    products = Product.objects.select_related('store').prefetch_related(images)
    if data['source'] == 'product':
        product = get_object_or_404(products, pk=data['product_id'], status__in=['active', 'archived'])
        lines = [(product, data['quantity'], None)]
    else:
        items = list(CartItem.objects.filter(cart__user=request.user, pk__in=data['item_ids'])
                     .prefetch_related(Prefetch('product', queryset=products)).order_by('pk'))
        if len(items) != len(data['item_ids']):
            raise NotFound('Some selected items are no longer in your cart. Refresh and try again.')
        lines = [(item.product, item.quantity, item.pk) for item in items]

    held = dict(StockReservation.objects.filter(
        product_id__in=[product.pk for product, _, _ in lines],
        status=StockReservation.Status.HELD, expires_at__gt=timezone.now(),
    ).values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total'))
    currency, rates = checkout_rates(request.user, {p.store.currency for p, _, _ in lines})
    groups, snapshots, issues = {}, [], []
    for product, quantity, item_id in lines:
        store = product.store
        visible = product.status in (Product.Status.ACTIVE, Product.Status.ARCHIVED)
        available = max(0, product.stock - held.get(product.pk, 0)) if visible else 0
        code, message = None, None
        if product.status != Product.Status.ACTIVE or available == 0:
            code, message = 'product_unavailable', 'This product is no longer available.'
        elif quantity > available:
            code, message = 'insufficient_stock', f'Only {available} available. Adjust the quantity on the product or cart page.'
        rate = rates[store.currency]
        unit = rounded_unit_price(product.price, currency, rate['rate']) if visible else None
        if visible and product.price < 0:
            code, message, unit = 'invalid_price', 'This product cannot currently be priced.', None
        subtotal = unit * quantity if unit is not None else None
        if subtotal is not None and (unit > MAX_AMOUNT or subtotal > MAX_AMOUNT):
            code, message, unit, subtotal = 'amount_too_large', 'Reduce the quantity before checkout.', None, None
        if code:
            issues.append({'code': code, 'product_id': product.pk, 'cart_item_id': item_id, 'message': message})
        group = groups.setdefault(store.pk, {'store': {
            'id': store.pk, 'name': store.name,
            'profile_image': request.build_absolute_uri(store.profile_image.url) if store.profile_image else None,
        }, 'items': [], 'subtotal': None})
        product_images = list(product.images.all()) if visible else []
        image = next((image for image in product_images if image.is_default), product_images[0] if product_images else None)
        group['items'].append({
            'cart_item_id': item_id, 'product': {
                'id': product.pk, 'name': product.name if visible else 'Unavailable product',
                'image': image,
            }, 'quantity': quantity, 'available_stock': available,
            'unit_price': _money(unit, currency) if unit is not None else None,
            'subtotal': _money(subtotal, currency) if subtotal is not None else None,
        })
        snapshots.append({
            'product_id': product.pk, 'cart_item_id': item_id, 'store_id': store.pk,
            'product_name': product.name, 'store_name': store.name, 'quantity': quantity,
            'source_currency': store.currency, 'original_unit_price': str(product.price),
            'exchange_rate': str(rate['rate']), 'exchange_rate_date': rate['date'],
            'unit_price': format(unit, 'f') if unit is not None else None,
        })
    stores = [groups[key] for key in sorted(groups)]
    for group in stores:
        if all(item['subtotal'] is not None for item in group['items']):
            group['subtotal'] = _money(sum(Decimal(item['subtotal']['amount']) for item in group['items']), currency)
    total = None
    if all(group['subtotal'] is not None for group in stores):
        amount = sum(Decimal(group['subtotal']['amount']) for group in stores)
        if amount > MAX_AMOUNT:
            issues.append({'code': 'amount_too_large', 'product_id': None, 'cart_item_id': None,
                           'message': 'Reduce the selected quantities before checkout.'})
        else:
            total = _money(amount, currency)
    token, expires_at = None, None
    if not issues:
        token, expires = sign_preview(request.user, {
            'source': data['source'], 'currency': currency, 'total_amount': total['amount'],
            'items': sorted(snapshots, key=lambda item: item['product_id']),
        })
        expires_at = datetime.fromtimestamp(expires, datetime_timezone.utc)
    return {'source': data['source'], 'currency': currency, 'stores': stores, 'total': total,
            'can_place_order': not issues, 'issues': issues, 'preview_token': token, 'expires_at': expires_at}
