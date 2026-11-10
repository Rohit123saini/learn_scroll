#!/usr/bin/env bash
# REAL restore procedure (T3.7, hardened in PC2): restores a dump into a NEW database so the live DB is never
# overwritten in place. You then swap names during a short maintenance window (steps printed at the end).
#
#   ./restore_postgres.sh [--file /path/learnscroll_X.dump[.gpg]] [--target-db NAME] [--from-remote]
#                         [--owner ROLE] [--dry-run] [--yes]
#
#   --file         default: newest dump from LATEST.json
#   --from-remote  download the newest dump from $BACKUP_REMOTE/postgres first (server lost / disk lost)
#   --target-db    default: learnscroll_restored_<UTC timestamp>. Must be a NEW plain name; the live DB,
#                  system DBs and existing DBs are refused.
#   --owner        role that will own the restored objects (default: the user in DATABASE_URL, i.e. the app role).
#                  Dumps are taken with --no-owner, so without this the objects belong to the admin role and the
#                  app gets "permission denied" after the swap.
#   --dry-run      verify checksum / decrypt / safety checks and print the plan; creates NOTHING
#   --yes          skip the interactive "type the database name" prompt (automation only)
#
# Safety: without --yes you must type the target DB name; the summary (server, live DB, dump, age) is shown first.
# If the restore fails half-way the half-restored target DB is dropped again (it is always one this script created).
SCRIPT_NAME=restore_postgres
source "$(dirname "$(readlink -f "$0")")/lib.sh"
need psql; need pg_restore; need sha256sum; need python3

FILE=""; TARGET=""; REMOTE=false; YES=false; DRY=false; OWNER=""
while (( $# )); do
  case "$1" in
    --file) [[ $# -ge 2 ]] || die "--file needs a value"; FILE="$2"; shift 2;;
    --target-db) [[ $# -ge 2 ]] || die "--target-db needs a value"; TARGET="$2"; shift 2;;
    --owner) [[ $# -ge 2 ]] || die "--owner needs a value"; OWNER="$2"; shift 2;;
    --from-remote) REMOTE=true; shift;;
    --dry-run) DRY=true; shift;;
    --yes) YES=true; shift;;
    -h|--help) sed -n '2,22p' "$0"; exit 0;;
    *) die "unknown option: $1";;
  esac
done

