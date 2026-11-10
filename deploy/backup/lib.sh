#!/usr/bin/env bash
# Shared helpers for the LearnScroll backup / restore-drill scripts (T3.7).
# Sourced, never executed directly.
set -Eeuo pipefail

ENV_FILE="${LEARNSCROLL_ENV_FILE:-/srv/learnscroll/.env}"

# Read ONE key from the .env file without `source`-ing it (values with spaces / & / $ would
# be executed or mangled by the shell). Strips optional quotes and CRLF.
env_get() {
  local key="$1" default="${2:-}" line val
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return; fi
  if [[ -f "$ENV_FILE" ]]; then
    line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n1 || true)"
    if [[ -n "$line" ]]; then
      val="${line#*=}"; val="${val%$'\r'}"
      val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
      printf '%s' "$val"; return
    fi
  fi
  printf '%s' "$default"
}

BACKUP_DIR="$(env_get BACKUP_DIR /var/backups/learnscroll)"
BACKUP_RETENTION_DAYS="$(env_get BACKUP_RETENTION_DAYS 14)"
BACKUP_KEEP_WEEKS="$(env_get BACKUP_KEEP_WEEKS 8)"
BACKUP_REMOTE="$(env_get BACKUP_REMOTE '')"            # rclone remote, e.g. s3backup:learnscroll-backups
BACKUP_GPG_RECIPIENT="$(env_get BACKUP_GPG_RECIPIENT '')"  # optional: encrypt dumps for this key
BACKUP_MIN_DUMP_BYTES="$(env_get BACKUP_MIN_DUMP_BYTES 10240)"
BACKUP_SANITY_TABLES="$(env_get BACKUP_SANITY_TABLES 'django_migrations,login_user,core_notification,post_events')"
DATABASE_URL="$(env_get DATABASE_URL '')"
SENTRY_DSN="$(env_get SENTRY_DSN '')"
MEDIA_ROOT="$(env_get LEARNSCROLL_MEDIA_ROOT /srv/learnscroll/backend/media)"
USE_S3_STORAGE="$(env_get USE_S3_STORAGE false)"

log()  { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "${SCRIPT_NAME:-backup}" "$*"; }
die()  { log "ERROR: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

# ---- database-name safety (PC2) -----------------------------------------------------------------
# Names that reach DROP/CREATE DATABASE must be plain identifiers (no quotes / spaces / SQL) and must
# never be a system database or the LIVE application database.
valid_dbname()    { [[ "${1:-}" =~ ^[a-zA-Z_][a-zA-Z0-9_]{0,62}$ ]]; }
reserved_dbname() { case "${1:-}" in postgres|template0|template1|"") return 0;; *) return 1;; esac; }
# url_part <url> <dbname|host|user>   (same parsing as pg_env_from_url: no URL-decoding)
url_part() {
  python3 - "$1" "$2" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
print({"dbname": (u.path or "/")[1:], "host": u.hostname or "", "user": u.username or ""}[sys.argv[2]])
PY
}

# Row counts recorded at dump time (marker "row_counts") vs. a restored database.
# usage: row_count_check <db> <marker.json> [tolerance_pct]   -> fills RC_PROBLEMS (empty = all good)
RC_PROBLEMS=()
row_count_check() {
  local db="$1" marker="$2" tol="${3:-98}" tbl expected got
  RC_PROBLEMS=()
  [[ -f "$marker" ]] || return 0
  while IFS='=' read -r tbl expected; do
    [[ "$tbl" =~ ^[a-zA-Z0-9_]+$ && "$expected" =~ ^[0-9]+$ ]] || continue
    got="$(PGDATABASE="$db" psql -qAtc "SELECT count(*) FROM public.${tbl}" 2>/dev/null || true)"
    if [[ ! "$got" =~ ^[0-9]+$ ]]; then RC_PROBLEMS+=("table ${tbl} missing/unreadable after restore"); continue; fi
    if (( got * 100 < expected * tol )); then
      RC_PROBLEMS+=("table ${tbl}: restored ${got} rows but ${expected} existed at dump time")
    else
      log "  rows ${tbl}: ${got} (dump-time ${expected}) ok"
    fi
  done < <(python3 -c 'import json,sys;[print(f"{k}={v}") for k,v in json.load(open(sys.argv[1])).get("row_counts",{}).items()]' "$marker" 2>/dev/null)
}

# Export libpq env vars from DATABASE_URL (parsed exactly like settings.py does -- no URL-decoding --
# so the credentials match what Django itself uses). Keeps the password out of `ps` output.
pg_env_from_url() {
  local url="${1:-$DATABASE_URL}"
  [[ -n "$url" ]] || die "DATABASE_URL is empty (SQLite dev boxes: just copy db.sqlite3 while the app is stopped)"
  eval "$(python3 - "$url" <<'PY'
import shlex, sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
for k, v in (("PGHOST", u.hostname or ""), ("PGPORT", str(u.port or 5432)),
             ("PGUSER", u.username or ""), ("PGPASSWORD", u.password or ""),
             ("PGDATABASE", (u.path or "/")[1:])):
    print(f"export {k}={shlex.quote(v)}")
PY
)"
}

