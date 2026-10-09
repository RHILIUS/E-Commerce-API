"""Trusted internal posting primitives; callers own business authorization."""

from dataclasses import dataclass
import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, F, Sum, When

from apps.store.models import Store
from .models import LedgerAccount, LedgerEntry, LedgerTransaction, Wallet

MAX_CENTS = 2**63 - 1


@dataclass(frozen=True)
class Posting:
    """One account's positive USD cents, recorded on its debit or credit side."""

    account_id: int
    side: str
    amount_cents: int


@transaction.atomic
def create_wallet(*, user_id=None, store_id=None):
    """Return one owned USD wallet with three zero-balance liability accounts."""
    if (user_id is None) == (store_id is None):
        raise ValidationError("Supply exactly one user or store owner.")
    owner_model = get_user_model() if user_id is not None else Store
    owner_id = user_id if user_id is not None else store_id
    owner_model.objects.select_for_update().get(pk=owner_id)
    wallet, _ = Wallet.objects.get_or_create(user_id=user_id, store_id=store_id)
    for bucket in (LedgerAccount.Bucket.PENDING, LedgerAccount.Bucket.AVAILABLE,
                   LedgerAccount.Bucket.WITHDRAWAL_PENDING):
        LedgerAccount.objects.get_or_create(
            wallet=wallet, bucket=bucket,
            defaults={"code": f"wallet:{wallet.pk}:{bucket}", "kind": LedgerAccount.Kind.LIABILITY},
        )
    return wallet


def get_treasury_account():
    """Lazily create the singleton simulated treasury without creating funds."""
    account, _ = LedgerAccount.objects.get_or_create(
        bucket=LedgerAccount.Bucket.TREASURY,
        defaults={"code": "platform:treasury:usd", "kind": LedgerAccount.Kind.ASSET},
    )
    return account


def account_balance(account):
    """Read journal-derived cents; posting callers must hold the account lock."""
    debit_net = account.entries.aggregate(total=Sum(Case(
        When(side=LedgerEntry.Side.DEBIT, then=F("amount_cents")),
        default=-F("amount_cents"), output_field=models.BigIntegerField(),
    )))["total"] or 0
    return int(debit_net if account.kind == LedgerAccount.Kind.ASSET else -debit_net)


def _validated_postings(postings):
    """Reject lossy amounts and ambiguous lines before touching the database."""
    lines = tuple(postings)
    if len(lines) < 2 or any(not isinstance(line, Posting) for line in lines):
        raise ValidationError("Provide at least two Posting entries.")
    for line in lines:
        if type(line.account_id) is not int or line.account_id <= 0:
            raise ValidationError("Account IDs must be positive integers.")
        if type(line.amount_cents) is not int or not 0 < line.amount_cents <= MAX_CENTS:
            raise ValidationError("Amounts must be positive integer USD cents within bigint range.")
        if line.side not in LedgerEntry.Side.values:
            raise ValidationError("Entry side must be debit or credit.")
    if len({line.account_id for line in lines}) != len(lines):
        raise ValidationError("Provide exactly one net entry per account.")
    debit = sum(line.amount_cents for line in lines if line.side == LedgerEntry.Side.DEBIT)
    credit = sum(line.amount_cents for line in lines if line.side == LedgerEntry.Side.CREDIT)
    if debit != credit:
        raise ValidationError("Total debits must equal total credits.")
    return tuple(sorted(lines, key=lambda line: line.account_id))


@transaction.atomic
def post_transaction(*, operation_key, operation_type, postings):
    """Post balanced cents once, or replay identical input; reject conflicts/overdrafts.

    Every posting first locks the singleton treasury, then affected accounts in ID
    order. This intentionally serializes this small demo's journal, including
    duplicate keys with disjoint accounts. PostgreSQL is required for row locks.
    The internal caller must validate business ownership and payment provenance.
    """
    lines = _validated_postings(postings)
    if not isinstance(operation_key, str) or not operation_key.strip() or len(operation_key) > 200:
        raise ValidationError("A nonempty operation key of at most 200 characters is required.")
    if not isinstance(operation_type, str) or not operation_type.strip() or len(operation_type) > 60:
        raise ValidationError("A nonempty operation type of at most 60 characters is required.")
    fingerprint = hashlib.sha256(json.dumps(
        [operation_type, [(line.account_id, line.side, line.amount_cents) for line in lines]],
        separators=(",", ":"),
    ).encode()).hexdigest()
    treasury = get_treasury_account()
    LedgerAccount.objects.select_for_update().get(pk=treasury.pk)
    existing = LedgerTransaction.objects.filter(operation_key=operation_key).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise ValidationError("Operation key already used with different posting contents.")
        return existing
    accounts = {account.pk: account for account in LedgerAccount.objects.select_for_update().filter(
        pk__in=[line.account_id for line in lines],
    ).order_by("pk")}
    if len(accounts) != len(lines):
        raise ValidationError("An account does not exist.")
    for line in lines:
        account = accounts[line.account_id]
        increases = (line.side == LedgerEntry.Side.DEBIT) == (account.kind == LedgerAccount.Kind.ASSET)
        balance = account_balance(account) + (line.amount_cents if increases else -line.amount_cents)
        if not 0 <= balance <= MAX_CENTS:
            raise ValidationError("Posting would overdraw an account or exceed its bigint balance limit.")
    journal = LedgerTransaction(
        operation_key=operation_key, operation_type=operation_type, request_fingerprint=fingerprint,
    )
    journal.full_clean()
    # Only this transaction boundary bypasses the journal's public write guards.
    models.Model.save(journal, force_insert=True)
    entries = [LedgerEntry(transaction=journal, account_id=line.account_id,
                           side=line.side, amount_cents=line.amount_cents) for line in lines]
    models.QuerySet.bulk_create(LedgerEntry.objects.all(), entries)
    return journal
