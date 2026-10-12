"""Public store catalog selection and qualifying purchase quantities."""
from datetime import timedelta

from django.db.models import BigIntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ecommerce.models import Order, OrderItem, Product, ProductImage


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


def store_products(store, now=None):
    """Annotate active listings with lifetime and rolling 30-day qualifying units."""
    now = now or timezone.now()
    items = qualifying_items(now).filter(product_id=OuterRef("pk"), order__store_id=store.pk)
    totals = items.order_by().values("product_id").annotate(units=Sum("quantity")).values("units")
    recent = items.filter(order__created_at__gte=now - timedelta(days=30)).order_by().values(
        "product_id").annotate(units=Sum("quantity")).values("units")
    return Product.objects.filter(store=store, status=Product.Status.ACTIVE).select_related("store").annotate(
        sales_total=Coalesce(Subquery(totals, output_field=BigIntegerField()), Value(0), output_field=BigIntegerField()),
        sales_recent=Coalesce(Subquery(recent, output_field=BigIntegerField()), Value(0), output_field=BigIntegerField()),
    ).prefetch_related(Prefetch(
        "images", queryset=ProductImage.objects.filter(is_default=True).order_by("order", "pk"),
        to_attr="catalog_images",
    ))


SORT_FIELDS = {
    "latest": ("-created_at", "-pk"),
    "oldest": ("created_at", "pk"),
    "popular": ("-sales_recent", "-created_at", "-pk"),
    "best_sellers": ("-sales_total", "-created_at", "-pk"),
}
