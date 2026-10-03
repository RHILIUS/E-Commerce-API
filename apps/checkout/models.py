import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from core.common.models import TimeStampedModel


class Checkout(TimeStampedModel):
    class PaymentMethod(models.TextChoices):
        COD = "cod", "Cash on Delivery"
        STRIPE = "stripe", "Stripe"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="checkouts",
    )
    idempotency_key = models.UUIDField()
    request_fingerprint = models.CharField(
        max_length=64, blank=True, default="", editable=False,
        validators=[RegexValidator(r"^[0-9a-f]{64}$", "Use a SHA-256 request fingerprint.")],
        help_text="Normalized placement request hash. Empty for records created before placement APIs.",
    )

    payment_method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.COD,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    currency = models.CharField(
        max_length=3, validators=[RegexValidator(r"^[A-Z]{3}$", "Use an uppercase currency code.")],
    )
    total_amount = models.DecimalField(
        max_digits=18,
        decimal_places=3,
    )

    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name="unique_user_checkout_request",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="checkout_total_nonnegative",
            ),
        ]

    def clean(self):
        """Prevent changing the purchaser or payment terms of an existing purchase."""
        super().clean()
        if self.pk and (self.orders.exists() or self.payment_attempts.exists()):
            previous = type(self).objects.filter(pk=self.pk).values(
                "user_id", "currency", "total_amount", "payment_method",
                "idempotency_key", "request_fingerprint").first()
            if previous and any(previous[field] != getattr(self, field) for field in previous):
                raise ValidationError("A checkout with orders or payment attempts cannot change its payment terms.")

    def save(self, *args, **kwargs):
        """Validate checkout fields and uniqueness on ordinary writes."""
        self.full_clean()
        return super().save(*args, **kwargs)


class PaymentAttempt(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    checkout = models.ForeignKey(
        Checkout,
        on_delete=models.PROTECT,
        related_name="payment_attempts",
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=3,
    )
    currency = models.CharField(
        max_length=3, validators=[RegexValidator(r"^[A-Z]{3}$", "Use an uppercase currency code.")],
    )

    stripe_checkout_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )

    failure_code = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_attempt_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["checkout"],
                condition=models.Q(status="pending"),
                name="one_pending_payment_per_checkout",
            ),
        ]

    def clean(self):
        """Require Stripe attempts to use the checkout's agreed amount and currency."""
        super().clean()
        checkout = Checkout.objects.filter(pk=self.checkout_id).first()
        if not checkout:
            return
        errors = {}
        if checkout.payment_method != Checkout.PaymentMethod.STRIPE:
            errors["checkout"] = "COD checkouts do not have Stripe payment attempts."
        if self.amount != checkout.total_amount:
            errors["amount"] = "The amount must equal the checkout total."
        if self.currency != checkout.currency:
            errors["currency"] = "The currency must match the checkout currency."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate payment terms without contacting Stripe or updating order status."""
        self.full_clean()
        return super().save(*args, **kwargs)