# Sentry Crons check-in using only the DSN (no auth token needed). Silent no-op without a DSN.
# usage: sentry_checkin <monitor-slug> <in_progress|ok|error>
sentry_checkin() {
  [[ -n "$SENTRY_DSN" ]] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  local slug="$1" status="$2" rest key host project
  rest="${SENTRY_DSN#*://}"; key="${rest%%@*}"; rest="${rest#*@}"
  host="${rest%%/*}"; project="${rest##*/}"
  curl -fsS -m 10 -o /dev/null \
    "https://${host}/api/${project}/cron/${slug}/${key}/?status=${status}" \
    || log "warning: sentry check-in (${slug}/${status}) failed"
}

# Page someone through Sentry when a script fails (in addition to the missed/failed Cron check-in).
sentry_message() {
  [[ -n "$SENTRY_DSN" ]] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  local msg="$1" rest key host project
  rest="${SENTRY_DSN#*://}"; key="${rest%%@*}"; rest="${rest#*@}"
  host="${rest%%/*}"; project="${rest##*/}"
  curl -fsS -m 10 -o /dev/null -X POST "https://${host}/api/${project}/store/?sentry_version=7&sentry_key=${key}" \
    -H 'Content-Type: application/json' \
    -d "{\"message\":$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$msg"),\"level\":\"error\",\"logger\":\"backup\",\"environment\":\"$(env_get SENTRY_ENVIRONMENT production)\"}" \
    || true
}

# Atomic JSON marker writer: write_marker <path> <json>
write_marker() { local tmp="$1.tmp.$$"; printf '%s\n' "$2" > "$tmp" && mv -f "$tmp" "$1"; }

# Prometheus metrics for the backup jobs (node_exporter "textfile collector"). Silent no-op unless
# NODE_EXPORTER_TEXTFILE_DIR is set. The last-success time survives failed runs (kept in BACKUP_DIR), so
# "no success for 26 h" alerts work even when every run fails. Alert rules: deploy/monitoring/prometheus_backup_rules.yml
# usage: metrics_publish <backup: postgres|media|restore_drill> <1|0> [extra metric lines]
metrics_publish() {
  local job="$1" ok="$2" extra="${3:-}" dir now state last_ok out tmp dur
  dir="$(env_get NODE_EXPORTER_TEXTFILE_DIR '')"
  [[ -n "$dir" ]] || return 0
  if [[ ! -d "$dir" || ! -w "$dir" ]]; then log "warning: NODE_EXPORTER_TEXTFILE_DIR=$dir is not a writable directory -- metrics skipped"; return 0; fi
  now="$(date -u +%s)"; state="$BACKUP_DIR/.metrics_${job}.last_ok"
  if [[ "$ok" == "1" ]]; then printf '%s\n' "$now" > "$state" || true; fi
  last_ok="$(cat "$state" 2>/dev/null || echo 0)"
  dur=$(( now - ${GUARD_START:-$now} ))
  out="$dir/learnscroll_backup_${job}.prom"; tmp="$out.$$"
  {
    printf 'learnscroll_backup_last_run_ok{backup="%s"} %s\n' "$job" "$ok"
    printf 'learnscroll_backup_last_run_timestamp_seconds{backup="%s"} %s\n' "$job" "$now"
    printf 'learnscroll_backup_last_success_timestamp_seconds{backup="%s"} %s\n' "$job" "$last_ok"
    printf 'learnscroll_backup_last_duration_seconds{backup="%s"} %s\n' "$job" "$dur"
    [[ -z "$extra" ]] || printf '%s\n' "$extra"
  } > "$tmp" && mv -f "$tmp" "$out" || log "warning: could not write metrics file $out"
}

# Standard wrapper: run `main` with a lock, Sentry check-ins and an EXIT trap.
# The EXIT trap (not ERR) is what makes `die` -- plain `exit 1` -- alert too: an ERR trap never sees it.
# If the script defines a function named `cleanup`, it is called on every exit (success or failure).
# A script may set METRIC_EXTRA (newline-separated Prometheus lines) before it returns / fails.
# usage: run_guarded <sentry-monitor-slug> <lock-name> main [metrics-job]
GUARD_SLUG=""; GUARD_JOB=""; GUARD_START=0; GUARD_DONE=0; METRIC_EXTRA=""
_guard_on_exit() {
  local rc=$?
  trap - EXIT
  if declare -F cleanup >/dev/null 2>&1; then cleanup || true; fi
  if [[ -n "$GUARD_SLUG" && "$GUARD_DONE" != "1" ]]; then
    (( rc != 0 )) || rc=1
    log "FAILED (exit $rc)"
    metrics_publish "$GUARD_JOB" 0 "$METRIC_EXTRA" || true
    sentry_checkin "$GUARD_SLUG" error || true
    sentry_message "$GUARD_SLUG failed on $(hostname) (exit $rc)" || true
  fi
  exit "$rc"
}
run_guarded() {
  local slug="$1" lock="$2" fn="$3" job="${4:-$2}"
  mkdir -p "$BACKUP_DIR"
  exec 9>"$BACKUP_DIR/.${lock}.lock"
  flock -n 9 || die "another ${lock} run is still in progress"
  GUARD_SLUG="$slug"; GUARD_JOB="$job"; GUARD_START="$(date -u +%s)"; GUARD_DONE=0
  trap _guard_on_exit EXIT
  trap 'exit 143' TERM     # systemd timeout / `systemctl stop` -> still alerts + cleans up
  trap 'exit 130' INT
  sentry_checkin "$slug" in_progress
  "$fn"
  GUARD_DONE=1
  metrics_publish "$job" 1 "$METRIC_EXTRA" || true
  sentry_checkin "$slug" ok
}
