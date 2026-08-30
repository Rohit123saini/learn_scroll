
from pathlib import Path
import os
from dotenv import load_dotenv
from datetime import timedelta
from django.db.backends.signals import connection_created

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# SECURITY
SECRET_KEY = os.getenv("SECRET_KEY")
# NOTE (fix — security): DEBUG defaulted to "True" and ALLOWED_HOSTS had a
# literal '*' — so anyone deploying this without explicitly setting both in
# .env got a production server leaking full tracebacks (source code, local
# vars, SECRET_KEY-adjacent config) to any visitor on any error page, and
# accepting Host headers from anywhere (Host-header injection surface).
# Defaults now fail SAFE (DEBUG off, no wildcard host) — add the real
# values to .env instead of relying on code defaults being right.
DEBUG = os.getenv("DEBUG", "False") == "True"
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

# NOTE (fix — local dev on a physical device): a phone/emulator on the same
# Wi-Fi hits this server via the PC's LAN IP (e.g. 10.224.54.189), not
# localhost/127.0.0.1 — that IP isn't in the allowlist above, so every
# request 400s with DisallowedHost before it reaches any view (and Channels'
# AllowedHostsOriginValidator rejects the /ws/ handshake for the same
# reason). Put that LAN IP in ALLOWED_HOSTS in your .env for local device
# testing instead of wildcarding this — e.g.
# ALLOWED_HOSTS=localhost,127.0.0.1,10.224.54.189
#
# NOTE (fix — CRITICAL, reverted): this used to be unconditionally
# `ALLOWED_HOSTS = ["*"]` below this comment, overwriting the safe
# env-driven allowlist two lines up — so that allowlist was dead code and
# every deployment, including production, accepted a Host header from
# literally anywhere (Host-header-injection surface: cache poisoning,
# password-reset-link poisoning if this project ever builds absolute URLs
# from request.get_host()). Restored to the safe, env-driven allowlist.
# The DEBUG-only wildcard kept below is a narrow, explicit local-dev
# convenience — it can never be true in production as long as DEBUG=False
# is set there, and it only kicks in if ALLOWED_HOSTS wasn't set in .env at
# all, so it never silently overrides a real allowlist someone did set.
if DEBUG and not os.getenv("ALLOWED_HOSTS"):
    ALLOWED_HOSTS = ["*"]




