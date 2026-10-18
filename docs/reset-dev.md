# Reset a disposable development environment

`reset_dev` rebuilds the PostgreSQL `public` schema and deletes all configured
uploaded media, then runs migrations, imports taxonomy and seeds development data.
Existing `seed` and `import_taxonomy` commands remain independently available.

Enable explicitly in the backend environment:

```dotenv
ALLOW_DEV_RESET=True
```

Leave this disabled in environments containing valuable data. A deployed
disposable portfolio may explicitly opt in; use the
[production demo reset wrapper](#one-command-disposable-demo-reset)
so active services and old broker jobs are handled before rebuilding.
Stop API servers and workers before running the command. Ensure the database and
entire media container/directory are dedicated to disposable application data.

```bash
python manage.py reset_dev --dry-run
python manage.py reset_dev
# Automation: skips the RESET prompt, but still requires ALLOW_DEV_RESET=True.
python manage.py reset_dev --no-input
# Optional explicit local taxonomy snapshot:
python manage.py reset_dev --directory data/taxonomy/2026-08
```

For Docker Compose, stop the API first, then use a one-off container with the same
configuration and media volume. Enable the setting in `.env` before running:

```bash
docker compose -f compose.dev.yaml stop api
docker compose -f compose.dev.yaml run --rm --no-deps api python manage.py reset_dev
# Restart only after successful completion:
docker compose -f compose.dev.yaml start api
```

The database must already exist, PostgreSQL must be running, and the connection
user must be able to drop `public` and recreate it. Only the default connection,
sole user schema `public`, and default public search path are supported. Schema
objects, migration history and custom schema grants are removed. The recreated
schema belongs to the connection user. Migration source files remain intact.

Local deletion is restricted to the backend's `media/` directory, preserves that
directory, and does not follow child symlinks. Azure deletion removes current
blobs and snapshots from the entire configured container, preserving the
container. Soft-delete policies and historical versions can retain recoverable
content; this is not a permanent Azure history purge. Source images in
`static/seed_images/` remain available for reseeding. Unsupported storage backends
are refused. No new dependencies or API contracts are introduced.

Preflight checks configuration, snapshot content, seed assets, schema layout and
storage read access before deletion. Dry run does not prove delete/create
permissions. The command prints targets without credentials. Interactive runs
require the exact confirmation `RESET`; cancellation deletes nothing.

A session advisory lock prevents concurrent reset commands. It does not block
application traffic, so stopping servers/workers is an operator responsibility.
Schema drop/recreation share one transaction; the overall workflow does not.
Subsequent storage, migration or seed failures can leave a partially reset
environment. The command stops at the failed stage and reports the cause. Inspect
the targets, fix the cause and rerun the whole reset. Do not restart the application
until it succeeds. No backup, automatic retry, or stage-resume facility is provided.
External payment objects, caches and sent email are outside reset scope.

## Disposable demo reset opt-in

Production settings inherit `ALLOW_DEV_RESET` from the shared `.env` (default
`False`). Set it to `True` only for the disposable portfolio database and media.
The reset wrapper rebuilds the required images and recreates application services.
For other environment-only changes, containers need recreation;
`docker compose restart` does not reload `.env`. Recreation preserves named volumes
and does not itself reset data.

The ordinary API image excludes demo assets. The optional `maintenance` image
target includes taxonomy snapshots and seed images; use the reset wrapper below. Stop API traffic, workers and Beat before any reset, use
a one-off container, inspect `--dry-run`, and restart only after success. The
command deletes the public schema and configured uploaded media before rebuilding
and seeding; the complete operation is not atomic.

## One-command disposable demo reset

The executable wrapper automates build, preflight, confirmation, service shutdown,
queue cleanup, reset and restart. It uses `ecommerce-backend-prod` explicitly and
works from any directory. It does not affect the development Compose project.

Run from the backend directory:

```bash
./scripts/reset-demo.sh --dry-run
./scripts/reset-demo.sh
```

Set `ALLOW_DEV_RESET=True` in the backend `.env` for this disposable environment.
The wrapper rebuilds API and maintenance images, checks Compose, requires running
PostgreSQL/Redis, refuses concurrent release jobs, and runs the existing command's
preflight. Dry run does not stop services, clear queues, or reset data, but builds
images and opens a temporary preflight container. It checks media read access and
fixture/schema validity; it does not prove destructive write permissions.

Inspect the database/media targets printed by preflight. The exact confirmation
is `RESET ecommerce-backend-prod`. Cancellation leaves services and data alone.
After confirmation the script stops Nginx, API, both workers and Beat, allowing up
to 120 seconds per stop operation. It then clears the stack's dedicated Redis DB 0
(all queued/reserved broker state), and runs `reset_dev --no-input` in a temporary
maintenance container. The command repeats preflight before deletion, takes a
PostgreSQL advisory lock, resets the schema, clears configured media, migrates,
imports taxonomy and seeds. Azure mode clears the entire configured Azure
container; filesystem mode clears the shared `/app/media` volume contents.

On success, the script starts API/workers/Beat without dependency release jobs,
waits for API socket readiness and container-running state, then starts Nginx.
Worker-running state does not prove queue processing or email delivery. Verify
catalog, images, login, and payment behavior afterward. Previously issued JWTs are not cryptographically revoked by resetting tables;
reused user IDs can match fixtures. Before resetting a publicly accessible demo,
rotate `SECRET_KEY` in `.env` so restarted services reject old signatures. Clear
browser authentication and checkout recovery state afterward.
Existing superusers and uploaded data are deleted; fixture accounts have known
credentials. Review/secure these before exposing a public demo.

Failure after shutdown leaves traffic stopped until the reset succeeds. Restart
failures may leave some internal services running, while Nginx remains stopped
unless it already started. Inspect status/logs, fix the failure and rerun the
wrapper; do not manually reopen traffic against a partially rebuilt schema. An
interrupt or machine outage requires the same operator check. There is no global
rollback: schema recreation is transactional, subsequent media/migration/seed
steps are not. Never use this on valuable customer data.

The host `flock` lock prevents concurrent wrapper runs from the same checkout;
the management command also prevents concurrent resets of the same database.
The auto-deployment script shares this lock. Manual Docker commands and other
operators can still start services mid-reset.
Coordinate deployments and use one operational checkout. Redis is dedicated to
this Compose project; do not repoint it at a shared broker. External Stripe
sessions, sent email and browser state are not reset. Static assets are unchanged,
and Docker volumes are preserved (their application contents are rebuilt).

The maintenance service is excluded from ordinary `up` unless its profile is
explicitly enabled. Its default command is a dry run. The normal API image excludes the fixture assets. See the
[reset wrapper implementation](../scripts/reset-demo.sh) for command ordering.
