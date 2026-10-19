---
name: database-changes
description: Change backend models, migrations, admin behavior, and stored-file lifecycles, or maintain backend ERD documentation.
---

# Database changes

Apply this workflow when changing persisted models, their relationships or lifecycle, or creating/updating backend ERDs. Repository paths in backticks are relative to the backend root. This workflow does not authorize running migrations or deleting data against an unrelated environment.

## Models, migrations, admin, and files

Use `settings.AUTH_USER_MODEL` for model relationships and `get_user_model()` in runtime code. Reuse `TimeStampedModel` for models requiring creation and update timestamps.

Before modifying models, review the relevant documentation, relationships, serializers, views, tests, and frontend usage.

Create and review a new migration for intentional model changes. Do not rewrite existing migrations unless explicitly requested.

Review the affected Django admin configuration when models or relationships change. Update model registration, fields, list displays, filters, search, autocomplete fields, or inlines when the change affects admin usability or manual testing; do not modify the admin when the model change has no meaningful admin impact.

When adding or changing model fields backed by cloud storage, review the complete file lifecycle, including replacement and deletion. Use signals or another established lifecycle mechanism when cleanup is required to prevent orphaned remote files. Schedule destructive storage operations after a successful database commit, and do not delete files that may still be referenced by another record.

Protect data integrity for carts, orders, inventory, and prices. Do not trust client-provided ownership, totals, prices, or stock values when they can be determined by the backend.

## Model checks

Run the root instructions' system checks and relevant tests. For model changes, also run the following from the backend root in its configured environment:

```bash
python manage.py makemigrations --check
python manage.py migrate --plan
```

Review the generated migration and migration plan; neither demonstrates that a migration has been applied to a target environment. Preserve required transaction boundaries and concurrency controls, and report checks blocked by unavailable services or configuration.

## ERD documentation

When creating or updating an ERD, apply [erd-documentation](../erd-documentation/SKILL.md) for the backend-owned guides and [drawio-erd](../drawio-erd/SKILL.md) when its editable illustration changes. These skills own model/field explanations, lifecycle notes, index maintenance, and SVG validation. Do not create or maintain Mermaid ERDs. Load them for ERD work rather than every database task.
