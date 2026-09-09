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

Before changing database design, API contracts, authentication, architecture, or cross-application workflows, review the relevant documentation in `../docs` and inspect frontend usage in `../frontend`.

Do not assume the code, frontend, and documentation are synchronized. Report inconsistencies before changing an established contract.

## Backend Conventions

This project uses Python, Django, Django REST Framework, PostgreSQL, SimpleJWT, and `django-environ`. Treat `requirements.txt` and the current source code as the source of truth for implementation details.

- `ecommerce_backend/` contains project settings and root routing.
- `authentication/` contains the custom user model and authentication API.
- `ecommerce/` contains the E-Commerce domain models and API.
- `core/common/` contains shared code.

Follow the existing Django app structure. Add shared abstractions only when they are genuinely used by multiple apps.

Use `settings.AUTH_USER_MODEL` for model relationships and `get_user_model()` in runtime code. Reuse `TimeStampedModel` for models requiring creation and update timestamps.

Use serializers for request validation and response transformation. Keep URL configuration limited to routing, and avoid placing substantial business logic directly in views.

Preserve existing API request formats, response structures, status codes, permissions, and authentication behavior unless the task explicitly changes the contract.

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

Add focused tests for new behavior and bug fixes. Report any checks that could not run because required environment variables, PostgreSQL, or another service was unavailable.

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
