# Production backend image

## Current scope

`Dockerfile.prod` builds the backend runtime image for the proposed demo stack.
It installs runtime dependencies including Gunicorn 26.2.0 and runs as UID/GID 10001. It copies only Django runtime source, not environment files, development
tools, or local media. API, workers, Beat, and migration jobs will reuse this
image with different commands. No migrations, SMTP delivery, Stripe operations,
or database initialization happen during the build.

`compose.prod.yaml` now defines the backend, Nginx TLS proxy, private PostgreSQL
and Redis, both workers, Beat, migrations, and static collection. The frontend
is hosted separately. Nginx serves API/admin routes, Django static assets, and
filesystem media; other paths return 404.

## Build and inspect

Run from the backend repository root:

```bash
docker build -f Dockerfile.prod -t ecommerce-backend-prod:local .
docker run --rm ecommerce-backend-prod:local id
docker run --rm ecommerce-backend-prod:local gunicorn --version
docker run --rm ecommerce-backend-prod:local python -m pip check
```

Gunicorn defaults to one synchronous worker. Both Celery workers use concurrency
one, preserving the dedicated registration-email queue. Tune the worker count after
measuring the complete stack's memory and load. `GUNICORN_CMD_ARGS` supports
additional settings. Access logging is not enabled by default; avoid logging
credentials or sensitive request data when configuring observability.

## Environment and release behavior

Retain the single backend `.env` convention. Compose fixes internal database and
broker addressing and selects `settings_production`, which forces `DEBUG=False`,
inherits the explicit `ALLOW_DEV_RESET` environment opt-in, configures static
collection and trusts the HTTPS
header overwritten by private Nginx. Existing Brevo/Azure credentials remain in
`.env`. Production settings do not modify development settings or its volumes.

Only Nginx publishes ports, bound to loopback by default: HTTP 8080 redirects to
HTTPS 8443. The project is `ecommerce-backend-prod`, separate from
`ecommerce-backend-dev`; PostgreSQL, Redis, static and media volumes are distinct.
There is no pgAdmin service. This is a new empty database, not a copy of development.
The API health check verifies its listening socket only, not database or workers.
Restart policies do not automatically restart containers merely marked unhealthy.

Migrations wait for database readiness; API/workers/Beat wait for successful
migrations and healthy Redis. Nginx waits for the API and successful static
collection. Failed jobs block dependent startup. Jobs do not roll back releases
or schema changes; inspect logs and fix failures rather than deleting volumes.
Do not run multiple Beat instances. Volumes survive container removal but do not
protect against host loss.

## Configure and run the backend

Run backend commands from the backend repository root. Keep the existing `.env`;
do not overwrite it from the template. Configure database credentials,
`ALLOWED_HOSTS`, exact frontend `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`
where applicable, and `STRIPE_FRONTEND_ORIGIN` for the separately hosted frontend.
`PUBLIC_HTTPS_ORIGIN` controls the proxy's HTTP-to-HTTPS redirect; it no longer
sets frontend return URLs automatically. Use the domain/authentication layout in
the central architecture document before publishing. Brevo and the selected
media backend continue to use their existing environment settings.

Provide the certificate chain and key at `deploy/certs/fullchain.pem` and
`deploy/certs/privkey.pem`. The files must match the configured API hostname and
be trusted by clients. They remain ignored by Git and excluded from image builds.
Certificate provisioning/renewal is external to Compose. The base configuration
retains loopback bindings on ports 8080/8443 until explicitly configured for public
hosting; these are API ports, not frontend hosting.

```bash
docker compose -f compose.prod.yaml config --quiet
docker compose -f compose.prod.yaml build api
docker compose -f compose.prod.yaml up -d
docker compose -f compose.prod.yaml ps -a
```

`migrate` and `collectstatic` exiting successfully is expected. First startup runs
migrations on this project's database; development accounts and product data are
not copied. Create an administrator if needed:

```bash
docker compose -f compose.prod.yaml exec api python manage.py createsuperuser
```

