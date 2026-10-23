"""Shared non-cancelled order quantities for browsing, ranking, and admin."""
from django.db.models import BigIntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ecommerce.models import Order, OrderItem


def counted_order_items(now):
    """Return placed, non-cancelled units regardless of payment or refund state.

    Future-dated orders are not placed yet. Refunded orders retain their demand
    contribution unless cancelled. Historical store totals retain deleted products.
    """
    return OrderItem.objects.filter(order__created_at__lte=now).exclude(
        order__status=Order.Status.CANCELLED,
    )


def with_ordered_totals(products, now=None):
    """Annotate lifetime ordered units without a separate query per product."""
    now = now or timezone.now()
    totals = counted_order_items(now).filter(
        product_id=OuterRef("pk"), order__store_id=OuterRef("store_id"),
    ).order_by().values("product_id").annotate(units=Sum("quantity")).values("units")
    return products.annotate(ordered_total=Coalesce(
        Subquery(totals, output_field=BigIntegerField()), Value(0),
        output_field=BigIntegerField(),
    ))
