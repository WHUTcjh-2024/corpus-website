#!/bin/sh
set -eu

env_file=${1:-.env.single-host}
[ -f "$env_file" ] || {
  echo "environment file not found: $env_file" >&2
  exit 1
}

docker compose \
  --env-file "$env_file" \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  --profile tls run --rm certbot \
  renew --webroot --webroot-path /var/www/certbot --quiet

docker compose \
  --env-file "$env_file" \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  exec -T nginx nginx -s reload
