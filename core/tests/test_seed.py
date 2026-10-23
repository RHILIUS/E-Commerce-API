import uuid
from io import StringIO
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from apps.account.models import RecipientInfo
from apps.authentication.models import UserAddress
from apps.checkout.models import Checkout, OrderRefund, PaymentAttempt
from apps.ecommerce.services.sales import with_sales_totals
from apps.wallet.models import LedgerEntry, LedgerTransaction
from apps.wallet.services import account_balance
from apps.checkout.services.snapshots import recipient_snapshot
from apps.ecommerce.models import (
    Attribute,
    AttributeValue,
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
)
from apps.ecommerce.services.taxonomy_import import load_snapshot
from apps.inventory.models import StockReservation
from apps.store.models import Store
from core.management.commands.seed import (
    Command,
    resolve_product_taxonomy,
    validate_seed_data,
)
from core.seed_data import CUSTOMERS_DATA, STORES_DATA
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

CUSTOMER_USER_DATA = CUSTOMERS_DATA[0]
CUSTOMER_RECIPIENT_DATA = CUSTOMER_USER_DATA["recipient_infos"][0]
PRODUCT_DATA = [
    product for store in STORES_DATA for product in store["products"]]


class SeedDataTests(SimpleTestCase):
    def test_invalid_or_duplicate_order_keys_are_rejected(self):
        for key in (None, -1, "", " "):
            with self.subTest(key=key):
                customers = deepcopy(CUSTOMERS_DATA)
                customers[0]["orders"][0]["key"] = key
                with patch("core.management.commands.seed.CUSTOMERS_DATA", customers):
                    with self.assertRaisesMessage(CommandError, "unique non-empty strings"):
                        validate_seed_data()
        customers = deepcopy(CUSTOMERS_DATA)
        customers[0]["orders"].append(deepcopy(customers[0]["orders"][0]))
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers):
            with self.assertRaisesMessage(CommandError, "unique non-empty strings"):
                validate_seed_data()

    def test_invalid_order_reference_is_rejected_before_seeding(self):
        customers = deepcopy(CUSTOMERS_DATA)
        customers[0]["orders"][0]["order_items"][0]["product_name"] = "Missing product"
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers):
            with self.assertRaisesMessage(CommandError, "Order products must exist"):
                validate_seed_data()

    def test_cross_store_seed_order_is_rejected_before_writes(self):
        stores = deepcopy(STORES_DATA)
        other = deepcopy(stores[0])
        other["name"] = "Other store"
        other["products"] = [stores[0]["products"].pop()]
        stores.append(other)
        customers = deepcopy(CUSTOMERS_DATA)
        customers[0]["orders"][0]["order_items"].append({
            "product_name": other["products"][0]["name"], "quantity": 1,
        })
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers), patch(
            "core.management.commands.seed.STORES_DATA", stores
        ):
            with self.assertRaisesMessage(CommandError, "exactly one store"):
                validate_seed_data()

    def test_invalid_payment_fixtures_are_rejected(self):
        stripe_index = next(i for i, o in enumerate(CUSTOMER_USER_DATA["orders"]) if o["payment_method"] == "stripe")
        cases = [
            (0, {"payment_method": "unknown"}, "payment method"),
            (0, {"refunded": True}, "paid Stripe"),
            (0, {"refunded": "yes"}, "boolean"),
            (stripe_index, {"payment_status": "pending"}, "payment status"),
            (stripe_index, {"exchange_rate": "NaN"}, "exchange_rate"),
            (stripe_index, {"exchange_rate": "Infinity"}, "exchange_rate"),
            (stripe_index, {"exchange_rate": "0"}, "exchange_rate"),
            (stripe_index, {"exchange_rate": "-1"}, "exchange_rate"),
            (stripe_index, {"exchange_rate": "0.0000000000001"}, "exchange_rate"),
            (stripe_index, {"refunded": True, "payment_status": "unpaid"}, "paid Stripe"),
        ]
        for index, changes, message in cases:
            with self.subTest(changes=changes):
                customers = deepcopy(CUSTOMERS_DATA)
                customers[0]["orders"][index].update(changes)
                with patch("core.management.commands.seed.CUSTOMERS_DATA", customers):
                    with self.assertRaisesMessage(CommandError, message):
                        validate_seed_data()

    def test_seed_data_has_valid_products_and_images(self):
        product_images = validate_seed_data()

        self.assertEqual(len(PRODUCT_DATA), 24)
        self.assertEqual(len(product_images), 72)
        self.assertEqual(
            sum(image["is_default"] for image in product_images),
            24,
        )


