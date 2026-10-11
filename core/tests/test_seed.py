import uuid
from copy import deepcopy
from unittest.mock import patch

from apps.account.models import RecipientInfo
from apps.checkout.models import Checkout, PaymentAttempt
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
        command = Command()
        command.handle()
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
        command.handle()
        order.refresh_from_db()
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(order.concluded_at, concluded_at)
        self.assertEqual(order.items.get().pk, item.pk)
        self.assertEqual(order.checkout_id, checkout_id)
        self.assertEqual(order.checkout.reference, checkout_reference)
        self.assertEqual(order.paid_at, paid_at)
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertFalse(StockReservation.objects.exists())
        item.product.refresh_from_db()
        self.assertEqual(item.product.stock, stock)
        self.assertEqual(item.product.sold_count, sold_count)

    @patch("core.management.commands.seed.attach_image_if_missing")
    def test_multiple_customers_stores_and_recipients(self, _):
        customers = deepcopy(CUSTOMERS_DATA)
        second = deepcopy(customers[0])
        second.update(username="second", email="second@example.com",
                      phone_number="+639111111111")
        second["orders"][0]["key"] = "second-customer-order"
        customers.append(second)
        extra = deepcopy(customers[0]["recipient_infos"][0])
        extra.update(is_default=False, address_line1="Another address")
        customers[0]["recipient_infos"].append(extra)
        stores = deepcopy(STORES_DATA)
        second_store = deepcopy(stores[0])
        second_store.update(name="Second store", products=[])
        stores.append(second_store)
        with patch("core.management.commands.seed.CUSTOMERS_DATA", customers), patch(
            "core.management.commands.seed.STORES_DATA", stores
        ):
            Command().handle()
            Command().handle()
        from apps.ecommerce.models import Order
        self.assertEqual(Order.objects.count(), 2)
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
                Command().handle()
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
