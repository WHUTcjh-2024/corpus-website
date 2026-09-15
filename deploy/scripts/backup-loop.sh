#!/bin/sh
set -eu

umask 077

backup_interval=${BACKUP_INTERVAL_SECONDS:-86400}
retention_days=${BACKUP_RETENTION_DAYS:-7}
backup_once=${BACKUP_ONCE:-false}

case "$backup_interval" in
  ''|*[!0-9]*)
    echo "BACKUP_INTERVAL_SECONDS must be a positive integer" >&2
    exit 64
    ;;
esac
case "$retention_days" in
  ''|*[!0-9]*)
    echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2
    exit 64
    ;;
esac
if [ "$backup_interval" -lt 300 ] || [ "$retention_days" -lt 1 ]; then
  echo "backup interval must be at least 300 seconds and retention at least one day" >&2
  exit 64
fi
case "$backup_once" in
  true|false) ;;
  *) echo "BACKUP_ONCE must be true or false" >&2; exit 64 ;;
esac

mkdir -p /backups
chmod 700 /backups

run_backup() (
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  random_suffix=$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')
  backup_id="${timestamp}-${random_suffix}"
  final_dir="/backups/corpus-${backup_id}"
  partial_dir="/backups/.partial-${backup_id}"

  cleanup_partial() {
    if [ -d "$partial_dir" ]; then
      rm -rf -- "$partial_dir"
    fi
  }
  trap cleanup_partial EXIT HUP INT TERM

  mkdir -m 700 "$partial_dir"
  echo "starting backup ${backup_id}"

  pg_isready --quiet --timeout=5
  pg_dump \
    --format=custom \
    --compress=6 \
    --no-owner \
    --no-privileges \
    --file="$partial_dir/database.dump"
  pg_restore --list "$partial_dir/database.dump" >/dev/null

  tar --create --gzip --file="$partial_dir/data.tar.gz" --directory=/data .
  tar --list --gzip --file="$partial_dir/data.tar.gz" >/dev/null

  {
    echo "created_at_utc=${timestamp}"
    echo "database=${PGDATABASE}"
    echo "format=postgres-custom-plus-data-tar-gzip"
  } >"$partial_dir/manifest.txt"

  (
    cd "$partial_dir"
    sha256sum database.dump data.tar.gz manifest.txt >SHA256SUMS
    sha256sum -c SHA256SUMS >/dev/null
  )

  mv "$partial_dir" "$final_dir"
  trap - EXIT HUP INT TERM
  touch /tmp/last-backup-success
  echo "completed backup ${final_dir}"

  find /backups \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -name 'corpus-[0-9]*Z-*' \
    -mtime "+${retention_days}" \
    -exec rm -rf -- {} +
)

terminate=0
trap 'terminate=1' HUP INT TERM

while [ "$terminate" -eq 0 ]; do
  run_backup
  if [ "$backup_once" = "true" ]; then
    break
  fi
  sleep "$backup_interval" &
  wait "$!" || true
done
