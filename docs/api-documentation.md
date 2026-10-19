# Using the API documentation

An API contract describes what a caller sends and what the backend returns.
Swagger UI is an interactive page for exploring those contracts. OpenAPI is the
machine-readable description behind that page; it is generated from the backend's
schema annotations.

Feature guides explain why an operation exists, its business rules, and recovery
behavior. Use Swagger for the exact fields while reading the relevant guide from
the [documentation index](README.md).

## Open Swagger and the schema

Start the backend, then open:

| Address                    | Purpose                  |
| -------------------------- | ------------------------ |
| `/api/docs/`               | Interactive Swagger UI   |
| `/api/schema/`             | Generated OpenAPI schema |
| `/api/schema/?format=json` | Schema in JSON form      |

For local development, prefix these paths with `http://localhost:8000`.
Swagger assets are packaged with the dependencies, so loading the UI does not
require a separate asset CDN. On deployment, open these paths on the backend
host; the frontend Pages proxy is not an admin or Swagger host.

## Try an authenticated request

1. Open the login operation and submit an existing account's email and password.
2. Copy the returned access token into Swagger's **Authorize** Bearer-token control.
3. Open a protected operation and supply its documented inputs.
4. Inspect the response status and body. A valid token identifies the caller;
   ownership and business rules can still reject the requested action.

Swagger **Try it out** sends real requests to the selected backend. Use the
intended development environment for experiments: creating recipients, placing
orders, and cancelling purchases can change data.

Login returns an access token and public user data. The refresh token is set in
an HttpOnly browser cookie and never appears in the response body. Refresh reads
that cookie; the Authorize control's access token is not a replacement for it.
OTP verification similarly sets a separate registration cookie for account
creation. See [accounts and sign-in](authentication/README.md) for exact shapes,
cookie paths, lifetimes, and failures.

When trying cookie operations across origins, browser cookie policies and the
backend's CORS configuration both apply. CORS means the browser's permission to
read a response from another origin; it does not remove cookie restrictions.

## Read the result correctly

A successful HTTP status does not always mean the whole customer workflow is
complete. For example, a checkout preview can return 200 with blocking item
issues, code-send success confirms queuing rather than inbox delivery, and a
pending payment response does not mean paid.

Use each feature's error contract. Validation usually maps field names to message
arrays, while many other errors use `detail`. Some features add machine-readable
codes or retry timing. Do not invent a universal error shape that those contracts
do not provide.

Login/refresh user data exposes the personal-address country and cropped avatar.
The country comes from `UserAddress`, not the phone or delivery recipient. Photo
URLs follow the configured storage behavior. The detailed contracts live with
[authentication](authentication/README.md), [profile](account/profile-api.md), and
[photo management](account/profile-photo-api.md).

## Maintain the generated contract

Schema annotations live beside API views. Authentication response-only
serializers also live in `apps/authentication/openapi.py`. Explicit request and
response serializers describe operations automatic discovery cannot infer.

When changing an endpoint, update its annotations and maintained guide together.
Include parameters, status codes, permissions, and cookie behavior. Never document
refresh or registration cookie values as response-body token fields.

From a running development stack:

```sh
docker compose -f compose.dev.yaml exec api python manage.py spectacular --validate --fail-on-warn
```

For a configured host environment, activate its virtual environment and run the
same `manage.py` command directly. Review the changed operations in the generated
schema as well; schema generation cannot prove that business rules are correct.

See [development commands](docker-development.md#everyday-commands) and
[pull request checks](pull-request-checks.md) for the broader development workflow.
