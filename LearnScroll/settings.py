from pathlib import Path
import os
from dotenv import load_dotenv
from datetime import timedelta
from django.db.backends.signals import connection_created
from django.core.exceptions import ImproperlyConfigured

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
    "*"
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
# 🔧 GAP FIX — read once, up here, so both INSTALLED_APPS (needs
# 'storages' registered before Django can use its backend) and the
# STORAGES block further down (which sets the actual S3 credentials/
# options) agree on the same flag. See the STORAGES section below for
# the full explanation of what this switches on.
USE_S3_STORAGE = os.getenv("USE_S3_STORAGE", "false").lower() == "true"

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
    'campus',

    # NEW (task 42) — neutral notification + classroom<->chat bridge
    # layer. Must be able to resolve `liveclass.Classroom`/`ClassSession`
    # string FK references (core/models.py), so no strict load-order
    # requirement relative to 'liveclass' here (Django resolves lazy
    # "app_label.Model" references after all apps are loaded), but keeping
    # it listed after 'liveclass' for readability.
    'core',
] + (['storages'] if USE_S3_STORAGE else [])

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

# 🔧 FIX (silent production failure) — the in-memory fallback below is
# fine for local dev (one process, no scaling), but if it's ever picked
# in production it fails SILENTLY: no crash, no error log, no exception
# anywhere — messages just stop crossing between users connected to
# different daphne/gunicorn workers. Nobody would know until a support
# ticket says "my friend isn't getting my messages". Since this is
# infra-critical (not a per-feature toggle), fail loudly and immediately
# at startup instead — Django refuses to even boot rather than come up
# in a broken state.
if not REDIS_URL and not DEBUG:
    raise ImproperlyConfigured(
        "REDIS_URL (or CELERY_BROKER_URL) is not set and DEBUG=False. "
        "Refusing to start with an in-memory Channel Layer in production — "
        "it silently breaks realtime messaging across multiple workers. "
        "Set REDIS_URL in the environment before deploying."
    )

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
#
# 🔧 GAP FIX (this session) — uploaded chat media (`upload_view.py`,
# `chunked_upload_views.py` FileFields) used to ALWAYS go to local disk
# (`FileSystemStorage`, `MEDIA_ROOT`). That's fine for a single dev box,
# but the moment this app runs more than one app-server instance behind a
# load balancer (or the box is just recreated on redeploy), files land on
# whichever instance served that particular upload request — a request
# routed to a different instance afterwards gets a 404 for a file that
# genuinely exists, and any redeploy/restart on ephemeral disk (most PaaS/
# container platforms) loses every uploaded file outright.
#
# Fix is additive/opt-in and zero-risk for existing deployments: local
# `FileSystemStorage` stays the default. Setting `USE_S3_STORAGE=true`
# (plus the `AWS_*` env vars below) switches `default` to S3 — same
# `default_storage`/model-`FileField.url` API either way, so nothing else
# in the app needs to know or care which one is active (see
# `upload_view.py`, which was fixed in this same session to use
# `default_storage.url()` instead of hand-building a local-only URL, so
# it now also works correctly under S3). `USE_S3_STORAGE` itself is
# computed once, up near INSTALLED_APPS — see the comment there.
if USE_S3_STORAGE:
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "ap-south-1")
    # Optional — set for S3-compatible providers (Cloudflare R2, DigitalOcean
    # Spaces, MinIO) or to front the bucket with a CDN/custom domain.
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or None
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN") or None
    # Chat media is served straight from S3, never mutated in place, so a
    # long browser-cache lifetime is safe and reduces repeat egress cost.
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    # Bucket policy/ACLs manage public read (or the bucket stays private
    # and reads go through the CDN/custom domain) — the app itself never
    # needs to set a per-object ACL on upload.
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_FILE_OVERWRITE = False

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# NOTE: requires `django-storages[s3]` in requirements when
# `USE_S3_STORAGE=true` (`pip install "django-storages[s3]"`), and
# `"storages"` added to `INSTALLED_APPS`. Required env vars in that mode:
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME.

