# Interactive API documentation

With the backend running, open `/api/docs/` for Swagger UI. `/api/schema/`
returns the OpenAPI schema; append `?format=json` for JSON. The UI assets are
included in the project dependencies so the page does not require a CDN.

The schema covers authentication, [account profile](account/profile-api.md), [profile photos](account/profile-photo-api.md), [saved recipients](checkout/recipients-api.md),
[cart](ecommerce/carts.md), [customer orders/refunds](ecommerce/user-orders.md), [checkout preview](checkout/preview-api.md), [COD placement/confirmation](checkout/cod-api.md), product catalog, and
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

## Personal-address country

Registration keeps its existing flat address request (`country_code`, address lines,
city, optional region/postal code). It creates User, UserAddress and the default
RecipientInfo in one transaction. Their addresses are independent after creation.
Login and refresh now return `user.address_country_code` (two-letter country or null)
instead of `user.country_code`. This value comes from UserAddress, not the phone
number or default delivery recipient. Accounts created without a personal address
use USD for display conversions. Authentication cookies are unchanged.

UserAddress is a one-to-one personal address in the authentication app. The new
migration removes User.country_code without backfilling, as authorized for the
current disposable development database. Reset/reseed that database; this migration
must not be used to preserve an existing production country's data. A restored
schema on rollback cannot recover discarded country values. The profile read/update API and Profile-tab UI are implemented. Photo upload,
re-cropping, removal, and compression use the separate profile-photo API.
