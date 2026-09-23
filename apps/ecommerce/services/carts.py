"""Account-scoped carts with current inventory and display prices, never reservations."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, NotFound

from ..models import Cart, CartItem, Product, ProductImage
from ..serializers import CatalogImageSerializer
from .display_prices import convert_display_price, get_display_rates


class CartConflict(APIException):
    status_code = 409
    default_code = 'cart_conflict'


def read_carts(request):
    """Read saved store carts without creating records; redact unpublished products."""
    items = CartItem.objects.select_related('product').prefetch_related(
        Prefetch('product__images', queryset=ProductImage.objects.order_by('order', 'pk'))).order_by('pk')
    carts = list(Cart.objects.filter(user=request.user, items__isnull=False).distinct()
                 .select_related('store').prefetch_related(Prefetch('items', queryset=items)).order_by('pk'))
    rates = get_display_rates(request.user, {cart.store.currency for cart in carts})
    result = []
    for cart in carts:
        store = cart.store
        rows = []
        for item in cart.items.all():
            product = item.product
            visible = product.status in (Product.Status.ACTIVE, Product.Status.ARCHIVED)
            currency, divisor = rates.get(store.currency, (store.currency, Decimal(1)))
            price = convert_display_price(product.price, currency, divisor) if visible else None
            images = list(product.images.all()) if visible else []
            image = next((image for image in images if image.is_default), images[0] if images else None)
            rows.append({
                'id': item.pk, 'quantity': item.quantity,
                'product': {
                    'id': product.pk, 'name': product.name if visible else 'Unavailable product',
                    'status': product.status if visible else 'unavailable',
                    'stock': product.stock if visible else 0,
                    'can_purchase': product.status == Product.Status.ACTIVE and product.stock > 0,
                    'image': CatalogImageSerializer(image, context={'request': request}).data if image else None,
                    'display_price': price,
                },
                'subtotal': {'amount': format(Decimal(price['amount']) * item.quantity, 'f'), 'currency': currency} if price else None,
            })
        result.append({'id': cart.pk, 'store': {
            'id': store.pk, 'name': store.name,
            'profile_image': request.build_absolute_uri(store.profile_image.url) if store.profile_image else None,
        }, 'items': rows})
    return {'carts': result}


def lock_customer(user):
    """Serialize this customer's mutations, including first-cart creation."""
    get_user_model().objects.select_for_update().get(pk=user.pk)


def validate_stock(product, quantity):
    """Reject unavailable products and stale quantities without changing inventory."""
    if product.status != Product.Status.ACTIVE or product.stock == 0:
        raise CartConflict('This product is no longer available. Refresh your cart.')
    if quantity > product.stock:
        raise CartConflict(f'Only {product.stock} available. Choose a smaller quantity.')


@transaction.atomic
def add_item(user, product_id, quantity):
    """Increment one product in its store cart under customer and product row locks."""
    lock_customer(user)
    product = get_object_or_404(Product.objects.select_for_update(), pk=product_id, status=Product.Status.ACTIVE)
    validate_stock(product, quantity)
    cart, _ = Cart.objects.get_or_create(user=user, store_id=product.store_id)
    item = CartItem.objects.filter(cart=cart, product=product).first()
    total = quantity + (item.quantity if item else 0)
    validate_stock(product, total)
    if item:
        item.quantity = total
        item.save(update_fields=['quantity', 'updated_at'])
    else:
        CartItem.objects.create(cart=cart, product=product, quantity=total)


@transaction.atomic
def update_item(user, item_id, quantity):
    """Set an owned item's quantity after checking fresh stock."""
    lock_customer(user)
    item = get_object_or_404(CartItem.objects.filter(cart__user=user), pk=item_id)
    product = Product.objects.select_for_update().get(pk=item.product_id)
    validate_stock(product, quantity)
    item.quantity = quantity
    item.save(update_fields=['quantity', 'updated_at'])


@transaction.atomic
def remove_items(user, item_ids):
    """Remove only owned items, atomically rejecting missing or foreign IDs."""
    lock_customer(user)
    ids = set(item_ids)
    items = CartItem.objects.filter(cart__user=user, pk__in=ids)
    if items.count() != len(ids):
        raise NotFound('Some selected items are no longer in your cart. Refresh and try again.')
    items.delete()
    Cart.objects.filter(user=user, items__isnull=True).delete()
