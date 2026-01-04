#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

BACKUP_DIR="${PG_BACKUP_DIR:-${ROOT_DIR}/data/pg_backups}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-3}"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%S")"
FILENAME="${BACKUP_DIR}/gsns_bot_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

notify_telegram() {
  local message="$1"
  if [[ -z "${BOT_TOKEN:-}" || -z "${ADMIN_CHAT_ID:-}" ]]; then
    return 0
  fi
  curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${ADMIN_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    >/dev/null || true
}

on_error() {
  notify_telegram "ERROR: backup failed on ${HOSTNAME}: ${FILENAME}"
}

trap on_error ERR

docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" | gzip > "${FILENAME}"

find "${BACKUP_DIR}" -type f -name "*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete

notify_telegram "OK: backup saved on ${HOSTNAME}: ${FILENAME}"
echo "Backup saved: ${FILENAME}"
