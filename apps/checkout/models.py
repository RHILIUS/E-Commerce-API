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

    session_parameters = models.JSONField(default=dict, blank=True, editable=False)
    checkout_url = models.URLField(max_length=2048, blank=True)
    cancel_requested = models.BooleanField(default=False)
    receipt = models.OneToOneField(
        "wallet.LedgerTransaction", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payment_attempt",
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
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "checkout_id", "amount", "currency", "reference", "session_parameters",
                "stripe_checkout_session_id", "stripe_payment_intent_id", "receipt_id").first()
            for field in ("checkout_id", "amount", "currency", "reference"):
                if previous and previous[field] != getattr(self, field):
                    errors[field] = "Payment attempt identity and terms are immutable."
            for field in ("session_parameters", "stripe_checkout_session_id", "stripe_payment_intent_id", "receipt_id"):
                if previous and previous[field] and previous[field] != getattr(self, field):
                    errors[field] = "Assigned provider and ledger references cannot change."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate payment terms without contacting Stripe or updating order status."""
        self.full_clean()
        return super().save(*args, **kwargs)


class ProviderEvent(models.Model):
    """Durable, minimal Stripe webhook inbox; financial work is independently idempotent."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    payment_attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.PROTECT)
    event_id = models.CharField(max_length=255)
    environment = models.CharField(max_length=10, default="test", editable=False)
    event_type = models.CharField(max_length=100)
    session_id = models.CharField(max_length=255)
    attempt_reference = models.CharField(max_length=36, blank=True)
    processing_status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["environment", "event_id"], name="stripe_event_environment_unique"),
            models.CheckConstraint(condition=models.Q(environment="test"), name="provider_event_test_only"),
        ]
        indexes = [models.Index(fields=["processing_status", "received_at"], name="provider_event_pending_idx")]


class OrderRefund(TimeStampedModel):
    """One durable full-order refund, representing a partial multi-store Stripe charge."""
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.OneToOneField('ecommerce.Order', on_delete=models.PROTECT, related_name='refund')
    payment_attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name='refunds')
    amount_cents = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, default='pending', choices=[
        ('pending', 'Pending'), ('requires_action', 'Requires action'), ('succeeded', 'Succeeded'),
        ('failed', 'Failed'), ('canceled', 'Canceled'),
    ])
    stripe_refund_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    reversal = models.OneToOneField('wallet.LedgerTransaction', null=True, blank=True,
                                   on_delete=models.PROTECT, related_name='order_refund')
    failure_code = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_cents__gt=0), name='order_refund_positive')]

    def clean(self):
        """Protect purchase identity and the fixed refund amount on ordinary writes."""
        super().clean()
        if self.order_id and self.payment_attempt_id:
            if self.order.checkout_id != self.payment_attempt.checkout_id:
                raise ValidationError('The refund payment must belong to the order checkout.')
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values().first()
            for field in ('reference', 'order_id', 'payment_attempt_id', 'amount_cents'):
                if old and old[field] != getattr(self, field):
                    raise ValidationError('Refund identity and amount cannot change.')
            for field in ('stripe_refund_id', 'reversal_id'):
                if old and old[field] and old[field] != getattr(self, field):
                    raise ValidationError('Assigned refund references cannot change.')

    def save(self, *args, **kwargs):
        """Validate refund terms without performing provider or ledger actions."""
        self.full_clean()
        return super().save(*args, **kwargs)
