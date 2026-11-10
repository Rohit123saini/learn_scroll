#!/usr/bin/env bash
# Nightly PostgreSQL backup (T3.7).
#   pg_dump custom format -> verify (pg_restore --list) -> sha256 -> [gpg] -> [rclone upload]
#   -> LATEST.json marker (read by `manage.py check_backups`) -> retention.
# Run by deploy/systemd/learnscroll-backup-postgres.timer. Safe to run by hand.
SCRIPT_NAME=backup_postgres
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# Called by run_guarded on every exit: a failed run must not leave a half-written dump behind.
cleanup() { rm -f "$BACKUP_DIR"/postgres/*.partial 2>/dev/null || true; }

main() {
  need pg_dump; need pg_restore; need psql; need sha256sum; need flock; need python3
  pg_env_from_url
  local dir="$BACKUP_DIR/postgres"; mkdir -p "$dir"
  local ts file started
  ts="$(date -u +%Y%m%dT%H%M%SZ)"; started="$(date -u +%s)"
  file="$dir/learnscroll_${ts}.dump"

  log "dumping database '$PGDATABASE' on $PGHOST -> $file"
  pg_dump --format=custom --compress=6 --no-owner --no-acl --file="${file}.partial"
  mv "${file}.partial" "$file"

  local bytes; bytes="$(stat -c %s "$file")"
  (( bytes >= BACKUP_MIN_DUMP_BYTES )) || die "dump is only ${bytes} bytes (< ${BACKUP_MIN_DUMP_BYTES}); refusing to treat it as a valid backup"
  # A truncated / corrupt custom-format dump fails here, long before anyone needs it.
  pg_restore --list "$file" >/dev/null || die "pg_restore --list failed: the dump is corrupt"

  # Row counts of a few key tables at dump time -> the restore drill compares against them.
  local counts="{" first=1 t n
  IFS=',' read -ra tables <<< "$BACKUP_SANITY_TABLES"
  for t in "${tables[@]}"; do
    t="${t// /}"; [[ "$t" =~ ^[a-zA-Z0-9_]+$ ]] || continue
    [[ "$(psql -Atqc "SELECT to_regclass('public.${t}') IS NOT NULL" 2>/dev/null || echo f)" == "t" ]] || continue
    n="$(psql -Atqc "SELECT count(*) FROM public.${t}" 2>/dev/null || echo -1)"
    [[ "$n" == "-1" ]] && continue
    (( first )) || counts+=","; first=0; counts+="\"$t\":$n"
  done
  counts+="}"

  ( cd "$dir" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256" )
  local sha; sha="$(cut -d' ' -f1 "${file}.sha256")"

  local final="$file" encrypted=false
  if [[ -n "$BACKUP_GPG_RECIPIENT" ]]; then
    need gpg
    gpg --batch --yes --trust-model always -r "$BACKUP_GPG_RECIPIENT" -o "${file}.gpg" -e "$file"
    ( cd "$dir" && sha256sum "$(basename "$file").gpg" > "$(basename "$file").gpg.sha256" )
    rm -f "$file" "${file}.sha256"; final="${file}.gpg"; encrypted=true
  fi

  # A dump that suddenly shrinks to < half of yesterday's usually means data loss / a wrong DATABASE_URL, not a
  # smaller database -- the dump itself "succeeds", so only a size comparison catches it (PC2).
  local final_bytes prev_bytes=0 shrunk=false
  final_bytes="$(stat -c %s "$final")"
  if [[ -f "$dir/LATEST.json" ]]; then
    prev_bytes="$(python3 -c 'import json,sys;print(int(json.load(open(sys.argv[1])).get("bytes",0)))' "$dir/LATEST.json" 2>/dev/null || echo 0)"
  fi
  if (( prev_bytes > 0 && final_bytes * 100 < prev_bytes * 50 )); then
    shrunk=true
    log "WARNING: dump is ${final_bytes} bytes, previous one was ${prev_bytes} (< 50%) -- check what was deleted"
    sentry_message "postgres dump shrank from ${prev_bytes} to ${final_bytes} bytes on $(hostname)"
  fi

  local remote_ok=false
  if [[ -n "$BACKUP_REMOTE" ]]; then
    need rclone
    log "uploading to $BACKUP_REMOTE/postgres/"
    rclone copy "$final" "$BACKUP_REMOTE/postgres/" --checksum
    rclone copy "${final}.sha256" "$BACKUP_REMOTE/postgres/" --checksum
    remote_ok=true
  else
    log "warning: BACKUP_REMOTE is not set -- the only copy of this backup is on THIS server (not disaster-proof)"
  fi

  write_marker "$dir/LATEST.json" "{\"kind\":\"postgres\",\"status\":\"ok\",\"file\":\"$(basename "$final")\",\"bytes\":$(stat -c %s "$final"),\"sha256\":\"$sha\",\"encrypted\":$encrypted,\"remote_uploaded\":$remote_ok,\"shrunk\":$shrunk,\"prev_bytes\":$prev_bytes,\"row_counts\":$counts,\"finished_at\":\"$(date -u +%FT%TZ)\",\"duration_s\":$(( $(date -u +%s) - started ))}"

  METRIC_EXTRA="learnscroll_backup_dump_bytes{backup=\"postgres\"} ${final_bytes}"$'\n'"learnscroll_backup_remote_uploaded{backup=\"postgres\"} $([[ "$remote_ok" == true ]] && echo 1 || echo 0)"
  retention "$dir"
  log "done: $(basename "$final") ($(stat -c %s "$final") bytes)"
}

# Keep every dump for BACKUP_RETENTION_DAYS, plus the Sunday dump of the last BACKUP_KEEP_WEEKS weeks.
retention() {
  local dir="$1" f name d age dow
  shopt -s nullglob
  for f in "$dir"/learnscroll_*.dump "$dir"/learnscroll_*.dump.gpg; do
    name="$(basename "$f")"; d="${name#learnscroll_}"; d="${d:0:8}"
    age=$(( ( $(date -u +%s) - $(date -u -d "$d" +%s) ) / 86400 ))
    dow="$(date -u -d "$d" +%u)"
    if (( age > BACKUP_RETENTION_DAYS )); then
      if [[ "$dow" == "7" ]] && (( age <= BACKUP_KEEP_WEEKS * 7 )); then continue; fi
      log "retention: removing $name (age ${age}d)"; rm -f "$f" "$f.sha256"
    fi
  done
}

run_guarded learnscroll-backup-postgres backup-postgres main postgres