# ---------------------------------------------------------------------------
# ADD (missing — production HTTPS hardening): Django's own deployment
# checklist (`manage.py check --deploy`) flags every one of these as
# missing, and none of them existed before. Without them: cookies
# (sessionid, csrftoken — used by /admin/ and anything relying on
# SessionAuthentication) are sent in plain text over HTTP, there's no
# server-side redirect from http:// to https://, no HSTS header telling
# browsers to only ever speak HTTPS to this host, and Django can't tell it's
# behind a TLS-terminating proxy/load balancer (so request.is_secure() and
# the two redirects above would misfire behind nginx/Render/Railway/etc.).
# All gated on `not DEBUG` so local dev over plain http:// keeps working
# exactly as before — nothing here changes behavior until DEBUG=False.
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30 if not DEBUG else 0  # 30 days
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# NOTE: needed alongside CORS_ALLOWED_ORIGINS above — CORS governs which
# origins the *browser* is allowed to read cross-origin responses from;
# CSRF_TRUSTED_ORIGINS governs which Origin/Referer Django itself will
# accept an unsafe (POST/PUT/PATCH/DELETE) request from for
# session/cookie-authenticated requests (i.e. /admin/, or any endpoint hit
# via SessionAuthentication instead of the JWT Bearer flow the Flutter app
# uses). Same allowlist source as CORS so there's one place to configure
# your actual web frontend domain(s) in .env.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# ---------------------------------------------------------------------------
# ADD (missing — error tracking): console logging (see LOGGING below) means
# every log.exception()/log.error() call in the codebase — and there are
# several deliberate ones: _safe_delay() in liveclass/views.py swallowing a
# dead Celery broker, sync_missed_charges()'s best-effort call inside
# _perform_join, _try_promote_from_waitlist()'s own try/except, the signal
# receivers in liveclass/signals.py — only ever shows up as a line in
# whatever's tailing stdout. In production that means a real, actionable
# failure (broker down, a bug in waitlist promotion, a bad LiveKit
# response) is silently invisible unless someone happens to be watching
# logs at that exact moment. Wired as early as possible (before
# INSTALLED_APPS/apps load) per Sentry's own recommendation. Entirely
# inert with SENTRY_DSN unset — nothing changes for local dev unless you
# add it to .env. Requires `pip install --upgrade sentry-sdk`.
# ---------------------------------------------------------------------------
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production" if not DEBUG else "development"),
        release=os.getenv("RELEASE_VERSION"),
        integrations=[
            DjangoIntegration(),
            # Every notify_*.delay() call site in liveclass (views.py,
            # models.py's post_save signal, signals.py) runs through
            # Celery — without this, a task that throws only ever shows up
            # as a silent console line, exactly the gap this fix closes.
            CeleryIntegration(monitor_beat_tasks=False),
            # Captures every logger.exception()/logger.error() call as a
            # Sentry event (with INFO+ as breadcrumbs for context) — this is
            # what actually surfaces the deliberately-swallowed exceptions
            # listed above instead of leaving them as console-only noise.
            LoggingIntegration(level=None, event_level="ERROR"),
        ],
        # 1.0 = trace every request. Turn this down (e.g. 0.1-0.2) once
        # real traffic shows up — it's a cost/volume knob, not correctness.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        # Coin balances, coupon codes, join-request notes, etc. all pass
        # through this API — don't let Sentry capture request bodies/user
        # PII by default.
        send_default_pii=False,
    )

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "django.contrib.postgres",
    'django_filters',
    "rest_framework",
    "drf_spectacular",
    'corsheaders',
    'login',
    'user_profile',
    'post',
    "message",
    'liveclass',
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'LearnScroll.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'LearnScroll.wsgi.application'
ASGI_APPLICATION = 'LearnScroll.asgi.application'

# ---------------------------------------------------------------------------
# Database
# NOTE (fix — production readiness): SQLite was hardcoded with no way to
# switch without editing code. Fine for local dev, but it has no real
# concurrent-write story — a live-class platform doing session joins, chat
# messages, poll votes and coin transactions from many users at once will
# hit "database is locked" under real concurrency no matter how much
# WAL/busy_timeout are tuned. Now env-driven: set DATABASE_URL
# (postgres://user:pass@host:port/dbname) in .env to run on Postgres in
# production; leave it unset and you get the exact same SQLite/WAL setup as
# before for local dev — nothing breaks today. Requires
# `psycopg2-binary` (or `psycopg[binary]`) installed once DATABASE_URL is
# actually set.
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    from urllib.parse import urlparse

    _db_url = urlparse(DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _db_url.path[1:],
            "USER": _db_url.username,
            "PASSWORD": _db_url.password,
            "HOST": _db_url.hostname,
            "PORT": _db_url.port or 5432,
            # Reuse connections instead of opening a fresh one per request —
            # meaningful under real request volume.
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                'timeout': 60,
            },
        }
    }

    def _activate_sqlite_wal(sender, connection, **kwargs):
        if connection.vendor == 'sqlite':
            cursor = connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA busy_timeout=30000;')

    connection_created.connect(_activate_sqlite_wal)

# ---------------------------------------------------------------------------
# Channels
# NOTE (fix — production readiness): InMemoryChannelLayer only works
# correctly with a single process. The moment this runs behind more than
# one daphne/gunicorn worker (which any real production deployment does,
# for throughput and zero-downtime restarts), two users connected to
# different workers silently stop seeing each other's realtime messages —
# a live-class chat/poll feature that quietly breaks under normal
# horizontal scaling, not a rare edge case. REDIS_URL/CELERY_BROKER_URL
# already exists in this project's env for Celery — reused here so there's
# one Redis to run, not two. Falls back to in-memory only when neither is
# set (local dev), same as before. Requires `channels_redis` installed.
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
os.makedirs(MEDIA_ROOT, exist_ok=True)

