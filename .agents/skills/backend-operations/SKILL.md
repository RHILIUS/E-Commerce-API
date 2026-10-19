---
name: backend-operations
description: Maintain backend environment, container, deployment, and disposable-reset configuration while preserving persistent data and operational safeguards.
---

# Backend operations

Apply this workflow when maintaining backend environments, Docker/Dev Container configuration, deployment tooling, or disposable-demo operations. Repository paths in backticks are relative to the backend root. Use the current configuration and relevant operational guides for the actual commands and target; do not assume environment values from a previous task.

## Environment conventions

Use one backend `.env` for host Django, Docker Compose and VS Code Dev Containers.
Keep `.env.example` as the shared template. Compose supplies internal connection addressing (`DB_HOST=db`, `DB_PORT=5432`);
production Compose also fixes the internal Celery broker URL and selects
`ecommerce_backend.settings_production` for HTTPS proxy and deployment settings.
Never introduce a second environment file for Docker. `.env.deploy` is workstation-only
SSH/provisioning configuration for `scripts/deploy.sh`, not a Docker environment file;
the server still uses only the backend `.env`. Existing PostgreSQL volume credentials are not
changed by editing `.env` or recreating containers.

## Disposable demo maintenance

`scripts/reset-demo.sh` uses production Compose and the explicit maintenance image
for disposable portfolio resets. `--dry-run` checks prerequisites without
stopping services or deleting data. Keep
seed inputs in the maintenance target, not the normal API target. Never run a
reset or clear broker tasks as part of routine verification; use mocked wrapper
tests unless the user explicitly authorizes execution against disposable data.

## Scope and checks

A request to edit documentation or configuration does not authorize deployment, reset, broker cleanup, or execution against an unrelated environment. Preserve the task's authorized scope and existing approval; do not introduce another confirmation step for work already authorized.

Use relevant static checks and mocked wrapper tests when changing tooling. Do not substitute a live reset for verification. Record the actual checks, target assumptions, and unresolved operational limits in the handoff. Keep reusable operator instructions in the operations guides rather than historical execution reports.
