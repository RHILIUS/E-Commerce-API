import copy
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.store.models import Store

from ..models import (
    Attribute,
    AttributeValue,
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
)
from ..services.taxonomy_import import import_snapshot, load_snapshot


def sample_snapshot():
    """Represent Shopify's shared IDs and contextual handles, child before parent."""
    return {
        "version": "2026-08",
        "verticals": [{"categories": [
            {
                "id": "glasses", "name": "Glasses", "parent_id": "root",
                "attributes": [
                    {"id": "color", "handle": "frame-color", "name": "Frame color"},
                    {"id": "color", "handle": "lens-color", "name": "Lens color"},
                ],
            },
            {"id": "root", "name": "Accessories",
                "parent_id": None, "attributes": []},
        ]}],
    }


def sample_attributes():
    """Supply one shared choice for two independently identified color contexts."""
    return {"version": "2026-08", "attributes": [{
        "id": "color", "name": "Color", "handle": "color",
        "extended_attributes": [
            {"handle": "frame-color", "name": "Frame color"},
            {"handle": "lens-color", "name": "Lens color"},
        ],
        "values": [{"id": "white", "name": "White", "handle": "color__white"}],
    }]}


class SnapshotValidationTests(SimpleTestCase):
    def load_data(self, data):
        """Load a temporary snapshot through the same parser as the command."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "categories.en.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            path.with_name("attributes.en.json").write_text(json.dumps(sample_attributes()), encoding="utf-8")
            return load_snapshot(path)

    def test_contextual_attributes_are_distinct(self):
        snapshot = self.load_data(sample_snapshot())
        self.assertEqual(snapshot.attributes, {
            ("color", "frame-color"): "Frame color",
            ("color", "lens-color"): "Lens color",
        })
        self.assertEqual(len(snapshot.associations), 2)

    def test_malformed_shapes_and_field_limits_are_rejected(self):
        variants = [None, {}, {"version": "2026-08", "verticals": []}]
        for field, value in (("name", "x" * 151), ("id", 123), ("attributes", {}), ("parent_id", [])):
            data = sample_snapshot()
            data["verticals"][0]["categories"][0][field] = value
            variants.append(data)
        data = sample_snapshot()
        del data["verticals"][0]["categories"][0]["attributes"][0]["handle"]
        variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(CommandError):
                self.load_data(data)

    def test_invalid_json_and_missing_files_are_reported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "categories.en.json"
            with self.assertRaises(CommandError):
                load_snapshot(path)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(CommandError):
                load_snapshot(path)

    def test_unknown_parents_and_cycles_are_rejected(self):
        for parent in ("missing", "glasses"):
            data = sample_snapshot()
            data["verticals"][0]["categories"][1]["parent_id"] = parent
            with self.subTest(parent=parent), self.assertRaises(CommandError):
                self.load_data(data)

    def test_duplicate_categories_and_conflicting_context_names_are_rejected(self):
        data = sample_snapshot()
        categories = data["verticals"][0]["categories"]
        categories.append(copy.deepcopy(categories[0]))
        with self.assertRaisesMessage(CommandError, "Duplicate category"):
            self.load_data(data)
        data = sample_snapshot()
        attributes = data["verticals"][0]["categories"][0]["attributes"]
        attributes.append({**attributes[0], "name": "Conflicting name"})
        with self.assertRaisesMessage(CommandError, "Conflicting names"):
            self.load_data(data)


class TaxonomyImportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "categories.en.json"
        self.data = sample_snapshot()
        self.value_data = sample_attributes()
        self.write_snapshot()

    def write_snapshot(self):
        """Write the test's current source data for command and parser assertions."""
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.path.with_name("attributes.en.json").write_text(json.dumps(self.value_data), encoding="utf-8")

    def run_command(self, *, dry_run=False):
        """Capture importer output without depending on terminal colors."""
        output = StringIO()
        call_command("import_taxonomy", directory=self.path.parent,
                     dry_run=dry_run, stdout=output)
        return output.getvalue()

    def test_import_and_repeat_preserve_ids_and_contexts(self):
        self.run_command()
        child = Category.objects.get(taxonomy_id="glasses")
        self.assertEqual(child.parent.taxonomy_id, "root")
        self.assertEqual(Attribute.objects.filter(
            taxonomy_id="color").count(), 2)
        self.assertEqual(CategoryAttribute.objects.count(), 2)
        category_ids = list(Category.objects.values_list("pk", flat=True))
        attribute_ids = list(Attribute.objects.values_list("pk", flat=True))
        output = self.run_command()
        self.assertEqual(
            list(Category.objects.values_list("pk", flat=True)), category_ids)
        self.assertEqual(
            list(Attribute.objects.values_list("pk", flat=True)), attribute_ids)
        self.assertNotIn("created=1", output)
        self.assertEqual(CategoryAttribute.objects.count(), 2)
        self.assertEqual(AttributeValue.objects.count(), 2)
        self.assertEqual(ProductAttributeValue.objects.count(), 0)

    def test_dry_run_reports_without_writing(self):
        output = self.run_command(dry_run=True)
        self.assertIn("categories: created=2", output)
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Attribute.objects.exists())
        self.assertFalse(CategoryAttribute.objects.exists())

    def test_dry_run_does_not_update_existing_metadata(self):
        self.run_command()
        self.data["verticals"][0]["categories"][0]["name"] = "Renamed"
        self.write_snapshot()
        self.assertIn("updated=1", self.run_command(dry_run=True))
        self.assertEqual(Category.objects.get(
            taxonomy_id="glasses").name, "Glasses")

    def test_update_preserves_existing_references_and_inactive_flag(self):
        self.run_command()
        category = Category.objects.get(taxonomy_id="glasses")
        category.is_active = False
        category.save()
        attribute = Attribute.objects.get(handle="frame-color")
        value = AttributeValue.objects.create(
            attribute=attribute, taxonomy_id="black", name="Black")
        product = Product.objects.create(
            name="Glasses", price="20", category=category,
            store=Store.objects.create(name="Store", country_code="PH"),
        )
        specification = ProductAttributeValue.objects.create(
            product=product, attribute=attribute, value=value)
        source_category = self.data["verticals"][0]["categories"][0]
        source_category["name"] = "Eyeglasses"
        source_category["parent_id"] = None
        source_category["attributes"][0]["name"] = "Frame colour"
        self.value_data["attributes"][0]["extended_attributes"][0]["name"] = "Frame colour"
        source_category["attributes"].pop()
        self.write_snapshot()
        output = self.run_command()
        category.refresh_from_db()
        attribute.refresh_from_db()
        specification.refresh_from_db()
        self.assertEqual(category.name, "Eyeglasses")
        self.assertIsNone(category.parent_id)
        self.assertFalse(category.is_active)
        self.assertEqual(attribute.name, "Frame colour")
        self.assertEqual(specification.attribute_id, attribute.pk)
        self.assertEqual(CategoryAttribute.objects.count(), 2)
        self.assertIn("retained_outside_snapshot=1", output)

    def test_legacy_attributes_require_explicit_context_mapping(self):
        Attribute.objects.create(taxonomy_id="color", name="Color")
        with self.assertRaisesMessage(CommandError, "no Shopify handle"):
            self.run_command()
        self.assertFalse(Category.objects.exists())

    def test_failure_rolls_back_all_import_writes(self):
        with patch.object(CategoryAttribute.objects, "bulk_create", side_effect=IntegrityError("failure")):
            with self.assertRaises(CommandError):
                self.run_command()
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Attribute.objects.exists())

    def test_product_can_have_different_frame_and_lens_colors(self):
        self.run_command()
        frame = Attribute.objects.get(handle="frame-color")
        lens = Attribute.objects.get(handle="lens-color")
        frame_black = AttributeValue.objects.create(
            attribute=frame, taxonomy_id="black", name="Black")
        lens_black = AttributeValue.objects.create(
            attribute=lens, taxonomy_id="black", name="Black")
        lens_blue = AttributeValue.objects.create(
            attribute=lens, taxonomy_id="blue", name="Blue")
        product = Product.objects.create(
            name="Glasses", price="20", category=Category.objects.get(taxonomy_id="glasses"),
            store=Store.objects.create(name="Store", country_code="PH"),
        )
        ProductAttributeValue.objects.create(
            product=product, attribute=frame, value=frame_black)
        ProductAttributeValue.objects.create(
            product=product, attribute=lens, value=lens_blue)
        self.assertEqual(product.attribute_values.count(), 2)
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=product, attribute=frame, value=lens_black).full_clean()
        with self.assertRaises(ValidationError):
            ProductAttributeValue.objects.create(
                product=product, attribute=lens, value=lens_black)

    def test_database_rejects_duplicate_context_and_value(self):
        self.run_command()
        frame = Attribute.objects.get(handle="frame-color")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attribute.objects.create(
                taxonomy_id=frame.taxonomy_id, handle=frame.handle, name=frame.name)
        AttributeValue.objects.create(
            attribute=frame, taxonomy_id="black", name="Black")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttributeValue.objects.bulk_create([
                AttributeValue(attribute=frame,
                               taxonomy_id="black", name="Duplicate")
            ])

    def test_snapshot_plan_matches_created_counts(self):
        snapshot = load_snapshot(self.path)
        plan = import_snapshot(snapshot, dry_run=True)
        result = import_snapshot(snapshot)
        self.assertEqual(plan, result)

    def test_invalid_value_sources_fail_before_writes(self):
        original = sample_attributes()
        variants = []
        wrong_version = copy.deepcopy(original)
        wrong_version["version"] = "2025-01"
        variants.append(wrong_version)
        missing_context = copy.deepcopy(original)
        missing_context["attributes"][0]["extended_attributes"] = []
        variants.append(missing_context)
        duplicate = copy.deepcopy(original)
        duplicate["attributes"][0]["values"] *= 2
        variants.append(duplicate)
        long_name = copy.deepcopy(original)
        long_name["attributes"][0]["values"][0]["name"] = "x" * 151
        variants.append(long_name)
        for data in variants:
            with self.subTest(data=data):
                self.value_data = data
                self.write_snapshot()
                with self.assertRaises(CommandError):
                    self.run_command()
                self.assertFalse(Category.objects.exists())
                self.assertFalse(AttributeValue.objects.exists())
        self.path.with_name("attributes.en.json").unlink()
        with self.assertRaisesMessage(CommandError, "attributes.en.json"):
            self.run_command()

    def test_value_updates_preserve_ids_ownership_and_product_references(self):
        self.run_command()
        value = AttributeValue.objects.get(attribute__handle="frame-color", taxonomy_id="white")
        product = Product.objects.create(
            name="Glasses", price="20", category=Category.objects.get(taxonomy_id="glasses"),
            store=Store.objects.create(name="Store", country_code="PH"),
        )
        specification = ProductAttributeValue.objects.create(product=product, attribute=value.attribute, value=value)
        self.value_data["attributes"][0]["values"][0]["name"] = "White updated"
        self.write_snapshot()
        self.run_command(dry_run=True)
        value.refresh_from_db()
        self.assertEqual(value.name, "White")
        self.run_command()
        value.refresh_from_db()
        specification.refresh_from_db()
        self.assertEqual(value.name, "White updated")
        self.assertEqual(specification.value_id, value.pk)
        self.assertEqual(specification.attribute_id, value.attribute_id)
        self.value_data["attributes"][0]["values"] = []
        self.write_snapshot()
        self.run_command()
        self.assertTrue(AttributeValue.objects.filter(pk=value.pk).exists())

    def test_value_insert_failure_rolls_back_categories_and_attributes(self):
        with patch.object(AttributeValue.objects, "bulk_create", side_effect=IntegrityError("failure")):
            with self.assertRaises(CommandError):
                self.run_command()
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Attribute.objects.exists())
