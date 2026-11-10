#!/usr/bin/env bash
# Weekly RESTORE DRILL (T3.7, hardened in PC2): a backup you have never restored is only a hope.
#   0. SAFETY: the scratch DB name must look like a drill DB and must never be the live DB / a system DB
#   1. take the newest Postgres dump (must be fresh; verify sha256, decrypt if needed)
#   2. restore it into a throw-away database  (never touches the live DB)
#   3. sanity checks: table count, django_migrations, row counts vs. the numbers recorded at dump time,
#      optional `manage.py migrate --check`
#   4. media check: sample files of the newest snapshot are readable / non-empty
#   5. write drills/LAST_DRILL.json (incl. how long the restore took = your measured RTO) + history
#   6. drop the scratch DB. Exit non-zero + Sentry alert + failed LAST_DRILL.json on ANY failure, also if
#      the script dies half-way (the EXIT hook in lib.sh calls cleanup() below).
# Needs a role that may CREATE/DROP DATABASE: set DRILL_ADMIN_DATABASE_URL (defaults to DATABASE_URL).
SCRIPT_NAME=restore_drill
source "$(dirname "$(readlink -f "$0")")/lib.sh"

DRILL_DB="$(env_get DRILL_DB learnscroll_restore_drill)"
DRILL_ADMIN_DATABASE_URL="$(env_get DRILL_ADMIN_DATABASE_URL "$DATABASE_URL")"
DRILL_ROW_TOLERANCE_PCT="$(env_get DRILL_ROW_TOLERANCE_PCT 98)"
DRILL_MEDIA_SAMPLE="$(env_get DRILL_MEDIA_SAMPLE 20)"
DRILL_MAX_DUMP_AGE_HOURS="$(env_get DRILL_MAX_DUMP_AGE_HOURS 36)"   # newest dump older than this = the nightly backup is late
LEARNSCROLL_PYTHON="$(env_get LEARNSCROLL_PYTHON /srv/learnscroll/venv/bin/python)"
LEARNSCROLL_BACKEND_DIR="$(env_get LEARNSCROLL_BACKEND_DIR /srv/learnscroll/backend)"
TMP_PLAIN=""
FAILURES=()
WARNINGS=()
DRILLS_DIR="$BACKUP_DIR/drills"
DRILL_DB_SAFE=0          # 1 only after the name checks passed -> only then may cleanup() DROP it
DRILL_RECORDED=0         # 1 once LAST_DRILL.json was written by the normal path
DRILL_STARTED=0
PGDATABASE_ADMIN=""

# Runs on EVERY exit (called from lib.sh _guard_on_exit).
cleanup() {
  [[ -n "$TMP_PLAIN" ]] && rm -f "$TMP_PLAIN"
  if (( DRILL_DB_SAFE )) && [[ -n "$PGDATABASE_ADMIN" ]]; then
    PGDATABASE="$PGDATABASE_ADMIN" psql -qAtc "DROP DATABASE IF EXISTS \"$DRILL_DB\" WITH (FORCE)" >/dev/null 2>&1 || true
  fi
  # Died before the normal result was written (die / set -e / SIGTERM)? Then the marker must say FAILED,
  # otherwise `check_backups` would keep showing last week's green drill.
  if (( DRILL_STARTED && ! DRILL_RECORDED )); then
    local rec
    rec="{\"kind\":\"restore_drill\",\"status\":\"failed\",\"failures\":$(json_array "${FAILURES[@]:-}" "aborted before completion -- see: journalctl -u learnscroll-restore-drill"),\"warnings\":$(json_array "${WARNINGS[@]:-}"),\"finished_at\":\"$(date -u +%FT%TZ)\"}"
    mkdir -p "$DRILLS_DIR" 2>/dev/null || true
    write_marker "$DRILLS_DIR/LAST_DRILL.json" "$rec" 2>/dev/null || true
    printf '%s\n' "$rec" >> "$DRILLS_DIR/history.jsonl" 2>/dev/null || true
  fi
}

fail() { FAILURES+=("$*"); log "CHECK FAILED: $*"; }
warn() { WARNINGS+=("$*"); log "warning: $*"; }

# JSON array of the non-empty arguments ("${arr[@]:-}" expands to one empty string for an empty array).
json_array() { python3 -c 'import json,sys;print(json.dumps([a for a in sys.argv[1:] if a]))' "$@"; }

