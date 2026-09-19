"""Validate and import Shopify's English category distribution snapshot."""

import json
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import CommandError
from django.db import connection, transaction

from apps.ecommerce.models import Attribute, Category, CategoryAttribute


@dataclass
class TaxonomySnapshot:
    version: str
    categories: dict[str, tuple[str, str | None]]
    attributes: dict[tuple[str, str], str]
    associations: set[tuple[str, tuple[str, str]]]


def _text(record, field, limit, context):
    """Require nonempty text that fits the corresponding database field."""
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CommandError(f"{context}: {field} must be nonempty text of at most {limit} characters.")
    return value


def _list(record, field, context):
    """Require the array shape used by Shopify's category distribution."""
    value = record.get(field)
    if not isinstance(value, list):
        raise CommandError(f"{context}: {field} must be an array.")
    return value


def load_snapshot(path):
    """Read and validate the full snapshot before any database writes occur."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CommandError(f"Cannot read taxonomy JSON: {error}") from error
    if not isinstance(data, dict):
        raise CommandError("Taxonomy JSON must be an object.")
    version = _text(data, "version", 100, "Taxonomy")
    categories, attributes, associations = {}, {}, set()
    for vertical in _list(data, "verticals", "Taxonomy"):
        if not isinstance(vertical, dict):
            raise CommandError("Each vertical must be an object.")
        for category in _list(vertical, "categories", "Vertical"):
            if not isinstance(category, dict):
                raise CommandError("Each category must be an object.")
            category_id = _text(category, "id", Category._meta.get_field("taxonomy_id").max_length, "Category")
            name = _text(category, "name", Category._meta.get_field("name").max_length, category_id)
            if category_id in categories:
                raise CommandError(f"Duplicate category ID: {category_id}")
            if "parent_id" not in category:
                raise CommandError(f"{category_id}: parent_id is required (null for roots).")
            parent_id = category["parent_id"]
            if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
                raise CommandError(f"{category_id}: parent_id must be text or null.")
            categories[category_id] = (name, parent_id)
            for attribute in _list(category, "attributes", category_id):
                if not isinstance(attribute, dict):
                    raise CommandError(f"{category_id}: each attribute must be an object.")
                attribute_id = _text(attribute, "id", Attribute._meta.get_field("taxonomy_id").max_length, "Attribute")
                handle = _text(attribute, "handle", Attribute._meta.get_field("handle").max_length, attribute_id)
                attribute_name = _text(attribute, "name", Attribute._meta.get_field("name").max_length, attribute_id)
                attribute_key = (attribute_id, handle)
                if attribute_key in attributes and attributes[attribute_key] != attribute_name:
                    raise CommandError(f"Conflicting names for attribute {attribute_id} ({handle}).")
                attributes[attribute_key] = attribute_name
                associations.add((category_id, attribute_key))
    if not categories:
        raise CommandError("Taxonomy snapshot contains no categories.")
    _validate_parents(categories)
    return TaxonomySnapshot(version, categories, attributes, associations)


def _validate_parents(categories):
    """Reject dangling parents and cycles without relying on source ordering."""
    complete = set()
    for category_id in categories:
        path = set()
        current = category_id
        while current is not None and current not in complete:
            if current not in categories:
                raise CommandError(f"Unknown parent category: {current}")
            if current in path:
                raise CommandError(f"Category hierarchy contains a cycle at {current}.")
            path.add(current)
            current = categories[current][1]
        complete.update(path)


def import_snapshot(snapshot, *, dry_run=False):
    """Plan or atomically merge a snapshot without removing existing taxonomy data.

    PostgreSQL table locks serialize taxonomy writes during the merge. Bulk writes
    are limited to validated metadata, parents, and new associations; existing
    association endpoints, value ownership, and product specifications never change.
    """
    with transaction.atomic():
        if not dry_run and connection.vendor == "postgresql":
            tables = ", ".join(connection.ops.quote_name(model._meta.db_table)
                               for model in (Category, Attribute, CategoryAttribute))
            with connection.cursor() as cursor:
                cursor.execute(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE")

        categories = {obj.taxonomy_id: obj for obj in Category.objects.all()}
        attributes = {(obj.taxonomy_id, obj.handle): obj for obj in Attribute.objects.all()}
        incoming_ids = {key[0] for key in snapshot.attributes}
        unmapped_ids = sorted(key[0] for key in attributes if not key[1] and key[0] in incoming_ids)
        if unmapped_ids:
            raise CommandError(
                "Existing attributes have no Shopify handle. Assign the correct handle "
                "in admin before importing; the command will not guess their context: "
                + ", ".join(unmapped_ids[:10])
            )
        existing_links = {
            (category, (attribute, handle))
            for category, attribute, handle in CategoryAttribute.objects.values_list(
                "category__taxonomy_id", "attribute__taxonomy_id", "attribute__handle"
            )
        }
        # Include retained categories when checking that changed parents cannot
        # create a cycle through an older record absent from this snapshot.
        ids = {obj.pk: key for key, obj in categories.items()}
        combined = {key: (obj.name, ids.get(obj.parent_id)) for key, obj in categories.items()}
        combined.update(snapshot.categories)
        _validate_parents(combined)

        new_categories, changed_categories = [], []
        for key, (name, parent) in snapshot.categories.items():
            if key not in categories:
                new_categories.append(Category(taxonomy_id=key, name=name))
            elif categories[key].name != name or ids.get(categories[key].parent_id) != parent:
                changed_categories.append(categories[key])
        new_attributes, changed_attributes = [], []
        for key, name in snapshot.attributes.items():
            if key not in attributes:
                new_attributes.append(Attribute(taxonomy_id=key[0], handle=key[1], name=name))
            elif attributes[key].name != name:
                attributes[key].name = name
                changed_attributes.append(attributes[key])
        new_links = snapshot.associations - existing_links
        counts = {
            "categories": {
                "created": len(new_categories), "updated": len(changed_categories),
                "unchanged": len(snapshot.categories) - len(new_categories) - len(changed_categories),
                "retained_outside_snapshot": len(categories.keys() - snapshot.categories.keys()),
            },
            "attributes": {
                "created": len(new_attributes), "updated": len(changed_attributes),
                "unchanged": len(snapshot.attributes) - len(new_attributes) - len(changed_attributes),
                "retained_outside_snapshot": len(attributes.keys() - snapshot.attributes.keys()),
            },
            "associations": {"created": len(new_links), "retained_outside_snapshot": len(existing_links - snapshot.associations)},
        }
        if dry_run:
            return counts

        Category.objects.bulk_create(new_categories, batch_size=1000)
        categories.update({obj.taxonomy_id: obj for obj in new_categories})
        # All category IDs now exist, so parents may appear after their children
        # in the source file without requiring an arbitrary placeholder parent.
        for obj in new_categories + changed_categories:
            obj.name, parent = snapshot.categories[obj.taxonomy_id]
            obj.parent_id = categories[parent].pk if parent is not None else None
        Category.objects.bulk_update(new_categories + changed_categories, ["name", "parent"], batch_size=1000)
        Attribute.objects.bulk_create(new_attributes, batch_size=1000)
        Attribute.objects.bulk_update(changed_attributes, ["name"], batch_size=1000)
        attributes.update({(obj.taxonomy_id, obj.handle): obj for obj in new_attributes})
        CategoryAttribute.objects.bulk_create([
            CategoryAttribute(category_id=categories[category].pk, attribute_id=attributes[attribute].pk)
            for category, attribute in sorted(new_links)
        ], batch_size=1000)
        return counts
