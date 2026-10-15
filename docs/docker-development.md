# Docker development

This guide covers the backend development stack defined in
[`compose.dev.yaml`](../compose.dev.yaml) and [`Dockerfile.dev`](../Dockerfile.dev).
The frontend runs locally. Production containerization is a separate implementation.
Celery workers deliver registration and password-change OTP emails and, with Beat, provide scheduled Stripe payment recovery. Scheduled refund
recovery and seven-day settlement are not implemented by this setup.

## Services and storage

| Service   | Purpose                                                               | Access                           |
| --------- | --------------------------------------------------------------------- | -------------------------------- |
| `db`      | PostgreSQL 17 development database                                    | Compose network: `db:5432`       |
| `redis`   | Redis 7 broker with append-only persistence | Compose network: `redis:6379`    |
| `migrate` | One-shot Django migrations                                            | Exits after successful migration |
| `api`     | Django development server with source reload                          | <http://localhost:8000>          |
| `worker`  | Celery worker executing Stripe payment recovery | Compose network only |
| `email-worker` | Dedicated verification OTP delivery worker | Compose network only |
| `beat`    | Celery scheduler dispatching payment recovery every 60 seconds | Compose network only |
| `pgadmin` | Database administration UI                                            | <http://localhost:5050>          |

PostgreSQL readiness gates migrations; successful migrations gate API startup.
pgAdmin waits for PostgreSQL readiness. Worker and Beat wait for healthy Redis
and successful migrations. API, migrations, worker and Beat reuse the same backend
image in separate containers. A successfully exited `migrate` container is expected,
not an application failure.

The backend source directory is mounted at `/app`. Python edits trigger Django's
reload; dependency changes require an image rebuild. The development image installs
`requirements-dev.txt`, which includes the runtime requirements, and runs as the
non-root `vscode` user. Named volumes preserve
PostgreSQL data (`postgres_data`), Redis data (`redis_data`), uploaded files
(`media_data`), and pgAdmin settings (`pgadmin_data`). The media volume overlays
`/app/media`: existing host media files are not automatically imported.

This creates an independent development database. Existing host PostgreSQL data
is not automatically imported. Database and Redis ports are not published to the
host; API and pgAdmin ports are bound to localhost.

## First start

Install Docker Engine or Docker Desktop with Compose and ensure the daemon is
running and accessible. Check:

```bash
docker --version
docker compose version
```

Run all subsequent commands from the backend repository root. Copy the template
without overwriting an existing configuration:

```bash
cp -n .env.example .env
```

Edit `.env`: replace the Django secret and database/pgAdmin password
placeholders. Keep `DEBUG=True` for local development. The same `.env` is used by host
Django, Compose and VS Code. Compose overrides `DB_HOST=db` and `DB_PORT=5432`
inside backend containers; host Django uses the `.env` host and port. The Docker
database is not exposed to the host, so host Django needs a separately reachable
PostgreSQL instance. PostgreSQL initializes its database and login from `DB_NAME`,
`DB_USER`, and `DB_PASSWORD` only when its data volume is empty. Editing those values later
does not change the existing database or login.

For optional Stripe checkout, set `STRIPE_SECRET_KEY` to a test key and
`STRIPE_FRONTEND_ORIGIN` to the actual frontend origin. Local payment recovery
uses Celery without webhook delivery; `STRIPE_WEBHOOK_SECRET` can remain empty
for that workflow. Deployment webhook configuration is described in the
[Stripe setup guide](checkout/stripe-api.md).

Set `CELERY_BROKER_URL=redis://redis:6379/0` in `.env` for the container services.
The hostname `redis` resolves inside Compose; Redis is not exposed to host Django.
Keep `USE_AZURE_STORAGE=False` for persistent filesystem uploads and the console
email backend for local email testing.

`.env` is ignored by Git and excluded from image builds by `.dockerignore`.
Compose reads it automatically for interpolation and passes it into backend
containers with `env_file`. Django reads the same file during host execution.
Exported shell variables take precedence over Compose interpolation, so clear
unwanted `DB_NAME`, `DB_USER` and `DB_PASSWORD` exports before starting the stack.
The explicit backend `environment` entries keep API/migrations and PostgreSQL
aligned even when shell overrides are used.

