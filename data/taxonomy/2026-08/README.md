# Shopify Standard Product Taxonomy — 2026-08

English reference data for the product taxonomy tables. This directory pins the
`2026-08` release so development and deployment can use the same dataset.

## Source

- **Publisher:** Shopify
- **Release:** [2026-08 (`v2026-08`)](https://github.com/Shopify/product-taxonomy/releases/tag/v2026-08)
- **Language:** English (`en`)
- **Local file:** [categories.en.json](categories.en.json)
- **Download:** [categories.en.json.gz](https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/categories.en.json.gz)
- **Explorer:** [Browse the 2026-08 taxonomy](https://shopify.github.io/product-taxonomy/releases/2026-08/)
- **License:** [Shopify's MIT license](https://github.com/Shopify/product-taxonomy/blob/v2026-08/LICENSE). Preserve the upstream copyright and permission notice when redistributing the dataset.

## Local file contents

The downloaded JSON identifies its version as `2026-08` and contains 26 verticals
with 14,606 categories. Its size is 80,329,753 bytes (about 76.6 MiB).

Categories are nested under `verticals[].categories[]`. Each category includes its
Shopify ID, name, full name, level, parent ID, children, ancestors, applicable
attributes, and return reasons. Attribute entries include metadata such as ID,
name, handle, and description; they do not contain the predefined attribute values
needed to populate `AttributeValue`.

The local file's SHA-256 checksum is:

```text
fa034a4f33535b19708505e42a4b4debec86207467b0723d7476ba511931f85c
```

This checksum records the current local snapshot; it is not an independently
verified publisher checksum.

## Downloading the pinned file

Run from the backend repository root when restoring this snapshot:

```bash
curl --fail --location \
  https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/categories.en.json.gz \
  --output /tmp/shopify-categories-2026-08.en.json.gz
gzip --decompress --stdout /tmp/shopify-categories-2026-08.en.json.gz \
  > data/taxonomy/2026-08/categories.en.json
sha256sum data/taxonomy/2026-08/categories.en.json
```

Use the versioned URL rather than `latest` to avoid silently switching releases.
Keep future releases in their own version directories instead of editing this
snapshot by hand.

## Intended backend use

The `import_taxonomy` management command reads this local data after schema migrations
have been applied. Downloading or storing the file does not populate the database.
See [the backend taxonomy notes](../../../docs/taxonomy.md) for model validation
rules and import limitations.

- Map category IDs to `Category.taxonomy_id` and resolve parent relationships.
- Identify each `Attribute` by Shopify ID and handle. Contexts such as frame color
  and lens color can share an ID while remaining separate local attributes.
- Map each category's attribute assignments to `CategoryAttribute`.
- Obtain the predefined attribute values from the same release before importing
  `AttributeValue`; this file alone does not supply them.
- Assign categories and `ProductAttributeValue` records to actual products
  separately. The reference taxonomy does not describe our products' specifications.

Preview and import from the backend root:

```bash
python manage.py import_taxonomy --directory data/taxonomy/2026-08 --dry-run
python manage.py import_taxonomy --directory data/taxonomy/2026-08
```

This snapshot contains 8,500 distinct attribute contexts and 93,007 category–attribute
associations. The importer preserves existing local primary keys and retains records
and associations absent from the snapshot. See the backend notes for handling existing
attributes with no handle and for the command's transaction behavior.

Return reasons and disclosures are outside the current backend model scope.