# ---------------------------------------------------------------------------
# TASK 25 — production media serving toggle.
#
# `post/views.py:serve_media_with_range` (and any similar chat-media serve
# view in `message/`) is a fine *local dev* convenience but must never be
# the primary way media is served in production:
#   - it streams the whole read through a Python/WSGI-ASGI worker instead
#     of the webserver's zero-copy sendfile path — one worker tied up for
#     the entire duration of a video scrub/seek
#   - it sits behind zero shared HTTP/CDN caching
#   - it has no auth of its own (see the view's own docstring) — fine only
#     because it's dev-only right now
#
# The real production path is one of:
#   (a) USE_S3_STORAGE=true  → `S3Storage.url()` (used by every FileField
#       via `default_storage`, see STORAGES above) already returns an S3 /
#       CloudFront URL directly. Django's `/media/` route is never hit at
#       all for anything uploaded with this on — nginx/CDN config is the
#       whole fix here, see deploy/S3_CLOUDFRONT_SETUP.md.
#   (b) USE_S3_STORAGE=false → nginx serves `/media/` straight off disk in
#       front of Django (see deploy/nginx.conf's `location /media/` block).
#       Plain static-file serving in nginx supports byte-range requests
#       (video seeking, resumable downloads) with no extra module.
#
# SERVE_MEDIA_VIA_DJANGO is the explicit escape hatch for case (b): media
# served by Django itself instead of nginx/S3. It's meant to be temporary.
#
# 🔧 CURRENT STATE (low traffic / no budget yet for nginx media config or
# S3+CloudFront): defaulting this to **True even in production** — i.e.
# `serve_media_with_range` (post/views.py) stays live and serves real
# media traffic for now, worker-blocking and all. That's an accepted,
# deliberate trade-off at low user counts, NOT the long-term setup.
#
# ⚠️ TO CHANGE LATER, WHEN TRAFFIC/USERS GROW — do ONE of:
#   (a) Point nginx at MEDIA_ROOT using `deploy/nginx.conf`'s
#       `location /media/` block, then flip this line's default back to
#       `"False"` (or just set env var SERVE_MEDIA_VIA_DJANGO=false) —
#       zero other code changes needed.
#   (b) Move to S3/CloudFront per `deploy/S3_CLOUDFRONT_SETUP.md` and set
#       USE_S3_STORAGE=true — media URLs switch to S3/CloudFront
#       automatically (`STORAGES` above), and this flag becomes
#       irrelevant for new uploads.
# Either way, this is the ONLY line to touch — nothing in views.py/
# urls.py needs to change again.
SERVE_MEDIA_VIA_DJANGO = os.getenv("SERVE_MEDIA_VIA_DJANGO", "True") == "True"

