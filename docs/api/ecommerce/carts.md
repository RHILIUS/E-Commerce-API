# Store carts

A cart remembers products a customer may want to buy later. Items are grouped by
store, with one cart per customer and store. Saving an item does not reserve it or
reduce stock; checkout checks availability again before placing the purchase.

## How cart changes work

1. Add a product and quantity. The backend finds or creates its store cart.
2. Adding the same product again increments the existing quantity. Changing a
   line with PATCH replaces its quantity instead.
3. Read carts to see current product prices, stock, and subtotals. Quantities
   already saved are retained even if available stock has since fallen.
4. Remove selected lines together. If all lines in a store cart are removed, the
   backend also removes that empty cart.

The backend derives the customer from authentication. A line must belong to that
customer and its product must belong to the cart's store. Writes lock customer
and product records so competing additions cannot lose increments or bypass the
stock check.

## Availability and pending payments

Archived and sold-out products remain readable and removable. Draft products in
existing carts are redacted: a generic name, no image or price, and zero exposed
stock. Unavailable products cannot be added.

Catalog and cart reads expose physical `Product.stock`. Checkout preview subtracts
all unresolved Stripe holds. A hold is a temporary claim on stock for an online
payment, and its deadline alone does not safely release it.

Cart lines held by a pending Stripe purchase cannot be incremented, changed,
removed, or repurchased through cart checkout until that purchase resolves.
Other lines remain editable. Confirmed payment removes the purchased lines;
confirmed unpaid closure releases their holds. See
[Stripe inventory behavior](../checkout/stripe.md#purchase-and-inventory-behavior).

The frontend refreshes cart data every 15 seconds while visible and on focus.
This is periodic refresh, not a WebSocket stream. Product selection for checkout
is temporary browser state, not a stock reservation or saved server selection.

## API contract

| Method and route                    | Request                  | Behavior                                                       |
| ----------------------------------- | ------------------------ | -------------------------------------------------------------- |
| `GET /api/shop/carts/`              | No body                  | Read the caller's nonempty store carts; no database writes     |
| `POST /api/shop/carts/items/`       | `product_id`, `quantity` | Add units, incrementing an existing product line               |
| `PATCH /api/shop/carts/items/<id>/` | `quantity`               | Replace an owned line's quantity                               |
| `DELETE /api/shop/carts/items/`     | `item_ids: [id, ...]`    | Atomically remove up to 500 owned, present lines across stores |

Every successful operation returns HTTP 200:

```text
{carts: [{id, store: {id, name, profile_image},
  items: [{id, quantity, product: {id, name, status, stock,
    can_purchase, image, display_price}, subtotal}]}]}
```

Images use the catalog `{image, alt_text, crop}` shape; product image and store
profile image may be null. Display prices and subtotals use
`{amount: decimal-string, currency: ISO-code}`. Subtotals multiply displayed unit
price by quantity exactly. Conversion uses catalog pricing; fallback currencies
must not be added together as if they were one currency.

## Errors and recovery

- **401:** missing or invalid authentication.
- **400:** invalid input, returned as serializer field errors.
- **404:** foreign or missing lines, or a missing/non-active product when adding;
  a batch delete with any foreign or missing line fails entirely.
- **409:** stock/status conflicts or a cart line frozen by pending payment,
  returned as a human-readable `detail`.

After a failed write, reload current cart state before deciding the next action.
An uncertain add may already have incremented quantity; blindly repeating it can
add more units. Purchase placement uses a separate idempotency-key contract.

## Persistence and admin

The unique `Cart(user, store)` database constraint enforces one cart per store.
Cart-item quantities must be positive. Model validation prevents cross-store
items and moving a cart/product into a conflicting store. Raw SQL and bulk ORM
updates bypass model validation and must preserve those relationships.

Cart admin is inspection-only so manual edits cannot bypass pending-payment
freezes. [Checkout](../checkout/README.md), payments, and orders are implemented
by their own services; delivery operations remain outside the cart workflow.
