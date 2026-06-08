#!/usr/bin/env sh
set -eu

CRON_SCHEDULE="${BACKUP_SCHEDULE:-0 18 * * *}"
CRON_DIR=/etc/supercronic
CRON_FILE="${CRON_DIR}/backup.crontab"

mkdir -p "${CRON_DIR}"
echo "${CRON_SCHEDULE} /usr/local/bin/mariadb_backup.sh" > "${CRON_FILE}"

exec supercronic "${CRON_FILE}"
