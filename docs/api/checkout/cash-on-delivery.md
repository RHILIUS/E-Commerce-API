# COD placement and confirmation APIs

Cash on delivery (COD) places the order now and leaves payment due on delivery.
The backend saves the reviewed purchase and deducts stock, but does not collect
money or create wallet entries.

## How placement works

1. The customer reviews a [preview](preview.md) and chooses a saved recipient.
2. The frontend submits the preview token, recipient, COD method, and a unique
   idempotency key identifying this request.
3. The backend checks replay before preview expiry, then locks and rechecks the
   customer, recipient, products, stores, and cart lines.
4. Save the checkout, one order per store, purchase snapshots, stock deductions,
   and selected cart cleanup in one transaction. They all succeed or roll back together.
5. Return saved confirmation. Reading it later does not place the order again.

An uncertain response may mean placement already committed. Retry the exact body
and key; a new key can create another purchase. Unresolved Stripe holds reduce
available stock, and pending online purchases can freeze selected cart lines.
[Stripe test checkout](stripe.md) is implemented separately. Delivery and COD
payment collection remain outside this flow. See
[frontend behavior](../../../../frontend/docs/checkout/README.md) for the customer UI.

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
5. Subtract active, unresolved stock holds from available inventory. Recalculate
   the rounded unit prices using the **signed exchange multipliers**, and ensure
   the sum of line subtotals equals the signed total. No FX request is made during
   placement. A changed rate at the provider does not change an unresolved review.
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
is a UUID, not proof of ownership. Missing and other customers' checkouts return 404.
Purchase details use persisted snapshots, with no live price, recipient or exchange-rate lookup.
Optional images are loaded from the current linked catalog records.

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
      "store": { "id": 4, "name": "Camera store", "profile_image": null },
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
          "image": null,
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
name, quantity and prices remain. Each store includes nullable `profile_image` (an absolute
URL); each item includes nullable `image` (`image` URL, `alt_text`, `crop`). The default
product image is preferred, otherwise the first by order and ID. Deleted products and
products without images return null. Images are current presentation data, not purchase
snapshots; changing catalog images can change the receipt imagery. Clients show a
placeholder for missing or broken images. These fields appear in placement, replay and
confirmation responses. No database migration is required.

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

## Required database update

The repository includes [0002_checkout_request_fingerprint](../../../apps/checkout/migrations/0002_checkout_request_fingerprint.py).
The fingerprint supports safe request replay. Apply repository migrations as part
of setup or deployment before serving placement requests. Check application status
with `showmigrations`; a migration file in Git does not mean a target database has
applied it.
