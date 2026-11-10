"""P-C3 -- DEVELOPMENT profile:  DJANGO_SETTINGS_MODULE=LearnScroll.settings_dev

`LearnScroll/settings.py` stays the one base file (the config-drift scanner, `release_check` and several tests read it as a
file and by module name, so it must not move). This module only sets environment DEFAULTS before the base is imported, so
every value is still overridable from the shell / .env and the result is exactly "settings.py with a dev environment".
Nothing here changes what settings.py does when it is used directly.
"""
import os

os.environ.setdefault("DEBUG", "True")                       # SQLite fallback + permissive local behaviour (see settings.py)
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,10.0.2.2")  # 10.0.2.2 = Android emulator -> host
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")    # no worker needed locally
os.environ.setdefault("BEAT_SINGLE_INSTANCE_GUARD", "false") # two terminals on one laptop are fine
os.environ.setdefault("SENTRY_DSN", "")                      # never report dev noise

from LearnScroll.settings import *  # noqa: E402,F401,F403
