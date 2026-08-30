# LearnScroll/celery.py
"""
Celery app for LearnScroll.

WHY THIS FILE EXISTS:
    Several liveclass features only *look* implemented — the DB rows and
    the logic to act on them exist, but nothing ever actually runs that
    logic on a schedule:
        - ClassSchedule (recurrence rule) never turns into joinable
          ClassSession rows on its own.
        - ClassReminder rows get created but never sent (is_sent never
          flips to True).
        - SessionWaitlist promotion only flipped `notified=True` — no
          actual notification went out.
        - A session a teacher forgot to /end/ stays LIVE forever (LiveKit
          room never torn down, attendance never finalized).
    All four need something to run periodically outside the request/
    response cycle. See liveclass/tasks.py for the actual task bodies and
    CELERY_BEAT_SCHEDULE in settings.py for how often each one runs.

WIRING (one-time setup):
    1. In LearnScroll/__init__.py (the file next to this one), add:
         from .celery import app as celery_app
         __all__ = ("celery_app",)
       This makes `@shared_task` in any app pick up this Celery app
       automatically without every task file needing its own import.

    2. Install: pip install celery[redis] redis

    3. Run Redis (broker + result backend) — e.g. `docker run -p 6379:6379 redis`
       or a managed Redis instance. Point CELERY_BROKER_URL /
       CELERY_RESULT_BACKEND (settings.py) at it via .env.

    4. Run a worker (executes tasks):
         celery -A LearnScroll worker -l info
       Run the beat scheduler (fires periodic tasks — this is what makes
       generate_upcoming_sessions/send_due_reminders/etc. actually trigger
       on their own, you need this running alongside the worker):
         celery -A LearnScroll beat -l info
       In production run both as separate long-lived processes (systemd
       units / Docker services) alongside the Django app itself — neither
       one is optional; a worker with no beat means nothing ever fires on
       its own, and beat with no worker means tasks queue up but never run.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "LearnScroll.settings")

app = Celery("LearnScroll")
# Reads every CELERY_* setting from Django's settings.py (namespace="CELERY"
# means e.g. CELERY_BROKER_URL maps to Celery's `broker_url`).
app.config_from_object("django.conf:settings", namespace="CELERY")
# Auto-discovers a `tasks.py` in every INSTALLED_APPS app (liveclass/tasks.py
# included) — no manual task registration needed.
app.autodiscover_tasks()