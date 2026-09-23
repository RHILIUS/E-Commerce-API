# Store carts

Authenticated customers have one saved cart per store, enforced by the unique
`Cart(user, store)` constraint. A cart item must belong to that store and have a
positive quantity. This matches the store-linked cart in the shared project ERD.

## API

- `GET /api/shop/carts/`: read all nonempty carts belonging to the caller.
- `POST /api/shop/carts/items/`: `{product_id, quantity}` adds units, incrementing
  an existing line. The server finds or creates that product's store cart.
- `PATCH /api/shop/carts/items/<id>/`: `{quantity}` replaces an owned line's quantity.
- `DELETE /api/shop/carts/items/`: `{item_ids: [id, ...]}` removes selected lines
  across stores and deletes empty carts. Up to 500 IDs per request; every ID must
  be owned and present or the entire operation returns 404.

All successes return HTTP 200 with `{carts: [{id, store: {id, name,
profile_image}, items: [{id, quantity, product: {id, name, status, stock,
can_purchase, image, display_price}, subtotal}]}]}`. Images follow the catalog
`{image, alt_text, crop}` contract; image and profile image may be null. Display
prices and subtotals are `{amount: decimal-string, currency: ISO-code}`.
Subtotals multiply the displayed unit price by quantity exactly. Conversion
uses the existing catalog service; fallback currencies must not be summed together.

Unauthenticated/invalid-token requests return 401. Invalid input returns 400
with serializer field errors. Foreign or missing items return 404. Stock/status
conflicts return 409 with a human-readable `detail` string. Unavailable products
cannot be added; unpublished products are redacted in existing carts (generic
name, no image or price, zero exposed stock). Archived and sold-out products
remain readable/removable. GET has no database writes.

## Inventory and concurrency

Stock comes from `Product.stock`, not from order quantities. Reads return current
prices and stock. The frontend polls while visible every 15 seconds and refreshes
on focus. This is periodic refresh, not a WebSocket stream. Cart writes lock the
customer and product rows inside a transaction, validating the resulting quantity
against current stock. Concurrent adds cannot lose increments or exceed that stock.
Adding to a cart does not reserve/decrement inventory. Stored quantities may later
exceed reduced stock; reads preserve them so customers can adjust or remove them.

Checkout, order placement, reservations, payment, and delivery are deferred.
Selection is UI state, not a reservation or a persisted checkout session.

## Migration and admin

Migration 0013 changes the user relation to many carts, adds the store relation,
splits legacy items by their product's store, and preserves item IDs/quantities.
Empty legacy cart records (which have no store or products) are removed. Rollback
merges store carts per user before restoring the one-to-one relation. Invalid
legacy zero-quantity rows stop the migration explicitly for operator resolution.
The migration does not change product stock, prices, or orders.

The admin exposes carts and their items for inspection. Model validation prevents
cross-store items and moving a cart/product to a conflicting store. Raw SQL and
bulk ORM updates bypass model validation and must preserve these relationships.
