# Backend Agent Instructions

## Project Context

This repository contains the Django backend API for the larger E-Commerce project. The frontend, backend, and documentation are maintained as separate repositories under the same parent directory.

```text
E-Commerce/
├── frontend/
├── backend/     # This repository
└── docs/
```

### Repositories

- **Frontend:** [RHILIUS/E-Commerce-API-Frontend](https://github.com/RHILIUS/E-Commerce-API-Frontend)
- **Backend:** [RHILIUS/E-Commerce-API](https://github.com/RHILIUS/E-Commerce-API)
- **Documentation:** [RHILIUS/E-Commerce-Docs](https://github.com/RHILIUS/E-Commerce-Docs)

Related repositories should be available at:

- Frontend: `../frontend`
- Documentation: `../docs`

Backend-specific documentation is maintained in this repository's `docs/` folder:

Before changing database design, API contracts, authentication, architecture, or cross-application workflows, review the relevant local documentation in `docs/`, the shared project documentation in `../docs`, and frontend usage in `../frontend`. The local `docs/` folder is distinct from the sibling documentation repository.

Do not assume the code, frontend, and documentation are synchronized. Report inconsistencies before changing an established contract.

## Documentation Writing and Reading Flow

Write maintained documentation for a future implementer who has no prior project context. Explain the current system in simple, concrete language while preserving exact technical rules.

- Start with the feature's purpose and the person's experience before naming endpoints, models, serializers, or services. Define unfamiliar terms and abbreviations on first use; use short examples when they explain a real distinction.
- Provide a repository introduction, a documentation reading map, and an architecture introduction. These are responsibilities, not fixed filenames or assumptions that particular guides exist. Inspect the current structure and maintain or create the appropriate guide without relying on another document as a required style example.
- Organize feature guides around purpose and entry points, the normal successful flow, saved records and ownership, business rules and failure recovery, then exact requests/responses and code references. Adapt headings and combine sections when the feature is small.
- Explain transactions, locks, idempotency, uncertain outcomes, external-service handoffs, and persistence in plain language before detailing their mechanics. Simplicity must not hide authorization, concurrency, data-integrity, or recovery requirements.
- Setup and operations guides should explain services and connections before prerequisites, ordered commands, settings, and troubleshooting. Label where commands run, define placeholders, and distinguish local settings, container settings, and deployed settings.
- Keep frontend behavior in the frontend guides, exact backend contracts and code explanations in this repository, and shared architecture, ERDs, and business-process diagrams in the sibling documentation repository. Link to maintained sources instead of duplicating their full contents.
- Check statements against code and current consumers. Correct stale claims within scope and report discrepancies elsewhere. Distinguish implemented behavior from proposals and retain real limits such as test-only payments or unavailable settlement workflows.
- Write lasting explanations rather than change logs, implementation dates, PR narratives, or session reports. Do not add Verification sections, historical test results, completed checklists, or validation-run screenshots to maintained overview and feature guides. Put execution evidence in the PR or handoff; keep reusable testing instructions in development/contributor guides and operational health checks in runbooks.
- Continue all checks required by the change. Specialized diagram/ERD validation and PR reporting requirements remain in force; this writing guidance does not remove them.
- Keep indexes and relative links current when guides are added, moved, or removed. Check target files and anchors. Explain the sibling repository layout and provide repository URLs for readers browsing only one repository on GitHub.

## Backend Conventions

Use one backend `.env` for host Django, Docker Compose and VS Code Dev Containers.
Keep `.env.example` as the shared template. Compose supplies internal connection addressing (`DB_HOST=db`, `DB_PORT=5432`);
production Compose also fixes the internal Celery broker URL and selects
`ecommerce_backend.settings_production` for HTTPS proxy and deployment settings.
Never introduce a second environment file for Docker. `.env.deploy` is workstation-only
SSH/provisioning configuration for `scripts/deploy.sh`, not a Docker environment file;
the server still uses only the backend `.env`. Existing PostgreSQL volume credentials are not
changed by editing `.env` or recreating containers.

This project uses Python, Django, Django REST Framework, PostgreSQL, SimpleJWT, and `django-environ`. Treat `requirements.txt` and the current source code as the source of truth for implementation details.

- `ecommerce_backend/` contains project settings and root routing.
- `apps/authentication/` contains the custom user model and authentication API.
- `apps/ecommerce/` contains catalog, cart, order models and APIs.
- `apps/checkout/` contains checkout/payment-attempt models, the Stripe test webhook inbox, and online payment/recovery services.
- `apps/inventory/` contains stock-reservation models.
- `apps/wallet/` contains simulated USD wallets, the immutable ledger and internal posting services.
- `apps/account/` contains profile/photo editing, saved recipients, and password-change workflows.
- `apps/store/` contains store models, memberships, business addresses, and public store APIs.
- `core/common/` contains shared code.

Follow the existing Django app structure. Add shared abstractions only when they are genuinely used by multiple apps.

Use `settings.AUTH_USER_MODEL` for model relationships and `get_user_model()` in runtime code. Reuse `TimeStampedModel` for models requiring creation and update timestamps.

Use serializers for request validation and response transformation. Keep URL configuration limited to routing, and avoid placing substantial business logic directly in views.

Preserve existing API request formats, response structures, status codes, permissions, and authentication behavior unless the task explicitly changes the contract.

## Module Organization

When a Django app has multiple files for one responsibility, group them in a matching Python package: `tests/`, `serializers/`, `views/`, `services/`, or another clearly named responsibility directory. For example, account serializers belong in `apps/account/serializers/`, with modules such as `account.py`, `photo.py`, and `password.py`, rather than sibling `photo_serializers.py` and `password_serializers.py` files at the app root.

- Keep a single cohesive module as one file until splitting is useful; do not create packages or empty modules speculatively.
- When adding a second module for an existing responsibility, move the related modules into the matching package as part of that scoped change. Avoid unrelated repository-wide reorganizations.
- Add `__init__.py` to Python packages and use descriptive feature names within them. Test modules retain Django discovery names such as `test_profile.py`.
- Keep package initializers small. Re-export established shared imports when needed; use direct submodule imports for feature-specific consumers and avoid circular imports.
- Update imports, URL configuration, test patch targets, and documentation references affected by a move. Preserve API behavior and run relevant checks and tests to verify imports, routing, and test discovery.
- Follow Django's discovery requirements when grouping models or other framework-discovered modules; ensure required definitions remain imported and discoverable.

## OpenAPI Documentation

The OpenAPI schema is served at `/api/schema/` and Swagger UI at `/api/docs/` using drf-spectacular. Whenever an API endpoint is added or its request, response, status codes, parameters, permissions, or cookie behavior changes, update its schema annotation and the relevant backend documentation in the same change. For `APIView` endpoints, describe request and response serializers explicitly when automatic discovery cannot represent the actual contract. Never document refresh tokens as response-body fields.

Run `python manage.py spectacular --validate --fail-on-warn` after API changes. Review the generated schema for the changed operations, especially authentication and pagination, and add focused tests when the contract changes.

## Implementation Simplicity

Prefer the smallest clear implementation that satisfies the verified current requirement and contract.

- Inspect the local producer, consumer, types, and documented contract before adding fallbacks, alternate parsing paths, compatibility branches, or abstractions.
- When this backend and the frontend own a stable, explicit contract together, model that contract accurately and implement it directly. Do not support speculative request or response shapes unless a real compatibility requirement exists.
- Add defensive behavior when input crosses an untrusted or external boundary, the contract explicitly permits variation, backward compatibility requires it, or a demonstrated failure mode justifies it.
- Introduce services, helpers, and abstractions when they isolate substantial business logic or remove meaningful duplication; avoid single-use wrappers that obscure straightforward behavior.
- Add concise purpose docstrings to helper and service functions so reviewers can understand why they exist without tracing the implementation. Explain relevant input/output behavior, assumptions, side effects, or raised exceptions. Use short inline comments for non-obvious decisions; avoid narrating each line or merely repeating the function name. Update comments and docstrings when behavior changes.
- Any added complexity should correspond to a concrete case that can be explained and, where practical, tested. Simplicity must not weaken authentication, authorization, security, transaction safety, concurrency controls, data integrity, or required error handling.

## Authentication and Security

The API uses Bearer access tokens and an HttpOnly refresh-token cookie. Authentication is required by default.

- Use `AllowAny` only for intentionally public endpoints.
- Enforce authorization and resource ownership on the backend.
- Do not expose refresh tokens in response bodies.
- Preserve appropriate cookie security settings.
- Never log or expose passwords, tokens, cookies, credentials, or `.env` contents.
- Do not hard-code secrets or environment-specific configuration.

## Database Changes

Before modifying models, review the relevant documentation, relationships, serializers, views, tests, and frontend usage.

Create and review a new migration for intentional model changes. Do not rewrite existing migrations unless explicitly requested.

Review the affected Django admin configuration when models or relationships change. Update model registration, fields, list displays, filters, search, autocomplete fields, or inlines when the change affects admin usability or manual testing; do not modify the admin when the model change has no meaningful admin impact.

When adding or changing model fields backed by cloud storage, review the complete file lifecycle, including replacement and deletion. Use signals or another established lifecycle mechanism when cleanup is required to prevent orphaned remote files. Schedule destructive storage operations after a successful database commit, and do not delete files that may still be referenced by another record.

Protect data integrity for carts, orders, inventory, and prices. Do not trust client-provided ownership, totals, prices, or stock values when they can be determined by the backend.

## Testing

Run commands from the backend repository root:

```bash
source venv/bin/activate
python manage.py check
python manage.py test
```

For model changes, also run:

```bash
python manage.py makemigrations --check
python manage.py migrate --plan
```

Add focused tests for new behavior and bug fixes. Report any checks that could not run because required environment variables, PostgreSQL, or another service was unavailable. Don't add test that is not necessary.

## Pull Request Documentation

When asked to prepare a PR comment or PR description, use the following format and base every statement on the actual branch diff, tests, and documented contract. Omit sections that do not apply.

```markdown
## Summary

<Briefly explain the user-facing or system-level outcome and why the change is needed.>

## Changes

- <Group related implementation changes by behavior rather than listing commits.>
- <Call out database migrations, API contract changes, and security-sensitive behavior explicitly.>

## API Contract

- **Endpoint:** `<method and path>`
- **Success:** `<status and response shape>`
- **Errors:** `<relevant status codes and response shapes>`
- **Cookies/authentication:** `<cookie or token behavior, if applicable>`

## Testing

- [x] `<check that passed>`
- [ ] `<check that could not run and the reason>`

## Follow-up / Out of Scope

- <Known follow-up work, documented inconsistency, or intentionally excluded behavior.>
```

Keep the documentation reviewer-focused: describe observable behavior, important implementation and security decisions, verification performed, and unresolved risks. Do not claim that a check passed unless it was run successfully. Never include secrets, credentials, plaintext OTPs, complete tokens, cookies, or `.env` values.

## Working Practices

- Inspect related code before making changes.
- Preserve existing user changes in the working tree.
- Avoid unrelated refactoring or formatting.
- Do not commit `.env`, virtual environments, caches, bytecode, or generated artifacts.
- Remove debugging output and unused code.
- Update or report required documentation changes when behavior, data models, API contracts, or workflows change.

## Required ERD Documentation

Whenever creating or updating a Markdown ERD document in the **sibling documentation repository** at `../docs/diagrams/erd/`, include explanations alongside the diagram. This is distinct from the frontend's local `docs/` folder. Use lowercase kebab-case filenames and update an existing document when it already covers the model. Use [Password-change model](../docs/diagrams/erd/password-change.md) as a structural example, not as a source of requirements for unrelated entities.

Before writing, review the central diagramming specification, diagram index, relevant backend models and migrations, and the services and documented contracts that govern the fields. Verify current behavior against implementation; report inconsistencies rather than inventing semantics.

Each ERD Markdown document must:

1. State its scope and implementation status, clearly distinguishing implemented models from proposed designs. Identify omitted entities or fields when the diagram represents only a subset.
2. Include a fenced Mermaid `erDiagram` showing the relevant entities, fields, keys, and relationship cardinalities.
3. Explain each entity's purpose, ownership, and role in the feature, with its actual model name when implemented. Explain relationships, optionality, foreign-key ownership, and deletion behavior.
4. Provide a field table for every entity with columns **Field / column**, **Meaning**, and **Constraints and behavior**. Cover every field shown in the diagram. Explain its business purpose and relevant type, primary/foreign/unique keys, nullability, defaults, validation, and lifecycle behavior. Distinguish model field names from database column names when they differ. Do not merely repeat the field name as its explanation.
5. Add persistence and lifecycle notes where relevant, covering creation, updates, expiry, resets, counters, sensitive-data handling, transaction boundaries, concurrency, and failure or recovery behavior. Document only verified behavior, and identify unresolved decisions for proposed models.
6. Include implementation references and links to related business-process documents. Add or update the diagram index entry and ensure relative links resolve.

Keep the diagram, field tables, and lifecycle notes synchronized when the schema or behavior changes. Check that every diagram entity and field has an explanation. For new or changed Mermaid fences, record the Mermaid version used, validate parsing, and inspect the rendered diagram for readable labels and relationships. Check the intended Markdown host when available; report any parsing, rendering, or host-verification limitations without claiming checks that were not performed. Include documentation changes and actual validation results in the handoff.

## Disposable demo maintenance

`scripts/reset-demo.sh` uses production Compose and the explicit maintenance image
for disposable portfolio resets. `--dry-run` checks prerequisites without
stopping services or deleting data. Keep
seed inputs in the maintenance target, not the normal API target. Never run a
reset or clear broker tasks as part of routine verification; use mocked wrapper
tests unless the user explicitly authorizes execution against disposable data.
