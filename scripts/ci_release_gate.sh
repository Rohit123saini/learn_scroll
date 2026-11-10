#!/usr/bin/env bash
# X1 — backend release gate. Same script runs locally and in CI (deploy/ci/github-ci.yml).
#   1. migrations drift      : makemigrations --check --dry-run   (a model change without a migration file fails)
#   2. deploy check          : check --deploy --fail-level WARNING (production-like env, throw-away secrets)
#   3. release_check rules   : pure unit tests of the env rules (no DB)  +  the command with --skip-env
#   4. config drift (T3.5)   : scripts/ci_config_drift.sh
#   5. security settings     : check_security_settings (X6: CSRF / ALLOWED_HOSTS / cookies / CORS / middleware + csrf_exempt scan)
# Needs the project's Python deps. Writes nothing. Not run by the author.
set -euo pipefail
cd "$(dirname "$0")/.."

# Throw-away values: load settings.py the way production would without touching a real service.
export DEBUG="False"
export DJANGO_ENV="production"
export SECRET_KEY="${SECRET_KEY:-ci-only-Zp2vQ7xLm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd}"
export JWT_SIGNING_KEY="${JWT_SIGNING_KEY:-ci-only-jwt-key-Lm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd0Ey}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-ci.example.com}"
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-https://ci.example.com}"
export SECURE_SSL_REDIRECT="true"
export LIVEKIT_WS_URL="${LIVEKIT_WS_URL:-wss://lk.ci-only.dev}"
unset SENTRY_DSN || true
# P-C1: production settings need a Postgres URL to load. CI / `make release-check` pass the real one; this is only a
# throw-away so the gate can run on a laptop without services (checks below do not need a live DB).
export DATABASE_URL="${DATABASE_URL:-postgres://ci:ci@127.0.0.1:5432/ci}"

echo "== 1/5 makemigrations --check --dry-run =="
python manage.py makemigrations --check --dry-run

echo "== 2/5 check --deploy --fail-level WARNING =="
python manage.py check --deploy --fail-level WARNING

echo "== 3/5 release_check unit tests + command (env rules skipped: CI has no real secrets) =="
python -m unittest core.tests_release_check
python manage.py release_check --skip-env --skip-deploy-check --skip-migrations

echo "== 4/5 config drift =="
REQUIRE_DJANGO=1 bash scripts/ci_config_drift.sh

echo "== 5/5 security settings (X6) =="
python -m unittest core.tests_security_settings_check
python manage.py check_security_settings --production --fail-on error

echo "release gate OK"
