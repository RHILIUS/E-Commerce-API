# COD placement and confirmation APIs

**Implemented:** atomic COD placement and owner-only confirmation. Recipient APIs
and checkout preview feed this flow. Frontend checkout integration, Stripe,
delivery and payment collection remain pending.

## Place a COD purchase

`POST /api/shop/checkouts/` requires Bearer authentication.

```json
{
  "preview_token": "<token-returned-by-preview>",
  "recipient_id": 7,
  "payment_method": "cod",
  "idempotency_key": "01e8bdeb-7a97-482d-9b96-f542de778b6e"
}
```

All four fields are required. The key must be a UUID, recipient ID a positive
integer, and payment method must be `cod`. Extra fields (including client totals,
prices, currency or ownership) are rejected with 400. Validation occurs before
transaction processing.

Successful creation returns **201**. An identical successful request replay returns
**200**, with the same checkout reference and saved purchase details. Both responses
have the confirmation shape shown below. Payment is still unpaid.

### Transaction and retry rules

1. Lock the authenticated customer. This serializes first submissions, repeat keys
   and that customer's existing cart/default-recipient mutations.
2. Look up the customer's idempotency key **before** reading the preview, checking
   its expiry or reading cleaned-up cart lines. A matching completed checkout
   returns its confirmation. A different fingerprint or incomplete/legacy request
   returns 409.
3. Verify the signed preview's purchaser and five-minute lifetime. Lock the selected
   recipient and verify ownership and complete, valid contact/address fields using
   the recipient API's validation rules. Snapshot those validated fields.
4. Lock products in ascending ID order, then their stores, then selected cart
   lines. Check token expiry again after waiting for locks. Recheck product
   availability, store membership, store currency, names, source prices and saved
   cart quantities against the reviewed snapshot.
5. Subtract active, unexpired stock holds from available inventory. Recalculate
   the rounded unit prices using the **signed exchange multipliers**, and ensure
   the sum of line subtotals equals the signed total. No FX request is made during
   placement. A changed rate at the provider does not change an unexpired review.
6. Create one `completed` Checkout and one `pending`/`unpaid` Order per store. Save
   recipient, product/store names, original/converted prices and dated FX rates.
   Deduct stock and remove exactly the purchased selected cart lines. Remove carts
   made empty by cleanup. Direct-product purchases do not change any cart.
7. Commit everything together. Any failure rolls back checkout, orders, items,
   inventory and cart cleanup. COD creates no PaymentAttempt or StockReservation.

Future reservation writers must lock the same product rows before changing holds;
otherwise a preview/placement service cannot serialize inventory availability with
those writers. PostgreSQL row locking is required for production concurrency safety.

Keep the exact payload and key while a submission is in progress. After a timeout
or lost response, retry that same payload/key to discover whether it committed.
Do not generate a new key just because a response is uncertain. After a stale
preview error, resolve the issue and review a new preview before issuing a new
purchase request/key. A conflicting key must be resolved rather than blindly
reissued. The same UUID may be used independently by different customers.

## Read confirmation

`GET /api/shop/checkouts/{reference}/` requires Bearer authentication. The reference
is a UUID, not proof of ownership. Missing and other customers' checkouts return 404. The response uses persisted snapshots, with no live product, recipient or
exchange-rate lookup.

Example **200** confirmation (also the **201/200** placement body):

```json
{
  "reference": "c50678cd-c06e-4d8b-ae4a-a650b7ed936a",
  "status": "completed",
  "payment_method": "cod",
  "created_at": "2026-10-03T10:00:00Z",
  "completed_at": "2026-10-03T10:00:00Z",
  "total": { "amount": "24.68", "currency": "PHP" },
  "orders": [
    {
      "id": 11,
      "store": { "id": 4, "name": "Camera store" },
      "status": "pending",
      "payment_status": "unpaid",
      "recipient": {
        "name": "Juan Dela Cruz",
        "phone_number": "+639171234567",
        "country_code": "PH",
        "address_line1": "12 Sample Street",
        "address_line2": "",
        "city": "Quezon City",
        "region": "Metro Manila",
        "postal_code": "1100"
      },
      "subtotal": { "amount": "24.68", "currency": "PHP" },
      "exchange_rate": "1.000000000000",
      "exchange_rate_date": null,
      "items": [
        {
          "id": 19,
          "product_id": 12,
          "name": "Camera",
          "quantity": 2,
          "unit_price": { "amount": "12.34", "currency": "PHP" },
          "original_unit_price": { "amount": "12.34", "currency": "PHP" },
          "subtotal": { "amount": "24.68", "currency": "PHP" }
        }
      ]
    }
  ]
}
```

Store orders are ordered by store ID, items by order-item ID. Monetary amounts
are decimal strings formatted at their currency precision. Original prices carry
the source currency; converted prices and totals carry the checkout currency.
Subtotals are sums of saved item prices, not current catalog prices. Order/payment
statuses reflect their saved current state; recipient and purchase details remain
snapshots. If a product is later deleted, `product_id` becomes null while its saved
name, quantity and prices remain. Confirmation has no product-image field because
the current purchase model does not snapshot images.

Display “Order placed — payment due on delivery.” A completed COD checkout does
not mean paid or delivered. Reading confirmation must never resubmit placement.

## Errors

| Status | Placement                                                                                                                          | Confirmation                   |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| 400    | Invalid fields, unsupported payment method, tampered/foreign token, invalid recipient details                                      | —                              |
| 401    | Missing/invalid authentication                                                                                                     | Missing/invalid authentication |
| 404    | Missing or foreign recipient                                                                                                       | Missing or foreign checkout    |
| 409    | Expired preview, unavailable/deleted product, changed product/store/price/cart details or stock, reused key with different request | —                              |

Field validation errors map field names to messages. Conflict and missing-resource
errors use the standard `{"detail": "..."}` envelope. Missing selected cart lines
at placement are a stale-preview conflict (409); preview requests for missing or
foreign cart lines still return 404. Unexpected persistence failures roll back and
surface as server errors; a client with an uncertain result retries the same key.

## Tests and scope

`python manage.py test apps.checkout.tests.test_placement` covers direct and
multi-store cart purchases, retries after expiry/cart removal, ownership, stale
inputs, reservations, snapshot preservation, converted prices, amount verification,
rollback after partial work/cart cleanup, request-identity immutability and schema.
PostgreSQL-only tests use separate connections for duplicate requests, conflicting
payloads, last-unit contention and duplicate cart purchases with different keys.

Tests use an isolated database and mocked FX where required. No live payment,
fulfillment or frontend checkout behavior is introduced. Generate/apply the field
migration before testing against your normal development configuration.

## Implementation verification

- All 22 COD tests passed on an isolated PostgreSQL 18 instance, including four
  independent-connection concurrency tests.
- Related checkout, inventory, account, ecommerce and core suites ran 156 tests:
  155 passed and one removed historical-migration test was skipped.
- Django system checks and OpenAPI validation passed.
- A candidate additive fingerprint migration was generated only in a temporary
  migration module, reviewed, and applied when creating isolated test databases.
  Migration drift checks passed against that candidate. The repository intentionally
  still needs its new migration generated by the project owner.
- The temporary PostgreSQL instances were stopped. The development database and
  repository migration files were not changed.
