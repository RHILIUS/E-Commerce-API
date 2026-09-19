from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.store.models import Store

from .models import (
    Attribute, AttributeValue, Category, CategoryAttribute,
    Product, ProductAttributeValue,
)


class ProductSpecificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(taxonomy_id="laptops", name="Laptops")
        cls.other_category = Category.objects.create(taxonomy_id="phones", name="Phones")
        cls.ram = Attribute.objects.create(taxonomy_id="ram", name="RAM")
        cls.color = Attribute.objects.create(taxonomy_id="color", name="Color")
        cls.ram_value = AttributeValue.objects.create(
            taxonomy_id="ram-2", name="2 GB", attribute=cls.ram,
        )
        cls.color_value = AttributeValue.objects.create(
            taxonomy_id="black", name="Black", attribute=cls.color,
        )
        cls.mapping = CategoryAttribute.objects.create(
            category=cls.category, attribute=cls.ram,
        )
        store = Store.objects.create(name="Test Store", country_code="PH")
        cls.product = Product.objects.create(
            name="Laptop", store=store, price="100.00", category=cls.category,
        )

    def create_specification(self):
        """Create a valid RAM specification for relationship mutation tests."""
        return ProductAttributeValue.objects.create(
            product=self.product, attribute=self.ram, value=self.ram_value,
        )

    def test_valid_specification_is_saved(self):
        self.assertIsNotNone(self.create_specification().pk)

    def test_mismatched_value_is_rejected_on_create(self):
        with self.assertRaises(ValidationError) as caught:
            ProductAttributeValue.objects.create(
                product=self.product, attribute=self.ram, value=self.color_value,
            )
        self.assertIn("value", caught.exception.message_dict)

    def test_disallowed_attribute_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            ProductAttributeValue.objects.create(
                product=self.product, attribute=self.color, value=self.color_value,
            )
        self.assertIn("attribute", caught.exception.message_dict)

    def test_second_value_for_same_attribute_is_rejected(self):
        self.create_specification()
        another_value = AttributeValue.objects.create(
            taxonomy_id="ram-4", name="4 GB", attribute=self.ram,
        )
        with self.assertRaises(ValidationError):
            ProductAttributeValue.objects.create(
                product=self.product, attribute=self.ram, value=another_value,
            )

    def test_invalid_foreign_keys_produce_validation_errors(self):
        for field in ("product_id", "attribute_id", "value_id"):
            with self.subTest(field=field):
                specification = ProductAttributeValue(
                    product=self.product, attribute=self.ram, value=self.ram_value,
                )
                setattr(specification, field, 999999)
                with self.assertRaises(ValidationError) as caught:
                    specification.full_clean()
                self.assertIn(field.removesuffix("_id"), caught.exception.message_dict)

    def test_uncategorized_product_cannot_have_specifications(self):
        self.product.category = None
        self.product.save()
        with self.assertRaises(ValidationError):
            self.create_specification()

    def test_category_change_and_removal_preserve_existing_specifications(self):
        self.create_specification()
        for category in (self.other_category, None):
            with self.subTest(category=category):
                self.product.category = category
                with self.assertRaises(ValidationError):
                    self.product.save()
                self.product.refresh_from_db()
                self.assertEqual(self.product.category, self.category)

    def test_compatible_category_change_is_allowed(self):
        self.create_specification()
        CategoryAttribute.objects.create(category=self.other_category, attribute=self.ram)
        self.product.category = self.other_category
        self.product.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.category, self.other_category)

    def test_used_category_attribute_cannot_be_deleted(self):
        self.create_specification()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.mapping.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            CategoryAttribute.objects.filter(pk=self.mapping.pk).delete()

    def test_used_category_attribute_cannot_be_reassigned(self):
        self.create_specification()
        self.mapping.attribute = self.color
        with self.assertRaises(ValidationError):
            self.mapping.save()

    def test_used_value_cannot_be_reassigned(self):
        self.create_specification()
        self.ram_value.attribute = self.color
        with self.assertRaises(ValidationError):
            self.ram_value.save()

    def test_unused_mapping_can_be_deleted(self):
        mapping_id = self.mapping.pk
        self.mapping.delete()
        self.assertFalse(CategoryAttribute.objects.filter(pk=mapping_id).exists())

    def test_existing_specification_cannot_be_saved_with_wrong_value(self):
        specification = self.create_specification()
        specification.value = self.color_value
        with self.assertRaises(ValidationError):
            specification.save()
