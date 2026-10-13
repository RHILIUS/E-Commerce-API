from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import Select
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from core.test_support import create_customer
from apps.store.admin import StoreAdminForm
from apps.store.models import Store, StoreMember


class StoreCurrencyTests(TestCase):
    def test_currency_is_assigned_from_country(self):
        for country, currency in [("PH", "PHP"), ("US", "USD"), ("JP", "JPY")]:
            store = Store.objects.create(name=country, country_code=country)
            store.refresh_from_db()
            self.assertEqual(store.currency, currency)

    def test_country_and_currency_edits_cannot_relabel_prices(self):
        store = Store.objects.create(name="PH store", country_code="PH")
        store.country_code = "US"
        with self.assertRaises(ValidationError):
            store.save()
        store.refresh_from_db()
        store.currency = "USD"
        with self.assertRaises(ValidationError):
            store.save()
        store.refresh_from_db()
        self.assertEqual((store.country_code, store.currency), ("PH", "PHP"))

    def test_country_changes_with_the_same_currency_are_allowed(self):
        store = Store.objects.create(name="Euro store", country_code="FR")
        store.country_code = "DE"
        store.save()
        store.refresh_from_db()
        self.assertEqual(store.currency, "EUR")

    def test_unknown_country_does_not_silently_become_usd(self):
        with self.assertRaises(ValidationError):
            Store.objects.create(name="Unknown", country_code="XX")


class StoreCurrencyMigrationTests(TransactionTestCase):
    def test_existing_store_currencies_are_backfilled_without_repricing(self):
        from apps.ecommerce.models import Product

        executor = MigrationExecutor(connection)
        if ("store", "0003_storeaddress") not in executor.loader.graph.nodes:
            self.skipTest("Historical currency migration was removed by the development baseline reset.")
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [("store", "0003_storeaddress")]
        executor.migrate(before)
        historical_store = executor.loader.project_state(before).apps.get_model("store", "Store")
        ph = historical_store.objects.create(name="PH store", country_code="PH")
        us = historical_store.objects.create(name="US store", country_code="US")
        product = Product.objects.create(store_id=ph.pk, name="Existing price", price="899.00")
        MigrationExecutor(connection).migrate(latest)
        self.assertEqual(Store.objects.get(pk=ph.pk).currency, "PHP")
        self.assertEqual(Store.objects.get(pk=us.pk).currency, "USD")
        product.refresh_from_db()
        self.assertEqual(str(product.price), "899.00")


class StoreAdminFormTests(SimpleTestCase):
    def test_country_code_uses_phonenumbers_region_choices(self):
        field = StoreAdminForm().fields["country_code"]

        self.assertIsInstance(field.widget, Select)
        self.assertIn(("PH", "Philippines (PH)"), list(field.choices))


class StoreMemberTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_customer(
            username="store-member",
            email="member@example.com",
            full_name="Store Member",
            phone_number="+639171234567",
            country_code="PH",
        )
        cls.store = Store.objects.create(name="Test Store", country_code="PH")

    def test_membership_defaults_to_staff(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)

        self.assertEqual(membership.role, StoreMember.Role.STAFF)
        self.assertEqual(list(self.store.members.all()), [self.user])

    def test_admin_role_is_allowed(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store, role=StoreMember.Role.ADMIN,
        )
        membership.refresh_from_db()
        self.assertEqual(membership.role, StoreMember.Role.ADMIN)

    def test_duplicate_membership_is_rejected(self):
        StoreMember.objects.create(user=self.user, store=self.store)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMember.objects.create(user=self.user, store=self.store)

    def test_invalid_role_is_rejected_by_database(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMember.objects.filter(pk=membership.pk).update(role="owner")

    def test_string_representation_does_not_query_related_objects(self):
        membership = StoreMember.objects.create(
            user=self.user, store=self.store)
        membership = StoreMember.objects.get(pk=membership.pk)

        with self.assertNumQueries(0):
            self.assertEqual(
                str(membership),
                f"User {self.user.pk} - Store {self.store.pk} (staff)",
            )
