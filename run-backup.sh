#!/usr/bin/env bash
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/net-config-backup}"
CONFIG_FILE="${CONFIG_FILE:-$APP_DIR/devices.yaml}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
RETENTION_DAYS="${RETENTION_DAYS:-180}"

# Set this to your rclone remote path, for example:
# ONEDRIVE_REMOTE="onedrive:network-config-backups"
ONEDRIVE_REMOTE="${ONEDRIVE_REMOTE:-}"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

RUN_LOG="$LOG_DIR/run-backup-$(date +%F).log"
ONEDRIVE_SYNC_LOG="$LOG_DIR/onedrive-sync-$(date +%F).log"
ONEDRIVE_CLEANUP_LOG="$LOG_DIR/onedrive-cleanup-$(date +%F).log"

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

# Allow backup.env to define or override the OneDrive destination.
ONEDRIVE_REMOTE="${ONEDRIVE_REMOTE:-}"

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

RCLONE_RC=0
if [[ -n "$ONEDRIVE_REMOTE" ]]; then
  if command -v rclone >/dev/null 2>&1; then
    log "INFO OneDrive sync started: remote=$ONEDRIVE_REMOTE"
    rclone copy "$BACKUP_DIR" "$ONEDRIVE_REMOTE" \
      --create-empty-src-dirs \
      --log-file "$ONEDRIVE_SYNC_LOG" \
      --log-level INFO || RCLONE_RC=$?
    log "INFO OneDrive sync completed: rc=$RCLONE_RC"

    log "INFO OneDrive cleanup started: remote=$ONEDRIVE_REMOTE retention_days=$RETENTION_DAYS"
    rclone delete "$ONEDRIVE_REMOTE" \
      --min-age "${RETENTION_DAYS}d" \
      --log-file "$ONEDRIVE_CLEANUP_LOG" \
      --log-level INFO || RCLONE_RC=$?

    rclone rmdirs "$ONEDRIVE_REMOTE" \
      --leave-root \
      --log-file "$ONEDRIVE_CLEANUP_LOG" \
      --log-level INFO || RCLONE_RC=$?
    log "INFO OneDrive cleanup completed: rc=$RCLONE_RC"
  else
    RCLONE_RC=127
    log "ERROR rclone not found in PATH, skipping OneDrive sync and cleanup"
  fi
else
  log "INFO ONEDRIVE_REMOTE is empty, skipping OneDrive sync and cleanup"
fi

log "INFO run completed: backup_rc=$BACKUP_RC rclone_rc=$RCLONE_RC"

if [[ "$BACKUP_RC" -ne 0 ]]; then
  exit "$BACKUP_RC"
fi

exit "$RCLONE_RC"
