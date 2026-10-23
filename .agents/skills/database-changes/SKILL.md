---
name: database-changes
description: Change backend models, migrations, admin behavior, and stored-file lifecycles, or maintain backend ERD documentation.
---

# Database changes

Apply this workflow when changing persisted models, their relationships or lifecycle, Django admin behavior, or creating/updating backend ERDs. Repository paths in backticks are relative to the backend root. This workflow does not authorize running migrations or deleting data against an unrelated environment.

## Models, migrations, admin, and files

Use `settings.AUTH_USER_MODEL` for model relationships and `get_user_model()` in runtime code. Reuse `TimeStampedModel` for models requiring creation and update timestamps.

Before modifying models, review the relevant documentation, relationships, serializers, views, tests, and frontend usage.

Create and review a new migration for intentional model changes. Do not rewrite existing migrations unless explicitly requested.

When adding or changing model fields backed by cloud storage, review the complete file lifecycle, including replacement and deletion. Use signals or another established lifecycle mechanism when cleanup is required to prevent orphaned remote files. Schedule destructive storage operations after a successful database commit, and do not delete files that may still be referenced by another record.

Protect data integrity for carts, orders, inventory, and prices. Do not trust client-provided ownership, totals, prices, or stock values when they can be determined by the backend.

## Django admin

Review affected admin registrations and screens when models, derived values, or
business rules change. Consider the list, add/change forms, inlines, filters,
search, ordering, and autocomplete where relevant. A removed field can require a
calculated read-only replacement even when no explicit admin field list names it.
Make changes when they serve the staff workflow; do not expose every API value
automatically or reorganize unrelated admin screens.

Reuse the same business services and qualification rules as other consumers.
Annotate or prefetch derived list values to avoid a query per row. Keep calculated
values read-only, and do not allow admin forms or actions to bypass authorization,
inventory coordination, payment services, or immutable purchase/ledger records.

For changed admin behavior, verify the actual list and form responses, relevant
permissions/write restrictions, and query costs where calculated columns are added.
Use focused tests for the changed behavior and report what was checked; mention
explicitly when the review finds no admin change necessary.

## Model checks

Run the root instructions' system checks and relevant tests. For model changes, also run the following from the backend root in its configured environment:

```bash
python manage.py makemigrations --check
python manage.py migrate --plan
```

Review the generated migration and migration plan; neither demonstrates that a migration has been applied to a target environment. Preserve required transaction boundaries and concurrency controls, and report checks blocked by unavailable services or configuration.

## ERD documentation

When creating or updating an ERD, apply [erd-documentation](../erd-documentation/SKILL.md) for the backend-owned guides and [drawio-erd](../drawio-erd/SKILL.md) when its editable illustration changes. These skills own model/field explanations, lifecycle notes, index maintenance, and SVG validation. Do not create or maintain Mermaid ERDs. Load them for ERD work rather than every database task.