class SeedCustomerTests(TestCase):
    @patch("core.management.commands.seed.attach_image_if_missing")
    def test_customer_and_recipient_are_upserted_without_resetting_password(self, _):
        command = Command()
        customer = command._seed_user(CUSTOMER_USER_DATA, [])
        command._seed_recipient(customer, CUSTOMER_RECIPIENT_DATA)

        self.assertTrue(customer.check_password(
            CUSTOMER_USER_DATA["password"]))
        customer.set_password("customer-changed-password")
        customer.address.city = "Changed personal city"
        customer.address.save()
        customer.full_name = "Changed name"
        customer.save()
        recipient = customer.recipient_infos.get(is_default=True)
        recipient.city = "Changed city"
        recipient.save()

        same_customer = command._seed_user(CUSTOMER_USER_DATA, [])
        command._seed_recipient(same_customer, CUSTOMER_RECIPIENT_DATA)
        same_customer.refresh_from_db()
        recipient.refresh_from_db()

        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(RecipientInfo.objects.count(), 1)
        self.assertEqual(UserAddress.objects.count(), 1)
        self.assertEqual(same_customer.address.city, CUSTOMER_USER_DATA["address"]["city"])
        self.assertEqual(same_customer.pk, customer.pk)
        self.assertEqual(same_customer.full_name,
                         CUSTOMER_USER_DATA["full_name"])
        self.assertTrue(same_customer.check_password(
            "customer-changed-password"))
        self.assertEqual(recipient.city, CUSTOMER_RECIPIENT_DATA["city"])


class SeedTaxonomyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        snapshot = load_snapshot(
            settings.BASE_DIR / "data/taxonomy/2026-08/categories.en.json")
        for product in PRODUCT_DATA:
            category_id = product["category_taxonomy_id"]
            category, _ = Category.objects.get_or_create(
                taxonomy_id=category_id, defaults={
                    "name": snapshot.categories[category_id][0]},
            )
            for spec in product["attribute_values"]:
                key = (spec["attribute_taxonomy_id"], spec["attribute_handle"])
                # Verify every seed choice against the actual pinned files.
                if (category_id, key) not in snapshot.associations:
                    raise AssertionError(
                        f"Invalid seed category association: {product['name']}")
                value_name = snapshot.values[(key, spec["value_taxonomy_id"])]
                attribute, _ = Attribute.objects.get_or_create(
                    taxonomy_id=key[0], handle=key[1], defaults={
                        "name": snapshot.attributes[key]},
                )
                CategoryAttribute.objects.get_or_create(
                    category=category, attribute=attribute)
                AttributeValue.objects.get_or_create(
                    attribute=attribute, taxonomy_id=spec["value_taxonomy_id"], defaults={
                        "name": value_name},
                )
        cls.store = Store.objects.create(name="Seed test", country_code="PH")

    def seed_products(self):
        """Exercise the production resolver and product writer in one transaction."""
        with transaction.atomic():
            return Command()._seed_products(self.store, resolve_product_taxonomy(), PRODUCT_DATA)

    def test_every_product_has_valid_category_and_values_and_repeat_is_idempotent(self):
        products = self.seed_products()
        ids = list(ProductAttributeValue.objects.order_by(
            "pk").values_list("pk", flat=True))
        for data in PRODUCT_DATA:
            product = products[data["name"]]
            self.assertEqual(product.category.taxonomy_id,
                             data["category_taxonomy_id"])
            self.assertEqual(product.attribute_values.count(),
                             len(data["attribute_values"]))
            for specification in product.attribute_values.all():
                specification.full_clean()
        self.seed_products()
        self.assertEqual(Product.objects.count(), 24)
        self.assertEqual(list(ProductAttributeValue.objects.order_by(
            "pk").values_list("pk", flat=True)), ids)

    def test_seed_restores_changed_specification_value_without_duplicate(self):
        product = self.seed_products()[PRODUCT_DATA[0]["name"]]
        specification = product.attribute_values.get()
        original_value = specification.value
        other = AttributeValue.objects.create(
            attribute=specification.attribute, taxonomy_id="local:test", name="Other")
        specification.value = other
        specification.save()
        self.seed_products()
        specification.refresh_from_db()
        self.assertEqual(specification.value, original_value)

    @patch("core.management.commands.seed.attach_image_if_missing")
    def test_nested_seed_repeats_orders_without_duplicates(self, _):
        from apps.ecommerce.models import Order, OrderItem
        output = StringIO()
        call_command("seed", stdout=output)
        key = uuid.uuid5(
            uuid.NAMESPACE_URL, f"ecommerce:seed:order:{CUSTOMER_USER_DATA['orders'][0]['key']}")
        order = Order.objects.get(checkout__reference=key)
        self.assertGreater(order.pk, 0)
        client = APIClient()
        client.force_authenticate(order.user)
        response = client.get(f"/api/shop/orders/{order.pk}/history/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["order_id"], order.pk)
        concluded_at = order.concluded_at
        checkout_id = order.checkout_id
        checkout_reference = order.checkout.reference
        paid_at = order.paid_at
        self.assertEqual(order.checkout.total_amount, order.get_total())
        self.assertEqual(order.checkout.currency, order.store.currency)
        self.assertEqual(order.checkout.payment_method,
                         Checkout.PaymentMethod.COD)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(order.recipient_snapshot,
                         recipient_snapshot(order.recipient_info))
        item = order.items.get()
        self.assertEqual(order.recipient_info.user_id, order.user_id)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(item.price_at_purchase, item.product.price)
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.original_unit_price, item.product.price)
        self.assertEqual(item.product_name_at_purchase, item.product.name)
        self.assertEqual(order.store_id, item.product.store_id)
        stock = item.product.stock
        sold_count = item.product.sold_count
        self.assertFalse(order.user.store_memberships.exists())
        call_command("seed", stdout=output)
        self.assertEqual(output.getvalue().count("Seed complete:"), 2)
        self.assertEqual(output.getvalue().count("New files uploaded: 0."), 2)
        store = Store.objects.get(name=STORES_DATA[0]["name"])
        self.assertEqual(store.public_email, STORES_DATA[0]["public_email"])
        self.assertEqual(store.public_phone, STORES_DATA[0]["public_phone"])
        order.refresh_from_db()
        self.assertEqual(Order.objects.count(), len(CUSTOMER_USER_DATA["orders"]))
        self.assertEqual(OrderItem.objects.count(), sum(len(o["order_items"]) for o in CUSTOMER_USER_DATA["orders"]))
        self.assertEqual(order.concluded_at, concluded_at)
        self.assertEqual(order.items.get().pk, item.pk)
        self.assertEqual(order.checkout_id, checkout_id)
        self.assertEqual(order.checkout.reference, checkout_reference)
        self.assertEqual(order.paid_at, paid_at)
        self.assertEqual(Checkout.objects.count(), len(CUSTOMER_USER_DATA["orders"]))
        self.assertEqual(PaymentAttempt.objects.count(), 4)
        self.assertEqual(OrderRefund.objects.count(), 1)
        self.assertEqual(LedgerTransaction.objects.count(), 4)
        self.assertEqual(LedgerEntry.objects.count(), 8)
        self.assertFalse(StockReservation.objects.exists())
        item.product.refresh_from_db()
        self.assertEqual(item.product.stock, stock)
        self.assertEqual(item.product.sold_count, sold_count)

    @patch("core.management.commands.seed.attach_image_if_missing")
    def test_seed_sales_totals_payment_snapshots_and_ledger_repeat(self, _):
        from apps.ecommerce.models import Order, OrderEvent
        expected = {data["name"]: 0 for data in PRODUCT_DATA}
        expected.update({"Rhilius NovaPhone X1": 3, "Rhilius PulsePhone 5G": 3, "Rhilius TechPad 10": 4})
        call_command("seed", stdout=StringIO())
        before = list(Order.objects.order_by("pk").values_list("pk", "created_at", "paid_at", "concluded_at"))
        events = OrderEvent.objects.count()
        attempts = list(PaymentAttempt.objects.order_by("pk").values_list("pk", "reference", "receipt_id", "succeeded_at"))
        refund_ids = list(OrderRefund.objects.values_list("pk", "reversal_id"))
        journals = list(LedgerTransaction.objects.values_list("pk", flat=True))
        for repeat in range(2):
            if repeat:
                call_command("seed", stdout=StringIO())
            actual = dict(with_sales_totals(Product.objects.all()).values_list("name", "sales_total"))
            self.assertEqual(actual, expected)
            client = APIClient()
            with patch("apps.ecommerce.views.get_display_rates", return_value={}), patch("apps.store.views.get_display_rates", return_value={}):
                catalog = client.get("/api/shop/products/").json()
                for row in catalog["results"]:
                    self.assertEqual(row["sold_count"], expected[row["name"]])
                store = Store.objects.get(name=STORES_DATA[0]["name"])
                self.assertEqual(client.get(f"/api/shop/stores/{store.pk}/").json()["items_sold"], 10)
                for name in ("Rhilius NovaPhone X1", "Rhilius VisionPhone Fold", "Rhilius TitanPhone Pro"):
                    product = Product.objects.get(name=name)
                    self.assertEqual(client.get(f"/api/shop/products/{product.pk}/").json()["sold_count"], expected[name])
            self.assertEqual(list(Order.objects.order_by("pk").values_list("pk", "created_at", "paid_at", "concluded_at")), before)
            self.assertEqual(OrderEvent.objects.count(), events)
            self.assertEqual(list(PaymentAttempt.objects.order_by("pk").values_list("pk", "reference", "receipt_id", "succeeded_at")), attempts)
            self.assertEqual(list(OrderRefund.objects.values_list("pk", "reversal_id")), refund_ids)
            self.assertEqual(list(LedgerTransaction.objects.values_list("pk", flat=True)), journals)
        for attempt in PaymentAttempt.objects.select_related("checkout", "receipt"):
            self.assertEqual(attempt.currency, "USD")
            self.assertEqual(attempt.amount, attempt.checkout.orders.get().get_total())
            self.assertIsNone(attempt.stripe_checkout_session_id)
            self.assertIsNone(attempt.stripe_payment_intent_id)
            order = attempt.checkout.orders.get()
            self.assertEqual(order.exchange_rate, Decimal("0.02"))
            item = order.items.get()
            self.assertEqual(item.price_at_purchase, (item.original_unit_price * Decimal("0.02")).quantize(Decimal("0.01")))
            self.assertEqual(attempt.receipt_id is not None, attempt.status == "succeeded")
        refund = OrderRefund.objects.get()
        self.assertEqual(refund.status, "succeeded")
        self.assertEqual(refund.amount_cents, int(refund.order.get_total() * 100))
        self.assertIsNotNone(refund.reversal_id)
        pending = store.wallet.accounts.get(bucket="pending")
        # Only the two non-refunded Stripe fixtures remain in the simulated wallet.
        self.assertEqual(account_balance(pending), 426230)
        self.assertFalse(StockReservation.objects.exists())
        # Changing a terminal payment fixture must roll back instead of rewriting money history.
        customers = deepcopy(CUSTOMERS_DATA)
        paid_fixture = next(o for o in customers[0]["orders"] if o["key"] == "stripe-paid-phone")
        paid_fixture["payment_status"] = "unpaid"
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers):
            with self.assertRaisesMessage(CommandError, "Seed payment history changed"):
                call_command("seed", stdout=StringIO())
        self.assertEqual(list(PaymentAttempt.objects.order_by("pk").values_list("pk", "reference", "receipt_id", "succeeded_at")), attempts)
        self.assertEqual(list(LedgerTransaction.objects.values_list("pk", flat=True)), journals)
        self.assertEqual(account_balance(pending), 426230)

    @patch("core.management.commands.seed.attach_image_if_missing")
    def test_multiple_customers_stores_and_recipients(self, _):
        customers = deepcopy(CUSTOMERS_DATA)
        second = deepcopy(customers[0])
        second.update(username="second", email="second@example.com",
                      phone_number="+639111111111")
        for order in second["orders"]:
            order["key"] = "second-" + order["key"]
        customers.append(second)
        extra = deepcopy(customers[0]["recipient_infos"][0])
        extra.update(is_default=False, address_line1="Another address")
        customers[0]["recipient_infos"].append(extra)
        stores = deepcopy(STORES_DATA)
        second_store = deepcopy(stores[0])
        second_store.update(name="Second store", products=[])
        stores.append(second_store)
        output = StringIO()
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers), patch(
            "core.management.commands.seed.STORES_DATA", stores
        ):
            call_command("seed", stdout=output)
            call_command("seed", stdout=output)
        self.assertEqual(output.getvalue().count(f"Seed complete: 2 customers, 2 stores, {2 * len(CUSTOMER_USER_DATA['orders'])} orders"), 2)
        self.assertEqual(output.getvalue().count("New files uploaded: 0."), 2)
        from apps.ecommerce.models import Order
        self.assertEqual(Order.objects.count(), 2 * len(CUSTOMER_USER_DATA["orders"]))
        first_user = get_user_model().objects.get(email=customers[0]["email"])
        self.assertEqual(first_user.recipient_infos.count(), 2)
        self.assertEqual(Store.objects.filter(
            name__in=[s["name"] for s in stores]).count(), 2)
        for order in Order.objects.all():
            self.assertEqual(order.recipient_info.user_id, order.user_id)

    def test_missing_taxonomy_stops_before_writes_and_uploads(self):
        AttributeValue.objects.all().delete()
        with patch.object(Command, "_seed") as seed, patch("core.management.commands.seed.attach_image_if_missing") as upload:
            with self.assertRaisesMessage(CommandError, "import_taxonomy"):
                call_command("seed", stdout=StringIO())
            seed.assert_not_called()
            upload.assert_not_called()
        self.assertFalse(Product.objects.exists())

    def test_inactive_category_and_disallowed_attribute_are_rejected(self):
        category = Category.objects.get(
            taxonomy_id=PRODUCT_DATA[0]["category_taxonomy_id"])
        category.is_active = False
        category.save()
        with self.assertRaisesMessage(CommandError, "inactive category"):
            resolve_product_taxonomy()
        category.is_active = True
        category.save()
        CategoryAttribute.objects.filter(category=category).delete()
        with self.assertRaisesMessage(CommandError, "not allowed"):
            resolve_product_taxonomy()
