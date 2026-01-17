#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
NOTIFY=1
UPLOAD=0

for arg in "$@"; do
  case "${arg}" in
    --notify)
      NOTIFY=1
      ;;
    --no-notify)
      NOTIFY=0
      ;;
    --upload)
      UPLOAD=1
      ;;
    --no-upload)
      UPLOAD=0
      ;;
    *)
      ;;
  esac
done

if [[ -f "${ENV_FILE}" ]]; then
  load_key() {
    local key="$1"
    local line
    line=$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1) || return 0
    local value="${line#${key}=}"
    value="${value%$'\r'}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf -v "${key}" "%s" "${value}"
    export "${key}"
  }

  load_key "POSTGRES_USER"
  load_key "POSTGRES_DB"
  load_key "PG_BACKUP_DIR"
  load_key "PG_BACKUP_RETENTION_DAYS"
  load_key "BOT_TOKEN"
  load_key "ADMIN_CHAT_ID"
  load_key "ADMIN_TOPIC_ID"
fi

BACKUP_DIR="${PG_BACKUP_DIR:-${ROOT_DIR}/data/pg_backups}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-3}"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%S")"
FILENAME="${BACKUP_DIR}/gsns_bot_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

notify_telegram() {
  local message="$1"
  if [[ "${NOTIFY}" != "1" ]]; then
    return 0
  fi
  if [[ -z "${BOT_TOKEN:-}" || -z "${ADMIN_CHAT_ID:-}" ]]; then
    return 0
  fi
  curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${ADMIN_CHAT_ID}" \
    ${ADMIN_TOPIC_ID:+-d "message_thread_id=${ADMIN_TOPIC_ID}"} \
    --data-urlencode "text=${message}" \
    >/dev/null || true
}

send_backup_file() {
  if [[ "${UPLOAD}" != "1" ]]; then
    return 0
  fi
  if [[ -z "${BOT_TOKEN:-}" || -z "${ADMIN_CHAT_ID:-}" ]]; then
    return 0
  fi
  if [[ ! -f "${FILENAME}" ]]; then
    return 0
  fi
  local max_bytes=$((45 * 1024 * 1024))
  local size
  size=$(stat -c %s "${FILENAME}" 2>/dev/null || echo "")
  if [[ -n "${size}" && "${size}" -gt "${max_bytes}" ]]; then
    notify_telegram "Backup too large for Telegram (${size} bytes): ${FILENAME}"
    return 0
  fi
  curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
    -F "chat_id=${ADMIN_CHAT_ID}" \
    ${ADMIN_TOPIC_ID:+-F "message_thread_id=${ADMIN_TOPIC_ID}"} \
    -F "caption=Backup ${HOSTNAME} ${TIMESTAMP}" \
    -F "document=@${FILENAME}" \
    >/dev/null || true
}

on_error() {
  notify_telegram "ERROR: backup failed on ${HOSTNAME}: ${FILENAME}"
}

trap on_error ERR

docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" | gzip > "${FILENAME}"

find "${BACKUP_DIR}" -type f -name "*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete

send_backup_file
notify_telegram "OK: backup saved on ${HOSTNAME}: ${FILENAME}"
echo "Backup saved: ${FILENAME}"