If upgrading from the former two-file setup, keep existing `.env` credentials
that match the initialized database, copy any missing settings such as
`PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` from the old Docker file,
and retire that old file. Do not reset volumes to switch configuration files.
Changing credentials in `.env` does not update an existing PostgreSQL role.
Recreate containers after environment changes using `up -d --force-recreate`;
`restart` alone does not reload their environment. Named volumes are preserved.

Validate without printing resolved credentials, then start:

```bash
docker compose -f compose.dev.yaml config --quiet
docker compose -f compose.dev.yaml up --build
```

Compose automatically loads the backend `.env`; no environment-file flag is
required. Open <http://localhost:8000/api/docs/>.
Use `up -d --build` instead to run in the background. No host Python virtual
environment activation is needed for container commands.

## Local frontend

Run the frontend with its existing development command. Set its environment to:

```dotenv
VITE_API_URL=http://localhost:8000
```

Restart the frontend after environment changes. Match its actual origin in
`CORS_ALLOWED_ORIGINS` and `STRIPE_FRONTEND_ORIGIN`; the template assumes
`http://localhost:5173`. Browser requests use localhost, not the Compose service
name `api`.

## pgAdmin

Log in at <http://localhost:5050> using `PGADMIN_DEFAULT_EMAIL` and
`PGADMIN_DEFAULT_PASSWORD`. These are separate from the PostgreSQL credentials.
Register a server with the following connection settings:

| Setting              | Value                  |
| -------------------- | ---------------------- |
| Name                 | E-Commerce Development |
| Host                 | `db`                   |
| Port                 | `5432`                 |
| Maintenance database | Value of `DB_NAME`     |
| Username             | Value of `DB_USER`     |
| Password             | Value of `DB_PASSWORD` |

Use `db`, not localhost: pgAdmin connects from its own container. Its volume
preserves registration and account settings. Initial administrator environment
variables do not reset an account already stored in that volume.

## Everyday commands

```bash
# Build the backend image without starting services
docker compose -f compose.dev.yaml build

# Start services using existing images
docker compose -f compose.dev.yaml up -d

# Build and start services in one command
docker compose -f compose.dev.yaml up -d --build

# Service status and logs
docker compose -f compose.dev.yaml ps -a
docker compose -f compose.dev.yaml logs -f api migrate worker beat

# Django checks and tests
docker compose -f compose.dev.yaml exec api python manage.py check
docker compose -f compose.dev.yaml exec api python manage.py test

# Administrator account
docker compose -f compose.dev.yaml exec api python manage.py createsuperuser

# Apply new migrations while the API is running
docker compose -f compose.dev.yaml exec api python manage.py migrate

# Rebuild after requirements.txt or requirements-dev.txt changes
docker compose -f compose.dev.yaml up -d --build

# Stop and remove containers while retaining named volumes
docker compose -f compose.dev.yaml down
```

## Celery payment recovery