if not DEBUG and SERVE_MEDIA_VIA_DJANGO and USE_S3_STORAGE:
    # Contradictory combination: with S3 on, model FileFields already
    # resolve to S3/CloudFront URLs and MEDIA_ROOT has nothing in it for
    # this view to read — turning the Django fallback on here would just
    # mean every request 404s from an empty local media dir, disguising
    # the real (missing CloudFront setup) problem as a Django bug.
    raise ImproperlyConfigured(
        "SERVE_MEDIA_VIA_DJANGO=true is meaningless with USE_S3_STORAGE=true — "
        "media already resolves straight to S3/CloudFront URLs and MEDIA_ROOT "
        "has nothing to serve locally. Unset SERVE_MEDIA_VIA_DJANGO, or set up "
        "nginx (see deploy/nginx.conf) if you meant to serve from local disk "
        "instead of S3."
    )

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
        # NEW (task 9 — parent-join): ClassSessionViewSet.parent_join is
        # unauthenticated (see ParentJoinIPThrottle in liveclass/throttles.py
        # for why this is a separate, IP-keyed scope rather than reusing
        # session_token above). Rated tighter than session_token since this
        # is the endpoint that verifies a parent_token — brute-forcing/
        # guessing tokens is the abuse case, not legitimate retry traffic.
        "session_parent_join_ip": "10/min",
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
        # NOTE (fix — CRITICAL, same bug class as the four scopes documented
        # above, reintroduced twice since): CoinWithdrawalViewSet (Pass 5)
        # and CoinPurchaseViewSet (Pass 7) both set throttle_scope =
        # "coin_withdrawal" / "coin_purchase" with ScopedRateThrottle, but
        # neither scope had a rate here — ImproperlyConfigured on the very
        # first withdrawal request or coin top-up. Money-movement endpoints,
        # so rated tighter than the general chunked-upload scopes above.
        "coin_withdrawal": "10/min",
        "coin_purchase": "10/min",
        # NOTE (fix — same bug class as the scopes documented above):
        # ClassroomViewSet.share now sets throttle_scope="classroom_share"
        # via ScopedRateThrottle (see views.py) but had no rate here —
        # would ImproperlyConfigured on the first share request.
        # UPDATE: changed from a 10/min burst cap to a flat 100/day
        # per-user cap (product decision) — generous enough for genuine
        # sharing across a whole day, still stops a script from
        # inflating share_count or spamming in-app share notifications.
        # DRF's ScopedRateThrottle resets this on a rolling 24h window
        # per user, not a calendar-day boundary.
        "classroom_share": "100/day",
        # NOTE (fix — CRITICAL, same bug class as every other scope in this
        # dict, audit §12 item 18): ChatMessageViewSet.react() (Pass 12)
        # sets throttle_scope="chat_reaction" via ScopedRateThrottle but had
        # no matching rate here — ImproperlyConfigured on the very first
        # reaction tap, a guaranteed 500. A reaction is a single tap (not a
        # typed message), so rated generously relative to
        # chat_message_create above.
        "chat_reaction": "60/min",
        # ---------------------------------------------------------------
        # NOTE (fix — CRITICAL, same bug class as every scope documented
        # above, found in the `message` app this time): `message/throttles.
        # py` defines 6 custom-scoped throttles (`MessageSendThrottle`,
        # `CallInitiateThrottle`, `GroupCreateThrottle`, `ReactionThrottle`,
        # plus the newer `MessageSendIPThrottle`/`CallInitiateIPThrottle`)
        # and `message/views_ai.py` defines a 7th (`AiTranscribeThrottle`,
        # scope="ai_transcribe") — none of their scopes had a matching rate
        # here. `UserRateThrottle`/`SimpleRateThrottle` subclasses look up
        # their rate the exact same way `ScopedRateThrottle` does
        # (`DEFAULT_THROTTLE_RATES[self.scope]`), so the very first message
        # sent, call initiated, group created, reaction added, or voice
        # note transcribed would raise `ImproperlyConfigured` — a guaranteed
        # 500 on day one for the single most-used feature in the app (chat
        # itself), not a rare edge case. Rates match what's already
        # documented as each throttle class's intended rate in throttles.py
        # / views_ai.py.
        # ---------------------------------------------------------------
        "message_send": "60/min",
        "call_initiate": "10/min",
        "group_create": "5/min",
        "reaction": "120/min",
        "ai_transcribe": "15/min",
        # NOTE (fix — CRITICAL, same bug class as ai_transcribe/message_send
        # above): `message/views_ai.py`'s `SmartReplySuggestionsView` sets
        # throttle_scope="ai_smart_reply" via `SmartReplyThrottle`
        # (ScopedRateThrottle), but this scope had no matching rate here —
        # `ImproperlyConfigured` on the very first "smart reply" call, a
        # guaranteed 500, not a rare edge case. Rate matches what's already
        # documented as this throttle's intended rate in views_ai.py (30/min
        # — looser than ai_transcribe since a client may reasonably call
        # this on every incoming message).
        "ai_smart_reply": "30/min",
        # IP-level safety net (used alongside the per-user rates above, not
        # instead of them — see MessageSendIPThrottle/CallInitiateIPThrottle
        # in throttles.py for why per-user alone isn't enough).
        "message_send_ip": "120/min",
        "call_initiate_ip": "20/min",
        # NOTE (fix — same bug class as above, CONFIRMED missing): these
        # scopes are already wired to real views via throttle_classes/
        # get_throttles() but had no matching rate here — each was a
        # guaranteed ImproperlyConfigured (500) on its very first call.
        # Rates match each throttle class's own documented intended rate.
        "translate": "30/min",                      # TranslateThrottle -> MessageViewSet.translate
        "parent_code_verify_ip": "10/min",           # ParentCodeVerifyThrottle -> ParentVerifyCodeView (per-IP)
        "ai_class_transcript_chunk": "30/min",       # ClassTranscriptChunkThrottle -> ClassTranscriptChunkUploadView
        "ai_class_transcript_search": "60/min",      # ClassTranscriptSearchThrottle -> ClassTranscriptSearchView
        "ai_classroom_copilot": "15/min",            # ClassroomCopilotThrottle -> ClassroomCopilotView
        "ai_revision_deck": "10/min",                # RevisionDeckThrottle -> RevisionDeckView
        # NOTE (fix — Feature 12, Focus Mode): `views_focus.py`'s own
        # header comment suggests this scope (`FocusSessionThrottle`,
        # `UserRateThrottle`, 20/min) as an optional addition. Adding the
        # rate here now so it's ready the moment that throttle class is
        # actually wired onto `FocusSessionView` — own-account-only entry
        # point (no fan-out), so it isn't a crash risk without the class
        # wired in, but keeping the rate here avoids yet another "scope
        # exists in code, rate missing in settings" gap later.
        "focus_session": "20/min",
        # NOTE (fix — CRITICAL, same bug class as every scope above):
        # `ParentCodeRevealThrottle` (message/throttles.py, scope
        # `parent_code_reveal`) is wired onto `ParentAccessCodeRevealView`
        # but had no matching rate here — ImproperlyConfigured (guaranteed
        # 500) on the very first "reveal full parent code" request. Rate
        # matches the throttle class's own documented intended rate.
        "parent_code_reveal": "10/hour",
        # ---------------------------------------------------------------
        # B-4 fix (campus app, this pass): `campus` had ZERO
        # throttle_scope/ScopedRateThrottle usage anywhere — every other
        # app (liveclass, message) throttles its money-movement, token-
        # verification, and fan-out-notification endpoints; campus's
        # equivalents (fee payment, live-session start, notice post,
        # parent-link-token verify) were completely unprotected. Wired
        # onto the views via campus/throttles.py's four scoped classes
        # (each with a fixed `scope`, not `view.throttle_scope`, since
        # several sit as separate @action methods on the same
        # ViewSet — see that file's module docstring). Same bug class as
        # every other NOTE above: a ScopedRateThrottle subclass with no
        # matching rate here is a guaranteed ImproperlyConfigured (500)
        # on the very first request to that action, so these had to
        # land in the same commit as the throttle_classes= wiring in
        # views.py, not after it.
        # ---------------------------------------------------------------
        # FeePaymentViewSet.pay/.record/.refund — money movement, so
        # rated the same as the existing coin_withdrawal/coin_purchase
        # scopes above rather than a general read/write action.
        "campus_fee_payment": "10/min",
        # CampusLiveSessionViewSet.start — fires a notification fan-out
        # to every active enrollment in the section (see that action in
        # views.py); rated the same as liveclass's session_join, which
        # this scope is modeled on (campus has no separate student-join
        # endpoint of its own — start is the closest analogue).
        "campus_live_session_join": "20/min",
        # NoticeViewSet.create — any active staff member can post to an
        # entire campus/section roster (see NoticeViewSet's own
        # docstring in views.py); rated tight since one spammy/
        # compromised staff account can otherwise blast every student
        # and parent in a campus.
        "campus_notice_post": "10/min",
        # ParentLinkVerifyView.post — resolves a raw `token` from the
        # request body via bridge.resolve_parent_from_token; rated tight
        # for the same reason message's parent_code_reveal/
        # parent_code_verify_ip scopes are tight — this is a
        # token-guessing surface, not a retry-heavy legitimate flow (a
        # parent verifies their link once, not repeatedly).
        "campus_parent_link_verify": "10/min",
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
# NOTE (fix — Feature 9, Message Translate): translation_service.py's
# translate_text() reads this via getattr(settings, 'GOOGLE_TRANSLATE_API_KEY',
# None). It was never set anywhere, so MessageViewSet.translate 503'd with
# TranslationServiceUnavailable on every call even after the "translate"
# throttle-rate entry was fixed. Google Cloud Translate v2 REST API key
# (plain API key, no service-account/SDK needed).
GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")

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

