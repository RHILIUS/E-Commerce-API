# Customer orders and Stripe test card refunds

## API

All order endpoints require the authenticated owner and use the existing Bearer token
contract. Foreign/missing orders return 404. No cookies or authentication behavior
changes are introduced.

- `GET /api/shop/orders/?tab=all&page=1`: 200 `{count, next, previous, results}`.
  Page size is 20. Invalid tab returns 400; invalid/out-of-range page returns 404.
- `POST /api/shop/orders/{id}/cancel/`: empty body, 200 updated order, 409 if
  cancellation is no longer eligible. Safe to retry; a repeated cancelled paid
  order request reconciles the same persisted refund. Provider failure is exposed
  in the returned refund state, not confused with failure to cancel fulfillment.

| tab             | Included fulfillment statuses                        |
| --------------- | ---------------------------------------------------- |
| `all` (default) | All                                                  |
| `to_ship`       | `pending`, `accepted`, `packed`, `shipment_arranged` |
| `to_receive`    | `shipped`                                            |
| `cancelled`     | `cancelled`                                          |
| `delivered`     | `delivered`                                          |
| `completed`     | `completed`                                          |

Queries are owner-scoped and sorted by `-created_at, -pk` before pagination.
Each result has the existing confirmation order fields (store, recipient snapshot,
items, original and converted prices, subtotal, fulfillment/payment statuses,
exchange rate) plus `created_at`, nullable `concluded_at`, `checkout_reference`,
`payment_pending`, `can_cancel`, and nullable `refund`. A refund contains
`status`, `amount: {amount: decimal-string, currency: "USD"}`, and `needs_review`.
No provider identifiers or raw provider errors are exposed.

Purchase names, prices and quantities stay historical. Current optional store
and product images reuse confirmation serialization. Totals are calculated from
purchase line price × quantity once. Checkout completion is payment/placement,
not order fulfillment completion.

## Cancellation and consistency

Only Pending, Accepted and Packed orders in a completed checkout can cancel.
COD must be unpaid; Stripe must have a verified paid attempt with its immutable
ledger receipt and intent reference. Pending online checkouts remain visible but
use the existing whole-checkout payment cancellation/reconciliation flow.

Cancellation locks customer, checkout, order and products in deterministic order,
consistent with payment writers. It rechecks eligibility, restores extant products'
stock once, records `concluded_at`, and creates a single protected `OrderRefund`
for a positive paid USD amount in one atomic transaction. Deleted product lines
retain their snapshots but cannot restore a deleted product. A zero-value paid
store order needs no refund. COD creates no financial posting.

For paid orders, cancellation commits before the provider call; an unknown or
failed refund does not revive fulfillment or restore stock a second time.
`payment_status`/PaymentAttempt and the original receipt retain their historical
paid state; `refund` records the compensating event independently. Other stores
in the same checkout are unaffected.

## Refund recovery

`OrderRefund` fixes order, payment attempt, UUID and amount in integer USD cents.
It has a unique nullable Stripe refund ID and a protected unique ledger reversal.
Admin is inspection-only. The provider adapter uses the existing test-only Stripe
client. The amount is this order's full paid share, passed as a partial refund of
the checkout PaymentIntent, with a deterministic idempotency key and identifying
metadata. No external call holds DB locks.

Provider results must match the saved amount, currency, payment intent, metadata
and refund identity. `pending`, `requires_action`, `succeeded`, `failed` and
`canceled` are tracked separately. On verified success, a single atomic posting
debits the store pending liability and credits treasury, linked from the refund.
This uses the existing internal journal only; no customer wallet is provisioned,
credited or exposed. Pending/failed refunds do not claim a ledger reversal.

Lost responses retry the same key within 23 hours. After that conservative window,
recovery only searches existing Stripe refunds by saved metadata; it never creates
a fresh refund whose earlier outcome is unknown. An unmatched old request is
flagged for manual review. Failed/canceled provider refunds also require support;
the endpoint does not silently generate a second refund.

