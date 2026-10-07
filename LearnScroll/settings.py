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
# several deliberate ones: _safe_delay() in tuitionclass/views.py swallowing a
# dead Celery broker, sync_missed_charges()'s best-effort call inside
# _perform_join, _try_promote_from_waitlist()'s own try/except, the signal
# receivers in tuitionclass/signals.py — only ever shows up as a line in
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
            # Every notify_*.delay() call site in tuitionclass (views.py,
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

# ---------------------------------------------------------------------------
# C5 — CDN + cache headers (ONE toggle: MEDIA_CDN_ENABLED).
#
#   MEDIA_CDN_ENABLED=true
#   MEDIA_CDN_URL=https://cdn.learnscroll.app      # no trailing slash needed
#
# ON  -> every media URL (FileField.url, default_storage.url) is built on
#        MEDIA_CDN_URL instead of the origin, and media responses carry
#        `Cache-Control: public, max-age=31536000, immutable`.
# OFF -> exactly the old behaviour (MEDIA_URL="/media/", 1-day S3 cache).
#
# Origin setup (what the CDN pulls from):
#   * USE_S3_STORAGE=true  -> CDN origin = the bucket (CloudFront etc.); the
#     CDN host is wired into AWS_S3_CUSTOM_DOMAIN below.
#   * USE_S3_STORAGE=false -> CDN origin = this server's /media/ path
#     (nginx or Django); MEDIA_URL becomes MEDIA_CDN_URL + "/media/".
#
# `immutable` is only safe because uploaded files are NEVER overwritten
# (AWS_S3_FILE_OVERWRITE=False / Django's get_available_name appends a unique
# suffix) — a changed file always gets a NEW url. Don't turn overwrite on
# while this toggle is on.
# ---------------------------------------------------------------------------
MEDIA_CDN_ENABLED = os.getenv("MEDIA_CDN_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
MEDIA_CDN_URL = os.getenv("MEDIA_CDN_URL", "").strip().rstrip("/")
MEDIA_CACHE_CONTROL = "public, max-age=31536000, immutable"

if MEDIA_CDN_ENABLED and not MEDIA_CDN_URL.startswith(("https://", "http://")):
    raise ImproperlyConfigured(
        "MEDIA_CDN_ENABLED=true but MEDIA_CDN_URL is missing or has no scheme. "
        "Set e.g. MEDIA_CDN_URL=https://cdn.example.com — or set "
        "MEDIA_CDN_ENABLED=false."
    )

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
    'tuitionclass',
    'campus',
    'testseries',
    # TASK G7 (growth_and_feature_tasks.md â Leaderboards). Sits above
    # testseries/campus/post the same way 'core' sits above tuitionclass/
    # message â reads all three read-only via lazy imports, never the
    # other way around. Listed after them so its own migration (a plain
    # FK to settings.AUTH_USER_MODEL, no FK into those apps) never needs
    # a strict load-order relative to them.
    'leaderboard',
    # 🔧 GAP FIX — was 'assigments' (typo), which doesn't match this
    # app's real label anywhere else in the codebase (assigments/
    # models.py, assigments/bridge.py, assigments_APP_MASTER.md, etc. —
    # all "assigments", never "assigments"). Django resolves app labels
    # from the actual app directory/AppConfig, not from this string
    # matching anything else, so a typo here means the real
    # `assigments` app was never actually registered under this label —
    # `django_apps.get_app_config("assigments")` (e.g. what
    # `check_config_drift`'s admin-registration check calls) would have
    # raised `LookupError` the moment `assigments` was added to
    # `CONFIG_DRIFT_APPS` below, not just silently skipped it.
    'assigments',

    # NEW (task 42) — neutral notification + classroom<->chat bridge
    # layer. Must be able to resolve `tuitionclass.Classroom`/`ClassSession`
    # string FK references (core/models.py), so no strict load-order
    # requirement relative to 'tuitionclass' here (Django resolves lazy
    # "app_label.Model" references after all apps are loaded), but keeping
    # it listed after 'tuitionclass' for readability.
    'core',
] + (['storages'] if USE_S3_STORAGE else [])

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.security.SecurityMiddleware',
    # C5 — long-lived Cache-Control on /media/ responses (no-op unless
    # MEDIA_CDN_ENABLED=true; see common/media_cache.py).
    'common.media_cache.MediaCacheControlMiddleware',
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
# concurrent-write story — a tuition-class platform doing session joins, chat
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
# a tuition-class chat/poll feature that quietly breaks under normal
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
# C5 — with the CDN toggle on, local-disk media URLs point at the CDN
# (which pulls from <origin>/media/). Off -> unchanged "/media/".
MEDIA_URL = f"{MEDIA_CDN_URL}/media/" if MEDIA_CDN_ENABLED else '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
os.makedirs(MEDIA_ROOT, exist_ok=True)

# Chunked-upload temp storage — deliberately OUTSIDE MEDIA_ROOT. MEDIA_ROOT
# is served (directly by Django in DEBUG via serve_media_with_range in
# urls.py, and by nginx/S3 in production) — keeping partial chunks out of
# it means a half-uploaded (unvalidated) file can never become reachable
# mid-upload. See tuitionclass/chunked_upload_views.py.
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
    # C5 — toggle on: serve every object URL via the CDN host (wins over a
    # separately-set AWS_S3_CUSTOM_DOMAIN so there is only one switch).
    if MEDIA_CDN_ENABLED:
        AWS_S3_CUSTOM_DOMAIN = MEDIA_CDN_URL.split("://", 1)[1]
    # Chat media is served straight from S3, never mutated in place, so a
    # long browser-cache lifetime is safe and reduces repeat egress cost.
    # Toggle on -> 1 year + immutable (objects are never overwritten, see C5
    # block above). Toggle off -> the old conservative 1 day.
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": MEDIA_CACHE_CONTROL if MEDIA_CDN_ENABLED else "max-age=86400"
    }
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
# Default flips to False automatically when USE_S3_STORAGE=true, so enabling S3
# is a one-env-var change (the two used to conflict and crash boot in prod).
SERVE_MEDIA_VIA_DJANGO = os.getenv(
    "SERVE_MEDIA_VIA_DJANGO", "False" if USE_S3_STORAGE else "True"
) == "True"

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
#
# 🔧 FIX (production gap #7 — silent per-worker cache split): same failure
# mode as the Channels layer above, just for CACHES instead of realtime
# messaging — LocMemCache under gunicorn/daphne with >1 worker means each
# process has its own cache, so rate-limiting and anything cached becomes
# inconsistent depending purely on which worker served the request, with
# no error anywhere. Reuse the same fail-fast guard: refuse to boot with
# DEBUG=False and no REDIS_URL, rather than come up broken.
if not REDIS_URL and not DEBUG:
    raise ImproperlyConfigured(
        "REDIS_URL (or CELERY_BROKER_URL) is not set and DEBUG=False. "
        "Refusing to start with LocMemCache in production — it silently "
        "gives each gunicorn/daphne worker its own cache, breaking "
        "rate-limiting and caching consistency. Set REDIS_URL in the "
        "environment before deploying."
    )

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

