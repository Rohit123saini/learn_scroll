"""P-C3 -- PRODUCTION profile:  DJANGO_SETTINGS_MODULE=LearnScroll.settings_prod

Same idea as settings_dev.py: `settings.py` is the base, this module sets env defaults first and then refuses to boot if the
result is not production-shaped. It adds no new behaviour of its own - settings.py already fails safe (no DEBUG, no wildcard
host, PostgreSQL + Redis required); this just makes a wrong environment a boot error instead of a quiet fallback.
Optional: wsgi.py / asgi.py / celery.py still default to LearnScroll.settings, so nothing breaks if you never set this.
"""
import os

os.environ.setdefault("DJANGO_ENV", "production")
os.environ.setdefault("DEBUG", "False")

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from LearnScroll.settings import *  # noqa: E402,F401,F403
from LearnScroll.settings import DEBUG as _DEBUG, CELERY_TASK_ALWAYS_EAGER as _EAGER  # noqa: E402

if _DEBUG:
    raise ImproperlyConfigured("settings_prod loaded with DEBUG=True. Unset DEBUG (or set it to False) in the production environment.")
if _EAGER:
    raise ImproperlyConfigured("settings_prod loaded with CELERY_TASK_ALWAYS_EAGER=true: tasks would run inside web requests. Unset it.")
