#!/usr/bin/env bash
# Execute a staged deployment payload as the SSH login user.
# Configuration variables are supplied by the validated generated remote.conf.
# shellcheck disable=SC2153,SC2154
set -Eeuo pipefail
umask 077
payload=$1
# shellcheck source=/dev/null
source "$payload/remote.conf"
stage=preflight
paused=false
challenge=false
on_exit() {
    status=$?
    if "$challenge"; then docker rm -f ecommerce-bootstrap-http >/dev/null 2>&1 || true; fi
    if (( status != 0 )); then
        echo "Operation failed during $stage. Inspect server state before retrying; no data was rolled back." >&2
        if "$paused"; then "${compose[@]}" stop --timeout 30 nginx || true; fi
    fi
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ $(id -un) == "$DEPLOY_USER" && $UID != 0 ]] || { echo 'Use the configured non-root deployment account.' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo 'Only Ubuntu 24.04 LTS is supported.' >&2; exit 1; }
[[ $(uname -m) == x86_64 || $(uname -m) == aarch64 ]] || exit 1
sudo -n true
if [[ "$operation" == check || "$operation" == bootstrap ]]; then
[[ $(awk '/MemTotal/ {print $2}' /proc/meminfo) -ge 1900000 ]] || { echo 'At least 2 GB RAM is required.' >&2; exit 1; }
[[ $(df -Pk "$HOME" | awk 'NR==2 {print $4}') -ge 10485760 ]] || { echo 'At least 10 GiB free disk space is required.' >&2; exit 1; }
fi
if [[ "$operation" == check ]]; then
    getent ahosts "$API_DOMAIN" >/dev/null
    echo 'Ubuntu, architecture, sudo, memory, home-filesystem space and DNS lookup checks passed.'
    echo 'This does not prove inbound ports, DNS destination, Git access, application settings or certificate issuance.'
    exit 0
fi
if [[ "$operation" == bootstrap && "${2:-}" != --prepared ]]; then
    # Serialize host package/group setup before acquiring the checkout lock below.
    exec 8>"/tmp/ecommerce-provision-${UID}.lock"
    flock -n 8 || { echo 'Another bootstrap is preparing this host.' >&2; exit 1; }
    stage=packages
    if ! command -v docker >/dev/null; then
        for package in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
            if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                echo "Conflicting package $package is installed; resolve it explicitly." >&2; exit 1
            fi
        done
        sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update
        sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
        sudo -n install -d -m 755 /etc/apt/keyrings
        sudo -n curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        sudo -n chmod 644 /etc/apt/keyrings/docker.asc
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' "$(dpkg --print-architecture)" \
            | sudo -n tee /etc/apt/sources.list.d/ecommerce-docker.list >/dev/null
        sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update
        sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y git certbot openssl curl
    sudo -n systemctl enable --now docker
    sudo -n usermod -aG docker "$DEPLOY_USER"
    # A fresh credential context observes Docker group membership immediately.
    # FD 8 stays held by this parent; the child only takes the repository lock.
    sudo -n -u "$DEPLOY_USER" bash "$payload/remote.sh" "$payload" --prepared
    exit 0
fi
command -v git >/dev/null
docker info >/dev/null
docker compose version >/dev/null
export GIT_TERMINAL_PROMPT=0
export TMPDIR=/tmp
if [[ "$operation" == update ]]; then
    cd -- "$DEPLOY_PATH"
    [[ -f .bootstrap-state/complete ]] || { echo 'Complete bootstrap before update.' >&2; exit 1; }
    revision=$(git ls-remote origin refs/heads/main | awk '{print $1}')
    # Stream the release script belonging to the selected commit, as Actions does.
    git show "$revision:scripts/deploy-production.sh" | bash -s -- "$revision" "$DEPLOY_PATH"
    exit 0
fi
if [[ "$operation" == status ]]; then
    cd -- "$DEPLOY_PATH"
    git rev-parse HEAD
    docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml ps -a
    sudo -n openssl x509 -in deploy/certs/fullchain.pem -noout -dates
    sudo -n systemctl is-active certbot.timer
    exit 0
