#!/usr/bin/env bash
# P-I -- turn requirements.in (bounds) into requirements.txt (exact pins + hashes).
#   bash scripts/pin_requirements.sh            # re-pin everything to the newest allowed versions
#   bash scripts/pin_requirements.sh --upgrade-package Django   # bump one package only
# Needs network + python 3.12 (the same minor version as the Dockerfile). Commit requirements.txt afterwards,
# then run deploy/tools/dependency_scan.py and the staging smoke test (scripts/smoke_test.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install --quiet "pip-tools>=7.4"
python -m piptools compile --quiet --resolver=backtracking --generate-hashes --strip-extras \
  --output-file requirements.txt "$@" requirements.in
python -m piptools compile --quiet --resolver=backtracking --generate-hashes --strip-extras \
  --output-file requirements-dev.txt "$@" requirements-dev.in
echo "Wrote requirements.txt and requirements-dev.txt -- review the diff, then commit."
