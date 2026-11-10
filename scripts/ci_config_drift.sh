#!/usr/bin/env bash
# T3.5 (R10) — CI gate for config drift. Run from backend/ (or anywhere; it cd's there).
#
#   1. static scan   — NO Django needed. settings.py hazards (unconditional ALLOWED_HOSTS='*',
#                      hard-coded / truthy-default DEBUG) + NEW stale comments (baseline-aware).
#   2. pure tests    — drift scanner, doubts hub core, sizing model (no DB).
#   3. full command  — `manage.py check_config_drift --strict --production`: every throttle scope
#                      has a rate, every periodic task is scheduled, every model is in admin,
#                      every APIView is routed. Needs the project's Python deps.
#
# Step 3 is skipped (loudly) when Django isn't importable, unless REQUIRE_DJANGO=1 (CI sets it).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/3 static drift scan (no Django) =="
python core/drift_scan.py --fail-on warning

echo "== 2/3 pure unit tests (no DB) =="
python -m unittest core.tests_drift_scan doubts.tests_hub loadtest.tests_sizing

echo "== 3/3 check_config_drift --strict --production =="
if ! python -c "import django" 2>/dev/null; then
  if [ "${REQUIRE_DJANGO:-0}" = "1" ]; then
    echo "Django is not installed but REQUIRE_DJANGO=1" >&2; exit 1
  fi
  echo "SKIPPED: Django not importable here. Install the project requirements and re-run (CI does)."; exit 0
fi

# Throw-away values: load settings.py the way production would, without talking to any real service.
# (settings.py itself refuses DEBUG=False with an empty/wildcard ALLOWED_HOSTS.)
export SECRET_KEY="${SECRET_KEY:-ci-only-not-a-real-secret-key-0123456789abcdef}"
export DEBUG="False"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-ci.example.com}"
export DJANGO_ENV="production"
unset REDIS_URL CELERY_BROKER_URL SENTRY_DSN || true   # in-memory fallbacks
# P-C1: settings.py refuses to boot in production mode without a Postgres URL. Nothing here connects to it.
export DATABASE_URL="${DATABASE_URL:-postgres://ci:ci@127.0.0.1:5432/ci}"
python manage.py check_config_drift --strict --production --fail-on warning
