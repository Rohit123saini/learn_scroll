#!/usr/bin/env bash
# P-J1 -- roll staging back to a previous image tag.  bash deploy/staging/rollback.sh <tag>
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG="${1:?usage: rollback.sh <previous image tag>}"
docker image inspect "learnscroll-backend:$TAG" >/dev/null
IMAGE_TAG="$TAG" docker compose -f deploy/staging/docker-compose.staging.yml --env-file deploy/staging/.env.staging \
  up -d --no-build web worker beat
echo "Rolled back to $TAG. NOTE: database migrations are NOT reversed -- see ROLLBACK.md."
