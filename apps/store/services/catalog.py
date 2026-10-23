"""Public store catalog selection and qualifying purchase quantities."""
from datetime import timedelta

from django.db.models import BigIntegerField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ecommerce.models import Product, ProductImage
from apps.ecommerce.services.sales import qualifying_items, with_sales_totals



def store_products(store, now=None):
    """Annotate active listings with lifetime and rolling 30-day qualifying units."""
    now = now or timezone.now()
    items = qualifying_items(now).filter(product_id=OuterRef("pk"), order__store_id=store.pk)
    recent = items.filter(order__created_at__gte=now - timedelta(days=30)).order_by().values(
        "product_id").annotate(units=Sum("quantity")).values("units")
    return with_sales_totals(
        Product.objects.filter(store=store, status=Product.Status.ACTIVE).select_related("store"), now,
    ).annotate(
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
