"""Shared order qualification and lifetime product sales for public browsing."""
from django.db.models import BigIntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ecommerce.models import Order, OrderItem


def qualifying_items(now):
    """Count paid Stripe and delivered/completed COD units, excluding reversals.

    Placement time defines the sales window. Store totals retain historical items
    even when their product is archived or deleted. Partial refunds are not supported.
    """
    return OrderItem.objects.filter(
        Q(order__checkout__payment_method="stripe", order__payment_status=Order.PaymentStatus.PAID)
        | Q(order__checkout__payment_method="cod", order__status__in=[Order.Status.DELIVERED, Order.Status.COMPLETED]),
        order__created_at__lte=now,
    ).exclude(order__status=Order.Status.CANCELLED).exclude(order__refund__status="succeeded")


def with_sales_totals(products, now=None):
    """Annotate products with qualifying lifetime units in one database query.

    Keep sales_total separate from the legacy stored sold_count until its removal.
    A correlated aggregate preserves caller filters/order and returns zero for no sales.
    """
    now = now or timezone.now()
    totals = qualifying_items(now).filter(
        product_id=OuterRef("pk"), order__store_id=OuterRef("store_id"),
    ).order_by().values("product_id").annotate(units=Sum("quantity")).values("units")
    return products.annotate(sales_total=Coalesce(
        Subquery(totals, output_field=BigIntegerField()), Value(0),
        output_field=BigIntegerField(),
    ))
