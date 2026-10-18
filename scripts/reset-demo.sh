#!/usr/bin/env bash
# Rebuild a disposable demo without exposing active services to a schema reset.
set -Eeuo pipefail

usage() {
    echo "Usage: $0 [--dry-run]"
    echo "  --dry-run  Build and validate reset targets; do not stop services or delete data."
}

dry_run=false
for argument in "$@"; do
    case "$argument" in
        --dry-run) dry_run=true ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repository"
command -v docker >/dev/null
command -v flock >/dev/null

# This lock covers preflight, shutdown and restart for this checkout, not just SQL.
lock_key=$(printf '%s' "$repository" | sha256sum | cut -d ' ' -f 1)
exec 9>"${TMPDIR:-/tmp}/ecommerce-reset-${UID}-${lock_key}.lock"
flock -n 9 || { echo "Another deployment or demo reset is running for this checkout." >&2; exit 1; }

compose=(docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml)
compose+=(--profile maintenance)
paused=false

# Never automatically restart after a failed or interrupted destructive stage.
on_exit() {
    status=$?
    if (( status != 0 )) && "$paused"; then
        echo "Reset/restart failed or was interrupted. Application services may be stopped or partially restarted." >&2
        echo "Inspect the error and service status. Resolve the failure before reopening traffic." >&2
    fi
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" config --quiet
# Building does not run migrations or contact configured external providers.
"${compose[@]}" build api maintenance
running=$("${compose[@]}" ps --status running --services)
for required in db redis; do
    if ! grep -qx "$required" <<<"$running"; then
        echo "$required must already be running. Start the stack before resetting it." >&2
        exit 1
    fi
done
for job in migrate collectstatic; do
    if grep -qx "$job" <<<"$running"; then
        echo "$job is running; wait for the release job to finish before resetting." >&2
        exit 1
    fi
done

# Existing reset_dev validates the setting, schema, taxonomy, assets and media target.
"${compose[@]}" run --rm --no-deps maintenance python manage.py reset_dev --dry-run
if "$dry_run"; then
    echo "Preflight complete. No services stopped, queues cleared, or data reset."
    exit 0
fi

printf '\nTarget Compose project: ecommerce-backend-prod\n'
echo "This deletes the displayed database schema, ALL configured uploaded media,"
echo "and ALL tasks in this stack's Redis database 0, then migrates and seeds demo data."
echo "Application traffic will be unavailable. External Stripe objects and sent emails remain."
read -r -p 'Type RESET ecommerce-backend-prod to continue: ' confirmation
if [[ "$confirmation" != 'RESET ecommerce-backend-prod' ]]; then
    echo "Cancelled. No services stopped or data deleted."
    exit 0
fi

paused=true
"${compose[@]}" stop --timeout 120 nginx
"${compose[@]}" stop --timeout 120 api worker email-worker beat
# Redis DB 0 is the dedicated broker selected by compose.prod.yaml, not shared cache.
"${compose[@]}" exec -T redis redis-cli -n 0 FLUSHDB SYNC
"${compose[@]}" run --rm --no-deps maintenance python manage.py reset_dev --no-input

# reset_dev already migrated and seeded; do not run migration dependencies again.
"${compose[@]}" up -d --no-deps --wait --wait-timeout 120 api worker email-worker beat
"${compose[@]}" up -d --no-deps --wait --wait-timeout 60 nginx
paused=false
echo "Demo reset completed and application services started. Verify login, catalog and media."
