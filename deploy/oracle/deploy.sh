#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$here/.env"
compose_file="$here/compose.yaml"

if [ ! -f "$env_file" ]; then
    echo "Missing $env_file; run python3 deploy/oracle/init_env.py first." >&2
    exit 1
fi
if grep -q '^PUBLIC_DOMAIN=replace-with-your-domain.example$' "$env_file"; then
    echo "Set PUBLIC_DOMAIN to a DNS name pointing to this VM before deploying." >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker Engine and the Compose plugin are required on the Oracle VM." >&2
    exit 1
fi

docker compose --env-file "$env_file" -f "$compose_file" config --quiet
docker compose --env-file "$env_file" -f "$compose_file" up --build -d
docker compose --env-file "$env_file" -f "$compose_file" ps
echo "Check https://$(sed -n 's/^PUBLIC_DOMAIN=//p' "$env_file" | head -n 1)/ready after DNS and TLS settle."
