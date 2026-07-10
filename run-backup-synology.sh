#!/usr/bin/env bash
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/net-config-backup}"
CONFIG_FILE="${CONFIG_FILE:-$APP_DIR/devices.yaml}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
RETENTION_DAYS="${RETENTION_DAYS:-180}"

# Set this in backup.env, for example:
# NAS_REMOTE="synology:NetConfigBackup/backups"
NAS_REMOTE="${NAS_REMOTE:-}"

TODAY="$(date +%F)"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

RUN_LOG="$LOG_DIR/run-backup-synology-$TODAY.log"
NAS_SYNC_LOG="$LOG_DIR/nas-sync-$TODAY.log"
NAS_CLEANUP_LOG="$LOG_DIR/nas-cleanup-$TODAY.log"

log() {
  printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$RUN_LOG"
}

cleanup_local_old_files() {
  local target_dir="$1"
  local label="$2"

  if [[ ! -d "$target_dir" ]]; then
    log "INFO local cleanup skipped: $label directory does not exist: $target_dir"
    return 0
  fi

  log "INFO local cleanup started: label=$label dir=$target_dir retention_days=$RETENTION_DAYS"
  find "$target_dir" -type f -mtime +"$RETENTION_DAYS" -print -delete |
    while IFS= read -r removed_file; do
      log "INFO removed expired local $label: $removed_file"
    done

  find "$target_dir" -type d -empty -print -delete |
    while IFS= read -r removed_dir; do
      log "INFO removed empty local directory: $removed_dir"
    done

  log "INFO local cleanup completed: label=$label"
}

cd "$APP_DIR"

log "INFO run started"
log "INFO app_dir=$APP_DIR config=$CONFIG_FILE backup_dir=$BACKUP_DIR log_dir=$LOG_DIR retention_days=$RETENTION_DAYS"

if [[ -f "$APP_DIR/backup.env" ]]; then
  log "INFO loading environment file: $APP_DIR/backup.env"
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/backup.env"
  set +a
else
  log "INFO environment file not found, skipping: $APP_DIR/backup.env"
fi

NAS_REMOTE="${NAS_REMOTE:-}"

if [[ ! -f "$APP_DIR/.venv/bin/activate" ]]; then
  log "ERROR Python virtual environment not found: $APP_DIR/.venv"
  exit 2
fi

# shellcheck disable=SC1091
. "$APP_DIR/.venv/bin/activate"

BACKUP_RC=0
log "INFO device backup started"
python "$APP_DIR/backup.py" --config "$CONFIG_FILE" || BACKUP_RC=$?
log "INFO device backup completed: rc=$BACKUP_RC"

cleanup_local_old_files "$BACKUP_DIR" "backup"
cleanup_local_old_files "$LOG_DIR" "log"

NAS_RC=0
if [[ -n "$NAS_REMOTE" ]]; then
  if command -v rclone >/dev/null 2>&1; then
    log "INFO NAS sync started: remote=$NAS_REMOTE"
    rclone copy "$BACKUP_DIR" "$NAS_REMOTE" \
      --create-empty-src-dirs \
      --log-file "$NAS_SYNC_LOG" \
      --log-level INFO || NAS_RC=$?
    log "INFO NAS sync completed: rc=$NAS_RC"

    log "INFO NAS cleanup started: remote=$NAS_REMOTE retention_days=$RETENTION_DAYS"
    rclone delete "$NAS_REMOTE" \
      --min-age "${RETENTION_DAYS}d" \
      --log-file "$NAS_CLEANUP_LOG" \
      --log-level INFO || NAS_RC=$?

    rclone rmdirs "$NAS_REMOTE" \
      --leave-root \
      --log-file "$NAS_CLEANUP_LOG" \
      --log-level INFO || NAS_RC=$?
    log "INFO NAS cleanup completed: rc=$NAS_RC"
  else
    NAS_RC=127
    log "ERROR rclone not found in PATH, skipping NAS sync and cleanup"
  fi
else
  log "INFO NAS_REMOTE is empty, skipping NAS sync and cleanup"
fi

log "INFO run completed: backup_rc=$BACKUP_RC nas_rc=$NAS_RC"

if [[ "$BACKUP_RC" -ne 0 ]]; then
  exit "$BACKUP_RC"
fi

exit "$NAS_RC"