# NEW (task 65 — classroom refer & earn, student side): one-time,
# platform-funded coin bonus credited to a STUDENT who joins a classroom via
# someone else's per-classroom referral link (Classroom.referral_urls),
# awarded once from _charge_and_create_purchase (views.py). Distinct from
# Classroom.referral_commission_percent, which is the REFERRER's ongoing cut
# and is funded out of the teacher's own share, never the platform's — this
# one IS a platform cost, so it defaults conservatively lower than the
# signup bonus above.
CLASSROOM_REFERRAL_JOIN_BONUS_COINS = int(os.environ.get("CLASSROOM_REFERRAL_JOIN_BONUS_COINS", 20))

# ---------------------------------------------------------------------------
# F-3: campus engagement-reward bonuses (see campus/tasks.py's
# check_attendance_streak_rewards / check_assignment_ontime_streak_rewards,
# campus/services.py's compute_attendance_streak / compute_assignment_ontime_streak).
# Paid via user_profile.CoinLedger.record_transaction(transaction_type=
# CAMPUS_REWARD, ...) -- distinct wallet/ledger from liveclass's
# CoinTransaction above, but the same "flat, env-overridable settings
# constant" shape as REFERRAL_BONUS_COINS, deliberately, so ops can retune
# either program the same way.
# CAMPUS_ATTENDANCE_STREAK_DAYS -- how many consecutive PRESENT/LATE daily
# attendance marks earn one bonus (paid again every further multiple).
# CAMPUS_ASSIGNMENT_STREAK_COUNT -- same idea for consecutive on-time
# (non-LATE, non-MISSING) assignment submissions within one section.
# ---------------------------------------------------------------------------
CAMPUS_ATTENDANCE_STREAK_DAYS = int(os.environ.get("CAMPUS_ATTENDANCE_STREAK_DAYS", 7))
CAMPUS_ATTENDANCE_STREAK_BONUS_COINS = int(os.environ.get("CAMPUS_ATTENDANCE_STREAK_BONUS_COINS", 10))
CAMPUS_ASSIGNMENT_STREAK_COUNT = int(os.environ.get("CAMPUS_ASSIGNMENT_STREAK_COUNT", 5))
CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS = int(os.environ.get("CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS", 15))

