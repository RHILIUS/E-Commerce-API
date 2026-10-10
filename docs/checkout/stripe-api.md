# Stripe test checkout

Implemented: one USD hosted Checkout Session for a multi-store purchase, durable
stock holds, signed webhook inbox, owner-only status/reconciliation/cancellation,
and atomic pending store-wallet credits. Uses `stripe==16.0.0`. This integration
accepts test keys/events only. Delivery, settlement release, returns and withdrawals
remain separate future features. No real funds or connected accounts are involved.

## Local setup

From the backend repository, install requirements and apply migrations:

```bash
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
```

Set these values in backend `.env` (never in frontend variables or Git):

```dotenv
STRIPE_SECRET_KEY=sk_test_your_test_secret
STRIPE_WEBHOOK_SECRET=whsec_your_endpoint_signing_secret
STRIPE_FRONTEND_ORIGIN=http://localhost:5173
```

Use the origin where this frontend actually runs. HTTPS is required except for
localhost/127.0.0.1/::1 development. Do not change Stripe test accounts while
attempts are pending. No frontend Stripe SDK or publishable key is required for
this hosted redirect flow.

For local webhook forwarding, use Stripe CLI authenticated to that same test account:

```bash
stripe listen --events checkout.session.completed,checkout.session.expired,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed --forward-to localhost:8000/api/shop/stripe/webhook/
```

Use the signing secret printed by this listener for `STRIPE_WEBHOOK_SECRET`, then
restart Django. In that test account/sandbox's Dashboard payment-method settings,
enable Cards and disable other methods for this cards-only demo. Checkout uses
Dashboard-managed dynamic payment methods; do not send `payment_method_types`
with API version 2026-09-30.endive. See
[Stripe migration guidance](https://docs.stripe.com/payments/payment-methods/dynamic-payment-methods).
A Dashboard endpoint has its own signing secret. Run the backend
and frontend normally. Choose **Card (Stripe test)**, review the USD total, continue
to payment, then open Stripe. Use Stripe test card `4242 4242 4242 4242`, a future
expiry and any three-digit CVC. See [Stripe testing](https://docs.stripe.com/testing)
and [webhook setup](https://docs.stripe.com/webhooks).

The callback page never assumes a redirect means paid. Webhooks or explicit
provider reconciliation establish completion. Verify two store orders become paid,
stock decreases once, and each store's pending account receives its exact share.
Inspect PaymentAttempt, ProviderEvent and wallet journal records in Django admin.

## Recovery scheduling is required

Schedule the following management command every minute in the deployment's job
scheduler, using the same settings/environment as Django:

```bash
python manage.py reconcile_stripe --limit 100
```

Each run retries unfinished inbox events and pending attempts in bounded batches.
Failed attempts/events remain in PostgreSQL; row ordering rotates attempted work.
Overlapping runs are safe through row locks and unique payment posting keys.
This phase does not require Celery or Redis. The later settlement scheduler can
invoke the same recovery services from Celery when that infrastructure is added.
Without a scheduled runner, webhooks and the customer's **Check payment status**
button can reconcile payments, but abandoned-payment recovery is not automatic.

## API contract

All purchase endpoints require Bearer authentication and scope references to the
requesting customer. UUID references do not confer access. Webhooks instead require
a valid `Stripe-Signature` over the exact raw body and reject live-mode events.

| Method and route | Request | Success |
| --- | --- | --- |
| `POST /api/shop/checkout/preview/` | Existing product/cart selection plus optional `payment_method` (`cod` default, or `stripe`) | Existing preview shape; Stripe reviews are USD and tokens are bound to the chosen method. |
| `POST /api/shop/checkouts/stripe/` | `preview_token`, `recipient_id`, `payment_method: "stripe"`, UUID `idempotency_key` | 200 payment state; same request/key returns the same purchase. |
| `GET /api/shop/checkouts/{reference}/payment/` | None | 200 saved payment state, no Stripe calls. |
| `POST /api/shop/checkouts/{reference}/payment/` | Empty | 200 reconciled payment state. |
| `POST /api/shop/checkouts/{reference}/payment/cancel/` | Empty | 200 current payment state after cancellation request/reconciliation. |
| `GET /api/shop/checkouts/{reference}/` | None | Existing historical confirmation shape, including pending or completed online orders. |
| `POST /api/shop/stripe/webhook/` | Raw Stripe event JSON and signature header | 200 `{"received": true}` after durable inbox receipt; unsupported event types are ignored. |

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
COD placement and preview now honor this rule. Selected cart lines cannot be
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
for the same payment does not credit again. `sold_count` is unchanged because it
means delivered units. Zero-value store shares do not produce zero-amount entries.

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
released hold require investigation. No automatic refund/compensation is implemented.
A failure after provider success is shown as processing while local recovery runs.
These are separate systems, not a distributed database transaction.


## Verification on 2026-10-10

- Full backend suite passed on isolated PostgreSQL 16: 261 tests, two existing
  historical-migration skips. Three subsequent recovery/inventory regressions
  were added; the resulting 20-test Stripe suite passed, including two real
  PostgreSQL concurrency tests. Stripe HTTP requests were mocked.
- Raw webhook signature verification used the real Stripe SDK with test secrets.
- Django system checks, migration drift/plan and OpenAPI validation passed.
- Frontend: all 109 tests passed using `npm run test:run -- --maxWorkers=1`;
  TypeScript/production build, lint and changed-file formatting checks passed.
- Chromium: 40 state/layout checks across 330, 375, 768, 1024 and 1440px, both
  themes, a 375×400 short viewport, keyboard payment selection, cancellation and
  verified completion. APIs were mocked; screenshots were visually inspected.

No Stripe test key or webhook signing secret was configured in the agent environment,
so a real hosted Stripe test-account payment was not exercised. The new migration
was tested on an isolated database, not applied to the development database.

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

## Product-summary verification

On 2026-10-10, all 85 checkout/API regression tests passed on isolated PostgreSQL,
including 30 Stripe tests. Coverage includes default image selection, local-image
omission, saved parameters surviving catalog edits, exact totals for 100/101/500
product lines, zero-price items and rejection of mismatched totals. Provider calls
were mocked; the hosted Stripe display was not exercised in this verification.
