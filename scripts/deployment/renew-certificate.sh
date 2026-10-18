#!/usr/bin/env bash
# Root-owned Certbot hook; fixed paths below are populated during bootstrap.
set -Eeuo pipefail
repository=__REPOSITORY__
deploy_user=__DEPLOY_USER__
lock=__LOCK__
lineage=/etc/letsencrypt/live/ecommerce-backend
hook=/etc/letsencrypt/renewal-hooks/deploy/ecommerce-backend
if [[ "${1:-}" != --locked ]]; then
    [[ "${RENEWED_LINEAGE:-}" == "$lineage" ]] || exit 0
    # Acquire the same user's lock without root opening a user-owned /tmp file.
    exec sudo -n -u "$deploy_user" flock -w 300 "$lock" sudo -n "$hook" --locked
fi
# Rename inside the mounted directory so Nginx observes renewed files.
install -m 644 "$lineage/fullchain.pem" "$repository/deploy/certs/fullchain.pem.new"
install -m 600 "$lineage/privkey.pem" "$repository/deploy/certs/privkey.pem.new"
mv -f "$repository/deploy/certs/fullchain.pem.new" "$repository/deploy/certs/fullchain.pem"
mv -f "$repository/deploy/certs/privkey.pem.new" "$repository/deploy/certs/privkey.pem"
cd -- "$repository"
compose=(docker compose --project-name ecommerce-backend-prod -f compose.prod.yaml)
if [[ -n $("${compose[@]}" ps --status running -q nginx) ]]; then
    "${compose[@]}" exec -T nginx nginx -t
    "${compose[@]}" exec -T nginx nginx -s reload
fi