Swagger is served at the API origin's `/api/docs/`, admin at `/admin/`, and local
filesystem media at `/media/`. SPA routes such as `/login` belong to the separate
frontend host and are not served by backend Nginx. The 10 MB request-body limit
may reject larger uploads before Django; adjust it only when required.

To inspect or stop the backend:

```bash
docker compose -f compose.prod.yaml logs --tail=100 api nginx migrate collectstatic worker email-worker beat
docker compose -f compose.prod.yaml exec nginx nginx -t
docker compose -f compose.prod.yaml exec api python manage.py check --deploy
docker compose -f compose.prod.yaml down
```

`down` preserves named volumes; do not add `-v` unless intentionally discarding
this stack's data. Redact operational/customer data in logs before sharing. Do
not paste `.env` or resolved Compose output, which contain credentials.

## Before public hosting

The current defaults intentionally listen only on localhost. Public deployment
requires a domain, trusted publicly valid TLS certificates and renewal, appropriate
allowed/CORS/CSRF origins, a Stripe frontend origin, firewall rules, and deliberate
`PROD_BIND_ADDRESS`, `PROD_HTTP_PORT`, `PROD_HTTPS_PORT`, and
`PUBLIC_HTTPS_ORIGIN` values. Do not use local certificates publicly. Set a strong
unique Django secret; review `check --deploy` findings. HSTS remains deferred until
the domain and HTTPS setup are verified. Images currently use PostgreSQL 17, Redis
7 and Nginx stable tags; record/pin tested image digests for public releases.

Rebuild after code changes. On later releases, explicitly run migrations and
static collection with the new image and recreate all application consumers;
plain `restart` does not pick up a new image or changed environment. Schema
changes and rollback need release-specific review. This is single-host Compose,
not a zero-downtime or high-availability deployment.

See the maintained [production architecture](../../docs/architecture/production-deployment.md).
The link assumes the documented sibling repository layout.

## Recorded validation

- Production image build passed.
- Gunicorn reports version 26.2.0; runtime UID/GID is 10001.
- `python -m pip check` and `python manage.py check` passed.
- No `.env*` files were present under `/app`.
- Checks used dummy settings and disabled container networking. Database-backed
  tests, serving HTTP, workers, Nginx, and external integrations were not tested
  in this image-only step.

### Compose configuration validation

Docker Compose v5.0.2 validated the production file. Static assertions checked
project/volume isolation, private service ports, production settings selection,
and internal broker addressing. Production settings Python syntax and Git whitespace checks passed. Runtime
startup, Nginx `-t`, TLS/certificate trust, database migrations, and browser/integration
tests are deliberately left for the project owner to run.

## Disposable demo reset opt-in

Production settings now inherit `ALLOW_DEV_RESET` from the shared `.env` (default
`False`). Set it to `True` only for the disposable portfolio database and media.
The first update requires rebuilding the API image and recreating application
containers. Later environment-only changes need recreation, not image rebuilding;
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
explicitly enabled. Its default command is a dry run. The normal API image still
excludes the roughly 230 MB of fixture assets. See the maintained
[reset process](../../docs/diagrams/specific-business-processes/development/reset-development.md).

### Backend-only configuration cleanup

The local frontend Compose override, its reset-script option, and the Nginx SPA
mount/fallback have been removed. Frontend hosting and build-time API configuration
are managed separately. This cleanup does not stop/recreate running containers,
change `.env`, remove certificate files, or modify database/media volumes. To
apply it to an existing stack, explicitly recreate Nginx using only
`compose.prod.yaml`; existing containers retain old mounts until recreated.

## Automated EC2 releases

See [EC2 Auto Deploy on origin main changes](github-actions-deployment.md) for the
GitHub Actions workflow, dedicated SSH identity, server prerequisites and recovery.
It builds before downtime, stops application traffic for migrations/static collection,
and recreates application services while preserving PostgreSQL and Redis containers.
