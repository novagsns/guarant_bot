#!/usr/bin/env bash
set -euo pipefail

CRON_SCHEDULE_FAST="${1:-*/5 * * * *}"
CRON_SCHEDULE_NOTIFY="${2:-0 */2 * * *}"
SCRIPT_PATH="/opt/gsns-bot/scripts/backup_postgres.sh"
LOG_DIR="/opt/gsns-bot/data/pg_backups"
LOG_FILE="${LOG_DIR}/backup.log"

mkdir -p "${LOG_DIR}"

CRON_LINE_FAST="${CRON_SCHEDULE_FAST} ${SCRIPT_PATH} --no-notify >> ${LOG_FILE} 2>&1"
CRON_LINE_NOTIFY="${CRON_SCHEDULE_NOTIFY} ${SCRIPT_PATH} --notify --upload >> ${LOG_FILE} 2>&1"

EXISTING="$(crontab -l 2>/dev/null | grep -v -F "${SCRIPT_PATH}" || true)"
{
  if [[ -n "${EXISTING}" ]]; then
    echo "${EXISTING}"
  fi
  echo "${CRON_LINE_FAST}"
  echo "${CRON_LINE_NOTIFY}"
} | crontab -

echo "Cron installed:"
echo "  ${CRON_LINE_FAST}"
echo "  ${CRON_LINE_NOTIFY}"
