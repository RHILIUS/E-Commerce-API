from datetime import timedelta

from django.conf import settings
from django.db.models import BigIntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .order_quantities import counted_order_items


def rank_products_for_user(products, user):
    """Rank lazily by personal category quantity, recent popularity, date, then ID.

    Use current categories for preferences; uncategorized products can be popular.
    Guests have zero preference. Both scores use the same non-cancelled order
    quantities as ordered_count, including unpaid and refunded purchases.
    Scores do not persist fields or change the supplied product filters.
    """
    now = timezone.now()
    recent_purchases = (
        counted_order_items(now).filter(
            product_id=OuterRef("pk"),
            order__created_at__gte=now - timedelta(days=settings.PRODUCT_POPULARITY_DAYS),
        )
        .order_by()
        .values("product_id")
        .annotate(score=Sum("quantity"))
        .values("score")[:1]
    )
    products = products.annotate(
        recent_popularity=Coalesce(
            Subquery(recent_purchases, output_field=BigIntegerField()),
            Value(0), output_field=BigIntegerField(),
        )
    )

    preference = Value(0, output_field=BigIntegerField())
    if user.is_authenticated:
        category_purchases = (
            counted_order_items(now).filter(
                order__user_id=user.pk,
                product__category_id=OuterRef("category_id"),
            )
            .order_by()
            .values("product__category_id")
            .annotate(score=Sum("quantity"))
            .values("score")[:1]
        )
        preference = Coalesce(
            Subquery(category_purchases, output_field=BigIntegerField()),
            Value(0),
            output_field=BigIntegerField(),
        )
    return products.annotate(preference_score=preference).order_by(
        "-preference_score", "-recent_popularity", "-created_at", "-pk",
    )
