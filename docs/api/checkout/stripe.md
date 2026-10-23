# Stripe test checkout

Stripe test checkout lets a customer pay for several stores in one hosted card
payment. It records a simulated USD receipt and each store's pending share; no real
money moves. The integration accepts test keys and events only and uses the pinned
Stripe SDK in `requirements.txt`.

## How online purchase works

1. Review the selection in USD, choose a recipient, and submit placement with its
   original idempotency key.
2. Save a pending checkout, store orders, and stock holds together. A **hold**
   prevents those units being sold while payment is unresolved; it does not yet
   deduct physical stock.
3. Ask Stripe for a hosted payment session using saved parameters and a stable
   attempt key. Provider calls run outside database locks.
4. The customer completes the hosted flow. A browser return is only navigation;
   a webhook or reconciliation checks the actual provider state.
5. Verified success atomically posts the store credits, consumes holds, deducts
   stock, updates orders, and completes checkout. Verified unpaid closure releases
   holds. An uncertain provider result remains pending for recovery.

A **webhook** is a signed provider notification. **Reconciliation** reads current
provider facts and compares them with the saved attempt. Scheduled recovery,
webhooks, and explicit customer checks protect the same purchase against duplicate
stock deductions or credits.

[Order cancellation refunds](../ecommerce/user-orders.md) are implemented separately.
Delivery operations, settlement release, returns, and withdrawals remain outside
this flow. After this overview, read setup, API details, inventory rules, and
operational exceptions as needed.

## Local setup

Configure `.env` as described below before starting. From the backend repository,
build the image, apply migrations and start the development services:

```bash
docker compose -f compose.dev.yaml up -d --build
```

Set these values in backend `.env` (never in frontend variables or Git):

```dotenv
STRIPE_SECRET_KEY=sk_test_your_test_secret
STRIPE_WEBHOOK_SECRET=
STRIPE_FRONTEND_ORIGIN=http://localhost:5173
```

Use the origin where this frontend actually runs. HTTPS is required except for
localhost/127.0.0.1/::1 development. Do not change Stripe test accounts while
attempts are pending. No frontend Stripe SDK or publishable key is required for
this hosted redirect flow.

Local development uses scheduled payment reconciliation without webhook delivery.
Keep `STRIPE_WEBHOOK_SECRET` empty for that workflow and keep API, Redis, worker
and Beat running. Payment updates may wait until the next recovery run; provider
latency and queue backlog can add delay. The frontend can also request an explicit
payment-status check.

For deployment, register the public HTTPS `/api/shop/stripe/webhook/` endpoint
with Stripe for `checkout.session.completed`, `checkout.session.expired`,
`checkout.session.async_payment_succeeded` and `checkout.session.async_payment_failed`.
Set `STRIPE_WEBHOOK_SECRET` to that endpoint's signing secret and recreate affected
containers to reload environment values. Keep scheduled reconciliation as recovery
for missed deliveries or failed processing. Current payment code accepts only test
keys/events; live payments remain outside scope. Production images and services
are configured in the [deployment guide](../../operations/deployment.md).