# N1/N2-BE — in-app notification batching (core/notification_batching.py).
# Window (seconds) per batch type: a new event inside the window folds into
# the SAME bell row ("X and 4 others liked your post") and sends no new push;
# every event extends the window (sliding). NOTIFICATION_BATCH_MAX_AGE caps
# a batch's TOTAL age so a long window on a busy account (followers) still
# starts a fresh row/push eventually. Follow REQUESTS, mentions and chat
# messages are never batched (see the helper's docstring for why).
NOTIFICATION_BATCH_WINDOWS = {
    "post_liked": int(os.environ.get("NOTIF_BATCH_POST_LIKED_SECONDS", 120)),
    "post_commented": int(os.environ.get("NOTIF_BATCH_POST_COMMENTED_SECONDS", 120)),
    "new_follower": int(os.environ.get("NOTIF_BATCH_NEW_FOLLOWER_SECONDS", 6 * 3600)),
    "story_reaction": int(os.environ.get("NOTIF_BATCH_STORY_REACTION_SECONDS", 300)),
    "post_reposted": int(os.environ.get("NOTIF_BATCH_POST_REPOSTED_SECONDS", 120)),
}
NOTIFICATION_BATCH_MAX_AGE = {
    "new_follower": int(os.environ.get("NOTIF_BATCH_NEW_FOLLOWER_MAX_AGE_SECONDS", 24 * 3600)),
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

# --- Abuse / input-validation limits (user_profile) -----------------------
# Profile photo upload: size + type are enforced in
# user_profile.serializers.SafeProfilePhotoField (and the Content-Length
# pre-check in UpdateProfileView) BEFORE the file is stored.
PROFILE_PHOTO_MAX_BYTES = int(os.getenv("PROFILE_PHOTO_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
PROFILE_PHOTO_ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
PROFILE_PHOTO_MAX_DIMENSION = 8000  # px, per side (decompression-bomb guard)

# Languages a user may pick in UserPreference.language (ISO 639-1 primary
# subtag; a region/script suffix like "en-US" is accepted for these too).
# Add a code here when the app ships a new translation.
SUPPORTED_LANGUAGES = (
    "en", "hi", "bn", "mr", "ta", "te", "gu", "kn", "ml", "pa", "ur", "or", "as",
)

# INR value of ONE coin, used to snapshot `amount_inr` on every withdrawal
# request (CoinWithdrawalRequest.get_coin_to_inr_rate reads this at call time,
# so a changed env var takes effect on restart — no code deploy). Decimal
# string, must be > 0 (a bad value falls back to 1). Only NEW requests use
# the new rate; existing requests keep the amount they were created with.
# NOTE: tuitionclass has its own separate coin/INR constant — keep both in sync
# if pricing changes.
COIN_TO_INR_RATE = os.getenv("COIN_TO_INR_RATE", "1")

# Max time (ms) a wallet operation waits for another transaction's row lock
# (CoinLedger.record_transaction / purchase confirm / withdrawal steps) before
# failing fast with CoinLedgerBusy -> HTTP 503 + Retry-After instead of
# queueing behind a stuck transaction forever. PostgreSQL only; 0 disables.
COIN_LEDGER_LOCK_TIMEOUT_MS = int(os.getenv("COIN_LEDGER_LOCK_TIMEOUT_MS", "5000"))

# BuyCoinView DB-bloat guard: a user may hold at most this many PENDING
# (unconfirmed) purchase rows at once. Throttle rates are in
# DEFAULT_THROTTLE_RATES below ("profile_coin_purchase*").
COIN_PURCHASE_MAX_PENDING_PER_USER = int(os.getenv("COIN_PURCHASE_MAX_PENDING_PER_USER", "20"))

# Issue #5 — hard ceiling on any client-requested page size (`?page_size=`).
# Enforced by common.pagination.StandardPagination; views that define their
# own paginator (post/tuitionclass) already cap at <= 100 themselves.
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "100"))

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Was "rest_framework_simplejwt.authentication.JWTAuthentication"
        # directly. SlidingSessionAuthentication wraps that same class
        # (still does full JWT signature/exp validation first, nothing
        # about existing token checks is loosened) and adds the 10-day
        # inactivity sliding-expiry check on top — see
        # login/authentication.py for the full explanation.
        "login.authentication.SlidingSessionAuthentication",
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
        # NOTE (fix — CRITICAL, same bug class as every other scope
        # documented in this dict): login_app_reference.md confirms
        # `ForgotPasswordView`/`ResetPasswordView` (login/views.py) are
        # wired with ScopedRateThrottle to throttle_scope="forgot_password"
        # / "reset_password" respectively, but neither scope had a rate
        # here — ImproperlyConfigured (guaranteed 500) on the very first
        # forgot-password or reset-password request. Rates match each
        # view's own documented intended rate (same reasoning as
        # send_otp/verify_otp above, which these mirror one-for-one).
        "forgot_password": "5/min",
        "reset_password": "10/min",
        # NOTE (fix — CRITICAL, would crash in production): views.py wires
        # ScopedRateThrottle onto four tuitionclass actions —
        # ClassSessionViewSet.join (throttle_scope="session_join"),
        # ClassSessionViewSet.token (throttle_scope="session_token"),
        # CouponViewSet.validate (throttle_scope="coupon_validate"), and
        # ChatMessageViewSet.create via get_throttles()
        # (throttle_scope="chat_message_create") — but none of those four
        # scopes had a matching rate here. DRF's ScopedRateThrottle.get_rate()
        # raises ImproperlyConfigured ("No default throttle rate set for
        # '<scope>' scope") the very first time ANY of these four endpoints
        # is hit — i.e. the very first time any student tries to join a
        # tuition class, or send a single chat message. This wasn't a latent
        # edge case; it was a guaranteed 500 on day one. Rates chosen to
        # match the reasoning already documented next to each
        # throttle_scope= in views.py.
        "session_join": "20/min",
        "session_token": "30/min",
        # NEW (task 9 — parent-join): ClassSessionViewSet.parent_join is
        # unauthenticated (see ParentJoinIPThrottle in tuitionclass/throttles.py
        # for why this is a separate, IP-keyed scope rather than reusing
        # session_token above). Rated tighter than session_token since this
        # is the endpoint that verifies a parent_token — brute-forcing/
        # guessing tokens is the abuse case, not legitimate retry traffic.
        "session_parent_join_ip": "10/min",
        "coupon_validate": "20/min",
        "chat_message_create": "20/min",
        # Chunked upload (tuitionclass/chunked_upload_views.py) — starting a
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
        # user_profile.BuyCoinView (POST /profile/buy-coin/) — each call
        # inserts a PENDING row, so a burst limit AND a daily ceiling.
        # Deliberately NOT the tuitionclass "coin_purchase" scope: throttle
        # cache keys are per scope, so sharing it would make the two
        # unrelated endpoints eat each other's quota.
        "profile_coin_purchase": "10/min",
        "profile_coin_purchase_daily": "100/day",
        # Block system (user_profile/throttles.py): POST /blocked-users/ and
        # DELETE /blocked-users/<id>/ share these two buckets per user;
        # POST /reports/ has its own burst limit (plus a 30/hour cap in
        # services.file_report). Without these rates DRF would raise
        # ImproperlyConfigured on the first request (see the notes above).
        "profile_block_burst": "20/min",
        "profile_block_daily": "200/day",
        "profile_report_burst": "10/min",
        # P14-BE — user_profile ActivityView (aggregates 7 days + two post lists,
        # so a modest rate) and ActivityHeartbeatView (client beats every
        # ~30-60 s while foregrounded; 12/min leaves headroom for 2 devices).
        "profile_activity": "30/min",
        "profile_activity_heartbeat": "12/min",
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
        "referral_attribute": "20/min",
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
        "ai_ask_doubt": "20/min",                    # AskAIDoubtThrottle -> AskAIDoubtView (Task G15)
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
        # app (tuitionclass, message) throttles its money-movement, token-
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
        # views.py); rated the same as tuitionclass's session_join, which
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
        # NEW — Task 13/G13: campus invite-code generate/redeem (see
        # campus/throttles.py's CampusInviteCodeGenerateThrottle /
        # CampusInviteCodeRedeemThrottle for the reasoning behind each
        # rate). Same bug class as every NOTE above: these had to land
        # in the same commit as the throttle_classes= wiring in
        # campus_invite.py, not after it.
        "campus_invite_code_generate": "20/min",
        "campus_invite_code_redeem": "15/min",
        # NOTE (fix — CRITICAL, same bug class as the scopes above):
        # assigments/throttling.py::assigmentsPublicPageThrottle sets
        # scope = "assigments_public_page" for the one AllowAny surface in the
        # assigments app (PublicSubmissionView), but this dict never had that
        # key — DRF raises ImproperlyConfigured on the very first request, so
        # the public share URL of a submission was a guaranteed 500.
        "assigments_public_page": "60/min",
        # Public, unauthenticated surfaces of the advanced testseries/assignments
        # features (see testseries/throttling.py, assigments/throttling.py).
        "testseries_public_page": "60/min",
        "testseries_certificate_verify": "30/min",
        "assigments_explore": "120/min",
        # C1-BE: post.views.PostEventBulkAPIView (POST /post/events/) sets
        # throttle_scope = "post_events" with ScopedRateThrottle. One request
        # carries up to 100 events, so 60 requests/min/user = up to 6000
        # events/min — far above any real feed scroll rate, tight enough to
        # stop a runaway client. WITHOUT this key DRF raises
        # ImproperlyConfigured (500) on the very first request.
        "post_events": "60/min",
    },
    # NOTE (fix — production breaking gap): NOT having this meant every
    # list endpoint (classrooms, sessions, chat-messages, notices, etc.)
    # returned its ENTIRE table in one response, unbounded. Fine with 20
    # test rows; a real 500-row classroom chat thread or a growing
    # classrooms table turns into slow responses, high memory use per
    # request, and an easy accidental DoS vector as data grows. 20/page is
    # a reasonable default — the Flutter client should already handle
    # DRF's standard {"count","next","previous","results"} envelope.
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    # NOTE (fix — dead code activation): tuitionclass/exceptions.py already
    # contains a complete, well-designed error-envelope normalizer
    # (tuitionclass_exception_handler) — its own docstring says to wire it
    # here, but nothing ever did. Every error response in the app has been
    # falling back to DRF's default (inconsistent shape depending on
    # exception type — see that file's docstring for the exact problem).
    # This single line turns that already-written code on.
    "EXCEPTION_HANDLER": "tuitionclass.exceptions.tuitionclass_exception_handler",
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

