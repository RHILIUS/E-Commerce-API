# Saved-recipient APIs

A delivery recipient is a saved name, phone number, and delivery address. It can
belong to the customer or someone else. The same address book is used in account
settings and checkout; it is independent of the customer's personal address.

## How the address book works

1. List the customer's saved recipients, with the default first.
2. Create or edit a recipient. The first active recipient becomes the default;
   choosing a different default replaces it atomically.
3. At checkout, choose a recipient for that purchase. That choice does not change
   the saved default.
4. Removal hides a recipient from future purchases but retains its row for past
   orders and pending payments. Removing a default with other recipients requires
   explicitly choosing its replacement.

A new recipient POST is not idempotent: repeating it can create another record.
After an uncertain mutation, the frontend reloads the list before allowing more
changes. Order snapshots keep the accepted delivery details even after editing
or removing an address-book entry.

These endpoints support recipient selection for both COD and Stripe test
checkout. See [preview](preview.md), [placement and confirmation](cash-on-delivery.md),
and [frontend behavior](../../../../frontend/docs/checkout/README.md).

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

Only the current customer's active (not removed) rows are returned, with the default first and then
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

| Field           | Validation                                                                |
| --------------- | ------------------------------------------------------------------------- |
| `name`          | Required, nonblank, at most 100 characters.                               |
| `phone_number`  | Required valid international E.164 phone number, such as `+639171234567`. |
| `country_code`  | Required supported two-letter region; normalized to uppercase.            |
| `address_line1` | Required, nonblank, at most 150 characters.                               |
| `address_line2` | Optional, blank allowed, at most 150 characters.                          |
| `city`          | Required, nonblank, at most 100 characters.                               |
| `region`        | Optional, blank allowed, at most 100 characters.                          |
| `postal_code`   | Optional, blank allowed, at most 20 characters.                           |
| `is_default`    | Optional boolean, false unless requested or this is the first recipient.  |

Text fields trim surrounding whitespace. Optional address strings are returned as
empty strings when omitted; null is not accepted. Phone/country validation is
shared with registration, but recipient phone numbers are not unique. Multiple
recipients can use the customer's own phone number, and a phone's calling country
can differ from the delivery country. Address text is required where noted; no
external address-verification service is used.

## Default behavior and concurrency

The first active saved recipient is automatically default, even if the request supplies
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
and order recipient snapshots remain unchanged. GET, POST and PUT on the
detail endpoint return **405** for authenticated requests.

## Delete a saved recipient

`DELETE /api/account/recipients/{id}/` removes the owned recipient from the saved
address book. The row is retained with `is_deleted: true` and `is_default: false`
to preserve protected order links and historical delivery snapshots. Removed
recipients cannot be listed, edited or selected for a new COD/Stripe purchase.
Existing checkout retries, pending payments and confirmation snapshots continue
to work. The public recipient response shape does not expose the internal flag.

When deleting the default with other active recipients, supply the query parameter
`replacement_default_id=<id>` naming another active recipient owned by the same
customer. Missing, foreign, removed or self replacements return **400** under
`replacement_default_id`. A replacement is rejected when it is unnecessary.
Deleting the last recipient is allowed; the next created recipient becomes default.

Removal and replacement are atomic under the same customer lock used by creation,
editing and placement. Returns **200** with `{"recipients": [...]}` containing the
remaining active recipients, default first then ascending ID. Repeating an owned
removal returns the current list without changing the current default. Missing or
foreign target IDs return **404**, unauthenticated requests **401**. The operation
never deletes orders, payments, reservations or purchase snapshots.

Frontend Account and Checkout share the confirmation UI. Checkout clears a removed
selection and requires explicitly selecting/adding a recipient before placing a
new order. A network failure triggers a list reload before further mutations.

## Errors and scope

Validation errors return 400 as field names mapped to message arrays, for example:

```json
{ "phone_number": ["Enter a valid phone number."] }
```

Invalid input is rejected before changing defaults. PUT, PATCH and DELETE on the
collection return 405 for authenticated requests. Default switching uses PATCH with `is_default: true`; no separate default endpoint is needed.

Request/response schemas are published in `/api/schema/` and Swagger `/api/docs/`.
Concurrent default changes rely on PostgreSQL row locks, not only field validation.

## Django admin

Staff can inspect, add and edit recipients under **Account → Recipient infos**.
The list shows the recipient, customer, phone, city, country and default status,
with customer/contact/address search and country/default filters. Creation uses
customer autocomplete; ownership is read-only after creation. Contact and address
fields are grouped on the form.

Admin writes reuse API validation and the transactional recipient services.
The first recipient becomes default; choosing a new default replaces the old one
atomically. Clearing the current default is a form error. Phone/country values
are normalized, and invalid submissions do not save. Admin POST validation locks
the customer inside Django's change-form transaction, keeping default validation
and persistence consistent with concurrent API writes.

Django's standard view/add/change model permissions and admin change logs apply.
Customer autocomplete additionally requires view or change permission on the user
model, following Django's related-object autocomplete permissions.
Admin hard deletion, including bulk deletion, remains disabled. Removed recipients
are visible with an `is_deleted` filter and read-only detail view; staff cannot
restore or edit them. Customer removal uses the authenticated API above. Existing
order snapshots are unchanged by admin edits.
