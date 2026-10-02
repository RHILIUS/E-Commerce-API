# Interactive API documentation

With the backend running, open `/api/docs/` for Swagger UI. `/api/schema/`
returns the OpenAPI schema; append `?format=json` for JSON. The UI assets are
included in the project dependencies so the page does not require a CDN.

The schema covers authentication, [saved recipients](checkout/recipients-api.md),
[cart](ecommerce/carts.md), product catalog, and
[product detail](ecommerce/product-detail.md) routes. Use
Swagger UI's **Authorize** button with a Bearer access token for protected
operations. Login returns an access token and sets the refresh token in an
HttpOnly cookie. Refresh reads that cookie and returns another access token;
neither operation returns a refresh token in its response body. OTP verification
sets a short-lived HttpOnly registration cookie used by registration. When trying
cookie-based operations from a separate origin, browser cookie settings and
the backend's CORS configuration still apply.

Source annotations live beside API views, with documentation-only response
serializers in `apps/authentication/openapi.py`. The product catalog schema
includes its `q` and `page` parameters and paginated response shape. When the
contract changes, update those annotations and run:

```bash
source venv/bin/activate
python manage.py spectacular --validate --fail-on-warn
```

Review the affected operation in `/api/schema/` as well as the validator output.
