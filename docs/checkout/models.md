# Checkout and order models

These records preserve a purchase from review through payment and later order
history. A model describes stored data; the service decides when and how a whole
operation may change that data.

## Understand the records first

- **Checkout:** the customer's whole purchase, which may span several stores.
- **Order:** one store's part of that purchase, with independent fulfillment state.
- **OrderItem:** a product quantity with the accepted name and prices copied into it.
- **PaymentAttempt:** the Stripe session and provider facts for online payment.
- **StockReservation:** units held while an online purchase remains unresolved.
- **ProviderEvent:** a durable, deduplicated record of a Stripe notification.
- **OrderRefund:** one store order's cancellation refund and confirmed reversal.
- **OrderEvent:** an immutable milestone in the order's history.

A **snapshot** is a copy retained for historical accuracy. A protected relation
prevents deleting a record still referenced by another protected record. The
field tables below cover the core five models; later sections introduce payment,
refund, and history additions and link to their full behavior.

The five core models below inherit `created_at` and `updated_at` from
`TimeStampedModel`; the later history section describes occurrence timestamps separately.
See the [feature overview](README.md) for implemented scope and pending workflows.

## Checkout (`apps.checkout`)

One customer submission, grouping its store orders and payment attempts.

| Field                 | Meaning                                                                                                        |
| --------------------- | -------------------------------------------------------------------------------------------------------------- |
| `reference`           | Unique public UUID, generated once; not proof of authorization.                                                |
| `user`                | Customer who owns the checkout; deletion is protected.                                                         |
| `idempotency_key`     | UUID supplied to placement; unique per customer. Reuse for retries of the same request.                        |
| `request_fingerprint` | SHA-256 hash of the accepted placement payload. Empty only for legacy/seed records; never returned by the API. |
| `payment_method`      | `cod` (default) or `stripe`.                                                                                   |
| `status`              | `pending`, `completed`, `cancelled`, or `expired`. Defaults to pending.                                        |
| `currency`            | Required uppercase three-letter code for the final total.                                                      |
| `total_amount`        | Nonnegative Decimal with 18 digits, 3 decimal places.                                                          |
| `expires_at`          | Optional pending-checkout deadline.                                                                            |
| `completed_at`        | Optional time checkout succeeded.                                                                              |

