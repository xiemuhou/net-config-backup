#!/usr/bin/env bash
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/net-config-backup}"
CONFIG_FILE="${CONFIG_FILE:-$APP_DIR/devices.yaml}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
RETENTION_DAYS="${RETENTION_DAYS:-180}"

# abraunegg/onedrive config directory.
ONEDRIVE_CONFDIR="${ONEDRIVE_CONFDIR:-$APP_DIR/onedrive-conf}"

# Set to true if you want OneDrive to keep remote files deleted locally.
ONEDRIVE_NO_REMOTE_DELETE="${ONEDRIVE_NO_REMOTE_DELETE:-false}"

TODAY="$(date +%F)"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

RUN_LOG="$LOG_DIR/run-backup-onedrive-client-$TODAY.log"
ONEDRIVE_SYNC_LOG="$LOG_DIR/onedrive-client-sync-$TODAY.log"

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

ONEDRIVE_CONFDIR="${ONEDRIVE_CONFDIR:-$APP_DIR/onedrive-conf}"
ONEDRIVE_NO_REMOTE_DELETE="${ONEDRIVE_NO_REMOTE_DELETE:-false}"

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

ONEDRIVE_RC=0
if command -v onedrive >/dev/null 2>&1; then
  if [[ -d "$ONEDRIVE_CONFDIR" ]]; then
    log "INFO OneDrive client sync started: confdir=$ONEDRIVE_CONFDIR no_remote_delete=$ONEDRIVE_NO_REMOTE_DELETE"

    ONEDRIVE_ARGS=(
      --confdir="$ONEDRIVE_CONFDIR"
      --sync
      --upload-only
      --verbose
    )

    if [[ "$ONEDRIVE_NO_REMOTE_DELETE" == "true" ]]; then
      ONEDRIVE_ARGS+=(--no-remote-delete)
    fi

    onedrive "${ONEDRIVE_ARGS[@]}" >> "$ONEDRIVE_SYNC_LOG" 2>&1 || ONEDRIVE_RC=$?
    log "INFO OneDrive client sync completed: rc=$ONEDRIVE_RC"
  else
    ONEDRIVE_RC=2
    log "ERROR OneDrive config directory not found: $ONEDRIVE_CONFDIR"
  fi
else
  ONEDRIVE_RC=127
  log "ERROR onedrive command not found in PATH, skipping OneDrive client sync"
fi

log "INFO run completed: backup_rc=$BACKUP_RC onedrive_rc=$ONEDRIVE_RC"

if [[ "$BACKUP_RC" -ne 0 ]]; then
  exit "$BACKUP_RC"
fi

exit "$ONEDRIVE_RC"
