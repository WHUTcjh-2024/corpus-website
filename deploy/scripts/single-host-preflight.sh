#!/bin/sh
set -eu

env_file=${1:-.env.single-host}
prod_compose=docker-compose.prod.yml
host_compose=docker-compose.single-host.yml

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

warn() {
  echo "WARNING: $*" >&2
}

read_env_value() {
  key=$1
  sed -n "s/^${key}=//p" "$env_file" | tail -n 1
}

[ -f "$env_file" ] || fail "$env_file does not exist"
[ -f "$prod_compose" ] || fail "$prod_compose does not exist; run this script from the repository root"
[ -f "$host_compose" ] || fail "$host_compose does not exist; run this script from the repository root"
command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not available"

if grep -Eq '(^|=)(replace-with-|your-domain\.example|admin@your-domain\.example)' "$env_file"; then
  fail "$env_file still contains placeholder values"
fi

domain=$(read_env_value DOMAIN)
[ -n "$domain" ] || fail "DOMAIN is required"
case "$domain" in
  *[!A-Za-z0-9.-]*|.*|*..*|*.) fail "DOMAIN must be a plain DNS hostname" ;;
esac
case "$domain" in
  *.*) ;;
  *) fail "DOMAIN must be a fully qualified DNS hostname" ;;
esac
[ "$(read_env_value DJANGO_SETTINGS_MODULE)" = "config.settings.production" ] || \
  fail "DJANGO_SETTINGS_MODULE must be config.settings.production"
[ "$(read_env_value DJANGO_DEBUG)" = "false" ] || fail "DJANGO_DEBUG must be false"
[ "$(read_env_value DJANGO_SECURE_SSL_REDIRECT)" = "true" ] || \
  fail "DJANGO_SECURE_SSL_REDIRECT must be true"
[ "$(read_env_value NGINX_CONFIG_PATH)" = "./deploy/nginx.single-host.conf" ] || \
  fail "NGINX_CONFIG_PATH must select deploy/nginx.single-host.conf"
[ "$(read_env_value UPLOAD_SCANNER_BACKEND)" = "apps.corpora.scanners.ClamAVUploadScanner" ] || \
  fail "the production upload scanner must be ClamAVUploadScanner"
[ -n "$(read_env_value FEEDBACK_SUPPORT_NAME)" ] || fail "FEEDBACK_SUPPORT_NAME is required"
feedback_email=$(read_env_value FEEDBACK_SUPPORT_EMAIL)
case "$feedback_email" in
  *@*.*) ;;
  *) fail "FEEDBACK_SUPPORT_EMAIL is not a valid address" ;;
esac
allowed_hosts=$(read_env_value DJANGO_ALLOWED_HOSTS)
trusted_origins=$(read_env_value DJANGO_CSRF_TRUSTED_ORIGINS)
healthcheck_host=$(read_env_value DJANGO_HEALTHCHECK_HOST)
case ",$allowed_hosts," in
  *",$domain,"*) ;;
  *) fail "DOMAIN must be present in DJANGO_ALLOWED_HOSTS" ;;
esac
[ "$healthcheck_host" = "$domain" ] || fail "DJANGO_HEALTHCHECK_HOST must equal DOMAIN"
case ",$trusted_origins," in
  *",https://$domain,"*) ;;
  *) fail "https://DOMAIN must be present in DJANGO_CSRF_TRUSTED_ORIGINS" ;;
esac

django_secret=$(read_env_value DJANGO_SECRET_KEY)
postgres_password=$(read_env_value POSTGRES_PASSWORD)
redis_password=$(read_env_value REDIS_PASSWORD)
auditor_token=$(read_env_value CORPUS_AUDITOR_COMPAT_API_TOKEN)
metrics_token=$(read_env_value METRICS_BEARER_TOKEN)
check_secret_length() {
  secret_name=$1
  secret_value=$2
  [ "${#secret_value}" -ge 32 ] || fail "$secret_name must contain at least 32 characters"
}
check_secret_length DJANGO_SECRET_KEY "$django_secret"
check_secret_length POSTGRES_PASSWORD "$postgres_password"
check_secret_length REDIS_PASSWORD "$redis_password"
check_secret_length CORPUS_AUDITOR_COMPAT_API_TOKEN "$auditor_token"
check_secret_length METRICS_BEARER_TOKEN "$metrics_token"
for url_password in "$postgres_password" "$redis_password"; do
  case "$url_password" in
    *[!A-Za-z0-9_-]*) fail "database passwords may contain only URL-safe letters, digits, '-' and '_'" ;;
  esac
