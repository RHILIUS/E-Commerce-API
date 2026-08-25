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

## Working Practices

- Inspect related code before making changes.
- Preserve existing user changes in the working tree.
- Avoid unrelated refactoring or formatting.
- Do not commit `.env`, virtual environments, caches, bytecode, or generated artifacts.
- Remove debugging output and unused code.
- Update or report required documentation changes when behavior, data models, API contracts, or workflows change.
