"""Financial invariants, immutable admin and PostgreSQL posting races."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connections, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from apps.store.models import Store
from .models import LedgerAccount, LedgerEntry, LedgerTransaction, Wallet
from .services import Posting, account_balance, create_wallet, get_treasury_account, post_transaction


class WalletFixtures:
    """Create isolated zero-balance identities for financial behavior tests."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="wallet-owner", email="wallet@example.com", phone_number="+639123456789",
            country_code="PH", full_name="Wallet owner",
        )
        self.store = Store.objects.create(name="Demo store", country_code="PH")
        self.wallet = create_wallet(store_id=self.store.pk)
        self.treasury = get_treasury_account()
        self.pending = self.wallet.accounts.get(bucket="pending")
        self.available = self.wallet.accounts.get(bucket="available")
        self.withdrawal_pending = self.wallet.accounts.get(bucket="withdrawal_pending")

    def post(self, key, debit, credit, amount=10000):
        """Post one balanced movement through the public internal service."""
        return post_transaction(operation_key=key, operation_type="test",
                                postings=[Posting(debit.pk, "debit", amount), Posting(credit.pk, "credit", amount)])


class WalletTests(WalletFixtures, TestCase):
    def test_wallet_creation_is_zero_balance_and_idempotent(self):
        self.assertEqual(create_wallet(store_id=self.store.pk).pk, self.wallet.pk)
        customer = create_wallet(user_id=self.user.pk)
        self.assertEqual(customer.accounts.count(), 3)
        self.assertEqual(self.wallet.accounts.count(), 3)
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertTrue(all(account_balance(a) == 0 for a in LedgerAccount.objects.all()))

    def test_owner_is_exclusive_and_unique(self):
        for kwargs in ({}, {"user_id": self.user.pk, "store_id": self.store.pk}):
            with self.assertRaises(ValidationError):
                create_wallet(**kwargs)
        for kwargs in ({}, {"user": self.user, "store": self.store}, {"store": self.store}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Wallet.objects.create(**kwargs)

    def test_database_rejects_invalid_account_combinations_and_currency(self):
        cases = [
            {"code": "wrong-asset", "kind": "asset", "bucket": "pending", "wallet": self.wallet},
            {"code": "ownerless", "kind": "liability", "bucket": "available"},
            {"code": "second-treasury", "kind": "asset", "bucket": "treasury"},
            {"code": "duplicate-bucket", "kind": "liability", "bucket": "pending", "wallet": self.wallet},
        ]
        for kwargs in cases:
            with self.assertRaises(IntegrityError), transaction.atomic():
                LedgerAccount.objects.create(**kwargs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Wallet.objects.create(user=self.user, currency="PHP")

    def test_receipt_release_reserve_and_complete_preserve_balances(self):
        self.post("receipt", self.treasury, self.pending)
        self.post("release", self.pending, self.available)
        self.post("reserve", self.available, self.withdrawal_pending, 2500)
        self.post("complete", self.withdrawal_pending, self.treasury, 2500)
        self.assertEqual(account_balance(self.treasury), 7500)
        self.assertEqual(account_balance(self.available), 7500)
        self.assertEqual(account_balance(self.pending), 0)
        self.assertEqual(account_balance(self.withdrawal_pending), 0)

    def test_multi_store_receipt_is_one_balanced_transaction(self):
        other_store = Store.objects.create(name="Second store", country_code="US")
        other = create_wallet(store_id=other_store.pk).accounts.get(bucket="pending")
        receipt = post_transaction(operation_key="multi", operation_type="receipt", postings=[
            Posting(self.treasury.pk, "debit", 10000),
            Posting(self.pending.pk, "credit", 6000), Posting(other.pk, "credit", 4000),
        ])
        self.assertEqual(receipt.entries.count(), 3)
        self.assertEqual(account_balance(self.pending), 6000)
        self.assertEqual(account_balance(other), 4000)

    def test_replay_is_order_independent_even_after_funds_are_spent(self):
        first = self.post("receipt", self.treasury, self.pending)
        self.post("release", self.pending, self.available)
        replay = post_transaction(operation_key="receipt", operation_type="test", postings=[
            Posting(self.pending.pk, "credit", 10000), Posting(self.treasury.pk, "debit", 10000),
        ])
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(LedgerTransaction.objects.count(), 2)

    def test_reused_key_with_different_contents_conflicts(self):
        self.post("receipt", self.treasury, self.pending)
        with self.assertRaises(ValidationError):
            self.post("receipt", self.treasury, self.available)
        with self.assertRaises(ValidationError):
            post_transaction(operation_key="receipt", operation_type="changed", postings=[
                Posting(self.treasury.pk, "debit", 10000), Posting(self.pending.pk, "credit", 10000),
            ])
        self.assertEqual(account_balance(self.treasury), 10000)

    def test_invalid_amounts_unbalanced_missing_and_duplicate_accounts_leave_no_journal(self):
        invalid = [
            [Posting(self.treasury.pk, "debit", n), Posting(self.pending.pk, "credit", n)]
            for n in (0, -1, True, 1.5, Decimal("1"), "100", 2**63)
        ] + [
            [Posting(self.treasury.pk, "debit", 10)],
            [Posting(self.treasury.pk, "debit", 10), Posting(self.pending.pk, "credit", 9)],
            [Posting(self.treasury.pk, "bad", 10), Posting(self.pending.pk, "credit", 10)],
            [Posting(self.treasury.pk, "debit", 10), Posting(999999, "credit", 10)],
            [Posting(self.treasury.pk, "debit", 10), Posting(self.treasury.pk, "credit", 10)],
        ]
        for lines in invalid:
            with self.subTest(lines=lines), self.assertRaises(ValidationError):
                post_transaction(operation_key="invalid", operation_type="test", postings=lines)
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertFalse(LedgerEntry.objects.exists())

    def test_overdraft_rolls_back_all_accounts(self):
        with self.assertRaises(ValidationError):
            self.post("overdraft", self.available, self.withdrawal_pending, 1)
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertEqual(account_balance(self.withdrawal_pending), 0)

    def test_balance_limit_rejects_overflow_without_partial_receipt(self):
        self.post("maximum", self.treasury, self.pending, 2**63 - 1)
        with self.assertRaises(ValidationError):
            self.post("overflow", self.treasury, self.available, 1)
        self.assertEqual(account_balance(self.treasury), 2**63 - 1)
        self.assertEqual(account_balance(self.available), 0)

    def test_failed_entry_insert_rolls_back_transaction_header(self):
        with patch.object(models.QuerySet, "bulk_create", side_effect=RuntimeError("insert failed")):
            with self.assertRaises(RuntimeError):
                self.post("receipt", self.treasury, self.pending)
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertEqual(account_balance(self.treasury), 0)

    def test_outer_transaction_rollback_removes_whole_posting(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.post("receipt", self.treasury, self.pending)
            raise RuntimeError("purchase failed")
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_financial_records_cannot_be_mutated_via_public_orm(self):
        journal = self.post("receipt", self.treasury, self.pending)
        for obj in (self.wallet, self.pending, journal, journal.entries.first()):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ValidationError):
                    obj.save()
                with self.assertRaises(ValidationError):
                    obj.delete()
                with self.assertRaises(ValidationError):
                    type(obj).objects.filter(pk=obj.pk).delete()
                with self.assertRaises(ValidationError):
                    type(obj).objects.filter(pk=obj.pk).update(id=obj.pk)
                with self.assertRaises(ValidationError):
                    type(obj).objects.bulk_create([obj])
                with self.assertRaises(ValidationError):
                    type(obj).objects.bulk_update([obj], [obj._meta.fields[-1].name])
        with self.assertRaises(ValidationError):
            LedgerTransaction.objects.create(operation_key="bypass", operation_type="test")
        with self.assertRaises(ProtectedError):
            self.store.delete()

    def test_admin_is_inspection_only_even_for_superusers(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        request = RequestFactory().get("/")
        request.user = self.user
        journal = self.post("receipt", self.treasury, self.pending)
        for model, obj in ((Wallet, self.wallet), (LedgerAccount, self.pending),
                           (LedgerTransaction, journal), (LedgerEntry, journal.entries.first())):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request, obj))
            self.assertFalse(model_admin.has_delete_permission(request, obj))
            self.assertEqual(self.client.get(reverse(f"admin:wallet_{model._meta.model_name}_changelist")).status_code, 200)
            url = reverse(f"admin:wallet_{model._meta.model_name}_change", args=[obj.pk])
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertEqual(admin.site._registry[LedgerAccount].balance_cents(self.pending), 10000)


class WalletConcurrencyTests(WalletFixtures, TransactionTestCase):
    def race(self, jobs):
        """Use independent database connections and synchronize competing operations."""
        barrier = Barrier(len(jobs))

        def run(job):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return job()
                except ValidationError:
                    return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(run, jobs))

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_spending_cannot_overdraw(self):
        self.post("receipt", self.treasury, self.available, 100)
        results = self.race([
            lambda: self.post("reserve-a", self.available, self.withdrawal_pending, 80),
            lambda: self.post("reserve-b", self.available, self.withdrawal_pending, 80),
        ])
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(account_balance(self.available), 20)
        self.assertEqual(account_balance(self.withdrawal_pending), 80)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_duplicate_receipt_posts_once(self):
        results = self.race([lambda: self.post("receipt", self.treasury, self.pending) for _ in range(2)])
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(LedgerTransaction.objects.count(), 1)
        self.assertEqual(account_balance(self.pending), 10000)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_same_key_different_contents_conflicts(self):
        results = self.race([
            lambda: self.post("receipt", self.treasury, self.pending),
            lambda: self.post("receipt", self.treasury, self.available),
        ])
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(LedgerTransaction.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_wallet_provisioning(self):
        results = self.race([lambda: create_wallet(user_id=self.user.pk) for _ in range(2)])
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(results[0].accounts.count(), 3)
