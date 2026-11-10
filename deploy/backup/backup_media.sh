#!/usr/bin/env bash
# Daily MEDIA backup (T3.7) -- user uploads under MEDIA_ROOT.
#   * USE_S3_STORAGE=true  -> media lives in S3: nothing to copy here. The backup is S3 *versioning*
#     (+ optional cross-region replication); see deploy/BACKUP_RESTORE_RUNBOOK.md section "S3 media".
#   * local disk           -> hard-linked rsync snapshots (cheap: only changed files take space)
#                             + optional off-site `rclone sync` that keeps deleted/overwritten files.
SCRIPT_NAME=backup_media
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# Called by run_guarded on every exit: drop an unfinished snapshot (the next run starts clean).
cleanup() { rm -rf "$BACKUP_DIR"/media/snap_*.partial 2>/dev/null || true; }

main() {
  need flock; need python3
  local dir="$BACKUP_DIR/media"; mkdir -p "$dir"
  if [[ "$USE_S3_STORAGE" == "true" || "$USE_S3_STORAGE" == "True" ]]; then
    log "USE_S3_STORAGE is on -- media is in S3, relying on bucket versioning. Nothing to do."
    write_marker "$dir/LATEST.json" "{\"kind\":\"media\",\"status\":\"ok\",\"mode\":\"s3\",\"finished_at\":\"$(date -u +%FT%TZ)\"}"
    return 0
  fi
  need rsync
  [[ -d "$MEDIA_ROOT" ]] || die "MEDIA_ROOT does not exist: $MEDIA_ROOT (set LEARNSCROLL_MEDIA_ROOT)"

  local ts snap prev started; ts="$(date -u +%Y%m%dT%H%M%SZ)"; started="$(date -u +%s)"
  snap="$dir/snap_${ts}"
  prev="$(ls -1d "$dir"/snap_* 2>/dev/null | grep -v '\.partial$' | tail -n1 || true)"
  log "snapshotting $MEDIA_ROOT -> $snap (link-dest: ${prev:-none})"
  rsync -a --delete ${prev:+--link-dest="$prev"} "$MEDIA_ROOT"/ "${snap}.partial"/
  mv "${snap}.partial" "$snap"

  local files bytes
  files="$(find "$snap" -type f | wc -l)"
  bytes="$(du -sb "$snap" | cut -f1)"

  local remote_ok=false
  if [[ -n "$BACKUP_REMOTE" ]]; then
    need rclone
    log "syncing to $BACKUP_REMOTE/media/current (deleted/overwritten files kept under media/deleted/$ts)"
    rclone sync "$MEDIA_ROOT" "$BACKUP_REMOTE/media/current" --backup-dir "$BACKUP_REMOTE/media/deleted/$ts" --checksum
    remote_ok=true
  else
    log "warning: BACKUP_REMOTE is not set -- media snapshots exist only on this server"
  fi

  write_marker "$dir/LATEST.json" "{\"kind\":\"media\",\"status\":\"ok\",\"mode\":\"local\",\"snapshot\":\"$(basename "$snap")\",\"files\":$files,\"bytes\":$bytes,\"remote_uploaded\":$remote_ok,\"finished_at\":\"$(date -u +%FT%TZ)\",\"duration_s\":$(( $(date -u +%s) - started ))}"

  # retention: drop snapshots older than BACKUP_RETENTION_DAYS (hard links keep shared files alive)
  local s d age
  for s in "$dir"/snap_*; do
    [[ -d "$s" && "$s" != *.partial ]] || continue
    d="$(basename "$s")"; d="${d#snap_}"; d="${d:0:8}"
    age=$(( ( $(date -u +%s) - $(date -u -d "$d" +%s) ) / 86400 ))
    (( age > BACKUP_RETENTION_DAYS )) && { log "retention: removing $(basename "$s")"; rm -rf "$s"; }
  done
  METRIC_EXTRA="learnscroll_backup_media_files{backup=\"media\"} ${files}"$'\n'"learnscroll_backup_media_bytes{backup=\"media\"} ${bytes}"
  log "done: $files files, $bytes bytes"
}

run_guarded learnscroll-backup-media backup-media main media
