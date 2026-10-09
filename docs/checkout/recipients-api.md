# Saved-recipient APIs

**Implemented:** authenticated listing, creation and editing. These endpoints prepare the
recipient selection used by the implemented COD checkout flow. Checkout
[preview](preview-api.md), [placement and confirmation](cod-api.md), and
[frontend integration](../../../frontend/docs/checkout/README.md) are implemented.

## Authentication and ownership

All operations use the existing Bearer access token. Missing, expired or invalid
authentication returns 401. The backend takes ownership from the authenticated
customer. `user` and `user_id` are not writable fields; submitted values cannot
change ownership. `id` is read-only. No new cookies or token response fields exist.

## List recipients

`GET /api/account/recipients/` returns 200:

```json
{
  "recipients": [
    {
      "id": 7,
      "name": "Juan Dela Cruz",
      "phone_number": "+639171234567",
      "country_code": "PH",
      "address_line1": "123 Example Street",
      "address_line2": "",
      "city": "Marikina",
      "region": "Metro Manila",
      "postal_code": "1800",
      "is_default": true
    }
  ]
}
```

Only the current customer's rows are returned, with the default first and then
ascending ID. There is no pagination. An empty list is `{"recipients": []}`.
Listing does not change the default or create any rows.

## Create a recipient

`POST /api/account/recipients/` accepts:

```json
{
  "name": "Juan Dela Cruz",
  "phone_number": "+639171234567",
  "country_code": "PH",
  "address_line1": "123 Example Street",
  "address_line2": "",
  "city": "Marikina",
  "region": "Metro Manila",
  "postal_code": "1800",
  "is_default": true
}
```

Returns **201** with the single saved recipient object, including its `id` and
actual `is_default` value. The response is not wrapped in `recipients`.

| Field | Validation |
| --- | --- |
| `name` | Required, nonblank, at most 100 characters. |
| `phone_number` | Required valid international E.164 phone number, such as `+639171234567`. |
| `country_code` | Required supported two-letter region; normalized to uppercase. |
| `address_line1` | Required, nonblank, at most 150 characters. |
| `address_line2` | Optional, blank allowed, at most 150 characters. |
| `city` | Required, nonblank, at most 100 characters. |
| `region` | Optional, blank allowed, at most 100 characters. |
| `postal_code` | Optional, blank allowed, at most 20 characters. |
| `is_default` | Optional boolean, false unless requested or this is the first recipient. |

Text fields trim surrounding whitespace. Optional address strings are returned as
empty strings when omitted; null is not accepted. Phone/country validation is
shared with registration, but recipient phone numbers are not unique. Multiple
recipients can use the customer's own phone number, and a phone's calling country
can differ from the delivery country. Address text is required where noted; no
external address-verification service is used.

## Default behavior and concurrency

The first saved recipient is automatically default, even if the request supplies
`is_default: false`. Later recipients are non-default unless explicitly requested.
Creating one with `is_default: true` clears the old default and saves the new one
in the same transaction. A failed insertion rolls back the old-default change.

Creation locks the customer row before checking recipients. This serializes both
concurrent first-recipient requests and concurrent default replacements on
PostgreSQL. The existing partial unique database constraint provides an additional
at-most-one-default guarantee. Future recipient writers must use the same locking
protocol; direct ORM/SQL operations do not automatically inherit it.

A successful create is not idempotent: repeating a POST creates another recipient.
The frontend should disable duplicate submissions and select the returned recipient.
Choosing a recipient for checkout will not change the saved default.

## Edit a recipient

`PATCH /api/account/recipients/{id}/` partially updates an owned saved recipient
and returns **200** with the same single-object shape as creation. Omitted fields
remain unchanged, including `is_default` and optional address strings. The same
field validation and normalization apply. Missing or foreign IDs return **404**;
unauthenticated requests return **401**. Ownership and ID are never writable.

Setting `is_default: true` replaces the prior default atomically under the same
customer-row locking protocol as creation and order placement. Setting the current
default to false returns **400** with an `is_default` field error; choose another
default instead. Validation or save failure leaves the old data/default unchanged.

Editing changes saved recipient details for future purchases. Existing checkout
and order recipient snapshots remain unchanged. GET, POST, PUT and DELETE on the
detail endpoint return **405** for authenticated requests.

## Errors and scope

Validation errors return 400 as field names mapped to message arrays, for example:

```json
{"phone_number": ["Enter a valid phone number."]}
```

Invalid input is rejected before changing defaults. PUT, PATCH and DELETE on the
collection return 405 for authenticated requests. Deleting and separate default-switching endpoints are outside this recipient API.

Request/response schemas are published in `/api/schema/` and Swagger `/api/docs/`.
Regression tests cover authentication, owner scoping, normalized response shape,
required fields/limits, shared phones, defaults, rollback and concurrent creation.
Concurrency tests require row-lock support and skip on SQLite.
