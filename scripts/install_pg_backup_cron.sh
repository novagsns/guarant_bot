#!/usr/bin/env bash
set -euo pipefail

CRON_SCHEDULE="${1:-0 3 * * *}"
SCRIPT_PATH="/opt/gsns-bot/scripts/backup_postgres.sh"
LOG_DIR="/opt/gsns-bot/data/pg_backups"
LOG_FILE="${LOG_DIR}/backup.log"

mkdir -p "${LOG_DIR}"

CRON_LINE="${CRON_SCHEDULE} ${SCRIPT_PATH} >> ${LOG_FILE} 2>&1"

if crontab -l 2>/dev/null | grep -F "${SCRIPT_PATH}" >/dev/null; then
  echo "Cron job already exists for ${SCRIPT_PATH}"
  exit 0
fi

(crontab -l 2>/dev/null; echo "${CRON_LINE}") | crontab -
echo "Cron installed: ${CRON_LINE}"