# ---------------------------------------------------------------------------
# Coin purchase gateway (see CoinPurchase in liveclass/models.py,
# CoinPurchaseViewSet + _verify_gateway_signature in views.py). Written
# against Razorpay's order-create + HMAC-signature-verify shape. Both
# _create_gateway_order and _verify_gateway_signature already degrade
# safely (stub order id / fails-closed signature check) if this isn't set
# — but no real coin top-up can ever succeed until it is.
# ---------------------------------------------------------------------------
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# ---------------------------------------------------------------------------
# MSG91 (see liveclass/notifications.py _send_sms / _send_whatsapp).
# MSG91_AUTH_KEY is shared by both channels. SMS additionally needs a
# DLT-registered sender id; WhatsApp additionally needs the integrated
# number and a pre-approved template name (WhatsApp Business rule, not
# an MSG91 one — see _send_whatsapp's docstring). Any channel no-ops with
# a logged warning, never raises, if its settings aren't fully set.
# ---------------------------------------------------------------------------
MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
MSG91_SMS_SENDER_ID = os.environ.get("MSG91_SMS_SENDER_ID", "")
MSG91_WHATSAPP_INTEGRATED_NUMBER = os.environ.get("MSG91_WHATSAPP_INTEGRATED_NUMBER", "")
MSG91_WHATSAPP_TEMPLATE_NAME = os.environ.get("MSG91_WHATSAPP_TEMPLATE_NAME", "")

