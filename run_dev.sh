#!/usr/bin/env bash
# One-shot local backend: venv -> deps -> .env -> migrate -> tests(optional) -> server.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.dev.example .env
python manage.py check
python manage.py makemigrations --check --dry-run   # must say "No changes detected"
python manage.py migrate
[ "${RUN_TESTS:-0}" = "1" ] && python manage.py test
python manage.py createsuperuser --noinput 2>/dev/null || true
exec python manage.py runserver 0.0.0.0:8000
