# Product taxonomy

Categories, attributes, allowed category–attribute associations, predefined values,
and product specifications are managed through Django admin. The `import_taxonomy`
command imports categories, contextual attributes, predefined values, and category
associations from a local Shopify snapshot. The product specification API is not
implemented yet.

## Contextual attributes

An attribute is identified by `(taxonomy_id, handle)`. Shopify can reuse a taxonomy
ID for contextual attributes such as `frame-color` and `lens-color`; these are
separate local rows with separate category associations and product specifications.
`AttributeValue` IDs are unique within their attribute, allowing the same Shopify
value ID (such as Black) to be represented for both contexts. Existing local primary
keys and foreign keys are preserved by the schema migration.

Historical attributes receive an empty handle. If an import includes their Shopify
ID, the command stops before writing and asks you to assign the correct handle in
admin. It does not guess a context or move existing specifications between contexts.

## Importing the 2026-08 snapshot

From the backend repository root:

```bash
source venv/bin/activate
python manage.py migrate
python manage.py import_taxonomy --directory data/taxonomy/2026-08 --dry-run
python manage.py import_taxonomy --directory data/taxonomy/2026-08
```

The directory must contain `categories.en.json` and `attributes.en.json` with matching
`version` fields. Both files are required, including for dry runs. See the [snapshot README](../data/taxonomy/2026-08/README.md)
for its pinned download and checksum. The command reads local files only.

The full file contains 14,606 categories, 8,500 distinct contextual attributes, and
93,007 distinct category–attribute associations. The attributes distribution supplies
81,518 value rows for these contexts; only contexts referenced by categories are
imported. Extended contexts use the choices listed under their base attribute.
Each value is matched by local attribute context and Shopify value ID. Counts printed by the command show
planned creations, metadata updates, unchanged rows, and retained records outside
the snapshot. Importing the same file again creates no duplicates.

The command validates the JSON shape, required fields, database field lengths,
duplicate IDs, conflicting names for the same attribute context, missing parents,
and hierarchy cycles before writing. Parent ordering in the file does not matter.
The dry run compares the snapshot with the migrated database without writing rows
or consuming sequence IDs; it requires a working database connection.

Imports update names and category parents, preserve existing `is_active` settings,
and add missing associations. They do not delete absent records or associations,
reassign products, change value ownership, or modify product specifications. Existing
value names are updated while their primary keys remain unchanged; values absent
from the snapshot are retained. Retained associations may no longer appear in the
new release; review those separately.

All writes run in one transaction. PostgreSQL table locks serialize writes to the
category, attribute, association, and attribute value tables during the import; concurrent taxonomy
edits can wait until it finishes. Bulk inserts here are limited to validated new
values, associations, and metadata: the importer never changes existing association endpoints
or value ownership. This is a controlled exception to the general bulk-write warning
below. Failed imports roll back database changes.

`categories.en.json` supplies category assignments, while `attributes.en.json`
supplies base definitions, extended contexts, and predefined values. Missing contexts,
conflicting names between files, duplicate value IDs within an attribute, invalid
field lengths, and mismatched versions stop the import before any writes.
The command populates `Category`, `Attribute`, `CategoryAttribute`, and `AttributeValue`. Disclosures and return reasons are outside the current model scope.

## Specification validation

Each product can have one value per attribute. A specification's value must belong
to its attribute, and the category must allow that attribute. Ordinary specification
`save()` and `objects.create()` calls run full model validation. Missing related IDs
produce validation errors rather than errors while accessing related objects.

Changing a product's category must preserve compatibility with its specifications.
Used category–attribute associations cannot be changed or deleted, and used values
cannot be reassigned to another attribute. Remove affected specifications first,
or choose a category that supports them.

These relationship checks are application validation, not cross-table database
constraints. `QuerySet.update()`, `bulk_create()`, `bulk_update()`, and raw SQL bypass
model validation; do not use them for these relationships. Concurrent taxonomy edits
and specification writes are not serialized by these model checks. A future API must coordinate related writes in transactions with appropriate
locking. Future DRF serializers must validate these relationships and translate
validation failures into API errors; model validation alone does not provide that
API behavior.
