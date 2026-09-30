from django.core.exceptions import ValidationError
from django.db import models

from core.common.models import TimeStampedModel


class StockReservation(TimeStampedModel):
    """Record inventory held for an order item until payment resolves."""

    class Status(models.TextChoices):
        HELD = "held", "Held"
        CONSUMED = "consumed", "Consumed"
        RELEASED = "released", "Released"

    order_item = models.OneToOneField(
        "ecommerce.OrderItem",
        on_delete=models.PROTECT,
        related_name="stock_reservation",
    )
    product = models.ForeignKey(
        "ecommerce.Product",
        on_delete=models.PROTECT,
        related_name="stock_reservations",
    )
    quantity = models.PositiveIntegerField()

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.HELD,
    )
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="stock_reservation_quantity_positive",
            ),
        ]

    def clean(self):
        """Prevent reserving a different product or quantity from the purchase line."""
        super().clean()
        item_model = self._meta.get_field("order_item").remote_field.model
        item = item_model.objects.filter(pk=self.order_item_id).values("product_id", "quantity").first()
        if not item:
            return
        errors = {}
        if self.product_id != item["product_id"]:
            errors["product"] = "The reservation product must match the order item."
        if self.quantity != item["quantity"]:
            errors["quantity"] = "The reservation quantity must match the order item."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate the reservation record without changing inventory."""
        self.full_clean()
        return super().save(*args, **kwargs)
