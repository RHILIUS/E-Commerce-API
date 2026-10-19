---
name: backend-api
description: Implement or change backend API endpoints, serializers, permissions, authentication contracts, and OpenAPI annotations.
---

# Backend API

Apply this workflow to API implementation and contract changes. Repository paths in backticks are relative to the backend root. Read the root agent instructions for shared security, implementation, and testing rules.

## Inspect the contract and its consumers

Inspect routing, views, serializers, services, types, relevant backend documentation, and the frontend callers before changing a contract. Verify request fields, responses, status codes, permissions, ownership, and cookie behavior against implementation. Report inconsistencies; do not add speculative request/response compatibility.

Preserve established behavior unless the task explicitly changes it. Keep URL configuration focused on routing, serializers responsible for validation/response transformation, and substantial business mechanics in focused services. Public endpoints still need an explicit permission decision; the shared authentication and secret-handling rules apply throughout.

## OpenAPI and maintained documentation

The OpenAPI schema is served at `/api/schema/` and Swagger UI at `/api/docs/` using drf-spectacular. Whenever an API endpoint is added or its request, response, status codes, parameters, permissions, or cookie behavior changes, update its schema annotation and the relevant backend documentation in the same change. For `APIView` endpoints, describe request and response serializers explicitly when automatic discovery cannot represent the actual contract. Never document refresh tokens as response-body fields.

Run `python manage.py spectacular --validate --fail-on-warn` after API changes. Review the generated schema for the changed operations, especially authentication and pagination, and add focused tests when the contract changes.

## Validation and handoff

Run the root instructions' relevant checks and focused tests, plus the schema checks above. Review affected generated operations and frontend handoffs. Report changed contracts, actual validation results, and any limitations; an annotation change alone does not establish implementation behavior.
