"""USD-only simulated wallets and immutable double-entry journal records."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.common.models import TimeStampedModel


class ImmutableQuerySet(models.QuerySet):
    """Reject ORM shortcuts that bypass ledger validation and posting locks."""

    def update(self, **kwargs):
        raise ValidationError("Financial records cannot be updated.")

    def delete(self):
        raise ValidationError("Financial records cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Use the wallet services to create financial records.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("Financial records cannot be updated.")


class ImmutableModel(models.Model):
    """Validate new records and prevent ordinary mutation of persisted identities."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Allow validated insertion only; financial identities never change owners."""
        if not self._state.adding:
            raise ValidationError("Financial records cannot be updated.")
        # Database constraints arbitrate concurrent get_or_create calls.
        self.full_clean(validate_unique=False, validate_constraints=False)
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Preserve financial history even when the balance is zero."""
        raise ValidationError("Financial records cannot be deleted.")


class Wallet(ImmutableModel, TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="wallet",
    )
    store = models.OneToOneField(
        "store.Store", null=True, blank=True, on_delete=models.PROTECT, related_name="wallet",
    )
    currency = models.CharField(max_length=3, default="USD", editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(user__isnull=False, store__isnull=True)
                           | models.Q(user__isnull=True, store__isnull=False)),
                name="wallet_exactly_one_owner",
            ),
            models.CheckConstraint(condition=models.Q(currency="USD"), name="wallet_usd_only"),
        ]

    def __str__(self):
        return f"Wallet {self.pk} (USD)"


class LedgerAccount(ImmutableModel, TimeStampedModel):
    class Kind(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"

    class Bucket(models.TextChoices):
        TREASURY = "treasury", "Treasury"
        PENDING = "pending", "Pending"
        AVAILABLE = "available", "Available"
        WITHDRAWAL_PENDING = "withdrawal_pending", "Withdrawal pending"

    wallet = models.ForeignKey(
        Wallet, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts",
    )
    code = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    bucket = models.CharField(max_length=20, choices=Bucket.choices)
    currency = models.CharField(max_length=3, default="USD", editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["wallet", "bucket"], name="ledger_wallet_bucket_unique"),
            models.UniqueConstraint(
                fields=["bucket"], condition=models.Q(bucket="treasury"), name="ledger_one_treasury",
            ),
            models.CheckConstraint(condition=models.Q(currency="USD"), name="ledger_account_usd_only"),
            models.CheckConstraint(
                condition=(models.Q(wallet__isnull=True, bucket="treasury", kind="asset")
                           | models.Q(wallet__isnull=False, kind="liability",
                                      bucket__in=["pending", "available", "withdrawal_pending"])),
                name="ledger_account_owner_kind_bucket",
            ),
        ]

    def __str__(self):
        return self.code


class JournalRecord(ImmutableModel):
    """Require all journal insertions to pass through the atomic posting service."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        raise ValidationError("Use post_transaction() to write journal records.")


class LedgerTransaction(JournalRecord):
    operation_key = models.CharField(max_length=200, unique=True)
    operation_type = models.CharField(max_length=60)
    request_fingerprint = models.CharField(max_length=64)
    posted_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self):
        return self.operation_key


class LedgerEntry(JournalRecord):
    class Side(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    transaction = models.ForeignKey(LedgerTransaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="entries")
    side = models.CharField(max_length=6, choices=Side.choices)
    amount_cents = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_cents__gt=0), name="ledger_entry_positive"),
            models.CheckConstraint(condition=models.Q(side__in=["debit", "credit"]), name="ledger_entry_side"),
            models.UniqueConstraint(fields=["transaction", "account"], name="ledger_entry_account_unique"),
        ]
