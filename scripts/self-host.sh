#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo 'Install Docker with Compose, then run this command again.' >&2; exit 1; }
if [ ! -f .env.selfhost ]; then
  command -v openssl >/dev/null 2>&1 || { echo 'Install OpenSSL to generate deployment credentials.' >&2; exit 1; }
  umask 077
  task_env_file=$(mktemp .env.selfhost.XXXXXX)
  {
    printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'PUBLIC_BASE_URL=http://localhost:8000\n'
    printf 'ALLOWED_EMAIL_DOMAIN=berri.ai\n'
  } > "$task_env_file"
  mv "$task_env_file" .env.selfhost
fi
docker compose --env-file .env.selfhost up --build --wait --wait-timeout 240 -d
printf '\nOpen your PUBLIC_BASE_URL (initially http://localhost:8000).\n'
printf 'For Slack callbacks, set PUBLIC_BASE_URL to an HTTPS address and put a reverse proxy in front of port 8000.\n'
printf 'Retrieve the private, one-use setup code with:\n'
printf 'docker compose --env-file .env.selfhost exec litemem cat /data/setup-code\n'