# Chunked-upload temp storage — deliberately OUTSIDE MEDIA_ROOT. MEDIA_ROOT
# is served (directly by Django in DEBUG via serve_media_with_range in
# urls.py, and by nginx/S3 in production) — keeping partial chunks out of
# it means a half-uploaded (unvalidated) file can never become reachable
# mid-upload. See liveclass/chunked_upload_views.py.
CHUNKED_UPLOAD_TMP_ROOT = BASE_DIR / 'tmp' / 'chunked_uploads'
os.makedirs(CHUNKED_UPLOAD_TMP_ROOT, exist_ok=True)

# ADD (missing): WhiteNoiseMiddleware was already wired into MIDDLEWARE
# above, but without this it just serves STATIC_ROOT as-is — no gzip/brotli
# compression and no content-hashed filenames, so browsers can never safely
# cache-bust a redeployed static file. CompressedManifestStaticFilesStorage
# gives both, and is whitenoise's own recommended production setting.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# --- PRODUCTION AI FIX ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# NOTE (fix — production readiness): LocMemCache lives inside a single
# process's memory. With more than one worker process (any real
# deployment), each worker has its own separate cache — a value set by the
# worker that handled request 1 is invisible to the worker that handles
# request 2, so cached data (and any future rate-limit/session-style use of
# this cache) is inconsistent per-request depending purely on which worker
# you land on. Reuses the same REDIS_URL as Celery/Channels above so
# there's still just one Redis to run. Falls back to LocMemCache when Redis
# isn't configured (local dev), same behavior as before. Requires
# `django-redis` installed once REDIS_URL is set.
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "learnscroll-ai-cache",
        }
    }

# ADD (missing pieces): the console handler had no formatter — log lines
# had no timestamp/level/logger name, which is close to useless once you're
# grepping real production logs for when something happened. Also added an
# explicit "django" logger entry so Django's own request/server errors
# (unhandled 500s) are guaranteed to surface at ERROR level even though
# root already covers them — being explicit here means a later change to
# root's level can't accidentally silence them.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # ADD (missing — defense in depth): every current ViewSet/APIView
    # already sets its own permission_classes explicitly (verified), so
    # this doesn't change today's behavior. It matters for tomorrow: DRF's
    # own built-in default is AllowAny, so any future view added without
    # remembering to set permission_classes would silently be open to the
    # public instead of failing closed. IsAuthenticated as the project-wide
    # floor means a forgotten permission_classes is a 401, not a leak.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "100/min",
        "anon": "20/min",
        "ai_study": "20/min",
        "send_otp": "5/min",
        "verify_otp": "10/min",
        # NOTE (fix — CRITICAL, would crash in production): views.py wires
        # ScopedRateThrottle onto four liveclass actions —
        # ClassSessionViewSet.join (throttle_scope="session_join"),
        # ClassSessionViewSet.token (throttle_scope="session_token"),
        # CouponViewSet.validate (throttle_scope="coupon_validate"), and
        # ChatMessageViewSet.create via get_throttles()
        # (throttle_scope="chat_message_create") — but none of those four
        # scopes had a matching rate here. DRF's ScopedRateThrottle.get_rate()
        # raises ImproperlyConfigured ("No default throttle rate set for
        # '<scope>' scope") the very first time ANY of these four endpoints
        # is hit — i.e. the very first time any student tries to join a
        # live class, or send a single chat message. This wasn't a latent
        # edge case; it was a guaranteed 500 on day one. Rates chosen to
        # match the reasoning already documented next to each
        # throttle_scope= in views.py.
        "session_join": "20/min",
        "session_token": "30/min",
        "coupon_validate": "20/min",
        "chat_message_create": "20/min",
        # Chunked upload (liveclass/chunked_upload_views.py) — starting a
        # lot of uploads fast is the abuse signal for init/complete; chunk
        # itself is rated higher since one real upload fires it dozens of
        # times in quick succession (~3/sec covers a fast client on an
        # 8MB chunk size).
        "chunked_upload_init": "20/min",
        "chunked_upload_chunk": "180/min",
        "chunked_upload_complete": "20/min",
    },
    # NOTE (fix — production breaking gap): NOT having this meant every
    # list endpoint (classrooms, sessions, chat-messages, notices, etc.)
    # returned its ENTIRE table in one response, unbounded. Fine with 20
    # test rows; a real 500-row classroom chat thread or a growing
    # classrooms table turns into slow responses, high memory use per
    # request, and an easy accidental DoS vector as data grows. 20/page is
    # a reasonable default — the Flutter client should already handle
    # DRF's standard {"count","next","previous","results"} envelope.
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # NOTE (fix — dead code activation): liveclass/exceptions.py already
    # contains a complete, well-designed error-envelope normalizer
    # (liveclass_exception_handler) — its own docstring says to wire it
    # here, but nothing ever did. Every error response in the app has been
    # falling back to DRF's default (inconsistent shape depending on
    # exception type — see that file's docstring for the exact problem).
    # This single line turns that already-written code on.
    "EXCEPTION_HANDLER": "liveclass.exceptions.liveclass_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