The Celery application is configured in `ecommerce_backend/celery.py` and imported
by the project package. Django settings read `CELERY_BROKER_URL` and define
`reconcile-stripe-every-minute`. Every 60 seconds, Beat dispatches
`apps.checkout.tasks.reconcile_stripe_payments` through Redis. The worker invokes
`reconcile_stripe` with `limit=100`; see the [recovery contract](checkout/stripe-api.md#scheduled-payment-recovery).
No Celery result backend is configured.

Run only one Beat instance for this schedule. Its schedule file is
`/tmp/celerybeat-schedule`, writable by the container user. It survives a restart
but is lost when the container is recreated; pending business state remains in
PostgreSQL and is scanned again after startup.

```bash
# Start the scheduler and worker with their dependencies
docker compose -f compose.dev.yaml up -d worker beat

# Reload task code or schedule changes; these processes do not auto-reload
docker compose -f compose.dev.yaml restart worker beat

# Inspect execution
docker compose -f compose.dev.yaml logs --tail=100 worker beat
```

After `.env` changes, recreate affected containers rather than only restarting:

```bash
docker compose -f compose.dev.yaml up -d --force-recreate api worker beat
```

Dependency or Dockerfile changes require rebuilding the image. A successful task
means the command returned; inspect its unresolved-operation count and durable
payment state too. Command output may appear at WARNING level even on success.

## VS Code Dev Container

The shared configuration in [devcontainer.json](../.devcontainer/devcontainer.json)
connects VS Code to the existing `api` service and starts its database/migration
dependencies, Redis, pgAdmin, the Celery worker and Beat. Its `runServices` list
starts payment recovery automatically. It uses the same Compose project and volumes
as terminal startup. The frontend remains local.

1. Install Microsoft's **Dev Containers** extension in your local VS Code.
2. Configure `.env` as described above.
3. Open the backend repository in VS Code (in WSL for a WSL checkout).
4. Select **Dev Containers: Reopen in Container**. Use **Dev Containers: Rebuild
   and Reopen in Container** when replacing a previously created configuration.

[The Dev Container Compose wrapper](../.devcontainer/compose.yaml) includes the
existing development stack and explicitly supplies the same backend `.env` for
interpolation, including when VS Code runs from another directory.
It uses Compose's `include` support (Compose 2.20.0 or later), so no launcher,
`COMPOSE_ENV_FILES` export, or copied credentials are needed. The included file
retains its backend-relative build and mount paths, project name, and volumes.
The normal terminal workflow automatically loads `.env`. Exported
shell variables can override file values; clear unwanted database overrides before
opening VS Code.

Python and Pylance extensions are configured for the container, with interpreter
`/usr/local/bin/python`. Python tooling's separate `.env` loading is disabled so
terminal/debug settings use the container environment. In the container terminal:

```bash
whoami
python -m pip show Django django-stubs
python manage.py check
```

Expect `vscode` as the user. Run Django commands directly here without host virtual
environment activation or `docker compose exec`. Edits are saved into the host
repository. Git is installed in the image for editor source-control tooling.

Closing VS Code leaves the Compose services running (`shutdownAction: none`).
Stop them using the existing `down` command from a host terminal. Use **Dev
Containers: Rebuild Container** after changing dependencies or container settings.
For terminal-only startup on Linux with a non-1000 host user, export `DEV_UID` and
`DEV_GID` from `id -u` and `id -g` before building; defaults are 1000.

Existing root-owned files or a media volume created by the earlier root image do
not automatically change ownership. If uploads fail with permission errors,
inspect ownership and correct only the affected development files/volume; do not
reset the database or all volumes to resolve a file permission issue.

References: [VS Code Compose integration](https://code.visualstudio.com/docs/devcontainers/create-dev-container)
and [Compose environment selection](https://docs.docker.com/compose/how-tos/environment-variables/envvars/).

### Shared image builds

Only `api` declares the backend build. The `migrate` service reuses the same local
image with `pull_policy: never`; it does not build or pull a second copy. This
avoids parallel exporters competing for the same image tag. The existing
`up --build` and separate `build` then `up` commands remain valid. Before running
`migrate` alone on a fresh machine, build the image with the documented `build`
command first.
### Git over SSH

The development image includes Git and `openssh-client` for SSH remotes. After
changing the image, run **Dev Containers: Rebuild Container**. A missing SSH
executable causes `cannot run ssh: No such file or directory` before Git can
attempt authentication.

Dev Containers can forward a running host SSH agent. Load your existing key in
the host/WSL agent before opening VS Code, then check `ssh-add -l` in the container
terminal to confirm agent access. Private keys should remain on the host, not in
the repository or image. If authentication still fails, inspect agent forwarding
and the Git remote; installing the client alone does not supply credentials.
See [sharing Git credentials](https://code.visualstudio.com/remote/advancedcontainers/sharing-git-credentials).

## Registration email worker

Compose starts `email-worker` for registration and password-change OTPs on the
existing `registration-email` queue, separately from
payment recovery. For host development run:

```bash
celery -A ecommerce_backend worker --loglevel=info --queues=registration-email --concurrency=2
```

Restart workers after source changes. Production must provision a consumer for
this queue, monitor queue age and terminal delivery warnings, restrict Redis
access and use encrypted broker transport where required. Task argument display
is redacted and results are disabled, but the broker payload contains the OTP
and destination address. Do not enable payload logging or retain broker dumps
without appropriate access controls. OTP tasks expire and check database validity
before each delivery attempt. Broker acceptance does not establish worker health
or inbox delivery. SMTP must have a finite connection timeout (`EMAIL_TIMEOUT`).