fi
stage=repository
mkdir -p -- "$DEPLOY_PATH"
cd -- "$DEPLOY_PATH"
repository=$(pwd -P)
[[ "$repository" == "$DEPLOY_PATH" ]] || { echo 'DEPLOY_PATH must not contain symlinked directories.' >&2; exit 1; }
lock_key=$(printf '%s' "$repository" | sha256sum | cut -d ' ' -f 1)
lock="/tmp/ecommerce-reset-${UID}-${lock_key}.lock"
exec 9>"$lock"
flock -n 9 || { echo 'Another deployment or demo reset is running.' >&2; exit 1; }
install -d -m 700 "$HOME/.ssh"
# Preserve credentials/configuration on retry; conflicting values need operator review.
install_once() {
    local source=$1 destination=$2 mode=$3
    if [[ -e "$destination" ]]; then
        cmp -s "$source" "$destination" || { echo "Existing $destination differs; review it before retrying." >&2; exit 1; }
    else
        install -m "$mode" "$source" "$destination"
    fi
}
install_once "$payload/git-key" "$HOME/.ssh/ecommerce-repository" 600
install_once "$payload/git-known-hosts" "$HOME/.ssh/ecommerce-github-known-hosts" 600
ssh-keygen -y -P '' -f "$HOME/.ssh/ecommerce-repository" >/dev/null
ssh-keygen -F github.com -f "$HOME/.ssh/ecommerce-github-known-hosts" >/dev/null
printf -v git_ssh 'ssh -i %q -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=%q' \
    "$HOME/.ssh/ecommerce-repository" "$HOME/.ssh/ecommerce-github-known-hosts"
export GIT_SSH_COMMAND=$git_ssh
if [[ ! -d .git ]]; then
    [[ -z $(ls -A) ]] || { echo 'Deployment directory is not an empty Git checkout.' >&2; exit 1; }
    git clone --branch main --single-branch "$REPOSITORY" .
fi
[[ $(git remote get-url origin) == "$REPOSITORY" ]] || { echo 'Existing origin differs.' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=normal) ]] || { echo 'Checkout contains local changes.' >&2; exit 1; }
git config core.sshCommand "$git_ssh"
unset GIT_SSH_COMMAND
# Require the bootstrap-enabled contract in main; do not patch a live checkout.
if [[ ! -f scripts/deployment/renew-certificate.sh ]] || ! grep -Fq './deploy/acme' compose.prod.yaml; then
    echo 'Merge the bootstrap implementation into main before provisioning.' >&2; exit 1
fi
if [[ ! -f .bootstrap-state/identity ]] && {
    [[ -n $(docker ps -aq --filter label=com.docker.compose.project=ecommerce-backend-prod) ]] ||
    [[ -n $(docker volume ls -q --filter label=com.docker.compose.project=ecommerce-backend-prod) ]];
}; then
    echo 'An existing production stack cannot be adopted by bootstrap.' >&2; exit 1
fi
install -d -m 700 .bootstrap-state
printf '%s\n%s\n%s\n' "$REPOSITORY" "$API_DOMAIN" "$CERTIFICATE_EMAIL" > "$payload/identity"
install_once "$payload/identity" .bootstrap-state/identity 600
install_once "$payload/app.env" .env 600
chmod 600 .env
compose=(docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml)
"${compose[@]}" config --quiet
if [[ -f .bootstrap-state/complete ]]; then
    "${compose[@]}" ps -a
    echo 'Bootstrap was already completed. Use update for releases and status for inspection.'
    exit 0
fi
stage=build-and-configuration
"${compose[@]}" build api
# Validate effective settings inside the actual image without displaying secrets.
"${compose[@]}" run --rm --no-deps -T --interactive=false -e "EXPECTED_API_DOMAIN=$API_DOMAIN" api python -c '
import os
from django.conf import settings
valid = (
    len(settings.SECRET_KEY) >= 50 and "replace-with" not in settings.SECRET_KEY
    and os.environ["EXPECTED_API_DOMAIN"] in settings.ALLOWED_HOSTS
    and os.environ.get("PUBLIC_HTTPS_ORIGIN") == "https://" + os.environ["EXPECTED_API_DOMAIN"]
    and os.environ.get("PROD_BIND_ADDRESS") == "0.0.0.0"
    and os.environ.get("PROD_HTTP_PORT") == "80"
    and os.environ.get("PROD_HTTPS_PORT") == "443"
    and bool(os.environ.get("DB_PASSWORD"))
    and "replace-with" not in os.environ["DB_PASSWORD"]
    and not settings.DEBUG
)
if not valid:
    raise SystemExit("Production environment rejected: review secret, allowed hosts, database password and public listener settings")
