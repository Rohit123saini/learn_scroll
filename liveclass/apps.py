# liveclass/apps.py
"""
NOTE (fix — CRITICAL, signals were dead code without this file):
    Django never auto-imports signals.py just because it exists in an app
    folder. Every @receiver in liveclass/signals.py (LiveKit room teardown
    on session end, waitlist FCFS promotion + notification queueing,
    attendance-credit bump on leave) was correctly written but would NEVER
    actually run in a real deployment unless something imports
    liveclass.signals at Django startup. The standard, documented place to
    do that is AppConfig.ready() — which requires this file to exist AND
    default_app_config / INSTALLED_APPS to point at "liveclass.apps.LiveclassConfig"
    (Django 3.2+ autodetects this via default_auto_field/apps.py without
    needing an explicit default_app_config string, but INSTALLED_APPS must
    still list "liveclass", which it presumably already does).

    The signals defined directly at the bottom of models.py (rating sync,
    enrolled_count sync, auto-flag) still work fine without this — they're
    registered the moment models.py itself is imported, which Django does
    unconditionally. It's ONLY liveclass/signals.py that needed this fix.
"""

from django.apps import AppConfig


class LiveclassConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "liveclass"

    def ready(self):
        # Import registers every @receiver in signals.py. Import here (not
        # at module top-level) is the documented pattern — importing
        # signals.py before the app registry is fully populated can trigger
        # circular-import errors, since signals.py imports from .models.
        import liveclass.signals  # noqa: F401