tmp=""; created=0; finished=0; admin_db="postgres"
cleanup() {
  [[ -n "$tmp" ]] && rm -f "$tmp"
  if (( created && ! finished )); then
    log "restore did NOT finish -- dropping the half-restored database $TARGET"
    PGDATABASE="$admin_db" psql -qAtc "DROP DATABASE IF EXISTS \"${TARGET}\" WITH (FORCE)" >/dev/null 2>&1 \
      || log "could not drop $TARGET automatically: run  psql -d postgres -c 'DROP DATABASE \"$TARGET\"'"
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# ---- which database is the LIVE one? (parsed before pg_env_from_url switches to the admin URL)
LIVE_DB="$(url_part "$DATABASE_URL" dbname 2>/dev/null || true)"
APP_ROLE="$(url_part "$DATABASE_URL" user 2>/dev/null || true)"
LIVE_HOST="$(url_part "$DATABASE_URL" host 2>/dev/null || true)"

# ---- 1. pick + verify the dump
pgdir="$BACKUP_DIR/postgres"; mkdir -p "$pgdir"
if $REMOTE; then
  [[ -n "$BACKUP_REMOTE" ]] || die "BACKUP_REMOTE is not set"
  need rclone
  newest="$(rclone lsf "$BACKUP_REMOTE/postgres/" --files-only | grep -E '^learnscroll_.*\.dump(\.gpg)?$' | sort | tail -n1)"
  [[ -n "$newest" ]] || die "no dumps found in $BACKUP_REMOTE/postgres/"
  log "downloading $newest"
  rclone copy "$BACKUP_REMOTE/postgres/$newest" "$pgdir/" --checksum
  rclone copy "$BACKUP_REMOTE/postgres/$newest.sha256" "$pgdir/" --checksum
  FILE="$pgdir/$newest"
fi
if [[ -z "$FILE" && -f "$pgdir/LATEST.json" ]]; then
  FILE="$pgdir/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["file"])' "$pgdir/LATEST.json")"
fi
[[ -f "$FILE" ]] || die "dump not found: ${FILE:-<none>} (use --file, or --from-remote on a new server)"
( cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256" ) || die "checksum mismatch (or .sha256 missing) -- do NOT restore this file"
dump_age_h=$(( ( $(date -u +%s) - $(stat -c %Y "$FILE") ) / 3600 ))
dump_mb=$(( $(stat -c %s "$FILE") / 1048576 ))

# ---- 2. target database: new, plain name, never live / system
[[ -n "$TARGET" ]] || TARGET="learnscroll_restored_$(date -u +%Y%m%dT%H%M%SZ)"
valid_dbname "$TARGET"    || die "refusing: --target-db '$TARGET' must be a plain identifier ([A-Za-z_][A-Za-z0-9_]*, max 63)"
reserved_dbname "$TARGET" && die "refusing: '$TARGET' is a system database"
[[ -z "$LIVE_DB" || "$TARGET" != "$LIVE_DB" ]] || die "refusing: '$TARGET' is the LIVE database. Restore into a NEW name and swap (printed below)."
if [[ -n "$OWNER" ]]; then valid_dbname "$OWNER" || die "refusing: --owner '$OWNER' is not a plain role name"; fi

plain="$FILE"
if [[ "$FILE" == *.gpg ]]; then
  need gpg; tmp="$(mktemp "$BACKUP_DIR/.restore.XXXXXX.dump")"
  gpg --batch --yes -o "$tmp" -d "$FILE" || die "gpg decrypt failed (private key in this user's keyring?)"
  plain="$tmp"
fi

pg_env_from_url "$(env_get DRILL_ADMIN_DATABASE_URL "$DATABASE_URL")"
admin_db="${PGDATABASE:-postgres}"
ADMIN_USER="${PGUSER:-}"; ADMIN_HOST="${PGHOST:-}"
[[ -n "$OWNER" || -z "$APP_ROLE" || "$APP_ROLE" == "$ADMIN_USER" ]] || OWNER="$APP_ROLE"
if [[ -n "$OWNER" ]]; then
  [[ "$(PGDATABASE="$admin_db" psql -qAtc "SELECT 1 FROM pg_roles WHERE rolname='${OWNER}'")" == "1" ]] \
    || die "role '$OWNER' does not exist on $ADMIN_HOST -- create it first (CREATE ROLE \"$OWNER\" LOGIN PASSWORD '...';) or pass --owner"
fi
if PGDATABASE="$admin_db" psql -qAtc "SELECT 1 FROM pg_database WHERE datname='${TARGET}'" | grep -q 1; then
  die "database $TARGET already exists on $ADMIN_HOST -- pick another --target-db (nothing is overwritten)"
fi
live_exists=false
if [[ -n "$LIVE_DB" ]] && PGDATABASE="$admin_db" psql -qAtc "SELECT 1 FROM pg_database WHERE datname='${LIVE_DB}'" | grep -q 1; then live_exists=true; fi

# ---- 3. show the plan, ask for confirmation
cat <<MSG

  RESTORE PLAN
    server        : ${ADMIN_HOST:-?}   (as role ${ADMIN_USER:-?})
    live database : ${LIVE_DB:-<unknown>} on ${LIVE_HOST:-?}  (exists on this server: $live_exists)  -- NOT touched
    new database  : ${TARGET}
    owner         : ${OWNER:-<the admin role itself>}
    dump          : $(basename "$FILE")  (${dump_mb} MB, ${dump_age_h} h old)
MSG
(( dump_age_h > 48 )) && log "warning: this dump is more than 48 h old -- you will lose everything written since then"
if $DRY; then log "dry run: checksum + safety checks passed, nothing was created"; finished=1; exit 0; fi

if ! $YES; then
  [[ -t 0 ]] || die "no terminal for the confirmation prompt -- run it from a shell, or pass --yes for automation"
  read -r -p "  Type the new database name ($TARGET) to continue, anything else aborts: " answer
  [[ "$answer" == "$TARGET" ]] || die "confirmation did not match -- aborted, nothing was changed"
fi

# ---- 4. restore
log "creating $TARGET and restoring $(basename "$FILE")"
t0="$(date -u +%s)"
if [[ -n "$OWNER" ]]; then
  PGDATABASE="$admin_db" psql -qAtc "CREATE DATABASE \"${TARGET}\" OWNER \"${OWNER}\"" \
    || die "CREATE DATABASE ... OWNER \"$OWNER\" failed. The admin role must be a member of $OWNER:  GRANT \"$OWNER\" TO \"$ADMIN_USER\";"
  created=1
  pg_restore --dbname="$TARGET" --no-owner --no-acl --role="$OWNER" --exit-on-error --jobs=4 "$plain"
else
  PGDATABASE="$admin_db" psql -qAtc "CREATE DATABASE \"${TARGET}\""
  created=1
  pg_restore --dbname="$TARGET" --no-owner --no-acl --exit-on-error --jobs=4 "$plain"
fi
finished=1
restore_s=$(( $(date -u +%s) - t0 ))
log "restore complete into database: $TARGET (${restore_s}s)"

# ---- 5. quick verification (warnings only: you decide)
tables="$(PGDATABASE="$TARGET" psql -qAtc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo '?')"
migs="$(PGDATABASE="$TARGET" psql -qAtc "SELECT count(*) FROM django_migrations" 2>/dev/null || echo '?')"
log "verify: ${tables} tables, ${migs} applied migrations"
marker="$pgdir/LATEST.json"
if [[ -f "$marker" && "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("file",""))' "$marker" 2>/dev/null)" == "$(basename "$FILE")" ]]; then
  row_count_check "$TARGET" "$marker" 98
  for p in "${RC_PROBLEMS[@]:-}"; do [[ -n "$p" ]] && log "VERIFY WARNING: $p"; done
else
  log "verify: row counts skipped (this dump is not the one in LATEST.json)"
fi

if $live_exists; then
cat <<MSG

NEXT STEPS (maintenance window) -- the live database is '$LIVE_DB'
  1. sudo systemctl stop learnscroll-celery-worker learnscroll-celery-beat   # and your gunicorn/daphne service
  2. psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$LIVE_DB','$TARGET') AND pid <> pg_backend_pid();"
  3. psql -d postgres -c 'ALTER DATABASE "$LIVE_DB" RENAME TO "${LIVE_DB}_old_$(date -u +%Y%m%d)";'
  4. psql -d postgres -c 'ALTER DATABASE "$TARGET" RENAME TO "$LIVE_DB";'
     (wrong dump? undo: rename both back the same way -- the old database is still there)
  5. vacuumdb --analyze-only -d "$LIVE_DB"        # a restore has no planner statistics yet
  6. cd $(env_get LEARNSCROLL_BACKEND_DIR /srv/learnscroll/backend) && ../venv/bin/python manage.py migrate --check
  7. sudo systemctl start the services again, then hit /readyz/ and log in with a test account.
  8. Media: restore from the latest snapshot ($BACKUP_DIR/media/snap_*) or rclone copy from \$BACKUP_REMOTE/media/current.
MSG
else
cat <<MSG

NEXT STEPS (fresh server -- no live database exists here yet)
  1. sudo systemctl stop learnscroll-celery-worker learnscroll-celery-beat   # if they run
  2. psql -d postgres -c 'ALTER DATABASE "$TARGET" RENAME TO "${LIVE_DB:-learnscroll}";'
  3. vacuumdb --analyze-only -d "${LIVE_DB:-learnscroll}"
  4. cd $(env_get LEARNSCROLL_BACKEND_DIR /srv/learnscroll/backend) && ../venv/bin/python manage.py migrate --check
  5. start the services, hit /readyz/, log in with a test account.
  6. Media: rclone copy \$BACKUP_REMOTE/media/current <MEDIA_ROOT>   (see deploy/RESTORE_DRILL_CHECKLIST.md)
MSG
fi