# Base URL for the one-click "add parent" confirmation link — see
# common/parent_invite_links.py. This should be a universal/app link your
# Flutter app is registered to intercept (falls back to a safe default so
# local/dev still works if the env var isn't set).
PARENT_INVITE_LINK_BASE = os.getenv("PARENT_INVITE_LINK_BASE", "https://learnscroll.app/parent-link")

# TASK 11 — https share links / QR (see common/web_links.py + TASK_11_NATIVE_SETUP.md).
# The Flutter side reads its host from --dart-define=WEB_HOST (default learnscroll.app);
# keep it equal to the host of PARENT_INVITE_LINK_BASE above.
ANDROID_APP_PACKAGE = os.getenv("ANDROID_APP_PACKAGE", "")  # e.g. com.learnscroll.app
ANDROID_SHA256_CERT_FINGERPRINTS = os.getenv("ANDROID_SHA256_CERT_FINGERPRINTS", "")  # comma-separated
IOS_APP_ID = os.getenv("IOS_APP_ID", "")  # "<TEAMID>.<bundle id>"
PLAY_STORE_URL = os.getenv("PLAY_STORE_URL", "")
APP_STORE_URL = os.getenv("APP_STORE_URL", "")

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
# assigmentss/submissions, 10MB certificates, 5MB cover image). 500MB here
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
# Referral program (see tuitionclass/models.py Referral, ReferralViewSet in
# views.py). Both sides of a successful referral get REFERRAL_BONUS_COINS.
# REFERRAL_REDEEM_WINDOW_DAYS caps how long after signup a NEW account can
# redeem someone else's code — without this, a years-old account could farm
# bonuses indefinitely by redeeming a friend's code at any point; capping it
# to a signup-window means it's genuinely a new-user acquisition incentive,
# not a standing free-coins loophole.
# ---------------------------------------------------------------------------
REFERRAL_BONUS_COINS = int(os.environ.get("REFERRAL_BONUS_COINS", 50))
REFERRAL_REDEEM_WINDOW_DAYS = int(os.environ.get("REFERRAL_REDEEM_WINDOW_DAYS", 7))

# TASK G10 (growth_and_feature_tasks.md — certificates as a share loop):
# general app web base URL, used to build a `?ref=<code>` signup link that
# rides along on a certificate share card (testseries/certificate_share_card.py,
# views_advanced.py::certificate_share_card). Same `getattr(settings, ...,
# default)`-with-sane-fallback shape `Classroom.referral_urls()` already uses
# for `TUITIONCLASS_WEB_BASE_URL` — this is the app-wide equivalent, not scoped
# to one classroom, since a certificate's referral link should land a new
# signup on the general signup screen, not inside someone else's classroom.
APP_WEB_BASE_URL = os.environ.get("APP_WEB_BASE_URL", "https://app.example.com")

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
# TASK 12 — Refer & Earn commission (attribution + ledger + anti-fraud).
#
# REFERRAL_ATTRIBUTION_DAYS: a referee who opened a referral link is
#   "owned" by that referrer (first touch wins) for this many days. Any
#   eligible purchase inside the window earns the referrer a commission;
#   after it, the attribution is dead and a new link can claim the user.
# REFERRAL_ACCEPT_LEGACY_CODES: old codes were "R" + hex(user_id * 7919) —
#   guessable. New codes are random ("L" + 8 chars, stored in
#   tuitionclass.ReferralCode). Keep True until links already shared in the
#   wild have aged out, then flip to 0 to stop accepting the old format.
# TESTSERIES_REFERRAL_COMMISSION_PERCENT: cut of an INDIVIDUAL, PAID series'
#   price paid to the referrer, funded out of the creator's payout (never the
#   platform's). 0 switches test-series commission off. Hard-capped by
#   TESTSERIES_REFERRAL_MAX_PERCENT so a typo can't hand out the whole price.
# REFERRAL_MAX_COMMISSIONS_PER_DAY / _COINS_PER_DAY: velocity caps per
#   referrer over a rolling 24h (anti-farming; see user_profile/fraud.py).
# TESTSERIES_REFERRAL_HOOKS: dotted paths testseries/access.py loads (golden
#   rule: testseries never imports tuitionclass).
# ---------------------------------------------------------------------------
REFERRAL_ATTRIBUTION_DAYS = int(os.environ.get("REFERRAL_ATTRIBUTION_DAYS", 30))
REFERRAL_ACCEPT_LEGACY_CODES = os.environ.get("REFERRAL_ACCEPT_LEGACY_CODES", "1") == "1"
TESTSERIES_REFERRAL_COMMISSION_PERCENT = os.environ.get("TESTSERIES_REFERRAL_COMMISSION_PERCENT", "10")
TESTSERIES_REFERRAL_MAX_PERCENT = os.environ.get("TESTSERIES_REFERRAL_MAX_PERCENT", "50")
REFERRAL_MAX_COMMISSIONS_PER_DAY = int(os.environ.get("REFERRAL_MAX_COMMISSIONS_PER_DAY", 30))
REFERRAL_MAX_COMMISSION_COINS_PER_DAY = int(os.environ.get("REFERRAL_MAX_COMMISSION_COINS_PER_DAY", 2000))
TESTSERIES_REFERRAL_HOOKS = {
    "code_for_user": "tuitionclass.bridge.referral_code_for_user",
    "resolve_referrer": "tuitionclass.bridge.referral_resolve_referrer",
    "pay_commission": "tuitionclass.bridge.referral_pay_testseries_commission",
}

# ---------------------------------------------------------------------------
# F-3: campus engagement-reward bonuses (see campus/tasks.py's
# check_attendance_streak_rewards / check_assigments_ontime_streak_rewards,
# campus/services.py's compute_attendance_streak / compute_assigments_ontime_streak).
# Paid via user_profile.CoinLedger.record_transaction(transaction_type=
# CAMPUS_REWARD, ...) -- distinct wallet/ledger from tuitionclass's
# CoinTransaction above, but the same "flat, env-overridable settings
# constant" shape as REFERRAL_BONUS_COINS, deliberately, so ops can retune
# either program the same way.
# CAMPUS_ATTENDANCE_STREAK_DAYS -- how many consecutive PRESENT/LATE daily
# attendance marks earn one bonus (paid again every further multiple).
# CAMPUS_assigments_STREAK_COUNT -- same idea for consecutive on-time
# (non-LATE, non-MISSING) assigments submissions within one section.
# ---------------------------------------------------------------------------
CAMPUS_ATTENDANCE_STREAK_DAYS = int(os.environ.get("CAMPUS_ATTENDANCE_STREAK_DAYS", 7))
CAMPUS_ATTENDANCE_STREAK_BONUS_COINS = int(os.environ.get("CAMPUS_ATTENDANCE_STREAK_BONUS_COINS", 10))
CAMPUS_assigments_STREAK_COUNT = int(os.environ.get("CAMPUS_assigments_STREAK_COUNT", 5))
CAMPUS_assigments_STREAK_BONUS_COINS = int(os.environ.get("CAMPUS_assigments_STREAK_BONUS_COINS", 15))

