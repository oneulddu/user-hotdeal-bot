#!/usr/bin/env bash
set -euo pipefail

require() {
  if [[ -z "${!1:-}" ]]; then
    echo "Missing required env var: $1" >&2
    exit 1
  fi
}

normalize_s3_prefix() {
  local prefix="${1:-}"
  prefix="${prefix#/}"
  prefix="${prefix%/}"
  printf '%s' "$prefix"
}

validate_retention_days() {
  local days="${1:-}"
  if [[ -z "$days" ]]; then
    return
  fi
  if [[ ! "$days" =~ ^[0-9]+$ || "$days" -lt 1 ]]; then
    echo "RETENTION_DAYS must be a positive integer when set: ${days}" >&2
    exit 1
  fi
}

# Required DB connection settings
require MARIADB_HOST
require MARIADB_USER
require MARIADB_PASSWORD
require MARIADB_DATABASE

MARIADB_PORT=${MARIADB_PORT:-3306}

# Required S3/minio settings
require S3_BUCKET

S3_PREFIX=$(normalize_s3_prefix "${S3_PREFIX:-mariadb}")
S3_ENDPOINT=${S3_ENDPOINT:-}
S3_REGION=${S3_REGION:-us-east-1}
AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-$S3_REGION}
export AWS_DEFAULT_REGION

BACKUP_NAME_PREFIX=${BACKUP_NAME_PREFIX:-hotdeal}
BACKUP_TMP_DIR=${BACKUP_TMP_DIR:-/tmp}
RETENTION_DAYS=${RETENTION_DAYS:-}
validate_retention_days "$RETENTION_DAYS"
TIMESTAMP=${BACKUP_TIMESTAMP_OVERRIDE:-$(TZ=${TZ:-UTC} date +"%Y%m%dT%H%M%S%Z")}
BACKUP_FILENAME="${BACKUP_NAME_PREFIX}-mariadb-${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_TMP_DIR"

if [[ -n "$S3_PREFIX" ]]; then
  DEST_URI="s3://${S3_BUCKET}/${S3_PREFIX}/${BACKUP_FILENAME}"
else
  DEST_URI="s3://${S3_BUCKET}/${BACKUP_FILENAME}"
fi

AWS_ARGS=()
if [[ -n "$S3_ENDPOINT" ]]; then
  AWS_ARGS+=(--endpoint-url "$S3_ENDPOINT")
fi

command -v mysqldump >/dev/null 2>&1 || { echo "mysqldump not found" >&2; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "aws CLI not found" >&2; exit 1; }

tmp_sql="${BACKUP_TMP_DIR}/${BACKUP_FILENAME%.gz}"
tmp_gz="${BACKUP_TMP_DIR}/${BACKUP_FILENAME}"
trap 'rm -f "$tmp_sql" "$tmp_gz"' EXIT

echo "[backup] starting dump to ${tmp_gz}" >&2
mysqldump \
  --single-transaction \
  --quick \
  --lock-tables=false \
  --host="$MARIADB_HOST" \
  --port="$MARIADB_PORT" \
  --user="$MARIADB_USER" \
  --password="$MARIADB_PASSWORD" \
  "$MARIADB_DATABASE" > "$tmp_sql"

gzip -9 "$tmp_sql"

echo "[backup] uploading to ${DEST_URI}" >&2
aws s3 cp "$tmp_gz" "$DEST_URI" "${AWS_ARGS[@]}"

if [[ -n "$RETENTION_DAYS" ]]; then
  if [[ -z "$S3_PREFIX" ]]; then
    echo "[backup] RETENTION_DAYS set but S3_PREFIX is empty; skipping retention" >&2
  else
    cutoff_iso=$(date -u -d "-${RETENTION_DAYS} days" +"%Y-%m-%dT%H:%M:%SZ")
    echo "[backup] pruning objects older than ${cutoff_iso} under ${S3_PREFIX}/" >&2
    mapfile -t old_keys < <(aws s3api list-objects-v2 \
      --bucket "$S3_BUCKET" \
      --prefix "$S3_PREFIX/" \
      --query "Contents[?LastModified<=\`${cutoff_iso}\`].Key" \
      --output text \
      "${AWS_ARGS[@]}" | tr '\t' '\n')
    if [[ ${#old_keys[@]} -gt 0 && -n "${old_keys[0]}" && "${old_keys[0]}" != "None" ]]; then
      for key in "${old_keys[@]}"; do
        echo "[backup] deleting ${key}" >&2
        aws s3api delete-object --bucket "$S3_BUCKET" --key "$key" "${AWS_ARGS[@]}"
      done
    else
      echo "[backup] no objects to prune" >&2
    fi
  fi
fi

echo "[backup] completed" >&2