'
stage=certificate
install -d -m 755 deploy/acme deploy/certs
lineage=/etc/letsencrypt/live/ecommerce-backend
if sudo -n test -e "$lineage/fullchain.pem"; then
    sudo -n openssl verify -verify_hostname "$API_DOMAIN" \
        -CAfile /etc/ssl/certs/ca-certificates.crt -untrusted "$lineage/fullchain.pem" "$lineage/cert.pem"
    sudo -n openssl x509 -in "$lineage/fullchain.pem" -noout -checkend 604800 || {
        echo 'Certificate expires within seven days. Inspect renewal before retrying bootstrap.' >&2; exit 1;
    }
else
    getent ahosts "$API_DOMAIN" >/dev/null
    if [[ -n $(docker ps -aq --filter name=^/ecommerce-bootstrap-http$) ]]; then
        [[ $(docker inspect --format '{{index .Config.Labels "ecommerce.bootstrap"}}' ecommerce-bootstrap-http) == "$lock_key" ]] || {
            echo 'Unrelated certificate challenge container exists.' >&2; exit 1;
        }
        docker rm -f ecommerce-bootstrap-http >/dev/null
    fi
    cat > "$payload/http.conf" <<'NGINX'
server {
    listen 80;
    server_name _;
    access_log off;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 404; }
}
NGINX
    chmod 644 "$payload/http.conf"
    docker run -d --name ecommerce-bootstrap-http --label "ecommerce.bootstrap=$lock_key" \
        -p 0.0.0.0:80:80 -v "$repository/deploy/acme:/var/www/acme:ro" \
        -v "$payload/http.conf:/etc/nginx/conf.d/default.conf:ro" nginx:stable-alpine >/dev/null
    challenge=true
    # Wait for the local challenge server before asking the public CA to validate it.
    for _ in {1..30}; do
        if curl --silent --max-time 2 http://127.0.0.1/ -o /dev/null; then break; fi
        sleep 1
    done
    curl --silent --show-error --max-time 3 http://127.0.0.1/ -o /dev/null
    sudo -n certbot certonly --non-interactive --agree-tos --email "$CERTIFICATE_EMAIL" \
        --cert-name ecommerce-backend --domain "$API_DOMAIN" --webroot -w "$repository/deploy/acme"
    docker rm -f ecommerce-bootstrap-http >/dev/null
    challenge=false
fi
sudo -n install -m 644 "$lineage/fullchain.pem" deploy/certs/fullchain.pem
sudo -n install -m 600 "$lineage/privkey.pem" deploy/certs/privkey.pem
# Install a root-owned hook. Values have already passed strict path/user validation.
sed -e "s|__REPOSITORY__|$repository|" -e "s|__DEPLOY_USER__|$DEPLOY_USER|" \
    -e "s|__LOCK__|$lock|" "$payload/renew-certificate.sh" > "$payload/renew-hook"
sudo -n install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo -n install -o root -g root -m 755 "$payload/renew-hook" /etc/letsencrypt/renewal-hooks/deploy/ecommerce-backend
sudo -n systemctl enable --now certbot.timer
if [[ -f "$payload/actions.pub" ]]; then
    actions_key="restrict $(cat "$payload/actions.pub")"
    touch "$HOME/.ssh/authorized_keys"
    chmod 600 "$HOME/.ssh/authorized_keys"
    grep -Fxq "$actions_key" "$HOME/.ssh/authorized_keys" || printf '%s\n' "$actions_key" >> "$HOME/.ssh/authorized_keys"
fi
stage=infrastructure
"${compose[@]}" up -d --wait --wait-timeout 180 db redis
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
"${compose[@]}" exec -T api python manage.py shell -c 'from django.db import connection; connection.ensure_connection()'
stage=nginx-startup
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 nginx
"${compose[@]}" exec -T nginx nginx -t
stage=local-https
curl --fail --silent --show-error --resolve "$API_DOMAIN:443:127.0.0.1" \
    --connect-timeout 10 --max-time 30 "https://$API_DOMAIN/api/schema/" -o /dev/null
paused=false
git rev-parse HEAD > .bootstrap-state/complete
"${compose[@]}" ps -a
echo 'Bootstrap complete. Configure GitHub production environment values before enabling releases to this host.'
