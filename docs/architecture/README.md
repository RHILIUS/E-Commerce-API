# How the backend works

The backend is the shop's record keeper and rule checker. A customer uses the
frontend website; the website sends a request to the backend. The backend decides
whether the customer may perform the action, checks the values, and reads or saves
data in PostgreSQL.

For example, Add to Cart asks the backend to save a quantity. It does not reserve
stock. Checkout checks availability again because another customer may have bought
the last item in the meantime.

## Services and their jobs

| Part                             | Job                                                                                   |
| -------------------------------- | ------------------------------------------------------------------------------------- |
| Django and Django REST Framework | Receive HTTP requests and implement the API                                           |
| PostgreSQL                       | Store accounts, products, purchases, stock holds, and accounting records              |
| Redis                            | Carry queued background tasks to workers                                              |
| Celery worker                    | Recover pending Stripe payments and process queued work                               |
| Dedicated email worker           | Deliver registration and password-change codes on the `registration-email` queue      |
| Celery Beat                      | Schedule Stripe payment recovery every 60 seconds                                     |
| Media storage                    | Store uploaded images locally or in configured Azure storage                          |
| Email backend                    | Deliver email through the configured SMTP provider, or print it in local console mode |
| Stripe                           | Host test card payments and report their state                                        |
| Currency-rate provider           | Supply exchange rates for display and checkout review                                 |

A **worker** is a separate process that performs queued work. A **broker** carries
that work between processes; Redis is the broker here. PostgreSQL remains the
source of durable business state. A healthy API does not prove that workers are
running or messages are reaching inboxes.