# Fee Reminder Notifications feature (FEE-6 follow-up) -- how many days
# before a FeeInvoice’s FeeStructure.due_date campus.tasks.
# send_fee_due_reminders() starts sending its advance "Fee due in N
# days" warning. Same env-overridable-constant shape as
# CAMPUS_ATTENDANCE_STREAK_DAYS above.
CAMPUS_FEE_REMINDER_DAYS_BEFORE = int(os.environ.get("CAMPUS_FEE_REMINDER_DAYS_BEFORE", 3))

# ---------------------------------------------------------------------------
# TASK G1 (growth_and_feature_tasks.md — Streaks): app-wide daily-open streak
# (user_profile.models.Streak / StreakManager.record_activity, StreakView,
# user_profile.tasks.send_streak_risk_reminders). Same env-overridable-
# constant shape as CAMPUS_ATTENDANCE_STREAK_DAYS above, deliberately, so ops
# can retune milestones/bonuses without a deploy.
#
# STREAK_MILESTONE_DAYS -- streak lengths (in days) that pay a one-time
# coin bonus the moment they're first reached (see STREAK_MILESTONE_BONUS_COINS).
# STREAK_MILESTONE_BONUS_COINS -- {milestone_day_count: coins}. A milestone
# with no entry here (or an entry of 0) pays nothing but still shows in the
# streak UI as reached.
# STREAK_REMINDER_HOUR -- read directly by the CELERY_BEAT_SCHEDULE entry
# above, not by the task itself, since a crontab's hour is fixed at process
# start; changing this env var needs a beat restart to take effect, same as
# any other crontab-shaped schedule in this file.
# ---------------------------------------------------------------------------
STREAK_MILESTONE_DAYS = tuple(
    int(d) for d in os.environ.get("STREAK_MILESTONE_DAYS", "7,30,100").split(",") if d.strip()
)
STREAK_MILESTONE_BONUS_COINS = {
    7: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_7", 20)),
    30: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_30", 100)),
    100: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_100", 500)),
}

# ---------------------------------------------------------------------------
# TASK G2 (growth_and_feature_tasks.md — Daily/weekly "recap" screen):
# user_profile.models.WeeklyRecap / user_profile.recap.generate_weekly_recap_
# for_user / user_profile.tasks.generate_weekly_recaps. Same env-overridable-
# constant shape as STREAK_REMINDER_HOUR above, deliberately, so ops can
# retune the generation time without a deploy.
#
# RECAP_GENERATION_HOUR -- the CELERY_BEAT_SCHEDULE entry below fires the
# generation task every Sunday at this server-time hour (default 23:00 /
# 11pm), i.e. right after the week (Mon-Sun) has fully ended, so
# recap.week_bounds()'s "the week that just finished" always resolves to a
# week that's genuinely over by the time this runs.
# ---------------------------------------------------------------------------
RECAP_GENERATION_HOUR = int(os.environ.get("RECAP_GENERATION_HOUR", 23))

# ---------------------------------------------------------------------------
# TASK 37 — user_profile/fraud.py earn-rate-limit knobs (burst-farm guard on
# EARN/CAMPUS_REWARD coin credits — see check_earn_rate_limit() there for the
# two-cap logic). These used to be hardcoded directly inside fraud.py; moved
# here so ops can retune them without a deploy, same env-overridable-constant
# shape as REFERRAL_BONUS_COINS / CAMPUS_ATTENDANCE_STREAK_BONUS_COINS above.
# fraud.py reads these at import time (module-level, not per-call), so all
# three MUST exist here or importing fraud.py raises AttributeError.
#
# EARN_RATE_LIMIT_WINDOW_MINUTES -- rolling window length.
# EARN_RATE_LIMIT_MAX_TRANSACTIONS -- max EARN/CAMPUS_REWARD credits allowed
#   within that window, regardless of size (catches many small credits).
# EARN_RATE_LIMIT_MAX_COINS -- max total coins credited within that window,
#   regardless of transaction count (catches a few large credits instead).
# Either cap tripping blocks the next credit. Defaults below are a
# conservative starting point, not measured production signal.
# ---------------------------------------------------------------------------
EARN_RATE_LIMIT_WINDOW_MINUTES = int(os.environ.get("EARN_RATE_LIMIT_WINDOW_MINUTES", 60))
EARN_RATE_LIMIT_MAX_TRANSACTIONS = int(os.environ.get("EARN_RATE_LIMIT_MAX_TRANSACTIONS", 20))
EARN_RATE_LIMIT_MAX_COINS = int(os.environ.get("EARN_RATE_LIMIT_MAX_COINS", 500))

# ---------------------------------------------------------------------------
# Coin purchase gateway (see CoinPurchase in tuitionclass/models.py,
# CoinPurchaseViewSet + _verify_gateway_signature in views.py). Written
# against Razorpay's order-create + HMAC-signature-verify shape. Both
# _create_gateway_order and _verify_gateway_signature already degrade
# safely (stub order id / fails-closed signature check) if this isn't set
# — but no real coin top-up can ever succeed until it is.
# ---------------------------------------------------------------------------
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# ---------------------------------------------------------------------------
# ADD (task 10 — confirmed missing, not just "not confirmed present" like the
# other three items in this audit): user_profile's coin-purchase webhook
# confirmation verifies the gateway's callback signature before crediting
# coins — that verification needs the gateway's webhook secret and the
# header name it signs the payload in. Neither existed anywhere in this file,
# so every webhook call currently 503s (fails closed, no secret to check
# against) instead of ever confirming a purchase.
#
# Keyed by gateway name (dict, not a single value) since RAZORPAY_KEY_ID/
# SECRET above already anticipates more than one gateway eventually — same
# shape, so adding a second gateway later is one more key, not a schema
# change. Razorpay is the only wired gateway today (see CoinPurchaseViewSet
# above), so it's the only key populated now.
#
# NOTE: key names here are a best guess based on the Razorpay integration
# already in this file — verify these match whatever user_profile's actual
# webhook view reads (e.g. `settings.PAYMENT_GATEWAY_WEBHOOK_SECRETS["razorpay"]`)
# before relying on this in production; that view wasn't available to check
# against when this was added.
# ---------------------------------------------------------------------------
PAYMENT_GATEWAY_WEBHOOK_SECRETS = {
    "razorpay": os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
}
PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS = {
    "razorpay": "X-Razorpay-Signature",
}

# ---------------------------------------------------------------------------
# MSG91 (see tuitionclass/notifications.py _send_sms / _send_whatsapp).
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
# targets. Separate from the tuitionclass notification templates above —
# this one is fed through MSG91's dedicated `/api/v5/otp` endpoint (not
# the generic SMS/flow API `_send_sms` uses) so our own `secrets`-
# generated OTP code is what actually gets delivered, not one MSG91
# generates itself. Reuses MSG91_AUTH_KEY above; unlike the tuitionclass
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
# Without this, four tuitionclass features exist only as inert DB rows:
#   - ClassSchedule recurrence rules never turn into joinable ClassSession
#     rows (tuitionclass.generate_upcoming_sessions)
#   - A session nobody clicks /end/ on stays LIVE forever
#     (tuitionclass.auto_complete_overdue_sessions)
#   - ClassReminder rows never actually get sent (tuitionclass.send_due_reminders)
#   - SessionWaitlist promotion never notifies the promoted student
#     (tuitionclass.notify_waitlist_promotion, fired from signals.py)
#   - A pass's un-taught escrow balance never comes back to an inactive
#     student once it expires (tuitionclass.expire_and_refund_passes)
# See tuitionclass/tasks.py for the task bodies and LearnScroll/celery.py for
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