# NEW (task 14 — phone OTP delivery, see login/sms_service.py): DLT-
# registered MSG91 OTP template id, used by SendOTPView for phone
# targets. Separate from the liveclass notification templates above —
# this one is fed through MSG91's dedicated `/api/v5/otp` endpoint (not
# the generic SMS/flow API `_send_sms` uses) so our own `secrets`-
# generated OTP code is what actually gets delivered, not one MSG91
# generates itself. Reuses MSG91_AUTH_KEY above; unlike the liveclass
# notification channels, sms_service.send_otp_sms() fails LOUD (raises)
# rather than silently no-op'ing when this isn't set — see that module's
# docstring for why a missed OTP can't be treated like a missed reminder.
MSG91_OTP_TEMPLATE_ID = os.environ.get("MSG91_OTP_TEMPLATE_ID", "")

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
    # NOTE (fix — same "written but never registered" bug the
    # refresh-stale-enrolled-counts entry above already had to fix once):
    # tasks.reconcile_stuck_coin_purchases (Pass 7) exists specifically to
    # close the "payment retry" gap — a PENDING CoinPurchase whose client
    # never called verify/ (crash, closed tab, webhook lost) needs this
    # sweep to ever become retry-able. Without this entry it never ran,
    # so any stuck top-up sat invisible to the student forever. Interval
    # matches COIN_PURCHASE_PENDING_TIMEOUT in tasks.py (2h) with a
    # shorter run cadence than the timeout, same reasoning as every other
    # lookback-window job in this schedule.
    "liveclass-reconcile-stuck-coin-purchases": {
        "task": "liveclass.reconcile_stuck_coin_purchases",
        "schedule": crontab(minute="*/30"),
    },
    # NOTE (fix — same "written but never registered" bug class as
    # refresh-stale-enrolled-counts / reconcile-stuck-coin-purchases above,
    # audit §12 item 22): tasks.run_auto_renewals (Pass 16) exists
    # specifically to actually call PassPurchase.renew() on every
    # SUCCESS purchase with auto_renew=True past its expires_at — without
    # this entry a student's auto-renew opt-in silently lapsed with no
    # error anywhere. Self-cleaning query (no lookback window needed:
    # renew() always clears auto_renew on the row it processes, success
    # or fail), so a short, frequent cadence is safe and keeps a lapsed
    # renewal from sitting unresolved for long.
    "liveclass-run-auto-renewals": {
        "task": "liveclass.run_auto_renewals",
        "schedule": crontab(minute="*/30"),
    },
    # NOTE (fix — same gap as liveclass-run-auto-renewals immediately
    # above, audit §12 item 22): tasks.expire_unclaimed_gifts (Pass 16)
    # sweeps PENDING PassGifts past their 7-day CLAIM_WINDOW_DAYS deadline
    # and refunds the gifter — without this entry an unclaimed gift's
    # already-debited coins just sat in limbo forever. Same self-cleaning-
    # query reasoning, same cadence.
    "liveclass-expire-unclaimed-gifts": {
        "task": "liveclass.expire_unclaimed_gifts",
        "schedule": crontab(minute="*/30"),
    },
    # NOTE (fix — audit §12 item 24): tasks.send_notification_digests
    # actually reads NotificationPreference.digest_frequency/
    # last_digest_sent_at (Pass 14 fields that were previously pure dead
    # weight — nothing ever sent a digest email) and sends a batched
    # email to anyone whose daily/weekly interval has elapsed. Hourly is
    # frequent enough to keep a daily/weekly digest close to on-time
    # without re-scanning NotificationPreference constantly; the task
    # itself is a cheap no-op for any user not yet due.
    "liveclass-send-notification-digests": {
        "task": "liveclass.send_notification_digests",
        "schedule": crontab(minute=0),
    },
    # 🔥 NAYA — message app ka is Celery beat me pehle ZERO entry tha,
    # jabki dono tasks (message/tasks.py) ek poore feature ke liye zaroori
    # hain aur zero-risk / idempotent hain, same pattern jo liveclass ke
    # entries upar already follow karte hain.
    "message-send-scheduled-messages": {
        "task": "message.send_scheduled_messages",
        # scheduled_for minute-precision hai, isliye har minute chalna
        # zaroori hai (query sasti hai - is_scheduled+scheduled_for pe
        # composite index already model pe hai, aur bounded 200/run batch).
        "schedule": crontab(minute="*"),
    },
    "message-cleanup-expired-messages": {
        "task": "message.cleanup_expired_messages",
        # Disappearing-messages ka sabse chhota duration option bhi
        # "1_month" hai, isliye 15 min sweep lag bilkul invisible hai
        # users ko - same cadence liveclass ke lookback-window jobs jaisa.
        "schedule": crontab(minute="*/15"),
    },
    # 🔧 GAP FIX — `is_deleted`/`soft_delete()` (models.py `BaseModel`) ab
    # `GroupViewSet.destroy()` se actually set hota hai (see views.py),
    # par soft-deleted rows ko kabhi HARD-delete karne wala kuch nahi tha —
    # matlab wo hamesha ke liye DB me pade rehte (disk bloat, aur ek admin
    # jo galti se apna hi group delete kar de use kabhi "permanently gone"
    # confirmation nahi milta). Ye sweep grace period ke baad unhe asli
    # CASCADE-delete karta hai. Daily is enough — grace period din-level
    # hai (`GROUP_SOFT_DELETE_GRACE_DAYS`), minute-level precision ki
    # zaroorat nahi.
    "message-purge-soft-deleted-conversations": {
        "task": "message.purge_soft_deleted_conversations",
        "schedule": crontab(hour=3, minute=30),
    },
    # 🔧 TASK 28 — followers_count/following_count drift reconciliation.
    #
    # Both counters are updated atomically per-operation today
    # (FollowAPIView.post / AcceptFollowRequestView.post / BlockedUsersView.post
    # in user_profile/views.py all do their own F()-based +1/-1 right
    # alongside the Follow-row write) — correct for every write that goes
    # through those views. The gap is writes that DON'T: an admin deleting
    # a Follow row directly in /admin/ (FollowAdmin has no read-only
    # guard, unlike CoinLedgerAdmin), a user.delete() CASCADE-deleting
    # every Follow row the deleted user was party to (as follower AND as
    # following) with nothing re-running the counter update on the
    # *other* side of each of those rows, or a data migration / shell
    # bulk-delete. None of those fire the F()-update logic the views use,
    # so the stored counters can silently drift from what Follow rows
    # actually say.
    #
    # Detect-and-correct safety net, not the root-cause fix (a Follow
    # post_delete signal would make drift structurally impossible instead
    # of periodically corrected — see user_profile/tasks.py's own
    # docstring). 6-hourly matches the cadence already used for the other
    # counter-recompute jobs in this schedule.
    "user-profile-reconcile-follow-counts": {
        "task": "user_profile.tasks.reconcile_follow_counts",
        "schedule": crontab(hour="*/6", minute=15),
    },
    # FEE-6 — campus/tasks.py::send_fee_due_reminders. Task string is
    # "campus.tasks.<name>" (Celery's default module-path-derived name,
    # since campus/tasks.py never passes an explicit name= to
    # @shared_task) — same convention as the user_profile entry right
    # above, not the "app.func" shorthand liveclass/message entries use
    # (those explicitly rename their tasks; campus/user_profile don't).
    # Once-a-day is enough — same reasoning as
    # liveclass-send-notification-digests and campus's own
    # send_assignment_due_reminders (design doc §6): this task carries
    # no state of its own, so an occasional extra run is harmless.
    #
    "campus-send-fee-due-reminders": {
        "task": "campus.tasks.send_fee_due_reminders",
        "schedule": crontab(hour=8, minute=0),
    },
    # 🔧 FIX (this pass) — same "written but never registered" bug class
    # this file has already had to fix repeatedly for liveclass/message/
    # user_profile above.
    "campus-check-low-attendance": {
        "task": "campus.tasks.check_low_attendance",
        # check_low_attendance() takes no args — it loops over every
        # active Campus itself (campus/tasks.py), so a single global
        # crontab entry is correct as-is. Once-daily, end-of-school-day
        # check.
        "schedule": crontab(hour=18, minute=0),
    },
    "campus-send-assignment-due-reminders": {
        "task": "campus.tasks.send_assignment_due_reminders",
        # Also loops internally (every Assignment due today, across all
        # campuses) — no args needed. Staggered 30min after the
        # fee-reminder job above so both don't hit the DB in the same
        # minute.
        "schedule": crontab(hour=8, minute=30),
    },
    # 🔴 REMOVED (this pass) — "campus-rollover-session" and
    # "campus-refresh-analytics-snapshot" were both registered here with
    # NO `args`/`kwargs`, but campus/tasks.py confirms both tasks take
    # REQUIRED positional args:
    #   - rollover_session(campus_id, new_session_id)
    #   - refresh_analytics_snapshot(campus_id, session_id)
    # Unlike check_low_attendance/send_assignment_due_reminders/
    # send_fee_due_reminders above (which loop over every active Campus
    # themselves), neither of these two tasks has a "for every
    # campus/session" wrapper — they operate on ONE specific
    # campus+session pair, supplied by the caller. Scheduled as a bare
    # crontab entry with no args, Celery would call e.g.
    # `rollover_session()` with zero arguments every 5 minutes and it
    # would raise `TypeError: rollover_session() missing 2 required
    # positional arguments` on every single tick, forever — this is a
    # worse bug than "never runs" (it's "always crashes, floods error
    # logs/Sentry, still never actually does anything").
    #
    # rollover_session is already correctly invoked on-demand, with the
    # real campus_id/new_session_id, from
    # `AcademicSessionViewSet.rollover` (see campus/tasks.py's own
    # module docstring) — it was never meant to be periodic, so it's
    # intentionally left OUT of this schedule rather than "fixed" with
    # guessed args.
    #
    # refresh_analytics_snapshot genuinely does look like it wants to be
    # periodic (design doc §8 — pre-computed dashboard snapshot), but
    # that requires a new wrapper task in campus/tasks.py that iterates
    # every (active campus, its current session) pair and calls
    # refresh_analytics_snapshot(campus_id, session_id) for each —
    # that wrapper doesn't exist yet. Add it, then register the
    # wrapper's task path here, rather than the raw per-session task.
}

# 🔧 GAP FIX — grace window ke liye, dekho message/tasks.py:
# purge_soft_deleted_conversations aur views.py: GroupViewSet.destroy().
# Isse zyada rakhoge to soft-deleted data zyada der tak recoverable
# rehta hai, kam rakhoge to disk jaldi clear hota hai — 7 din WhatsApp
# jaisi apps ke "recently deleted" window se milta-julta safe default hai.
GROUP_SOFT_DELETE_GRACE_DAYS = int(os.getenv("GROUP_SOFT_DELETE_GRACE_DAYS", "7"))