done
[ "$postgres_password" != "$redis_password" ] || fail "PostgreSQL and Redis passwords must be different"
[ "$auditor_token" != "$metrics_token" ] || fail "auditor and metrics tokens must be different"

postgres_user=$(read_env_value POSTGRES_USER)
postgres_database=$(read_env_value POSTGRES_DB)
expected_database_url="postgresql://${postgres_user}:${postgres_password}@db:5432/${postgres_database}"
[ "$(read_env_value DATABASE_URL)" = "$expected_database_url" ] || \
  fail "DATABASE_URL is inconsistent with the single-host PostgreSQL settings"
expected_redis_prefix="redis://:${redis_password}@redis:6379/"
[ "$(read_env_value REDIS_URL)" = "${expected_redis_prefix}0" ] || fail "REDIS_URL is inconsistent"
[ "$(read_env_value CELERY_BROKER_URL)" = "${expected_redis_prefix}0" ] || fail "CELERY_BROKER_URL is inconsistent"
[ "$(read_env_value CELERY_RESULT_BACKEND)" = "${expected_redis_prefix}1" ] || fail "CELERY_RESULT_BACKEND is inconsistent"
[ "$(read_env_value CORPUS_AUDITOR_QUEUE_URL)" = "${expected_redis_prefix}0" ] || fail "CORPUS_AUDITOR_QUEUE_URL is inconsistent"

if command -v stat >/dev/null 2>&1; then
  env_mode=$(stat -c '%a' "$env_file" 2>/dev/null || true)
  case "$env_mode" in
    600|400) ;;
    *) warn "$env_file permissions are $env_mode; use chmod 600 $env_file" ;;
  esac
fi

memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
swap_kib=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$memory_kib" -lt 3500000 ]; then
  fail "at least 3.5 GiB of visible memory is required for the 2-core/4-GB profile"
fi
if [ "$swap_kib" -lt 1500000 ]; then
  warn "less than 1.5 GiB of swap is configured; create a 2 GiB swap file before launch"
fi
cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)
if [ "$cpu_count" -lt 2 ]; then
  fail "at least two visible CPU cores are required for the single-host profile"
fi

for key in DATA_ROOT_HOST_PATH BACKUP_ROOT_HOST_PATH LETSENCRYPT_HOST_PATH CERTBOT_WEBROOT_HOST_PATH; do
  value=$(read_env_value "$key")
  [ -n "$value" ] || fail "$key is missing from $env_file"
  case "$value" in
    /*) ;;
    *) fail "$key must be an absolute path" ;;
  esac
  [ -d "$value" ] || fail "$key directory does not exist: $value"
  [ -w "$value" ] || fail "$key directory is not writable: $value"
done

cert_root=$(read_env_value LETSENCRYPT_HOST_PATH)
for certificate_file in \
  "$cert_root/live/corpus-platform/fullchain.pem" \
  "$cert_root/live/corpus-platform/privkey.pem"; do
  [ -s "$certificate_file" ] || fail "TLS certificate file is missing: $certificate_file"
done
if command -v openssl >/dev/null 2>&1; then
  openssl x509 -checkend 604800 -noout \
    -in "$cert_root/live/corpus-platform/fullchain.pem" >/dev/null || \
    fail "TLS certificate expires in less than seven days"
  openssl x509 -checkhost "$domain" -noout \
    -in "$cert_root/live/corpus-platform/fullchain.pem" >/dev/null || \
    fail "TLS certificate does not cover DOMAIN"
else
  warn "openssl is unavailable; certificate expiry and hostname were not checked"
fi

docker compose \
  --env-file "$env_file" \
  -f "$prod_compose" \
  -f "$host_compose" \
  config --quiet

echo "single-host production preflight passed"
