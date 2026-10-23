---
name: drawio-erd
description: Generate or update editable Draw.io SVG ERDs from Django models, using per-app pages, database columns, Crow's Foot relationships, and name-only cross-app references.
---

# Draw.io ERDs

Use for ERD illustrations, independently or through [erd-documentation](../erd-documentation/SKILL.md). That skill owns guide prose, field tables, and the shared file layout. Do not generate guides or migrate existing diagrams unless the task includes them. Use Draw.io/vector tooling or XML/SVG editing, not raster image generation.

## Inspect before drawing

Read the relevant Django models, abstract/concrete inheritance, migrations, app labels, and settings such as `AUTH_USER_MODEL` and default primary-key configuration. Resolve the actual referenced model; do not assume every user field points to an app called authentication. Inspect an existing target SVG's visible image and embedded Draw.io document before modifying either. Preserve unrelated entities, pages, and user changes.

For maintained artifacts, use `docs/erd/diagrams/{app_label}.drawio.svg` relative to the backend root, with one internal page named `{app_label}`. Each app's guide embeds this separate image. A user-requested multipage exercise may retain its pages, but it is not the maintained Markdown layout. Do not depend on a temporary training file such as `../docs/temp.drawio.svg` being available to another agent.

## Entities and columns

- Title each full entity with its actual database table name: normally `{app_label}_{model_name_lowercase}`, such as `authentication_useraddress`. Honor `Meta.db_table` and other verified overrides.
- Use the established Draw.io table shape: centered bold title, 30-pixel title/row height, narrow left key-marker column, and left-aligned database column names. Use 30 pixels for key markers and size the table/field column to fit labels without clipping. Mark the primary key `PK` and foreign keys `FK`; make the primary-key name bold and underlined. Leave ordinary key cells empty.
- Include the implicit `id` primary key only when Django actually supplies it. Honor explicit/custom primary keys and include inherited concrete columns such as timestamps or `AbstractUser` fields. Abstract bases are not separate tables; concrete multi-table inheritance has real parent links/tables.
- Use database column names: normally `user_id` for a model attribute `user`, respecting `db_column` and `to_field`. Connect to the referenced unique field rather than assuming it is always `id`.
- For every field with declared choices, append all allowed stored values in parentheses to the column label, for example `status (pending, accepted, packed, shipment_arranged, shipped, cancelled, delivered, completed)`, `payment_method (cod, stripe)`, or `role (admin, staff)`. Read the relevant model's choices (including inline choices and referenced enums), preserve their spelling and order, and use stored values rather than display labels. Keep the same complete label in the visible SVG and embedded Draw.io data. Widen the table or wrap the label and increase its row height as needed so every value remains readable without clipping. Do not invent options for fields without declared choices.
- Include every concrete column of owned models unless a subset was explicitly requested; identify omissions. Do not render methods, properties, reverse accessors, or choice enums as columns/entities. Many-to-many attributes are not columns on the owning table: show their explicit through entity, or their real generated join table when that relationship is in scope.
- Render same-app referenced entities fully. For a cross-app target, use a plain rectangle containing only its actual table name, with no field rows, and connect the local FK row to it. Its full definition belongs to the owning app's illustration. Reuse a reference rectangle for multiple relationships to the same target. If the owning definition does not yet exist, report the gap rather than implying it has been documented.

## Relationships

Use Crow's Foot notation and attach connectors to the relevant field rows where those rows exist. Each endpoint describes how many instances at that end may relate to one instance at the opposite end. Use circles for zero, bars for one, and a branching foot for many; the foot itself is absent from one-to-one relationships.

Use orthogonal connectors with gently rounded bends, matching the soft transitions in the training template. Set `rounded=1;arcSize=20;` in the editable connector style and render actual curves at the SVG bends. Keep straight runs and Crow's Foot endpoint symbols crisp; rounded stroke joins alone do not create the intended transition. Preserve clear field attachments and leave enough straight space for the endpoint markers.

| Relationship           | Referenced-entity end       | Referencing-entity end        |
| ---------------------- | --------------------------- | ----------------------------- |
| Required ForeignKey    | Exactly one (`ERmandOne`)   | Zero or many (`ERzeroToMany`) |
| Nullable ForeignKey    | Zero or one (`ERzeroToOne`) | Zero or many (`ERzeroToMany`) |
| Required OneToOneField | Exactly one (`ERmandOne`)   | Zero or one (`ERzeroToOne`)   |
| Nullable OneToOneField | Zero or one (`ERzeroToOne`) | Zero or one (`ERzeroToOne`)   |

These defaults follow the schema: a required FK does not require every referenced entity to have a child. Reverse minimums need verified enforcement. A unique FK changes the reverse maximum to one; conditional uniqueness such as “at most one default recipient per user” does not turn the entire FK relationship into one-to-one. Keep deletion behavior and non-cardinality constraints in the guide.

For self-references, draw one entity and route a loop between its referenced and referencing field rows. For example, nullable `Category.parent` links `id` to `parent_id`, with optional-one at the `id` endpoint and optional-many at the `parent_id` endpoint. Route loops and other connectors clear of labels and unrelated entities.

Read [Draw.io format and validation](references/drawio-format.md) when constructing or editing the artifact. It defines the table structure, editable SVG requirements, and checks that distinguish an accurate image from merely valid XML.
