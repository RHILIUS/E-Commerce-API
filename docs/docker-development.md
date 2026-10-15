# Docker development

This guide covers the backend development stack defined in
[`compose.dev.yaml`](../compose.dev.yaml) and [`Dockerfile.dev`](../Dockerfile.dev).
The frontend runs locally. Production containerization is a separate implementation.
Celery integration, scheduled payment/refund recovery, and seven-day settlement are
not implemented by this setup.

## Services and storage

| Service   | Purpose                                                               | Access                           |
| --------- | --------------------------------------------------------------------- | -------------------------------- |
| `db`      | PostgreSQL 17 development database                                    | Compose network: `db:5432`       |
| `redis`   | Redis 7 with append-only persistence; reserved for future integration | Compose network: `redis:6379`    |
| `migrate` | One-shot Django migrations                                            | Exits after successful migration |
| `api`     | Django development server with source reload                          | <http://localhost:8000>          |
| `pgadmin` | Database administration UI                                            | <http://localhost:5050>          |

PostgreSQL readiness gates migrations; successful migrations gate API startup.
pgAdmin waits for PostgreSQL readiness. Redis has its own health check but is not
currently consumed by Django or Celery. A successfully exited `migrate` container
is expected, not an application failure.

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

For optional Stripe checkout, explicitly set `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, and `STRIPE_FRONTEND_ORIGIN`; use test credentials only.
Keep the two keys explicitly empty when checkout is unused. Containerization does
not configure Stripe webhook forwarding: follow the
[Stripe setup guide](checkout/stripe-api.md) for the CLI/webhook configuration.

Keep `USE_AZURE_STORAGE=False` for persistent filesystem uploads and the console
email backend for local email testing. Redis remains reserved for future
integration; current Django settings do not consume a broker URL.

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
docker compose -f compose.dev.yaml logs -f api migrate

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

## VS Code Dev Container

The shared configuration in [devcontainer.json](../.devcontainer/devcontainer.json)
connects VS Code to the existing `api` service and starts its database/migration
dependencies, Redis, and pgAdmin. It uses the same Compose project and volumes as
terminal startup. The frontend remains local. There is no Celery service.

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
