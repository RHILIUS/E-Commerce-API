# Development reset and verification

The owner will reset the disposable development database, generate migrations,
apply them and seed. This code change performs none of those operations against
the development database and does not rewrite migration history.

## Fresh migration baseline

If rebuilding initial migrations, remove only numbered/generated Python migration
files from the project's own `apps/*/migrations/` directories, preserving each
`__init__.py`. Coordinate authentication, account, store, ecommerce, checkout and
inventory together because they depend on one another. Leave Django and third-party
package migrations untouched. Resetting the database alone does not reset the
migration files; keeping old files instead requires forward migrations.

Using `python manage.py dbshell` connects to the configured database. For the
explicitly disposable database, the owner can recreate its public schema:

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

Run as the intended schema owner or restore the application's ownership/grants.
This deletes public-schema tables, including Django migration history and data.
It does not delete existing uploaded media or Azure blobs; reseeding a fresh
database uploads images again because the old references are gone.

After removing project migration history and resetting the intended database,
run from the backend root:

```bash
source venv/bin/activate
python manage.py makemigrations authentication account store checkout ecommerce inventory
python manage.py check
python manage.py migrate --plan
python manage.py migrate
python manage.py makemigrations --check
python manage.py import_taxonomy --directory data/taxonomy/2026-08
python manage.py seed
python manage.py test
```

Review generated files before migrating. The model schema requires no historical
backfill on an empty database. Taxonomy must be imported before seeding products.
Fix configured PostgreSQL authentication if login is rejected; do not use fake
migration application to work around a connection or schema problem.

The two old cart/currency data-migration tests skip when their historical nodes
are absent after baseline regeneration. They continue to run when history is kept.
Current model, API, seed and ownership tests continue to run normally.
The concurrent-cart test requires database row-lock support and skips on SQLite.

## Seed purchase behavior

The existing `CUSTOMERS_DATA[*].orders` shape is retained. Each fixture needs a
stable negative order ID, explicit `payment_status` of `unpaid` or `paid`, and
items from exactly one store. Mixed-store fixture orders are rejected before
writes or uploads. The model supports many store orders per checkout; this small
fixture format deliberately seeds one checkout per order, not the full checkout UI.

A deterministic UUID derived from the fixture order ID identifies its checkout
and idempotency key. The checkout is completed COD in the store currency, with
rate 1, a calculated total, saved recipient/name/price snapshots and no rate date.
Payment status is explicit fixture data, not inferred from fulfillment. Paid and
conclusion timestamps are preserved across identical reruns. Reruns reuse order,
item and checkout identities and restore fixture values.

Another customer's reserved ID or an unexpected checkout association stops the
seed transaction. Extra order items are rejected to avoid a total that omits them.
Changing an already populated checkout's payment terms is rejected; use a new
fixture order ID or the planned disposable reset for a changed purchase scenario.
Removed fixture records are not automatically deleted.

These fixtures do not contact Stripe, create payment attempts or reservations,
or change inventory based on historical orders. The product seeder still restores
configured stock and sold counts, as before. Image uploads use configured storage.

## Verification before migrations exist

`manage.py check` can validate model/admin configuration without applying migrations.
Database-backed tests ordinarily require the generated schema. During preparation,
current-model checks may run on an isolated test database with migration modules
disabled; that is not validation of the future generated migration graph.
PostgreSQL concurrency and migrations must be checked after the normal setup above.

### Preparation checks (2026-10-01)

- Django system check and `git diff --check` passed.
- Isolated SQLite tests built the current model schema with migrations disabled:
  158 discovered, 155 passed, 3 skipped (PostgreSQL row locking and two historical
  migration tests). Seed tests mocked image uploads; no live seed was performed.
- PostgreSQL tests could not start because the configured database password was
  rejected. No development schema reset or data writes were performed.
- A non-writing migration check against the retained history stopped at the new
  required `Order.checkout` field. This is expected until the owner generates the
  fresh initial baseline. No migration files were generated or deleted.
