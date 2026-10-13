"""Customer and purchase fixtures shared by backend tests."""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.authentication.models import UserAddress

from apps.checkout.models import Checkout
from apps.checkout.services.snapshots import recipient_snapshot
from apps.ecommerce.models import Order, OrderItem


def create_purchase(*, user, recipient, product, quantity=1, price=None,
                    status=Order.Status.PENDING, checkout=None):
    """Create valid same-currency purchase history without inventory or provider effects."""
    price = Decimal(str(product.price if price is None else price))
    if checkout is None:
        checkout = Checkout.objects.create(
            user=user, idempotency_key=uuid.uuid4(), currency=product.store.currency,
            total_amount=price * quantity, status=Checkout.Status.COMPLETED,
            completed_at=timezone.now(),
        )
    order = Order.objects.create(
        checkout=checkout, user=user, recipient_info=recipient,
        recipient_snapshot=recipient_snapshot(recipient), store=product.store,
        store_name_at_purchase=product.store.name, source_currency=product.store.currency,
        exchange_rate=Decimal("1"), status=status,
    )
    item = OrderItem.objects.create(
        order=order, product=product, quantity=quantity,
        product_name_at_purchase=product.name, original_unit_price=price,
        price_at_purchase=price,
    )
    return order, item


def create_customer(*, country_code, **fields):
    """Create a customer with an explicit personal address for country-aware tests."""
    user = get_user_model().objects.create_user(**fields)
    UserAddress.objects.create(user=user, country_code=country_code,
                              address_line1="Test street", city="Test city")
    return user
