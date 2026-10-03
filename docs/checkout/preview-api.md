# Checkout preview API

**Implemented:** authenticated product/cart preview. Order placement, confirmation
and frontend checkout integration are still pending. Preview is read-only for
application data: it creates no checkout, order, reservation or cart record and
never changes quantities or stock. FX responses may be cached.

## Request

`POST /api/shop/checkout/preview/` with `Authorization: Bearer <access-token>`.

Direct product checkout:

```json
{"source": "product", "product_id": 12, "quantity": 2}
```

Selected cart items:

```json
{"source": "cart", "item_ids": [31, 45]}
```

Use exactly one shape. Product quantities are positive integers up to 2147483647.
Cart requests accept 1–500 distinct cart-item IDs and use their saved quantities.
Missing or foreign cart IDs reject the entire request with 404. The backend does
not accept client prices, totals, currency, ownership or cart quantity overrides.
Malformed input and extra fields return 400 field errors.

## Response

A reviewable preview returns 200, for example:

```json
{
  "source": "product",
  "currency": "PHP",
  "stores": [{
    "store": {"id": 4, "name": "Camera store", "profile_image": null},
    "items": [{
      "cart_item_id": null,
      "product": {"id": 12, "name": "Camera", "image": null},
      "quantity": 2,
      "available_stock": 5,
      "unit_price": {"amount": "12.34", "currency": "PHP"},
      "subtotal": {"amount": "24.68", "currency": "PHP"}
    }],
    "subtotal": {"amount": "24.68", "currency": "PHP"}
  }],
  "total": {"amount": "24.68", "currency": "PHP"},
  "can_place_order": true,
  "issues": [],
  "preview_token": "<opaque-signed-preview>",
  "expires_at": "2026-10-02T12:05:00Z"
}
```

Amounts are decimal strings. The unit price is rounded half up to the target
currency's minor unit, then multiplied by quantity. Store subtotals and the grand
total sum those exact rounded line subtotals. Store groups are ordered by store ID;
cart lines are ordered by cart-item ID. Images follow the catalog's
`image`, `alt_text`, `crop` shape and use an absolute storage URL. The default image
is preferred, otherwise the first image by order and ID.

Stock is current product stock minus unexpired `held` reservations, floored at zero.
Preview is not a stock guarantee: placement must lock and revalidate inventory.
Archived/sold-out products and excessive quantities return 200 with issues, for
example:

```json
{
  "code": "insufficient_stock",
  "product_id": 12,
  "cart_item_id": 31,
  "message": "Only 1 available. Adjust the quantity on the product or cart page."
}
```

In that response `can_place_order` is false and `preview_token`/`expires_at` are
null. Known prices remain visible for review even when stock is insufficient.
Unpublished cart products are redacted as `Unavailable product`, with no image,
price or stock information. Their item subtotal, affected store subtotal and
grand total are null, rather than a misleading partial sum. Direct requests for
unpublished/missing products return 404. No quantity is reset by preview.

Issue codes are `product_unavailable`, `insufficient_stock`, `invalid_price` and
`amount_too_large`. A checkout-wide amount issue has null product/cart IDs.
Negative source prices and totals that cannot fit the purchase models block
placement. A grand total overflow is represented by a null total.

## Currency and FX policy

The source currency is always `Store.currency`. The target comes from the
customer account country using Babel, defaulting to USD if unresolved/ambiguous.
Requests cannot override it. Same-currency purchases need no FX service: any known
source currency can be used without conversion.

When conversion is needed and the target is not USD, the service checks
[Frankfurter v2's available currencies](https://frankfurter.dev/#currencies).
A target absent from that list falls back to USD. A failed capability lookup is
an outage, not evidence that the currency is unsupported, and returns 503.
The capability list is cached for 24 hours.

Checkout maintains separate rate tables from catalog display conversion because
purchase snapshots need dated multipliers and strict all-store conversion.
[Frankfurter v2 rates](https://frankfurter.dev/#rates) are requested once per target
table, never per product. The target-based quote is inverted to a source-to-target
multiplier and rounded to the order model's 12 decimal places. That same multiplier
is used to calculate and sign the review price; placement must preserve it.

Tables refresh after 24 hours. A five-minute cache gate bounds concurrent refreshes
and outage retries. After refresh failure, previously validated rows can be used
only until seven days after their provider date and only when valid for the full
five-minute review window. Missing, invalid, future-dated or stale required rates
return 503. Source amounts never get relabelled as USD, and no mixed-currency total
is returned. `CHECKOUT_FX_TIMEOUT_SECONDS` defaults to 2 seconds per provider call.
A cold converted preview can require two provider calls (capabilities plus rates).
The default Django local-memory cache is per worker; a shared cache can be configured
through normal Django settings when deploying multiple workers.

## Signed preview and placement handoff

A valid review carries a customer-bound signed token with a five-minute expiry.
It includes version, customer, source, product/cart IDs, store/product names,
quantities, original prices, source/target currencies, dated exchange multipliers,
rounded unit prices and the grand total. It contains no recipient contact details.
Signing protects integrity, not secrecy. Clients should treat the token as opaque.

`apps.checkout.services.tokens.read_preview` verifies signature, purchaser and expiry.
Invalid/foreign tokens raise 400; expired tokens raise 409. This is a service helper,
not an additional endpoint. `can_place_order` describes item/price readiness only;
the frontend must also require a complete recipient and payment method.

Future placement must check successful idempotency replay **before** validating
preview expiry, then verify this token and recipient ownership. It must lock and
recheck products, source prices, cart selection/quantities and available stock.
Token verification alone cannot authorize order creation. Changed purchase terms
require a fresh preview and customer review. No placement endpoint is introduced
by this implementation.

## Status codes

| Status | Meaning |
| --- | --- |
| 200 | Preview, including item issues that block placement |
| 400 | Invalid request shape, IDs or quantity |
| 401 | Missing/invalid Bearer authentication |
| 404 | Missing/unpublished direct product or missing/foreign selected cart item |
| 503 | Required conversion/capabilities unavailable or invalid source currency |

Validation errors map field names to message lists. Other HTTP errors use DRF's
`{"detail": "..."}` envelope. Item issues are inside the 200 preview body.

## Verification

Focused tests cover ownership, input shapes, decimal totals, selected cart lines,
current price/quantity reads, image serialization, redaction, stock holds,
no application writes, signed-token tampering/ownership/expiry, FX caching and
failures, USD fallback, amount limits and OpenAPI.

Run `python manage.py test apps.checkout.tests.test_preview` and
`python manage.py spectacular --validate --fail-on-warn`. No model changes or
migrations are required.

Implementation verification: Django system checks and OpenAPI validation passed.
The focused preview suite passed 20 tests on an isolated SQLite database with
real migrations. Related checkout, inventory, recipient, ecommerce and core
suites passed 130 tests with four skips (three PostgreSQL row-lock tests and
one removed historical migration test). Configured PostgreSQL testing was
blocked by password authentication failure. Provider responses were mocked; no
live payment or order-placement behavior was tested.
