# Backend agent instructions

## Project context

This backend is part of the E-Commerce project. Three separate Git repositories share a parent directory:

| Repository                                                          | Checkout        | Responsibility                                        |
| ------------------------------------------------------------------- | --------------- | ----------------------------------------------------- |
| [Backend](https://github.com/RHILIUS/E-Commerce-API)                | This repository | API, business rules, persistence, and background jobs |
| [Frontend](https://github.com/RHILIUS/E-Commerce-API-Frontend)      | `../frontend`   | Customer website and API consumers                    |
| [Project documentation](https://github.com/RHILIUS/E-Commerce-Docs) | `../docs`       | Shared architecture and business processes            |

The local `docs/` folder describes the backend; the sibling `../docs/` repository describes the complete project. Before changes to database design, API contracts, authentication, architecture, or cross-application workflows, review relevant local/shared documentation and frontend usage. Verify behavior against code and consumers; report inconsistencies before changing an established contract.

## Skills for task-specific workflows

Read and apply the linked skills when their conditions match. Combine them when a task spans responsibilities; they provide procedures, not permission to expand the task.

| Task                                                                                  | Required skill                                                                             |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Implement/change endpoints, serializers, permissions, or API/authentication contracts | [backend-api](.agents/skills/backend-api/SKILL.md)                                         |
| Change models, migrations, admin impact, or stored-file lifecycle                     | [database-changes](.agents/skills/database-changes/SKILL.md)                               |
| Create/update per-app ERD guides or document changed data models                      | [erd-documentation](.agents/skills/erd-documentation/SKILL.md); also documentation-writing |
| Create/update editable Draw.io ERD illustrations                                      | [drawio-erd](.agents/skills/drawio-erd/SKILL.md)                                           |
| Maintain environment, container, deployment, or disposable-demo tooling               | [backend-operations](.agents/skills/backend-operations/SKILL.md)                           |
| Write/revise maintained documentation, including alongside implementation             | [documentation-writing](.agents/skills/documentation-writing/SKILL.md)                     |
| Write a backend PR comment or description                                             | [pull-request-writing](.agents/skills/pull-request-writing/SKILL.md)                       |

Model changes still require reviewed migrations and relevant admin/lifecycle review. API contract changes still require matching OpenAPI annotations and maintained documentation. ERD guides and editable Draw.io SVGs belong in `docs/erd/guides/` and `docs/erd/diagrams/`, following erd-documentation and drawio-erd. Do not create or maintain Mermaid ERDs. Shared business-process diagrams remain in the sibling documentation repository.

## Backend conventions

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

Use serializers for request validation and response transformation. Keep URL configuration limited to routing, and avoid placing substantial business logic directly in views.

Preserve existing API request formats, response structures, status codes, permissions, and authentication behavior unless the task explicitly changes the contract.

## Authentication and security

The API uses Bearer access tokens and an HttpOnly refresh-token cookie. Authentication is required by default.

- Use `AllowAny` only for intentionally public endpoints.
- Enforce authorization and resource ownership on the backend.
- Do not expose refresh tokens in response bodies.
- Preserve appropriate cookie security settings.
- Never log or expose passwords, tokens, cookies, credentials, or `.env` contents.
- Do not hard-code secrets or environment-specific configuration.

## Essential data and operational safeguards

- Protect carts, orders, inventory, prices, and financial records. Derive ownership, totals, prices, and stock on the backend when available; do not trust client-supplied replacements.
- Preserve transaction safety and established lock/concurrency behavior. Model validation alone does not replace transaction coordination.
- Use one backend `.env` and `.env.example` template across host Django, Compose, and Dev Containers. Detailed container addressing and the distinct workstation `.env.deploy` role belong to the operations skill.
- Never run a reset or clear broker tasks during routine verification. Use mocked wrapper tests unless the user explicitly authorizes execution against disposable data.

## Module organization

When a Django app has multiple files for one responsibility, group them in a matching Python package: `tests/`, `serializers/`, `views/`, `services/`, or another clearly named responsibility directory. For example, account serializers belong in `apps/account/serializers/`, with modules such as `account.py`, `photo.py`, and `password.py`, rather than sibling `photo_serializers.py` and `password_serializers.py` files at the app root.

- Keep a single cohesive module as one file until splitting is useful; do not create packages or empty modules speculatively.
- When adding a second module for an existing responsibility, move the related modules into the matching package as part of that scoped change. Avoid unrelated repository-wide reorganizations.
- Add `__init__.py` to Python packages and use descriptive feature names within them. Test modules retain Django discovery names such as `test_profile.py`.
- Keep package initializers small. Re-export established shared imports when needed; use direct submodule imports for feature-specific consumers and avoid circular imports.
- Update imports, URL configuration, test patch targets, and documentation references affected by a move. Preserve API behavior and run relevant checks and tests to verify imports, routing, and test discovery.
- Follow Django's discovery requirements when grouping models or other framework-discovered modules; ensure required definitions remain imported and discoverable.

## Implementation simplicity

Prefer the smallest clear implementation that satisfies the verified current requirement and contract.

- Inspect the local producer, consumer, types, and documented contract before adding fallbacks, alternate parsing paths, compatibility branches, or abstractions.
- When this backend and the frontend own a stable, explicit contract together, model that contract accurately and implement it directly. Do not support speculative request or response shapes unless a real compatibility requirement exists.
- Add defensive behavior when input crosses an untrusted or external boundary, the contract explicitly permits variation, backward compatibility requires it, or a demonstrated failure mode justifies it.
- Introduce services, helpers, and abstractions when they isolate substantial business logic or remove meaningful duplication; avoid single-use wrappers that obscure straightforward behavior.
- Add concise purpose docstrings to helper and service functions so reviewers can understand why they exist without tracing the implementation. Explain relevant input/output behavior, assumptions, side effects, or raised exceptions. Use short inline comments for non-obvious decisions; avoid narrating each line or merely repeating the function name. Update comments and docstrings when behavior changes.
- Any added complexity should correspond to a concrete case that can be explained and, where practical, tested. Simplicity must not weaken authentication, authorization, security, transaction safety, concurrency controls, data integrity, or required error handling.

## Checks and handoff

Run commands from the backend repository root in the configured environment. Use focused test labels during implementation; before handing off executable changes, run the full Django suite once after the focused checks pass:

```bash
source venv/bin/activate
python manage.py check
python manage.py test
```

Before adding a test, inspect the existing coverage. Extend an existing test when it already exercises the same behavior. Add a separate test for a distinct requirement, failure mode, or regression; do not add tests merely because a file changed, repeat equivalent assertions at every layer, or test unchanged framework behavior without a project-specific requirement. Preserve distinct authorization, transaction, concurrency, and data-integrity checks even when they use similar inputs.

Use `TestCase`/`APITestCase` for ordinary database behavior, and `setUpTestData` for fixtures that can safely be shared through Django's per-test isolation. Keep mocks, clients, and other per-test state in `setUp`. Use `SimpleTestCase` for database-free checks. Reserve `TransactionTestCase`/`APITransactionTestCase` for behavior that needs real commits, rollbacks, or independent connections. Explicit callback execution can support functional assertions, but must not replace tests of actual commit timing, post-commit failures, or concurrent writes.

Run the script test suite when deployment/bootstrap/reset wrappers change. Repeat successful checks only when subsequent changes, failures, or unresolved concerns justify another run. See [local test feedback](docs/development/pull-request-checks.md#local-test-feedback) for focused commands, test-database reuse, and timing tools.

Report checks blocked by missing environment variables, PostgreSQL, or other services. Apply the API skill's schema checks for API changes and the database skill's migration checks for model changes. Use the current configured environment; omit host virtual-environment activation when running inside an existing container.

For documentation or instruction-only work, validate affected prose, links/anchors, formatting, and skill metadata/references as applicable. Application tests do not validate instructions. Report only checks actually performed and disclose limitations; follow specialized diagram validation when a diagram changes.

## Working practices

- Inspect related code before making changes.
- Preserve existing user changes in the working tree.
- Avoid unrelated refactoring or formatting.
- Do not commit `.env`, virtual environments, caches, bytecode, or generated artifacts.
- Remove debugging output and unused code.
- Update or report required documentation changes when behavior, data models, API contracts, or workflows change.

## Maintaining these instructions

Update the root instructions or the owning skill when code, configuration, or agreed workflow changes make its guidance inaccurate. Keep detailed procedures in one maintained home, retain essential safeguards here, and update references when moving instructions. Do not change instructions for unrelated work.
