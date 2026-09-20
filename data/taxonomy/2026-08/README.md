# Shopify Standard Product Taxonomy — 2026-08

English reference data for the product taxonomy tables. This directory pins the
`2026-08` release so development and deployment can use the same dataset.

## Source

- **Publisher:** Shopify
- **Release:** [2026-08 (`v2026-08`)](https://github.com/Shopify/product-taxonomy/releases/tag/v2026-08)
- **Language:** English (`en`)
- **Local files:** [categories.en.json](categories.en.json) and [attributes.en.json](attributes.en.json)
- **Downloads:** [categories.en.json.gz](https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/categories.en.json.gz) and [attributes.en.json.gz](https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/attributes.en.json.gz)
- **Explorer:** [Browse the 2026-08 taxonomy](https://shopify.github.io/product-taxonomy/releases/2026-08/)
- **License:** [Shopify's MIT license](https://github.com/Shopify/product-taxonomy/blob/v2026-08/LICENSE). Preserve the upstream copyright and permission notice when redistributing the dataset.

## Local file contents

Both JSON files identify their version as `2026-08`.

### Categories

`categories.en.json` contains 26 verticals
with 14,606 categories. Its size is 80,329,753 bytes (about 76.6 MiB).

Categories are nested under `verticals[].categories[]`. Each category includes its
Shopify ID, name, full name, level, parent ID, children, ancestors, applicable
attributes, and return reasons. Attribute entries include metadata such as ID,
name, handle, and description; they do not contain the predefined attribute values
needed to populate `AttributeValue`.

The categories file's SHA-256 checksum is:

```text
fa034a4f33535b19708505e42a4b4debec86207467b0723d7476ba511931f85c
```

### Attributes and predefined values

`attributes.en.json` contains 8,240 top-level attributes under `attributes[]`,
74,820 predefined value entries, and 316 extended attribute definitions. Its size
is 14,809,451 bytes (about 14.1 MiB).

Each attribute includes `id`, `name`, `handle`, `description`,
`extended_attributes`, and `values`. Each value supplies its Shopify ID, display
name, and handle. Extended definitions supply contextual names and handles, such
as `backing-color`, under their base attribute.

The attributes file's SHA-256 checksum is:

```text
5590707558ad2f5afc24f01e1512d6cffe773d727a52c0c7b03c12f0a3e69f22
```

The counts describe different structures: the categories file lists attribute
contexts assigned to categories, while the attributes file lists base definitions,
their extended contexts, and predefined values. Their attribute counts need not match.

These checksums record the current local snapshots; they are not independently
verified publisher checksums.

## Downloading the pinned files

Run from the backend repository root when restoring this snapshot:

```bash
curl --fail --location \
  https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/categories.en.json.gz \
  --output /tmp/shopify-categories-2026-08.en.json.gz
gzip --decompress --stdout /tmp/shopify-categories-2026-08.en.json.gz \
  > data/taxonomy/2026-08/categories.en.json
sha256sum data/taxonomy/2026-08/categories.en.json

curl --fail --location \
  https://github.com/Shopify/product-taxonomy/releases/download/v2026-08/attributes.en.json.gz \
  --output /tmp/shopify-attributes-2026-08.en.json.gz
gzip --decompress --stdout /tmp/shopify-attributes-2026-08.en.json.gz \
  > data/taxonomy/2026-08/attributes.en.json
sha256sum data/taxonomy/2026-08/attributes.en.json
```

Use the versioned URL rather than `latest` to avoid silently switching releases.
Keep future releases in their own version directories instead of editing this
snapshot by hand.

## Intended backend use

The `import_taxonomy` management command requires both JSON files after schema
migrations have been applied. Their versions and attribute definitions must agree.
Downloading or storing these files does not populate the database.
See [the backend taxonomy notes](../../../docs/taxonomy.md) for model validation
rules and import limitations.

- Map category IDs to `Category.taxonomy_id` and resolve parent relationships.
- Identify each `Attribute` by Shopify ID and handle. Contexts such as frame color
  and lens color can share an ID while remaining separate local attributes.
- Map each category's attribute assignments to `CategoryAttribute`.
- Read predefined values from `attributes.en.json` into `AttributeValue`, resolving
  base and extended contexts explicitly. Extended contexts use their base attribute's
  choices. Only contexts assigned in the categories file are imported.
  Value IDs are unique per local attribute, so a shared choice can belong to
  multiple contexts without merging their product specifications.
- Assign categories and `ProductAttributeValue` records to actual products
  separately. The reference taxonomy does not describe our products' specifications.

Preview and import categories, attributes, values, and category associations from the backend root:

```bash
python manage.py import_taxonomy --directory data/taxonomy/2026-08 --dry-run
python manage.py import_taxonomy --directory data/taxonomy/2026-08
```

This snapshot contains 8,500 distinct attribute contexts and 93,007 category–attribute
associations. The corresponding choices produce 81,518 contextual `AttributeValue`
rows, since a base choice can be used in several contexts. The importer preserves existing local primary keys and retains records
and associations absent from the snapshot. See the backend notes for handling existing
attributes with no handle and for the command's transaction behavior.

Return reasons and disclosures are outside the current backend model scope.