AUTH_USER_MODEL = "login.User"
# NOTE (fix — security): CORS_ALLOW_ALL_ORIGINS=True means ANY website can
# call this API using a logged-in user's browser session/cookies (relevant
# once anything here relies on cookies/CSRF rather than pure Bearer-token
# auth from a mobile app). Locked to an explicit allowlist from .env — add
# your actual web frontend origin(s) there, comma-separated. Falls back to
# allow-all only when DEBUG is on (local dev), never in production.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
CORS_ALLOW_ALL_ORIGINS = DEBUG and not CORS_ALLOWED_ORIGINS

SPECTACULAR_SETTINGS = {
    'TITLE': 'LearnScroll API',
    'DESCRIPTION': 'LearnScroll Production API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

# Upload limits
# NOTE (fix — DoS vector): DATA_UPLOAD_MAX_MEMORY_SIZE governs the total
# size of non-file request data (regular form fields / JSON body) — Django
# excludes actual uploaded file content from this check (that's bounded per
# field instead by MaxFileSizeValidator in models.py: 100MB materials, 50MB
# assignments/submissions, 10MB certificates, 5MB cover image). 500MB here
# meant any endpoint taking a plain text/JSON field (a classroom
# description, a chat message, a review comment) would accept a request
# body up to 500MB of non-file data before Django even rejects it — cheap
# to send, expensive to parse/hold in memory, an easy DoS lever against a
# server with no file involved at all. 10MB is generous for anything this
# app's serializers actually accept as plain text.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024
FILE_UPLOAD_PERMISSIONS = 0o644
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

FCM_SERVICE_ACCOUNT_JSON_PATH = BASE_DIR / "firebase-service-account.json"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
FREESOUND_API_KEY = os.environ.get('FREESOUND_API_KEY')

# ---------------------------------------------------------------------------
# Referral program (see liveclass/models.py Referral, ReferralViewSet in
# views.py). Both sides of a successful referral get REFERRAL_BONUS_COINS.
# REFERRAL_REDEEM_WINDOW_DAYS caps how long after signup a NEW account can
# redeem someone else's code — without this, a years-old account could farm
# bonuses indefinitely by redeeming a friend's code at any point; capping it
# to a signup-window means it's genuinely a new-user acquisition incentive,
# not a standing free-coins loophole.
# ---------------------------------------------------------------------------
REFERRAL_BONUS_COINS = int(os.environ.get("REFERRAL_BONUS_COINS", 50))
REFERRAL_REDEEM_WINDOW_DAYS = int(os.environ.get("REFERRAL_REDEEM_WINDOW_DAYS", 7))

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL")

# ---------------------------------------------------------------------------
# CELERY — background/scheduled jobs.
#
# Without this, four liveclass features exist only as inert DB rows:
#   - ClassSchedule recurrence rules never turn into joinable ClassSession
#     rows (liveclass.generate_upcoming_sessions)
#   - A session nobody clicks /end/ on stays LIVE forever
#     (liveclass.auto_complete_overdue_sessions)
#   - ClassReminder rows never actually get sent (liveclass.send_due_reminders)
#   - SessionWaitlist promotion never notifies the promoted student
#     (liveclass.notify_waitlist_promotion, fired from signals.py)
#   - A pass's un-taught escrow balance never comes back to an inactive
#     student once it expires (liveclass.expire_and_refund_passes)
# See liveclass/tasks.py for the task bodies and LearnScroll/celery.py for
# one-time wiring + how to run the worker/beat processes.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# Belt-and-suspenders: if a worker dies mid-task, don't silently lose it.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "liveclass-generate-upcoming-sessions": {
        "task": "liveclass.generate_upcoming_sessions",
        # Hourly is plenty — the task looks 14 days ahead and is fully
        # idempotent, so re-running it more or less often is always safe.
        "schedule": crontab(minute=0),
    },
    "liveclass-auto-complete-overdue-sessions": {
        "task": "liveclass.auto_complete_overdue_sessions",
        "schedule": crontab(minute="*/5"),
    },
    "liveclass-send-due-reminders": {
        "task": "liveclass.send_due_reminders",
        # Reminders are minute-precision (remind_at), so this needs to be
        # frequent — cheap query (filtered on is_sent + remind_at index-
        # worthy), safe to run every minute.
        "schedule": crontab(minute="*"),
    },
    # NOTE (fix): tasks.py's own refresh_stale_enrolled_counts docstring
    # says this "just never existed" as a scheduled job — it was written,
    # tested-looking, and fully idempotent, but never actually registered
    # here. Without this entry, Classroom.enrolled_count silently drifts
    # upward forever for any classroom whose passes expire without a fresh
    # purchase replacing them (nothing else ever touches that counter once
    # a pass ages out) — the exact problem this task exists to fix, sitting
    # unused. Interval matches REFRESH_ENROLLED_COUNT_LOOKBACK_MINUTES in
    # tasks.py (60 min) with a shorter run cadence than the lookback so a
    # slow/delayed tick can never let a batch of expiries fall in the gap
    # between two runs.
    "liveclass-refresh-stale-enrolled-counts": {
        "task": "liveclass.refresh_stale_enrolled_counts",
        "schedule": crontab(minute="*/15"),
    },
    # NOTE (fix — the actual "student loses money" gap): the per-day escrow
    # design (PassPurchase.charge_for_session in models.py) already stops a
    # quiet/stopped-teaching classroom draining a pass all at once — coins
    # only ever release to the teacher one taught day at a time. But once
    # expires_at passes, nothing was ever calling reverse() for whatever
    # was LEFT in escrow — an inactive student who never noticed to hit
    # cancel() themselves just lost that balance permanently. This sweep
    # (liveclass/tasks.py) auto-refunds it. Interval matches
    # EXPIRE_REFUND_LOOKBACK_MINUTES in tasks.py (60 min) with a shorter
    # run cadence than the lookback, same reasoning as the enrolled-counts
    # job right above — a slow/delayed tick can never let a batch of
    # expiries fall in the gap between two runs and get missed.
    "liveclass-expire-and-refund-passes": {
        "task": "liveclass.expire_and_refund_passes",
        "schedule": crontab(minute="*/15"),
    },
    # Sweeps abandoned chunked uploads (client crashed/closed mid-upload)
    # and reclaims their temp disk usage — see
    # liveclass/tasks.py:cleanup_stale_chunked_uploads for exactly what it
    # checks. Hourly is enough since the staleness window itself is 6h.
    "liveclass-cleanup-stale-chunked-uploads": {
        "task": "liveclass.cleanup_stale_chunked_uploads",
        "schedule": crontab(minute=0),
    },
}