main() {
  need psql; need pg_restore; need sha256sum; need python3
  mkdir -p "$DRILLS_DIR"
  local pgdir="$BACKUP_DIR/postgres" drills="$DRILLS_DIR"
  local started; started="$(date -u +%s)"
  DRILL_STARTED=1

  # ---- 0. safety: which database are we allowed to DROP + CREATE?
  local live_db; live_db="$(url_part "$DATABASE_URL" dbname 2>/dev/null || true)"
  valid_dbname "$DRILL_DB"      || die "refusing: DRILL_DB='$DRILL_DB' is not a plain identifier ([A-Za-z_][A-Za-z0-9_]*, max 63)"
  reserved_dbname "$DRILL_DB"   && die "refusing: DRILL_DB='$DRILL_DB' is a system database"
  [[ "$DRILL_DB" == *restore_drill* ]] || die "refusing: DRILL_DB must contain 'restore_drill' (got '$DRILL_DB') so a typo can never point the drill at a real database"
  [[ -z "$live_db" || "$DRILL_DB" != "$live_db" ]] || die "refusing: DRILL_DB equals the LIVE database name ('$live_db')"
  local admin_db; admin_db="$(url_part "$DRILL_ADMIN_DATABASE_URL" dbname 2>/dev/null || true)"; admin_db="${admin_db:-postgres}"
  [[ "$admin_db" != "$DRILL_DB" ]] || die "refusing: the admin URL points at the drill DB itself"
  DRILL_DB_SAFE=1
  log "drill target: database '$DRILL_DB' on host '$(url_part "$DRILL_ADMIN_DATABASE_URL" host)' (live DB '${live_db:-?}' on host '$(url_part "$DATABASE_URL" host 2>/dev/null || echo '?')' is never touched)"

  # ---- 1. locate + verify the newest dump
  local dump="" marker="$pgdir/LATEST.json"
  if [[ -f "$marker" ]]; then dump="$pgdir/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["file"])' "$marker")"; fi
  if [[ ! -f "$dump" ]]; then
    dump="$(ls -1t "$pgdir"/learnscroll_*.dump* 2>/dev/null | grep -v '\.sha256$' | head -n1 || true)"
    [[ -f "$dump" ]] && warn "LATEST.json missing/unusable -- using the newest file by mtime"
  fi
  [[ -f "$dump" ]] || die "no Postgres dump found in $pgdir"
  log "drill uses: $(basename "$dump")"

  # An old dump means the NIGHTLY backup is not running -- a green drill on a week-old file would hide that.
  local age_h; age_h=$(( ( $(date -u +%s) - $(stat -c %Y "$dump") ) / 3600 ))
  if (( age_h > DRILL_MAX_DUMP_AGE_HOURS )); then
    fail "newest dump is ${age_h}h old (limit ${DRILL_MAX_DUMP_AGE_HOURS}h) -- the nightly backup is late or failing"
  fi

  if ! ( cd "$pgdir" && sha256sum -c "$(basename "$dump").sha256" >/dev/null 2>&1 ); then
    die "sha256 mismatch (or .sha256 file missing) for $(basename "$dump") -- refusing to restore a file that does not match its checksum"
  fi

  local plain="$dump"
  if [[ "$dump" == *.gpg ]]; then
    need gpg; TMP_PLAIN="$(mktemp "$BACKUP_DIR/.drill.XXXXXX.dump")"
    gpg --batch --yes -o "$TMP_PLAIN" -d "$dump" || die "gpg decrypt failed (is the private key in the learnscroll user's keyring?)"
    plain="$TMP_PLAIN"
  fi

  # ---- 2. restore into the scratch DB
  pg_env_from_url "$DRILL_ADMIN_DATABASE_URL"
  PGDATABASE_ADMIN="${PGDATABASE:-postgres}"
  local t0 restore_s=0
  PGDATABASE="$PGDATABASE_ADMIN" psql -qAtc "DROP DATABASE IF EXISTS \"$DRILL_DB\" WITH (FORCE)" >/dev/null
  PGDATABASE="$PGDATABASE_ADMIN" psql -qAtc "CREATE DATABASE \"$DRILL_DB\"" >/dev/null
  t0="$(date -u +%s)"
  if pg_restore --dbname="$DRILL_DB" --no-owner --no-acl --exit-on-error --jobs=2 "$plain" 2> "$drills/last_restore.stderr"; then
    restore_s=$(( $(date -u +%s) - t0 )); log "pg_restore finished in ${restore_s}s"
  else
    restore_s=$(( $(date -u +%s) - t0 ))
    log "pg_restore stderr (last lines): $(tail -n 5 "$drills/last_restore.stderr" | tr '\n' ' ')"
    fail "pg_restore failed (see $drills/last_restore.stderr)"
  fi

  # ---- 3. sanity checks on the restored copy
  q() { PGDATABASE="$DRILL_DB" psql -qAtc "$1" 2>/dev/null || echo ""; }
  local tables migs
  tables="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
  migs="$(q "SELECT count(*) FROM django_migrations")"
  log "restored: ${tables:-?} tables, ${migs:-?} applied migrations"
  [[ "${tables:-0}" -ge 10 ]] 2>/dev/null || fail "only ${tables:-0} tables in the restored DB"
  [[ "${migs:-0}" -ge 1 ]] 2>/dev/null || fail "django_migrations is empty in the restored DB"

  row_count_check "$DRILL_DB" "$marker" "$DRILL_ROW_TOLERANCE_PCT"
  local p; for p in "${RC_PROBLEMS[@]:-}"; do [[ -n "$p" ]] && fail "$p"; done

  if [[ -x "$LEARNSCROLL_PYTHON" && -d "$LEARNSCROLL_BACKEND_DIR" ]]; then
    local base_url; base_url="postgres://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${DRILL_DB}"
    if ( cd "$LEARNSCROLL_BACKEND_DIR" && DATABASE_URL="$base_url" SENTRY_DSN="" "$LEARNSCROLL_PYTHON" manage.py migrate --check >/dev/null 2>&1 ); then
      log "  django: no unapplied migrations"
    else
      warn "django reports unapplied migrations on the restored copy (normal if a deploy happened after the dump)"
    fi
  else
    warn "skipped django check ($LEARNSCROLL_PYTHON / $LEARNSCROLL_BACKEND_DIR not found)"
  fi

  # ---- 4. media sample check
  local mmark="$BACKUP_DIR/media/LATEST.json" media_checked=0
  if [[ -f "$mmark" ]] && grep -q '"mode":"local"' "$mmark"; then
    local snap; snap="$BACKUP_DIR/media/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["snapshot"])' "$mmark")"
    if [[ -d "$snap" ]]; then
      local f
      while IFS= read -r f; do
        { [[ -s "$f" ]] && head -c 1 "$f" >/dev/null 2>&1; } || fail "unreadable/empty media file in snapshot: ${f#$snap/}"
        media_checked=$((media_checked + 1))
      done < <(find "$snap" -type f | shuf -n "$DRILL_MEDIA_SAMPLE")
      log "  media: $media_checked sample files readable"
    else
      fail "media snapshot directory missing: $snap"
    fi
  else
    warn "media check skipped (S3 mode or no local media backup yet)"
  fi

  # ---- 5. record the result
  local status="ok"; (( ${#FAILURES[@]} == 0 )) || status="failed"
  local total=$(( $(date -u +%s) - started ))
  local rec="{\"kind\":\"restore_drill\",\"status\":\"$status\",\"dump\":\"$(basename "$dump")\",\"restore_seconds\":${restore_s:-0},\"total_seconds\":$total,\"tables\":${tables:-0},\"media_files_checked\":$media_checked,\"failures\":$(json_array "${FAILURES[@]:-}"),\"warnings\":$(json_array "${WARNINGS[@]:-}"),\"finished_at\":\"$(date -u +%FT%TZ)\"}"
  write_marker "$drills/LAST_DRILL.json" "$rec"; printf '%s\n' "$rec" >> "$drills/history.jsonl"
  DRILL_RECORDED=1
  METRIC_EXTRA="learnscroll_restore_drill_restore_seconds ${restore_s:-0}"$'\n'"learnscroll_restore_drill_failed_checks ${#FAILURES[@]}"

  if [[ "$status" != "ok" ]]; then
    die "restore drill FAILED: ${FAILURES[*]}"
  fi
  log "restore drill OK -- measured restore time ${restore_s}s (total ${total}s)"
}

run_guarded learnscroll-restore-drill restore-drill main restore_drill