In that test account/sandbox's Dashboard payment-method settings,
enable Cards and disable other methods for this cards-only demo. Checkout uses
Dashboard-managed dynamic payment methods; do not send `payment_method_types`
with API version 2026-09-30.endive. See
[Stripe migration guidance](https://docs.stripe.com/payments/payment-methods/dynamic-payment-methods).
A Dashboard endpoint has its own signing secret. Run the backend
and frontend normally. Choose **Card (Stripe test)**, review the USD total, continue
to payment, then open Stripe. Use Stripe test card `4242 4242 4242 4242`, a future
expiry and any three-digit CVC. See [Stripe testing](https://docs.stripe.com/testing)
and [webhook setup](https://docs.stripe.com/webhooks).

The callback page never assumes a redirect means paid. Webhooks or provider
reconciliation establish completion. Inspect PaymentAttempt, ProviderEvent, and
wallet journal records in Django admin when diagnosing payment state.

## Scheduled payment recovery

Implemented: Django's `CELERY_BEAT_SCHEDULE` dispatches
`apps.checkout.tasks.reconcile_stripe_payments` every 60 seconds through Redis.
The worker calls `reconcile_stripe` with `limit=100`. Run one Beat instance and
at least one worker using the [Docker development stack](../../development/docker.md#celery-payment-recovery).
Scheduling is periodic, not a guarantee of completion within exactly 60 seconds.

For a manual recovery run from the backend directory:

```bash
docker compose -f compose.dev.yaml exec api python manage.py reconcile_stripe --limit 100
```

Each run selects up to 100 unfinished inbox events and, separately, up to 100
pending payment attempts. Events are ordered by last-attempt time and attempts by
update time; attempted pending rows rotate to allow later batches to progress.
Overlapping payment recovery is protected by existing row locks and unique payment
posting keys. Durable pending state stays in PostgreSQL across worker downtime.
An unpaid session remains pending; successful task execution alone does not prove
payment completion. Exceptions caught per row increment the reported unresolved
count without failing the Celery task. Inspect that count and admin records.

This task does not schedule refund recovery or seven-day settlement. See the
[refund recovery contract](../ecommerce/user-orders.md#refund-recovery) for the
separate command. Without worker/Beat, local abandoned-payment recovery stops;
explicit customer status checks can still reconcile payments.

## API contract

All purchase endpoints require Bearer authentication and scope references to the
requesting customer. UUID references do not confer access. Webhooks instead require
a valid `Stripe-Signature` over the exact raw body and reject live-mode events.

| Method and route                                       | Request                                                                                     | Success                                                                                    |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `POST /api/shop/checkout/preview/`                     | Existing product/cart selection plus optional `payment_method` (`cod` default, or `stripe`) | Existing preview shape; Stripe reviews are USD and tokens are bound to the chosen method.  |
| `POST /api/shop/checkouts/stripe/`                     | `preview_token`, `recipient_id`, `payment_method: "stripe"`, UUID `idempotency_key`         | 200 payment state; same request/key returns the same purchase.                             |
| `GET /api/shop/checkouts/{reference}/payment/`         | None                                                                                        | 200 saved payment state, no Stripe calls.                                                  |
| `POST /api/shop/checkouts/{reference}/payment/`        | Empty                                                                                       | 200 reconciled payment state.                                                              |
| `POST /api/shop/checkouts/{reference}/payment/cancel/` | Empty                                                                                       | 200 current payment state after cancellation request/reconciliation.                       |
| `GET /api/shop/checkouts/{reference}/`                 | None                                                                                        | Existing historical confirmation shape, including pending or completed online orders.      |
| `POST /api/shop/stripe/webhook/`                       | Raw Stripe event JSON and signature header                                                  | 200 `{"received": true}` after durable inbox receipt; unsupported event types are ignored. |

Payment state:

```json
{
  "reference": "78b776ef-6e84-4a64-b70c-5ac73de4a48b",
  "status": "pending",
  "checkout_url": "https://checkout.stripe.com/c/pay/...",
  "expires_at": "2026-10-10T10:00:00Z",
  "cancel_requested": false
}
```

`status` is pending/completed/cancelled/expired. `checkout_url` is null when closed,
locally overdue, cancellation is requested, or creation has not yet been recovered.
The backend accepts only HTTPS URLs on `checkout.stripe.com`. Pending is not failure
and does not authorize another charge. Returning via Stripe's cancel URL only
navigates back; the explicit cancellation endpoint closes the session safely.

Errors: 400 invalid fields/signature or confirmed session-creation rejection; 401 authentication; 404 absent/foreign data;
409 stale review, frozen cart or conflicting key; 503 unavailable configuration,
provider uncertainty or a reconciliation mismatch. On 503 or an interrupted POST,
retry the exact original placement body/key. Do not replace it with a new key.
Replays are checked before token expiry and cart cleanup. A closed attempt does not
get a replacement session; a new purchase needs a fresh review and request key.

## Purchase and inventory behavior

Stripe review converts source prices to USD with snapshotted rates, rounds unit
prices half-up to cents, and sums quantity subtotals. Total must be USD 0.50 through
999,999.99. Hosted Stripe shows product lines with snapshotted product/store names, USD unit
prices, quantities, and the selected product image when storage supplies a suitable
absolute HTTPS URL. Images follow the catalog's default/order/ID preference; local,
relative, credential-bearing and non-HTTPS URLs are omitted. Configure public HTTPS
media storage for sandbox images; Stripe cannot fetch localhost media. The original
image is used; frontend crop metadata is not applied by Stripe.

Up to 100 product lines are shown individually. Larger carts show the first 99
ordered by store/item ID plus one labelled aggregate for the remaining products.
The combined USD total is checked against the payment attempt. Full details remain
in the app review and receipt. Parameters, including image URLs, are saved once;
retries and already-created sessions keep their original summary. Storage URLs must
remain accessible for the session lifetime. Adaptive pricing is disabled;
no tax, discounts, shipping, commission or FX/processor fees are added.

Shared placement validation locks customer, recipient, products, stores and cart
rows in stable order. All store orders and reservations commit together or none do.
Online orders start pending/pending; physical stock is deducted only on confirmed
payment. Local reservation deadline is one hour from creation; Stripe receives
that fixed deadline in the persisted session-creation parameters.

Every unresolved `held` reservation reduces availability, even after its deadline.
COD placement and preview honor this rule. Selected cart lines cannot be
incremented, changed, removed or repurchased through cart checkout while held.
Unselected lines remain editable. Cart admin is inspection-only to prevent deletion
cascades bypassing these guards. Product saves lock the inventory row and reject
stock below unresolved holds; raw SQL/bulk edits remain outside this service contract.
Catalog/cart display stock continues to show physical stock; checkout review is
the authoritative availability check.

Provider network calls run outside database transactions. Session creation uses a
stable attempt key and saved parameters; the returned session ID is persisted
before another network request. On verified completion, one transaction locks the
customer, checkout, attempt, products, stores and reservations, then:

1. Verifies test mode, session/intent IDs, metadata, USD amount and paid intent state.
2. Posts one ledger receipt: debit simulated treasury, credit each positive store share to pending.
3. Consumes holds, decrements physical stock, marks all store orders paid, removes
   frozen purchased cart lines, links the receipt and completes the checkout.

Any failure rolls back all local changes. Retries post once; a second webhook event
for the same payment does not credit again. The legacy stored `Product.sold_count` is unchanged. Browsing APIs calculate
sold units from qualifying order history, so these paid Stripe orders contribute
immediately unless cancelled or successfully refunded in full. Zero-value store shares do not produce zero-amount entries.

Expiry/cancellation releases holds only after current Stripe state confirms an
expired, unpaid session with no unsettled intent. A cancellation racing successful
payment completes the purchase instead. A stale failure event cannot undo payment.

## Records and operational exceptions

PaymentAttempt adds immutable `session_parameters`, saved `checkout_url`, durable
`cancel_requested`, and a protected one-to-one ledger `receipt`. Provider session,
intent and receipt references cannot be reassigned through ordinary model saves.

ProviderEvent stores event ID/environment (unique together), event type, session ID,
attempt reference, optional matched attempt, processing status, receipt/attempt/
completion timestamps and a safe error code. Full card/customer event payloads are
not persisted. `processed` means the notification was handled, not that it reported
a successful payment. Pending/failed events are retried by the scheduled command.

A provider timeout preserves holds. If an unknown creation result cannot be recovered
within 23 hours, automated creation stops rather than risking a new charge after
Stripe's idempotency retention window. Fixed expired creation parameters may also
require manual investigation if no session ID was ever obtained. In both cases the
attempt stays pending and inventory stays reserved; never clear it merely to free stock.
Inspect Stripe's test Dashboard for the attempt reference. A developer can retrieve
the matching session and pass its expanded current state to `apply_session()`;
its validation/locking still applies. If no session can be established, leave the
attempt held until an explicit evidence-based administrative resolution is designed.
See [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests).

Mismatched payment facts, missing reserved stock, or payment after an already
released hold require investigation. No automatic compensation for these payment-mismatch exceptions is implemented.
This differs from the supported customer cancellation-refund flow.
A failure after provider success is shown as processing while local recovery runs.
These are separate systems, not a distributed database transaction.

## Recovery from the unsupported payment-method parameter

New attempts omit `payment_method_types`. Historical attempts retain their immutable
parameters and original Stripe idempotency key. Retrying the original placement
request (or running `reconcile_stripe`) replays creation; a Stripe HTTP 400
`InvalidRequestError` explicitly reporting `payment_method_types` as no longer
supported closes the attempt as failed (`session_creation_rejected`), cancels its
checkout/orders and releases all stock holds in one transaction. No cart lines,
physical stock or ledger entries change. Existing provider/receipt references or
inconsistent reservations prevent release. Other errors, including timeouts,
authentication errors, generic invalid requests and retrieval errors, stay pending.

Placement returns a definitive 400 after this verified closure, including identical
replays. The frontend removes the saved request and requires a fresh review before
using a new key. Saved status remains `cancelled`, so payment pages use their existing
terminal-state handling. Do not edit stored parameters or delete purchase records.
The existing 23-hour unknown-creation guard still applies; older unknown attempts
require evidence-based investigation rather than automatic creation or release.