Completed means COD order placement or verified online payment, not fulfillment
or delivery. The current models do not automatically set status/timestamps.
Currency shape validation does not verify Stripe or FX-provider support.
Once orders or attempts exist, ordinary saves reject changes to the customer,
currency, amount, payment method, idempotency key or request fingerprint. COD
placement rejects keys reused with different contents and verifies the signed
total against all reviewed order lines. The fingerprint field is introduced by an
additive migration; see [COD setup](cod-api.md#required-database-update).

## Order (`apps.ecommerce`)

One store's part of a checkout, with its own fulfillment and payment state.

| Field                    | Meaning                                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `checkout`               | Protected parent checkout; `(checkout, store)` is unique.                                                                       |
| `store`                  | Protected store reference.                                                                                                      |
| `user`                   | Existing customer reference, required to match checkout ownership.                                                              |
| `recipient_info`         | Existing protected saved-recipient reference; must belong to the customer.                                                      |
| `store_name_at_purchase` | Snapshot for historical display.                                                                                                |
| `recipient_snapshot`     | Required JSON copy of recipient contact/address fields.                                                                         |
| `status`                 | Existing fulfillment states: pending, accepted, packed, shipment_arranged, shipped, cancelled, delivered, completed.            |
| `concluded_at`           | Existing optional fulfillment conclusion timestamp.                                                                             |
| `payment_status`         | unpaid (default), pending, paid, failed, cancelled.                                                                             |
| `paid_at`                | Optional verified payment/collection timestamp.                                                                                 |
| `source_currency`        | Original store pricing currency at purchase.                                                                                    |
| `exchange_rate`          | Positive Decimal, 28 digits and 12 decimal places. Multiply source price by this rate to obtain checkout price before rounding. |
| `exchange_rate_date`     | Optional date of the rate used; not needed when currencies match.                                                               |

The charged currency is `order.checkout.currency`. For equal currencies, use rate 1. `get_total()` sums recorded item subtotals.
Catalog display conversion uses a divisor; checkout preview signs a multiplier
that COD placement preserves unchanged.

Recipient JSON has exactly these string fields: `name`, `phone_number`,
`country_code`, `address_line1`, `address_line2`, `city`, `region`, `postal_code`.
Name, phone, country, address line 1 and city cannot be empty. Optional address
parts may be empty strings. `recipient_snapshot(recipient)` copies this shape;
COD placement also validates phone/address details through the recipient serializer.

Recipient/product changes do not update snapshots automatically. Existing orders
cannot move to another customer, store or checkout through ordinary saves.
Payment and fulfillment states are independent; no collection or refund is
inferred from a delivery/cancellation status.

## OrderItem (`apps.ecommerce`)

A purchased product and its saved quantity and prices.

| Field                      | Meaning                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `order`                    | Parent store order; existing cascading relation.                                            |
| `product`                  | Nullable live reference, set to null on deletion. Historical snapshots remain.              |
| `product_name_at_purchase` | Saved name, independent of later product edits.                                             |
| `quantity`                 | Positive integer enforced by a database constraint.                                         |
| `original_unit_price`      | Nonnegative source-currency unit price.                                                     |
| `price_at_purchase`        | Nonnegative checkout-currency unit price.                                                   |
| `source_cart_item`         | Optional source line for cart cleanup; null for direct checkout and set to null if removed. |

Both prices have 18 digits and 3 decimal places. `get_subtotal()` multiplies the
recorded checkout price by quantity. The service must round to the currency's
actual precision; three stored places do not mean all currencies use three.

Ordinary saves require product/store agreement. A source cart line must match the
product and customer. A product already referenced by orders cannot move stores.
An item with a reservation cannot change its product or quantity; the reservation
must continue to describe that item. Source cart linkage alone does not remove
items: the online service freezes selected cart lines until resolution and removes them atomically on success.

## PaymentAttempt (`apps.checkout`)

One Stripe Checkout Session attempt against an agreed checkout. Card retries
inside that session do not need extra rows. The model permits historical attempts,
but the current online service uses one attempt per checkout: it recovers the same
session and does not create a replacement session for a closed attempt.

| Field                         | Meaning                                                               |
| ----------------------------- | --------------------------------------------------------------------- |
| `reference`                   | Unique UUID; candidate idempotency key for session creation.          |
| `checkout`                    | Protected parent; must use Stripe, not COD.                           |
| `status`                      | pending (default), succeeded, failed, cancelled, expired.             |
| `amount` / `currency`         | Positive Decimal amount and uppercase currency matching the checkout. |
| `stripe_checkout_session_id`  | Optional unique Stripe session ID, null until known.                  |
| `stripe_payment_intent_id`    | Optional unique Stripe PaymentIntent ID, null until known.            |
| `failure_code`                | Optional provider failure code; never store card details or secrets.  |
| `expires_at` / `succeeded_at` | Optional provider lifecycle timestamps.                               |

A partial unique constraint allows only one pending attempt per checkout. Use
null, not an empty string, for unknown provider IDs. A declined card can leave a
session open, so it need not make the entire attempt terminal. Verified provider
state must determine when a retry session is safe. Stripe test webhook verification, atomic stock/order/ledger updates and duplicate-success
protection are implemented in the online payment services.
COD placement already provides atomic stock/order updates and idempotent replay.
Creating this record never contacts Stripe or marks an order paid.

## StockReservation (`apps.inventory`)

One inventory hold for one order item.

| Field         | Meaning                                                   |
| ------------- | --------------------------------------------------------- |
| `order_item`  | Protected one-to-one relation; one reservation per line.  |
| `product`     | Protected reference; must match the order item's product. |
| `quantity`    | Positive integer equal to the item quantity.              |
| `status`      | held (default), consumed, released.                       |
| `expires_at`  | Required deadline for resolving a pending hold.           |
| `resolved_at` | Optional consumption/release timestamp.                   |

Saving a reservation does not reduce stock, lock products or schedule expiry.
The online checkout service locks product rows and accounts for every unresolved held quantity;
current catalog/cart APIs still expose `Product.stock` and do not subtract holds.
Creating a reservation row directly does not run the online service's locking,
provider reconciliation, or release workflow.
Coordinate confirmed Stripe expiry with releasing stock to avoid late-payment races.

Protected references prevent deleting reserved products/order lines even after the
reservation resolves. They intentionally preserve history; any later purge policy
needs an explicit design. Checkout/PaymentAttempt and Order store references are
also protected. The existing Order-to-user relation still uses CASCADE, but the
customer's checkout reference prevents deleting a customer with checkout history.

## Validation, admin and limits

Checkout, Order, OrderItem, PaymentAttempt and StockReservation call `full_clean()`
from `save()`; ordinary `objects.create()`/`update_or_create()` are validated too.
Missing foreign keys produce validation errors. Database checks cover amounts,
quantities and uniqueness. Cross-table matching is application validation.
`QuerySet.update()`, `bulk_create()`, `bulk_update()` and SQL bypass model validation;
future transactional services must preserve these rules, lock related rows and
handle concurrent uniqueness failures. Validation alone is not concurrency control.

Purchase records and order-item inlines are inspection-only in Django admin,
including for superusers. Manual payment, quantity and deletion controls would
bypass the COD/Stripe services and financial/inventory invariants. Fulfillment
workflows remain future work. Catalog and cart admin remain separate.

## Stripe workflow additions

See [Stripe API and setup](stripe-api.md) for implemented behavior and recovery.
PaymentAttempt also includes saved immutable session parameters, a hosted checkout
URL, cancellation intent and a protected unique ledger receipt. Provider IDs cannot
be reassigned once known. ProviderEvent is a durable test-only inbox unique by
environment/event ID, retaining only routing identifiers, processing timestamps and
safe error codes. Both models are inspection-only in admin. No OrderSettlement or
SettlementHold is created yet; paid store shares remain pending in the journal.

## Customer cancellation refunds

[OrderRefund](../ecommerce/user-orders.md) tracks durable Stripe test card refunds
and their immutable compensating ledger entries. `concluded_at` is set by new
order cancellations and terminal online cancellation/expiry; historical nulls
remain unknown. Fulfillment Completed is separate from Checkout completed.

## OrderEvent

[Persistent order history](../ecommerce/user-orders.md#persistent-order-history)
adds immutable milestones with independent occurrence times and protected order and
actor references. Order saves log placement, fulfillment changes and confirmed
payment atomically; cancellation/refund services log their own relevant events.
Historical entries use only existing timestamps and are explicitly marked partial.
