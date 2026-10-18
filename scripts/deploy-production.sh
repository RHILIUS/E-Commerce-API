#!/usr/bin/env bash
# Release one main commit on the existing EC2 production Compose stack.
set -Eeuo pipefail

if (( $# != 2 )) || [[ ! "$1" =~ ^[0-9a-f]{40}$ ]] || [[ "$2" != /* ]]; then
    echo "Usage: bash deploy-production.sh <40-character-commit-sha> <absolute-repository-path>" >&2
    exit 2
fi
revision=$1
cd -- "$2"
repository=$(pwd -P)
for required in git docker flock; do
    command -v "$required" >/dev/null
done

# Match reset-demo.sh: one operational checkout and user must own both commands.
lock_key=$(printf '%s' "$repository" | sha256sum | cut -d ' ' -f 1)
exec 9>"${TMPDIR:-/tmp}/ecommerce-reset-${UID}-${lock_key}.lock"
flock -n 9 || { echo "Another deployment or demo reset is running." >&2; exit 1; }

if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
    echo "Checkout has local changes. Resolve them before deploying; nothing was discarded." >&2
    exit 1
fi
export GIT_TERMINAL_PROMPT=0
# Retain the bootstrap checkout's dedicated identity and verified GitHub host keys.
git_ssh_command=$(git config --get core.sshCommand || printf '%s' ssh)
export GIT_SSH_COMMAND="$git_ssh_command -o BatchMode=yes"
git fetch origin +refs/heads/main:refs/remotes/origin/main
latest=$(git rev-parse refs/remotes/origin/main)
if [[ "$revision" != "$latest" ]]; then
    echo "Skipping superseded commit $revision; origin/main is $latest."
    exit 0
fi
git checkout --detach "$revision"

compose=(docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml)
paused=false
stage=preflight

# Keep traffic closed on release failure; never roll back schema automatically.
on_exit() {
    status=$?
    if (( status != 0 )); then
        echo "Deployment failed during $stage (commit $revision)." >&2
        if "$paused"; then
            "${compose[@]}" stop --timeout 30 nginx || true
            echo "Traffic shutdown requested. Services may be stopped or partially restarted; inspect status before recovery." >&2
        fi
    fi
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

"${compose[@]}" config --quiet
for service in db redis; do
    container=$("${compose[@]}" ps -q "$service")
    if [[ -z "$container" ]] || [[ $(docker inspect --format '{{.State.Health.Status}}' "$container") != healthy ]]; then
        echo "$service must already be healthy; initial provisioning is manual." >&2
        exit 1
    fi
done
running=$("${compose[@]}" ps --status running --services)
for job in migrate collectstatic maintenance; do
    if grep -qx "$job" <<<"$running"; then
        echo "$job is running; wait for it to finish before deploying." >&2
        exit 1
    fi
done

stage=build
"${compose[@]}" build api
stage=shutdown
paused=true
"${compose[@]}" stop --timeout 120 nginx
"${compose[@]}" stop --timeout 120 api worker email-worker beat
stage=migrations
"${compose[@]}" run --rm --no-deps -T --interactive=false migrate
stage=static-collection
"${compose[@]}" run --rm --no-deps -T --interactive=false collectstatic
stage=application-startup
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 180 api worker email-worker beat
stage=database-check
"${compose[@]}" exec -T --interactive=false api python manage.py shell -c \
    'from django.db import connection; connection.ensure_connection()'
stage=nginx-startup
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 nginx
"${compose[@]}" exec -T --interactive=false nginx nginx -t
paused=false
echo "Deployed $revision. API socket, database connection, Nginx configuration and service startup checks passed."
echo "Verify public HTTPS and application behavior separately; worker startup does not prove queue delivery."