Run `python manage.py reconcile_order_refunds` periodically (for example, every
minute) for durable pending/requires-action recovery. Customer **Check refund
status** uses the same service. This change supplies the command, not a deployed
scheduler. Existing checkout webhooks remain unchanged; refund reconciliation
uses current provider reads rather than trusting webhook delivery order.

Apply `ecommerce.0002_alter_order_status` and `checkout.0004_orderrefund`.
Completed was already present in model code but missing from migration state and
model documentation; this change aligns them. Historical null `concluded_at`
values are not fabricated. New online cancellation/expiry writes record it.
Automatic seven-day completion, deliveries, returns, settlement release, wallets
and withdrawals are outside scope.

Stripe contracts: [create refund](https://docs.stripe.com/api/refunds/create),
[refund object](https://docs.stripe.com/api/refunds/object), and
[idempotency](https://docs.stripe.com/api/idempotent_requests).


## Persistent order history

`GET /api/shop/orders/{id}/history/` returns the authenticated owner's complete
recorded timeline (200), with 401 for no authentication and 404 for missing/foreign
orders. Reading history never writes records or contacts Stripe. Response:

- `order_id`, `current_status`, `is_partial`
- `events`: `{id, event_type, label, occurred_at, actor_role, is_backfilled}`,
  ordered by `occurred_at, id`, oldest first. No truncation or pagination.

Actor roles are customer, seller, system or unknown. The protected actor identity
is retained for internal auditing but is not returned to customers; names, emails,
payment identifiers, arbitrary messages and internal retry errors are not exposed.

`OrderEvent` is append-only through ordinary model saves, queryset updates/deletes,
bulk mutations and admin. It has protected order/actor foreign keys, event type,
occurrence timestamp, actor role and a historical-backfill marker. Admin exposes a
read-only timeline inline on Order. These are ORM safeguards, not database triggers;
raw SQL and low-level ORM bypasses are outside this boundary.

`Order.save()` locks the persisted order, compares actually saved fields and appends
placement/status/payment-confirmation milestones in the same transaction. New
placements explicitly attribute the customer; verified online payment and automatic
payment closure are system events. Customer cancellation records cancellation and
refund request in the cancellation transaction. Verified refund-state transitions
are recorded in the same transaction as their refund/ledger changes. No-op saves,
placement/cancellation replay and unchanged provider statuses produce no duplicate
event. Outer transaction rollback removes both the change and its event.

Future fulfillment services must authorize their actor, preserve the established
lock order, and call `order.save(update_fields=["status", "updated_at"],
event_actor=actor, event_actor_role="seller")`. System transitions omit the actor.
Do not use queryset `update()`, `bulk_update()` or raw SQL for status changes: they
bypass model-level history. Existing production bulk order-status writers were
converted to saves. No seller endpoints or seven-day completion jobs are added here.

A Delivered event keeps its own timestamp permanently even after Completed gets
its later timestamp. The existing `concluded_at` follows the latest terminal status;
history retains both milestones. The trusted `event_at` argument lets a service use
one authoritative timestamp across its atomic changes. Refund timestamps represent
when this backend verified the state, not an invented card-bank processing time.

Migrations `ecommerce.0003_orderevent` and `0004_backfill_order_events` add storage
and backfill older orders from `created_at`, `paid_at`, terminal `concluded_at`,
refund creation and confirmed reversal `posted_at`. No `updated_at` guesses or
intermediate accepted/shipped/delivered milestones are synthesized. Actors are
unknown on backfilled entries and `is_partial` is true for those orders. Orders
already containing events are skipped, including on a repeated backfill. Failed or
pending historical refunds have no guessed outcome time.

Tests: `apps/ecommerce/tests/test_order_history.py`, plus cancellation/refund replay
and concurrency assertions in `test_user_orders.py`. Coverage includes chronology,
all fulfillment statuses, field-restricted saves, ownership/privacy, immutable
records, rollback, payment deduplication and accurate idempotent legacy backfill.