# Cross-app side effects (notifications, chat-group sync — see
# core/async_utils.py + core/tasks.py) are enqueued from inside web requests,
# so an unreachable Redis must fail FAST (then core.async_utils falls back to
# running the task inline) instead of hanging the request on kombu's default
# multi-second connection retries.
CELERY_BROKER_CONNECTION_TIMEOUT = 2
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 2}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Under `manage.py test` / pytest there is no worker: run tasks inline so
# tests never try to reach Redis. Also switchable via env for local dev
# without a worker (CELERY_TASK_ALWAYS_EAGER=true).
import sys as _sys  # noqa: E402

TESTING = "test" in _sys.argv or "pytest" in _sys.modules
CELERY_TASK_ALWAYS_EAGER = TESTING or os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = False

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "tuitionclass-generate-upcoming-sessions": {
        "task": "tuitionclass.generate_upcoming_sessions",
        # Hourly is plenty — the task looks 14 days ahead and is fully
        # idempotent, so re-running it more or less often is always safe.
        "schedule": crontab(minute=0),
    },
    "tuitionclass-auto-complete-overdue-sessions": {
        "task": "tuitionclass.auto_complete_overdue_sessions",
        "schedule": crontab(minute="*/5"),
    },
    "tuitionclass-send-due-reminders": {
        "task": "tuitionclass.send_due_reminders",
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
    "tuitionclass-refresh-stale-enrolled-counts": {
        "task": "tuitionclass.refresh_stale_enrolled_counts",
        "schedule": crontab(minute="*/15"),
    },
    # NOTE (fix — the actual "student loses money" gap): the per-day escrow
    # design (PassPurchase.charge_for_session in models.py) already stops a
    # quiet/stopped-teaching classroom draining a pass all at once — coins
    # only ever release to the teacher one taught day at a time. But once
    # expires_at passes, nothing was ever calling reverse() for whatever
    # was LEFT in escrow — an inactive student who never noticed to hit
    # cancel() themselves just lost that balance permanently. This sweep
    # (tuitionclass/tasks.py) auto-refunds it. Interval matches
    # EXPIRE_REFUND_LOOKBACK_MINUTES in tasks.py (60 min) with a shorter
    # run cadence than the lookback, same reasoning as the enrolled-counts
    # job right above — a slow/delayed tick can never let a batch of
    # expiries fall in the gap between two runs and get missed.
    "tuitionclass-expire-and-refund-passes": {
        "task": "tuitionclass.expire_and_refund_passes",
        "schedule": crontab(minute="*/15"),
    },
    # Sweeps abandoned chunked uploads (client crashed/closed mid-upload)
    # and reclaims their temp disk usage — see
    # tuitionclass/tasks.py:cleanup_stale_chunked_uploads for exactly what it
    # checks. Hourly is enough since the staleness window itself is 6h.
    "tuitionclass-cleanup-stale-chunked-uploads": {
        "task": "tuitionclass.cleanup_stale_chunked_uploads",
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
    "tuitionclass-reconcile-stuck-coin-purchases": {
        "task": "tuitionclass.reconcile_stuck_coin_purchases",
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
    "tuitionclass-run-auto-renewals": {
        "task": "tuitionclass.run_auto_renewals",
        "schedule": crontab(minute="*/30"),
    },
    # NOTE (fix — same gap as tuitionclass-run-auto-renewals immediately
    # above, audit §12 item 22): tasks.expire_unclaimed_gifts (Pass 16)
    # sweeps PENDING PassGifts past their 7-day CLAIM_WINDOW_DAYS deadline
    # and refunds the gifter — without this entry an unclaimed gift's
    # already-debited coins just sat in limbo forever. Same self-cleaning-
    # query reasoning, same cadence.
    "tuitionclass-expire-unclaimed-gifts": {
        "task": "tuitionclass.expire_unclaimed_gifts",
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
    "tuitionclass-send-notification-digests": {
        "task": "tuitionclass.send_notification_digests",
        "schedule": crontab(minute=0),
    },
    # 🔥 NAYA — message app ka is Celery beat me pehle ZERO entry tha,
    # jabki dono tasks (message/tasks.py) ek poore feature ke liye zaroori
    # hain aur zero-risk / idempotent hain, same pattern jo tuitionclass ke
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
        # users ko - same cadence tuitionclass ke lookback-window jobs jaisa.
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
    # 🔧 GAP FIX (this pass) — CHAT_APP_DOCUMENTATION.md §9.4 item 25:
    # `management/commands/expire_stale_parent_access.py` (Parent Mode DB
    # hygiene — see that file's own docstring) existed with ZERO
    # registration anywhere: no CELERY_BEAT_SCHEDULE entry, no confirmed
    # external cron. Low urgency (the command is explicitly DB hygiene
    # only — `HasValidParentToken` already rejects expired tokens/codes
    # live, on every request, regardless of whether this has ever run),
    # but left unscheduled, stale `ParentToken`/`ParentAccessCode` rows
    # just accumulate forever instead of ever getting cleaned up.
    #
    # ⚠️ ASSUMPTION — `message/tasks.py` wasn't part of this pass's
    # upload, so it can't be confirmed here whether a Celery task wrapper
    # for this command already exists. Unlike `send_scheduled_messages`/
    # `cleanup_expired_messages` above (each of which has BOTH a
    # management command AND a matching `@shared_task` in
    # `message/tasks.py`), `expire_stale_parent_access` is currently a
    # management command ONLY — a `CELERY_BEAT_SCHEDULE` "task" string
    # only does anything if a task is actually registered under that
    # exact name; it is not a management-command path Celery can invoke
    # directly. This entry assumes a thin wrapper task named
    # "message.expire_stale_parent_access" — same explicit "message.<name>"
    # naming this file's other message entries already use (see
    # message-send-scheduled-messages above) — e.g.:
    #
    #     @shared_task(name="message.expire_stale_parent_access")
    #     def expire_stale_parent_access():
    #         from django.core.management import call_command
    #         call_command("expire_stale_parent_access")
    #
    # If `message/tasks.py` doesn't already have this wrapper (or an
    # equivalent under a different name), add it there first and update
    # the "task" string below to match — registering this entry alone
    # with no task registered under that name means Celery Beat sends a
    # tick nobody picks up (silently dropped, not even an error), not
    # that the cleanup actually runs.
    #
    # Once that task exists, once-daily is plenty per this pass's own
    # request — DB hygiene only, with an internal grace window measured
    # in days (TOKEN_DELETE_GRACE_DAYS=14 / CODE_DEACTIVATE_GRACE_DAYS=30
    # in the command itself), not minutes. Staggered 30 min after
    # message-purge-soft-deleted-conversations (3:30) so the two
    # message-app hygiene sweeps don't land in the same minute.
    "message-expire-stale-parent-access": {
        "task": "message.expire_stale_parent_access",
        "schedule": crontab(hour=4, minute=0),
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
    # UPDATE (issue #4): the root-cause fix is now in place —
    # user_profile/signals.py recounts from real Follow rows on every
    # Follow post_save/post_delete, and the views no longer do their own
    # F() +1/-1. This job is now just a weekly safety net for writes that
    # never emit signals (QuerySet.update()/bulk_create()/raw SQL), so it
    # was moved from every-6-hours to Sunday 03:15. If its log line ever
    # reports corrections, some code path is bypassing the signals.
    "user-profile-reconcile-follow-counts": {
        "task": "user_profile.tasks.reconcile_follow_counts",
        "schedule": crontab(hour=3, minute=15, day_of_week=0),
    },
    # TASK G1 (growth_and_feature_tasks.md — Streaks) —
    # user_profile.tasks.send_streak_risk_reminders. Runs once a day at
    # STREAK_REMINDER_HOUR (default 22:00 / 10pm server time), which is
    # "~2 hours before day-end" for the same midnight-day-boundary
    # `timezone.localdate()` rollover Streak.objects.record_activity()
    # already uses — deliberately a single daily crontab entry (not
    # every-N-minutes) since the task itself is idempotent per call but
    # there is nothing to gain from firing it more than once in that
    # window; same "no state of its own, occasional extra run is
    # harmless" reasoning campus's own daily reminder jobs already rely
    # on above.
    "user-profile-send-streak-risk-reminders": {
        "task": "user_profile.tasks.send_streak_risk_reminders",
        "schedule": crontab(
            hour=int(os.environ.get("STREAK_REMINDER_HOUR", 22)), minute=0,
        ),
    },
    # TASK G2 (growth_and_feature_tasks.md — recap screen) —
    # user_profile.tasks.generate_weekly_recaps. Sunday only (day_of_week=0
    # in crontab's Sun=0 convention), at RECAP_GENERATION_HOUR (default
    # 23:00) — deliberately once a week, not more often: the task is
    # idempotent per (user, week_start) via WeeklyRecap's unique constraint
    # (update_or_create in recap.py), so a re-run within the same week just
    # recomputes and re-notifies, which is harmless but pointless to do on
    # a tighter schedule.
    "user-profile-generate-weekly-recaps": {
        "task": "user_profile.tasks.generate_weekly_recaps",
        "schedule": crontab(
            hour=int(os.environ.get("RECAP_GENERATION_HOUR", 23)), minute=0, day_of_week=0,
        ),
    },
    # FEE-6 — campus/tasks.py::send_fee_due_reminders. Task string is
    # "campus.tasks.<name>" (Celery's default module-path-derived name,
    # since campus/tasks.py never passes an explicit name= to
    # @shared_task) — same convention as the user_profile entry right
    # above, not the "app.func" shorthand tuitionclass/message entries use
    # (those explicitly rename their tasks; campus/user_profile don't).
    # Once-a-day is enough — same reasoning as
    # tuitionclass-send-notification-digests and campus's own
    # send_assigments_due_reminders (design doc §6): this task carries
    # no state of its own, so an occasional extra run is harmless.
    #
    # CAMPUS_FEE_REMINDER_DAYS_BEFORE (env, default 3) controls how many
    # days out the task’s own "advance" branch starts firing (see that
    # function’s docstring for the day-by-day re-fire caveat this
    # produces) — read directly by send_fee_due_reminders() at call
    # time, same "settings knob, not a code constant" pattern this
    # file already uses elsewhere (e.g. CAMPUS_ATTENDANCE_STREAK_DAYS).
    "campus-send-fee-due-reminders": {
        "task": "campus.tasks.send_fee_due_reminders",
        "schedule": crontab(hour=8, minute=0),
    },
    # 🔧 FIX (this pass) — same "written but never registered" bug class
    # this file has already had to fix repeatedly for tuitionclass/message/
    # user_profile above.
    "campus-check-low-attendance": {
        "task": "campus.tasks.check_low_attendance",
        # check_low_attendance() takes no args — it loops over every
        # active Campus itself (campus/tasks.py), so a single global
        # crontab entry is correct as-is. Once-daily, end-of-school-day
        # check.
        "schedule": crontab(hour=18, minute=0),
    },
    "campus-send-assigments-due-reminders": {
        "task": "campus.tasks.send_assigments_due_reminders",
        # Also loops internally (every assigments due today, across all
        # campuses) — no args needed. Staggered 30min after the
        # fee-reminder job above so both don't hit the DB in the same
        # minute.
        "schedule": crontab(hour=8, minute=30),
    },
    # 🔧 FIX (this pass) — same "written but never registered" bug this
    # file already had to fix for check_low_attendance/
    # send_assigments_due_reminders above: campus/tasks.py's F-3
    # check_attendance_streak_rewards() and
    # check_assigments_ontime_streak_rewards() both existed and (per the
    # previous pass) no longer crash on import, but neither was ever
    # added to this schedule — a Celery task that's never registered
    # here simply never fires on its own, no error, no log, nothing.
    # Both loop internally over every ACTIVE enrollment themselves (same
    # shape as check_low_attendance), so a single global crontab entry
    # each is correct as-is — no args needed. Once-daily, staggered
    # after the existing 8:00/8:30 fee/assigments jobs and the 18:00
    # low-attendance check above so none of the five campus jobs land in
    # the same minute.
    "campus-check-attendance-streak-rewards": {
        "task": "campus.tasks.check_attendance_streak_rewards",
        "schedule": crontab(hour=19, minute=0),
    },
    "campus-check-assigments-ontime-streak-rewards": {
        "task": "campus.tasks.check_assigments_ontime_streak_rewards",
        "schedule": crontab(hour=19, minute=30),
    },
    # 🔴 REMOVED (this pass) — "campus-rollover-session" and
    # "campus-refresh-analytics-snapshot" were both registered here with
    # NO `args`/`kwargs`, but campus/tasks.py confirms both tasks take
    # REQUIRED positional args:
    #   - rollover_session(campus_id, new_session_id)
    #   - refresh_analytics_snapshot(campus_id, session_id)
    # Unlike check_low_attendance/send_assigments_due_reminders/
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

    # ------------------------------------------------------------------
    # TESTSERIES. Same "written but never registered" bug class as every
    # block above: testseries/tasks.py defined refund_unchecked_paid_attempts
    # and send_pending_check_reminders, but neither was ever scheduled — so
    # escrowed coins for a test the creator never checked were NEVER
    # auto-refunded, whatever TESTSERIES_AUTO_REFUND_DAYS said. Task names are
    # "testseries.tasks.<fn>" (no explicit name= is passed to @shared_task).
    # ------------------------------------------------------------------
    "testseries-refund-unchecked-paid-attempts": {
        "task": "testseries.tasks.refund_unchecked_paid_attempts",
        "schedule": crontab(hour=3, minute=0),
    },
    "testseries-send-pending-check-reminders": {
        "task": "testseries.tasks.send_pending_check_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
    # Server-side timer: closes attempts whose deadline + grace passed and
    # that the student never submitted (autosave draft is graded).
    "testseries-auto-submit-expired-attempts": {
        "task": "testseries.tasks.auto_submit_expired_attempts",
        "schedule": crontab(minute="*"),
    },
    # A host who forgot to press "End" must not leave a LiveKit room running.
    "testseries-end-overrun-live-sessions": {
        "task": "testseries.tasks.end_overrun_live_sessions",
        "schedule": crontab(minute="*/10"),
    },
    "testseries-expire-stale-recordings": {
        "task": "testseries.tasks.expire_stale_recordings",
        "schedule": crontab(minute=5),
    },
    # TASK G7 (growth_and_feature_tasks.md â Leaderboards) â
    # leaderboard.tasks.recompute_weekly_boards. Only scans the CURRENT
    # ISO week's TestAttempt/Attendance/PostLike-etc rows (see
    # leaderboard/services.py's week_bounds()), so it stays cheap enough
    # to run every 30 min and keep "your rank this week" close to live â
    # same cadence class as the other near-live jobs above
    # (tuitionclass-reconcile-stuck-coin-purchases etc.).
    "leaderboard-recompute-weekly": {
        "task": "leaderboard.recompute_weekly_boards",
        "schedule": crontab(minute="*/30"),
    },
    # TASK G7 â leaderboard.tasks.recompute_all_time_boards. Scans full
    # history per board, so it runs once a day, off-peak (03:30, same slot
    # class as message-purge-soft-deleted-conversations above) rather than
    # on the weekly job's tighter cadence.
    "leaderboard-recompute-all-time": {
        "task": "leaderboard.recompute_all_time_boards",
        "schedule": crontab(hour=3, minute=30),
    },
    # C1-BE — post.tasks.prune_old_post_events: deletes PostEvent analytics
    # rows older than 30 days, in batches. Daily, off-peak (03:15, just before
    # the 03:30 leaderboard job above so the two heavy jobs never overlap).
    "post-prune-old-events": {
        "task": "post.tasks.prune_old_post_events",
        "schedule": crontab(hour=3, minute=15),
    },
}

# 🔧 GAP FIX — grace window ke liye, dekho message/tasks.py:
# purge_soft_deleted_conversations aur views.py: GroupViewSet.destroy().
# Isse zyada rakhoge to soft-deleted data zyada der tak recoverable
# rehta hai, kam rakhoge to disk jaldi clear hota hai — 7 din WhatsApp
# jaisi apps ke "recently deleted" window se milta-julta safe default hai.
GROUP_SOFT_DELETE_GRACE_DAYS = int(os.getenv("GROUP_SOFT_DELETE_GRACE_DAYS", "7"))

# ---------------------------------------------------------------------------
# F-1 — `core/management/commands/check_config_drift.py`. That command's
# own docstring expects this to be set here; it was never actually added
# in a previous pass (core_app_documentation.md §9 item 17 / §11 flagged
# this as "verify karo ye setting maujood hai" — it wasn't, until now).
# Hardcoded fallback in the command itself is `["user_profile", "core"]`
# if this setting is absent, so this isn't strictly required to avoid a
# crash — but leaving it unset means every other app silently gets zero
# drift coverage (throttle-scope, celery-beat, admin-registration,
# urls-wiring checks) without that being an explicit, visible decision.
#
# 'assigments'/'testseries'/'campus' added this pass too — per §11's own
# "Scope (deliberate, limitation nahi)" note, extending coverage is just
# adding a label here, no other code changes needed, since all 4 checks
# are fully generic (Django app-registry + AST/regex scans, no per-app
# logic). They were left out originally only because they didn't exist
# yet when this command was written. Doing this now surfaced a real bug
# it would otherwise have hit immediately: 'assigments' was registered
# under a typo'd label ('assigments') in INSTALLED_APPS above — fixed
# there, required before this list could include it at all
# (`django_apps.get_app_config("assigments")` would otherwise raise
# `LookupError`, not just skip it quietly).
#
# 'tuitionclass'/'message'/'post'/'login' are NOT added here — no signal in
# any doc reviewed so far that they were considered or excluded on
# purpose; left out rather than guessed onto this list. Add them in a
# future pass once that's an explicit decision, not a default.
CONFIG_DRIFT_APPS = ["user_profile", "core", "assigments", "testseries", "campus"]

# Escape hatches for the same command — intentionally left empty. Per
# §11's own guidance, only add an entry here once a specific check has
# actually flagged something that's genuinely deliberate (not a bug the
# check is right to catch) — not preemptively for apps just added above.
CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}


# =====================================================================
# TESTSERIES — ADVANCED CONFIG  (see testseries/policy.py, access.py, live.py)
# Every value has a sane default in code; these lines just make the knobs
# visible. Env overrides are for staging / experiments.
# =====================================================================

# Who may charge for a test series.
#   mode "required"  -> must be paid   "optional" -> creator chooses
#         "forbidden" -> always free
# Product rule: a test series a user creates on their own (individual) is
# PAID; a campus one AND a tuition-class one are always FREE (TASK 9.1 — this is
# hard-coded in testseries/policy.py::ALWAYS_FREE_SOURCES, so the "campus" /
# "tuitionclass" entries below cannot make them paid). Flip the individual mode
# to change that one — e.g. TESTSERIES_INDIVIDUAL_PRICING=optional lets
# individuals publish free tests.
TESTSERIES_PRICING_POLICY = {
    "individual": {
        "mode": os.getenv("TESTSERIES_INDIVIDUAL_PRICING", "required"),
        "min_coins": int(os.getenv("TESTSERIES_MIN_PRICE_COINS", "1")),
        "max_coins": int(os.getenv("TESTSERIES_MAX_PRICE_COINS", "100000")),
    },
    "campus": {"mode": "forbidden"},
    # TASK 9.1: always free. (TESTSERIES_TUITIONCLASS_PRICING is no longer read.)
    "tuitionclass": {"mode": "forbidden"},
}

# Server-side timer. A submit arriving up to this many seconds after the
# deadline is still accepted as on-time (network latency / clock jitter).
TESTSERIES_SUBMIT_GRACE_SECONDS = int(os.getenv("TESTSERIES_SUBMIT_GRACE_SECONDS", "30"))
# What to do with a submit later than deadline + grace:
#   "use_draft" (default) grade the last autosave · "accept" grade it, flag it · "reject" 400
TESTSERIES_LATE_SUBMIT = os.getenv("TESTSERIES_LATE_SUBMIT", "use_draft")

# Campus / tuition-class series are visible and startable only by members of that
# context. Resolvers live in the owning apps (golden rule: testseries never
# imports campus/tuitionclass). Set a value to "public" to make that source
# world-readable (tuition-class marketplace), or ENFORCE=0 to turn the check off.
TESTSERIES_ENFORCE_CONTEXT_ACCESS = os.getenv("TESTSERIES_ENFORCE_CONTEXT_ACCESS", "1") == "1"
TESTSERIES_CONTEXT_ACCESS = {
    "section": "campus.bridge.user_accessible_testseries_context_ids",
    "classroom": "tuitionclass.bridge.user_accessible_testseries_context_ids",
}

# TASK 9.2 — "a class / campus series was published": the owning app announces it
# (notice board entry + notification/push), exactly once per series. Same dotted-
# path pattern as TESTSERIES_CONTEXT_ACCESS above (testseries never imports the
# other apps). Hook signature: fn(payload: dict). A context_type with no entry is
# not announced here (campus "section" notifies its roster at creation instead).
TESTSERIES_PUBLISH_HOOKS = {
    "classroom": "tuitionclass.bridge.on_testseries_published",
}

# Public share link, e.g. "https://learnscroll.app/test/{slug}". Empty = the API
# preview URL (/testseries/public/<slug>/) is returned instead.
TESTSERIES_SHARE_URL_TEMPLATE = os.getenv("TESTSERIES_SHARE_URL_TEMPLATE", "")

# Escrow safety nets (previously read via getattr() defaults only).
TESTSERIES_AUTO_REFUND_DAYS = int(os.getenv("TESTSERIES_AUTO_REFUND_DAYS", "14"))
TESTSERIES_REMINDER_DAYS = int(os.getenv("TESTSERIES_REMINDER_DAYS", "3"))

# Live video: a recording still "recording" after this long never got its
# egress_ended webhook -> marked failed. A live session is force-ended this many
# minutes after its window closed.
TESTSERIES_STALE_RECORDING_HOURS = int(os.getenv("TESTSERIES_STALE_RECORDING_HOURS", "6"))
TESTSERIES_LIVE_OVERRUN_MINUTES = int(os.getenv("TESTSERIES_LIVE_OVERRUN_MINUTES", "30"))


# =====================================================================
# ASSIGNMENTS — PUBLISHING (assigments/views.py: publish / explore / join)
# =====================================================================
# Public share link of a published assignment / project, e.g.
# "https://learnscroll.app/a/{slug}". Empty = the API preview URL
# (/assigments/p/<slug>/) is returned instead.
ASSIGNMENTS_SHARE_URL_TEMPLATE = os.getenv("ASSIGNMENTS_SHARE_URL_TEMPLATE", "")

# =====================================================================
# HOME FEED DISCOVERY MIX (post/feed_mix.py)
# Share of every feed page taken from each source. Values are relative
# weights (auto-normalised to 1.0): 0.6/0.3/0.1 = 60% following, 30%
# recommended, 10% trending. Env override: FEED_MIX_RATIOS="60,30,10".
# If a source has too few posts, its slots are refilled from the others.
# =====================================================================
def _feed_ratios_from_env():
    raw = os.getenv("FEED_MIX_RATIOS", "60,30,10")
    try:
        f, r, t = [float(x) for x in raw.split(",")]
        return {"following": f, "recommended": r, "trending": t}
    except (ValueError, TypeError):
        return {"following": 60.0, "recommended": 30.0, "trending": 10.0}


FEED_MIX_RATIOS = _feed_ratios_from_env()
# Optional caps/windows; any key omitted falls back to post/feed_mix.py defaults.
FEED_MIX_LIMITS = {
    "following_pool_cap": 600,
    "recommended_pool_cap": 400,
    "trending_pool_cap": 100,
    "trending_window_days": 7,
    "recommended_window_days": 30,
}

# =====================================================================
# HOME FEED "SEEN" HANDLING (post/feed_mix.py + HomeFeedView)
# Posts the user already saw (PostView rows, incl. the batch
# POST /post/feed/seen/ ones) are dropped from recommended/trending and
# pushed to the bottom of following. Same pattern as FEED_MIX_LIMITS: any
# key omitted falls back to DEFAULT_SEEN_LIMITS in post/feed_mix.py.
#   enabled                 master switch (env FEED_SEEN_ENABLED=0/1)
#   window_days             look-back window in days (env FEED_SEEN_WINDOW_DAYS)
#   cap                     max most-recent seen ids per request (env FEED_SEEN_CAP)
#   fill_min                recommended/trending pool smaller than this after
#                           excluding seen -> topped up with seen posts
#   cutoff_max_age_minutes  older `seen_cutoff` (stale `next` link) = new session
# =====================================================================
def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


FEED_SEEN_LIMITS = {
    "enabled": os.getenv("FEED_SEEN_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "window_days": _env_int("FEED_SEEN_WINDOW_DAYS", 30),
    "cap": _env_int("FEED_SEEN_CAP", 2000),
    "fill_min": 20,
    "cutoff_max_age_minutes": 180,
}

# =====================================================================
# VIDEO WATCH-TIME RANKING (post/feed_mix.py video_watch_boost)
# boost = confidence * (completion_rate * completion_points
#                       + min(avg_watch_s, watch_seconds_cap) / cap * watch_seconds_points)
# confidence = n / (n + confidence_k), n = viewers who reported watch progress.
# Applies to Home (following/recommended/trending) and Explore. Any key omitted
# falls back to DEFAULT_WATCH_TIME. enabled=False (env FEED_WATCH_TIME_ENABLED=0)
# restores the old `video_completion_rate * 30`.
# =====================================================================
FEED_WATCH_TIME = {
    "enabled": os.getenv("FEED_WATCH_TIME_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "completion_points": 30.0,
    "watch_seconds_points": 10.0,
    "watch_seconds_cap": 60.0,
    "confidence_k": 5.0,
}

# =====================================================================
# AUTHOR AFFINITY (post/feed_mix.py load_author_affinity)
# Authors the user likes / comments on rank higher in ALL Home pools
# (following: only inside the recent/unseen bands). Any key omitted falls back
# to DEFAULT_AFFINITY. Env FEED_AUTHOR_AFFINITY_ENABLED=0 switches it off.
#   window_days / half_life_days   30-day window, an interaction halves every 14 days
#   like_weight / comment_weight   1 / 3  ("wrong" reactions never count)
#   points_per_unit / max_points   2 points per unit, capped at 20
#   max_authors                    strongest N authors are used (SQL CASE branches)
# An author with an active "show fewer" row gets no affinity (show fewer wins).
# =====================================================================
FEED_AUTHOR_AFFINITY = {
    "enabled": os.getenv("FEED_AUTHOR_AFFINITY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "window_days": 30,
    "half_life_days": 14.0,
    "like_weight": 1.0,
    "comment_weight": 3.0,
    "points_per_unit": 2.0,
    "max_points": 20.0,
    "max_authors": 50,
}

# =====================================================================
# HOME FEED "SHOW FEWER LIKE THIS" (post/feed_mix.py, FeedFeedback model)
# A tap on "Show fewer" on a post's category / hashtag / author adds a
# NEGATIVE ranking signal (the mirror image of UserInterest's +15). It is
# subtracted in the recommended + trending pools only - never from the
# following pool. Any key omitted falls back to DEFAULT_FEEDBACK in
# post/feed_mix.py; `points` / `load_cap` may be overridden per kind.
#   enabled         master switch (env FEED_FEEDBACK_ENABLED=0/1); off ->
#                   rows are kept but ignored by ranking
#   half_life_days  the weight halves every N days (env FEED_FEEDBACK_HALF_LIFE_DAYS,
#                   default 30 = slow decay). 0 = permanent, never decays.
#   step            weight added per tap; max_weight caps repeated taps
#   min_effective   decayed below this -> ignored (and pruned on next write)
#   points          minus points at weight 1.0, per kind (category 15 = the
#                   interest bonus, hashtag 8, author 25)
# =====================================================================
def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


FEED_FEEDBACK = {
    "enabled": os.getenv("FEED_FEEDBACK_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "half_life_days": _env_float("FEED_FEEDBACK_HALF_LIFE_DAYS", 30.0),
    "step": 1.0,
    "max_weight": 3.0,
    "min_effective": 0.05,
    "points": {"category": 15.0, "hashtag": 8.0, "author": 25.0},
}

# =====================================================================
# HOME FEED RANKED SNAPSHOT (post/feed_snapshot.py + HomeFeedView)
# GET /post/feed/ builds the ranked id pools ONCE per scrolling session,
# stores them in CACHES (Redis when REDIS_URL is set) and pages through the
# frozen list with an opaque `cursor`, so posts don't jump up/down while
# scores change. Cache miss/expiry falls back to a rebuild, never an error.
#   enabled      master switch (env FEED_SNAPSHOT_ENABLED=0/1); off -> old
#                page/offset behaviour
#   ttl_seconds  how long one session's ranking stays frozen
#                (env FEED_SNAPSHOT_TTL, default 900 = 15 min)
# =====================================================================
FEED_SNAPSHOT = {
    "enabled": os.getenv("FEED_SNAPSHOT_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "ttl_seconds": _env_int("FEED_SNAPSHOT_TTL", 900),
}

# =====================================================================
# FEED_REELS (vertical short-video feed: post/reels.py + post/reels_views.py)
# GET /post/reels/  [?start=<post_id>] [?cursor=...]. One ranked pool built from
# the Home pieces (engagement + video watch boost + velocity + interest + taste +
# friend-of-follow + author affinity - show-fewer), +following_bonus for authors
# you follow, seen videos excluded, order frozen per scrolling session (same
# snapshot store as Home, namespace "reels"). Any key omitted falls back to
# DEFAULT_REELS in post/reels.py.
#   enabled               master switch (env REELS_ENABLED=0/1); off -> empty page
#   min_aspect            height/width must be >= this; unknown dimensions are
#                         allowed (env REELS_MIN_ASPECT, default 1.2; 0 = off)
#   max_duration_seconds  longer videos are not reels (env REELS_MAX_DURATION_SECONDS,
#                         default 180; 0 = no cap). Videos need a KNOWN duration.
#   following_bonus       extra points for authors the caller follows (default 10)
#   pool_cap              max ranked ids per scrolling session (default 300)
#   page_size / max_page_size   default 10 / 30 (?page_size=)
#   fallback              never-empty: when the unseen pool is smaller than min_pool the media
#                         rules are loosened in tiers (any aspect -> any duration -> any video
#                         row) and appended after the better reels; safety rules never loosen
#                         (env REELS_FALLBACK=0/1, default on)
#   min_pool              pool size below which the next tier is added (env REELS_MIN_POOL, default 10)
# Debug an empty feed: python manage.py reels_diagnose --user <name>
# =====================================================================
FEED_REELS = {
    "enabled": os.getenv("REELS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "min_aspect": _env_float("REELS_MIN_ASPECT", 1.2),
    "max_duration_seconds": _env_int("REELS_MAX_DURATION_SECONDS", 180),
    "following_bonus": _env_float("REELS_FOLLOWING_BONUS", 10.0),
    "pool_cap": _env_int("REELS_POOL_CAP", 300),
    "page_size": _env_int("REELS_PAGE_SIZE", 10),
    "max_page_size": 30,
    "fallback": os.getenv("REELS_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off"),
    "min_pool": _env_int("REELS_MIN_POOL", 10),
}