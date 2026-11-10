#!/usr/bin/env bash
# P-J1 -- build, start, seed smoke users, run scripts/smoke_test.sh. Rolls back automatically if smoke fails.
#   BASE_URL=https://staging-api.learnscroll.app bash deploy/staging/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${BASE_URL:?set BASE_URL}"
C="docker compose -f deploy/staging/docker-compose.staging.yml --env-file deploy/staging/.env.staging"
PREV="$(docker inspect --format '{{index .Config.Image}}' learnscroll-staging-web-1 2>/dev/null || true)"
export IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
echo "$PREV" > deploy/staging/.previous_image || true
$C up -d --build
$C run --rm web python manage.py seed_smoke --confirm-staging --out /tmp/smoke_fixtures.json
$C run --rm web cat /tmp/smoke_fixtures.json > /tmp/smoke_fixtures.json 2>/dev/null || true
sleep 10
if SMOKE_FIXTURES=/tmp/smoke_fixtures.json HEALTHCHECK_TOKEN="${HEALTHCHECK_TOKEN:-}" bash scripts/smoke_test.sh; then
  echo "Deploy $IMAGE_TAG OK"
else
  echo "Smoke FAILED -> rolling back"; bash deploy/staging/rollback.sh "${PREV##*:}" || true; exit 1
fi
