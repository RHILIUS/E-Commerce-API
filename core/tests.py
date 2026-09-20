from unittest.mock import patch

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TestCase

from apps.ecommerce.models import Attribute, AttributeValue, Category, CategoryAttribute, Product, ProductAttributeValue
from apps.ecommerce.services.taxonomy_import import load_snapshot
from apps.store.models import Store
from core.management.commands.seed import Command, resolve_product_taxonomy, validate_seed_data
from core.seed_data import PRODUCT_DATA


class SeedDataTests(SimpleTestCase):
    def test_seed_data_has_valid_products_and_images(self):
        product_images = validate_seed_data()

        self.assertEqual(len(PRODUCT_DATA), 24)
        self.assertEqual(len(product_images), 72)
        self.assertEqual(
            sum(image["is_default"] for image in product_images),
            24,
        )


class SeedTaxonomyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        snapshot = load_snapshot(settings.BASE_DIR / "data/taxonomy/2026-08/categories.en.json")
        for product in PRODUCT_DATA:
            category_id = product["category_taxonomy_id"]
            category, _ = Category.objects.get_or_create(
                taxonomy_id=category_id, defaults={"name": snapshot.categories[category_id][0]},
            )
            for spec in product["attribute_values"]:
                key = (spec["attribute_taxonomy_id"], spec["attribute_handle"])
                # Verify every seed choice against the actual pinned files.
                if (category_id, key) not in snapshot.associations:
                    raise AssertionError(f"Invalid seed category association: {product['name']}")
                value_name = snapshot.values[(key, spec["value_taxonomy_id"])]
                attribute, _ = Attribute.objects.get_or_create(
                    taxonomy_id=key[0], handle=key[1], defaults={"name": snapshot.attributes[key]},
                )
                CategoryAttribute.objects.get_or_create(category=category, attribute=attribute)
                AttributeValue.objects.get_or_create(
                    attribute=attribute, taxonomy_id=spec["value_taxonomy_id"], defaults={"name": value_name},
                )
        cls.store = Store.objects.create(name="Seed test", country_code="PH")

    def seed_products(self):
        """Exercise the production resolver and product writer in one transaction."""
        with transaction.atomic():
            return Command()._seed_products(self.store, resolve_product_taxonomy())

    def test_every_product_has_valid_category_and_values_and_repeat_is_idempotent(self):
        products = self.seed_products()
        ids = list(ProductAttributeValue.objects.order_by("pk").values_list("pk", flat=True))
        for data in PRODUCT_DATA:
            product = products[data["name"]]
            self.assertEqual(product.category.taxonomy_id, data["category_taxonomy_id"])
            self.assertEqual(product.attribute_values.count(), len(data["attribute_values"]))
            for specification in product.attribute_values.all():
                specification.full_clean()
        self.seed_products()
        self.assertEqual(Product.objects.count(), 24)
        self.assertEqual(list(ProductAttributeValue.objects.order_by("pk").values_list("pk", flat=True)), ids)

    def test_seed_restores_changed_specification_value_without_duplicate(self):
        product = self.seed_products()[PRODUCT_DATA[0]["name"]]
        specification = product.attribute_values.get()
        original_value = specification.value
        other = AttributeValue.objects.create(attribute=specification.attribute, taxonomy_id="local:test", name="Other")
        specification.value = other
        specification.save()
        self.seed_products()
        specification.refresh_from_db()
        self.assertEqual(specification.value, original_value)

    def test_missing_taxonomy_stops_before_writes_and_uploads(self):
        AttributeValue.objects.all().delete()
        with patch.object(Command, "_seed") as seed, patch("core.management.commands.seed.attach_image_if_missing") as upload:
            with self.assertRaisesMessage(CommandError, "import_taxonomy"):
                Command().handle()
            seed.assert_not_called()
            upload.assert_not_called()
        self.assertFalse(Product.objects.exists())

    def test_inactive_category_and_disallowed_attribute_are_rejected(self):
        category = Category.objects.get(taxonomy_id=PRODUCT_DATA[0]["category_taxonomy_id"])
        category.is_active = False
        category.save()
        with self.assertRaisesMessage(CommandError, "inactive category"):
            resolve_product_taxonomy()
        category.is_active = True
        category.save()
        CategoryAttribute.objects.filter(category=category).delete()
        with self.assertRaisesMessage(CommandError, "not allowed"):
            resolve_product_taxonomy()
