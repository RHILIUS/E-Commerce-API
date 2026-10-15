# Reset a disposable development environment

`reset_dev` rebuilds the PostgreSQL `public` schema and deletes all configured
uploaded media, then runs migrations, imports taxonomy and seeds development data.
Existing `seed` and `import_taxonomy` commands remain independently available.

Enable explicitly in the backend environment:

```dotenv
ALLOW_DEV_RESET=True
```

Leave this disabled in production and any environment containing valuable data.
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

See the maintained [business-process diagram](../../docs/diagrams/specific-business-processes/development/reset-development.md).