The exact versions live in [runtime requirements](../../requirements.txt),
[development requirements](../../requirements-dev.txt), and the Docker configuration.
See [local services](../development/docker.md#services-and-storage) for startup order.

## What happens to a request

```text
Frontend → URL routing → API view → serializer and service → database
```

1. Root routing sends the URL to the relevant Django app.
2. Authentication identifies the caller when credentials are supplied. Permissions
   decide whether that caller may use the endpoint.
3. A view coordinates the request. A serializer checks input fields and shapes
   response data. Ownership checks and business rules also live in the appropriate
   view or service; a valid field type alone does not authorize an action.
4. A service performs the business operation, including transactions and locks
   where needed. Models and database constraints protect additional relationships
   and values.
5. The API returns an HTTP status and a JSON body. The frontend displays the result
   or gives the customer a recovery action.

An **API contract** is the agreement about the URL, input, response, and errors.
A **serializer** translates and validates that data. A **model** describes a saved
record. A **migration** changes database structure or existing data in a controlled
order. Applying code alone does not apply its migrations.

The [interactive API guide](../api/README.md) explains `/api/docs/` and
`/api/schema/`. Feature guides include request examples and error contracts.

## How the code is divided

| Location               | Responsibility                                                             |
| ---------------------- | -------------------------------------------------------------------------- |
| `ecommerce_backend/`   | Settings, top-level routing, and Celery configuration                      |
| `apps/authentication/` | Users, personal addresses, registration codes, login, and token validation |
| `apps/account/`        | Profile editing, photos, saved recipients, and password changes            |
| `apps/store/`          | Stores, memberships, business addresses, and public store browsing         |
| `apps/ecommerce/`      | Products, taxonomy, carts, store orders, order items, and history          |
| `apps/checkout/`       | Checkout review and placement, payment attempts, webhooks, and refunds     |
| `apps/inventory/`      | Stock-reservation records                                                  |
| `apps/wallet/`         | Simulated USD accounts and immutable accounting entries                    |
| `core/common/`         | Shared contact validation and other common helpers                         |
| `core/`                | Development seed data and management commands                              |
| `scripts/`, `deploy/`  | Deployment, release, and operational tooling                               |

An app is a related area of Django code, not a separate deployed server. API,
workers, and scheduler use the same backend code in separate processes. Within
apps, `urls` route requests, `views` coordinate them, `serializers` define data
contracts, `services` own business mechanics, and `models` define persisted data.
Some responsibilities are single files; larger ones use packages.

## Account ownership and sign-in

Protected API requests use an access token in `Authorization: Bearer ...`. A
refresh token is stored in an HttpOnly cookie, which browser JavaScript cannot
read. Login and refresh return access tokens and public user data, never the
refresh token in their JSON body.

The backend checks ownership even if the frontend already hides another user's
records. A checkout reference identifies a purchase; knowing it is not permission
to read it. Public product and store endpoints permit guests, but an invalid token
supplied to them can still be rejected.

Registration uses an emailed one-time code and a separate registration cookie.
Password changes invalidate existing sessions through the account's authentication
version. Read [accounts and sign-in](../api/authentication/README.md) for the lifecycle
and exact endpoints.

## The main saved records

A user has a personal address and can save several delivery recipients. Those
addresses are independent after registration. A store has products and members.
A customer can have one cart per store.

A **checkout** groups one customer purchase. It has one **order** per store, and
each order has purchased **items**. Buying from two stores therefore creates one
checkout with two store orders. Checkout completion means COD placement or verified
online payment; it does not mean that either store delivered its items.

Orders keep **snapshots**: copies of the names, recipient details, quantities, and
prices accepted at purchase time. Later profile or catalog edits do not rewrite
those copies. Receipt images are current catalog presentation, so images can
change independently of purchase text and prices.

See [checkout](../api/checkout/README.md) and [model details](checkout-models.md).

## Keeping changes consistent

A **transaction** groups database changes so they succeed together. If part of a
COD purchase fails, its order creation, stock deduction, and cart cleanup roll
back together rather than leaving half an order.

A **row lock** makes competing operations wait before changing the same records.
It matters when two customers want the last item or two requests try to spend the
same balance. PostgreSQL supplies the required row-lock behavior. Model validation
alone does not prevent races, and bulk SQL/ORM writes can bypass model safeguards.

An **idempotency key** lets the backend recognize a repeated purchase request.
After a timeout, the original request may already have succeeded. Retrying the
same body with its original key allows the backend to return that purchase instead
of creating another. Reusing a key with different content is a conflict. This
protection is operation-specific: creating a recipient is not idempotent.

## Payments, stock holds, and accounting

COD deducts stock when placement succeeds and leaves payment unpaid. Stripe test
checkout first creates a saved purchase and stock holds, then asks Stripe to create
a hosted payment session. Physical stock is deducted after verified payment.

A **stock hold** reserves units for an unresolved online purchase. Its deadline
alone is not proof that payment failed; holds remain until provider state permits
safe resolution. Checkout availability subtracts unresolved holds, while catalog
and cart displays still show physical `Product.stock`.

A **webhook** is a message Stripe sends to the backend. Its signature must be
checked. A browser returning from Stripe is not proof of payment. Webhooks,
explicit payment checks, and scheduled recovery all reconcile saved state with
the provider. **Reconciliation** means comparing those facts and applying the
appropriate local result.

A **ledger** is an append-only record of accounting entries. Verified Stripe
payment records each store's pending share in integer USD cents. A confirmed
cancellation refund creates compensating entries; it does not erase the original
payment. These are demo balances, not real bank funds or customer wallet spending.

Database changes can roll back, but an external payment or email cannot be undone
by rolling back PostgreSQL. Provider calls and recovery therefore have explicit
rules. See [Stripe checkout](../api/checkout/stripe.md),
[order refunds](../api/ecommerce/user-orders.md), and [the ledger](wallet-ledger.md).

## Configuration and deployment

Local development uses one backend `.env` for host Django, Compose, and Dev
Containers. Compose supplies internal service addresses. Workstation `.env.deploy`
configures the deployment script; it is separate from application settings and is
not a second Docker environment file. The repository includes example templates,
not the real deployment's secret values.

Production runs Django through Gunicorn behind Nginx on the backend server. The
frontend is hosted separately. Its Cloudflare Pages Function forwards `/api`
requests to the backend, allowing browser cookies to remain on the frontend host.
Media can be served by the backend or configured storage.

Follow [deployment](../operations/deployment.md) for provisioning and operations. The
[shared architecture](../../../docs/architecture/README.md) describes the wider
system; its proposed sections must not be read as additional implemented features.
