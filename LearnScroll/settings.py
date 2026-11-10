from pathlib import Path
import os
from dotenv import load_dotenv
from datetime import timedelta
from django.db.backends.signals import connection_created
from django.core.exceptions import ImproperlyConfigured
from LearnScroll.ops_settings import env_bool, env_float, env_int  # P-C3

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
# Env-driven allowlist (comma-separated). Fails SAFE: with DEBUG=False and no
# ALLOWED_HOSTS set, Django rejects every request (400) instead of accepting any
# Host header. DEBUG-only wildcard below is for local dev convenience.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
# T0.1 (B1): the hard-coded ALLOWED_HOSTS = ['*'] that used to live here is gone;
# the env-driven allowlist above is the only source. Wrong Host -> 400.
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

# T0.1: in production an empty allowlist would 400 every request (incl. health
# checks) with no obvious reason -> fail loudly at boot instead. Skipped while
# running the test-suite (Django's test runner injects 'testserver' itself).
import sys as _sys  # noqa: E402
if not DEBUG and not ALLOWED_HOSTS and "test" not in _sys.argv:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS is empty. Set ALLOWED_HOSTS in the environment, e.g. "
        "ALLOWED_HOSTS=api.learnscroll.app (comma-separated, no wildcard)."
    )
if not DEBUG and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must not contain '*' when DEBUG is False.")


# ---------------------------------------------------------------------------
# C4: LiveKit (calls / study rooms / live classes) production sanity check.
# Fails ONLY when LIVEKIT_WS_URL IS set to something that can never work in
# production (plain ws://, localhost, private/LAN IP) — iOS ATS + Android
# cleartext policy block ws://, and a LAN IP is unreachable from the internet.
# Unset => no crash (calls/study-room return a clean 503, text chat unaffected).
# ---------------------------------------------------------------------------
def _validate_livekit_config():
    import ipaddress
    import warnings
    from urllib.parse import urlparse

    ws = (os.getenv("LIVEKIT_WS_URL") or "").strip()
    if not ws:
        warnings.warn(
            "LIVEKIT_WS_URL is not set: calls, study rooms and live classes will answer 503. "
            "Set it to wss://<livekit-domain> (see deploy/livekit/README.md).",
            RuntimeWarning,
        )
        return
    parsed = urlparse(ws)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "wss":
        raise ImproperlyConfigured(
            f"LIVEKIT_WS_URL must be wss://<domain> when DEBUG is False (got {ws!r}). "
            "Plain ws:// is blocked by iOS ATS / Android cleartext policy."
        )
    private = host in ("localhost", "") or host.endswith((".local", ".internal", ".lan"))
    try:
        ip = ipaddress.ip_address(host)
        private = private or ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    if private:
        raise ImproperlyConfigured(
            f"LIVEKIT_WS_URL points at a local/private host ({host!r}); clients on the internet cannot reach it."
        )
    http_url = (os.getenv("LIVEKIT_HTTP_URL") or "").strip()
    if http_url and urlparse(http_url).scheme not in ("http", "https"):
        raise ImproperlyConfigured("LIVEKIT_HTTP_URL must start with http:// or https://")


if not DEBUG and "test" not in _sys.argv:
    _validate_livekit_config()




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
# T0.1: `check --deploy` W008 -> redirect http->https in production. Safe behind a
# TLS-terminating proxy because SECURE_PROXY_SSL_HEADER below is set (the proxy
# MUST send X-Forwarded-Proto). Override with SECURE_SSL_REDIRECT=false only if
# the proxy itself already does the redirect.
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "false" if DEBUG else "true").strip().lower() in ("1", "true", "yes", "on")
# T3.7 probes (/healthz/, /readyz/) must answer 200 even when a load balancer / local checker hits
# them over plain http — never redirect them. (The Host header must still be in ALLOWED_HOSTS.)
SECURE_REDIRECT_EXEMPT = [r"^healthz/?$", r"^readyz/?$", r"^healthz/deep/?$",
                         # C2: LiveKit webhook POST ko 301 redirect nahi (signature hi auth hai)
                         r"^message/livekit/webhook/?$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30 if not DEBUG else 0  # 30 days
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# X6 -- cookie / header hardening (the app itself uses Bearer tokens; these protect /admin/ and any cookie-auth page).
# HttpOnly: JavaScript can never read the session cookie. SameSite=Lax: a cookie is not sent on cross-site POSTs.
# (CSRF_COOKIE_HTTPONLY is left at Django's default False on purpose: the admin's own JS reads the CSRF cookie.)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")
SECURE_REFERRER_POLICY = os.getenv("SECURE_REFERRER_POLICY", "same-origin")
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")

# X6 -- wrong CURRENT password while changing a sensitive setting (change-password, change e-mail / phone, unlink
# Google): login/sensitive.py ReauthGuard. N wrong tries inside the window lock the action for the lockout time.
# X6 -- files (relative to BASE_DIR) allowed to use Django's @csrf_exempt (a signed webhook). `check_security_settings`
# warns about every other use. Empty today: the code base has none (DRF APIViews are exempt by design - Bearer auth).
CSRF_EXEMPT_ALLOWED: list = []

REAUTH_MAX_FAILURES = int(os.getenv("REAUTH_MAX_FAILURES", "5"))
REAUTH_FAILURE_WINDOW_SECONDS = int(os.getenv("REAUTH_FAILURE_WINDOW_SECONDS", "900"))
REAUTH_LOCKOUT_SECONDS = int(os.getenv("REAUTH_LOCKOUT_SECONDS", "900"))

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
# P-C3: share of requests traced (0..1). Default 0.1 (was 1.0 = every request); a bad value falls back to the default.
SENTRY_TRACES_SAMPLE_RATE = env_float("SENTRY_TRACES_SAMPLE_RATE", 0.1, lo=0.0, hi=1.0)

# T3.7 — never spend trace quota on uptime-monitor pings (they hit /healthz/ + /readyz/ every minute).
_SENTRY_IGNORED_PATHS = ("/healthz", "/readyz", "/tuitionclass/healthz")


def _sentry_traces_sampler(ctx):
    path = ((ctx.get("wsgi_environ") or {}).get("PATH_INFO")
            or (ctx.get("asgi_scope") or {}).get("path") or "")
    if path.startswith(_SENTRY_IGNORED_PATHS):
        return 0.0
    return SENTRY_TRACES_SAMPLE_RATE


# T3.7 — backups + health probes (see deploy/BACKUP_RESTORE_RUNBOOK.md)
BACKUP_DIR = os.getenv("BACKUP_DIR", "/var/backups/learnscroll")
HEALTHCHECK_TOKEN = os.getenv("HEALTHCHECK_TOKEN", "")  # enables GET /healthz/deep/

def _sentry_before_send(event, hint):
    from LearnScroll.log_filters import scrub_sentry_event
    return scrub_sentry_event(event)


def _sentry_before_breadcrumb(crumb, hint):
    from LearnScroll.log_filters import scrub_sentry_event
    return scrub_sentry_event(crumb)


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
            # Task 13: monitor_beat_tasks=True turns every CELERY_BEAT_SCHEDULE entry (incl. the 5
            # copyrights.* automations) into a Sentry *Cron Monitor*. If beat or the worker is
            # DOWN, a run is missed and Sentry alerts -- plain exception capture can't see that.
            CeleryIntegration(monitor_beat_tasks=True),
            # Captures every logger.exception()/logger.error() call as a
            # Sentry event (with INFO+ as breadcrumbs for context) — this is
            # what actually surfaces the deliberately-swallowed exceptions
            # listed above instead of leaving them as console-only noise.
            LoggingIntegration(level=None, event_level="ERROR"),
        ],
        # 1.0 = trace every request. Turn this down (e.g. 0.1-0.2) once
        # real traffic shows up — it's a cost/volume knob, not correctness.
        traces_sampler=_sentry_traces_sampler,
        # Coin balances, coupon codes, join-request notes, etc. all pass
        # through this API — don't let Sentry capture request bodies/user
        # PII by default.
        send_default_pii=False,
        # P-B2: a websocket / request URL may carry ?ticket= (or, from an old build, ?token=<JWT>) - scrub it from
        # events and breadcrumbs before they leave the server (LearnScroll/log_filters.py).
        before_send=_sentry_before_send,
        before_breadcrumb=_sentry_before_breadcrumb,
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
    # = django.contrib.admin with the hardened site (TOTP 2FA, staff password policy,
    # login lockout) -- see common/admin_site.py. Same app label ('admin').
    'common.apps.SecureAdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "django.contrib.postgres",
    # Two-factor auth for moderators/admins (TOTP authenticator app + one-time backup codes).
    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',
    'django_filters',
    "rest_framework",
    # L1 gap 9 -- without this app SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] was a silent no-op.
    # Needs `python manage.py migrate` (creates the OutstandingToken / BlacklistedToken tables).
    "rest_framework_simplejwt.token_blacklist",
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
    # T3.4 (D8) — unified Doubts hub: aggregation/dispatch layer over the five
    # doubt systems (message, campus, tuitionclass, post, testseries). Owns no tables.
    'doubts',
    # Help & feedback: support chat, bug reports, feature-request board.
    'support',
    'aiquality',  # P-F6: AI 👍/👎 + report-wrong feedback, prompt-version + cost log
    'social_learning',  # P-F5: study-with-me Pomodoro, accountability partner, leaderboard seasons
    'spaced_repetition',  # P-F8: SM-2 review of the Revision deck + daily due-cards reminder
    'copyrights',  # copyright claims, takedowns, counter-notices, strikes
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
    'assignments',  # T3.10 — package renamed; AppConfig.label stays 'assigments' (DB/migrations unchanged)

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
    'common.admin_ip_allowlist.AdminIPAllowlistMiddleware',  # P-B8 - optional IP allow-list for /<ADMIN_URL> (off while ADMIN_IP_ALLOWLIST is empty)
    'common.api_version.ApiVersionMiddleware',
    'common.feature_gate.FeatureFlagGateMiddleware',  # P-J2 - server side of the remote kill switches (+ optional hard min-version gate)
    # C5 — long-lived Cache-Control on /media/ responses (no-op unless
    # MEDIA_CDN_ENABLED=true; see common/media_cache.py).
    'common.media_cache.MediaCacheControlMiddleware',
    'common.api_cache_headers.ApiCacheSafetyMiddleware',  # P-C8 - private,no-store on API responses with no Cache-Control
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',  # sets request.user.is_verified() (must follow AuthenticationMiddleware)
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'common.csp.ContentSecurityPolicyMiddleware',  # P-B6 - CSP for admin + public HTML pages (common/csp.py)
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
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# P-C1 -- PostgreSQL is COMPULSORY in production. With DEBUG=False and no DATABASE_URL the old code fell back to SQLite
# without a word: a forgotten env var meant production silently ran on a single-writer file DB ("database is locked"
# under real traffic, no backups wired to it, data lost on a container rebuild). Now it refuses to boot.
#   * allowed: DEBUG=True (local dev: SQLite/WAL below), `manage.py test`, and `collectstatic` (an image build step that
#     never touches the DB). Nothing else - there is deliberately no "I know what I'm doing" switch.
#   * DATABASE_URL must be a postgres:// / postgresql:// URL in every mode (sqlite:// was silently mis-parsed before).
# The URL is never put in the error text (it holds the password).
_DB_BOOT_EXEMPT_COMMANDS = ("test", "collectstatic")
if not DATABASE_URL and not DEBUG and not any(_c in _sys.argv for _c in _DB_BOOT_EXEMPT_COMMANDS):
    raise ImproperlyConfigured(
        "DATABASE_URL is not set while DEBUG is False. Production needs PostgreSQL - set "
        "DATABASE_URL=postgres://USER:PASSWORD@HOST:5432/DBNAME (see deploy/env.production.example). "
        "SQLite is for local development only (DEBUG=True)."
    )

if DATABASE_URL:
    from urllib.parse import urlparse

    _db_url = urlparse(DATABASE_URL)
    if _db_url.scheme.split("+")[0] not in ("postgres", "postgresql"):
        raise ImproperlyConfigured(
            "DATABASE_URL must be a PostgreSQL URL (postgres://USER:PASSWORD@HOST:5432/DBNAME); "
            f"got scheme '{_db_url.scheme}'. SQLite is selected by leaving DATABASE_URL unset in local development."
        )
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
            # P-C3: DB_CONN_MAX_AGE (default 60 = same as before). See deploy/PGBOUNCER.md for pgbouncer values.
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60, lo=0),
            "CONN_HEALTH_CHECKS": env_bool("DB_CONN_HEALTH_CHECKS", False),
            # pgbouncer in transaction mode cannot keep server-side cursors (.iterator()) -> set DB_DISABLE_SERVER_SIDE_CURSORS=true.
            "DISABLE_SERVER_SIDE_CURSORS": env_bool("DB_DISABLE_SERVER_SIDE_CURSORS", False),
        }
    }
else:
    # T2.1 - dev SQLite + Daphne (multi-thread): jab ek DEFERRED transaction read se write me upgrade hoti hai to
    # SQLite turant "database is locked" deta hai (busy_timeout ka fayda nahi). `IMMEDIATE` write-lock transaction ke
    # shuru me hi le leta hai, to writers line me lagte hain aur beech me fail nahi hote. Django 5.1+ feature;
    # purane Django me ye key unsupported hai, isliye version-guard.
    import django as _django

    _sqlite_options = {'timeout': 30}
    if _django.VERSION >= (5, 1):
        _sqlite_options['transaction_mode'] = 'IMMEDIATE'
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': _sqlite_options,
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
# P-C8 -- optional read replica (OFF by default; see core/db_routing.py + deploy/CDN_AND_REPLICA.md).
#   DB_REPLICA_ENABLED=true  DB_REPLICA_URL=postgres://USER:PASS@REPLICA_HOST:5432/DBNAME
#   DB_REPLICA_PATH_PREFIXES=/post/feed/,/post/reels/,/profile/search/   (comma list; EMPTY = nothing uses the replica)
DB_REPLICA_ENABLED = env_bool("DB_REPLICA_ENABLED", False)
DB_REPLICA_URL = (os.getenv("DB_REPLICA_URL") or "").strip()
DB_REPLICA_PATH_PREFIXES = tuple(x.strip() for x in (os.getenv("DB_REPLICA_PATH_PREFIXES") or "").split(",") if x.strip())
DB_REPLICA_PIN_SECONDS = env_int("DB_REPLICA_PIN_SECONDS", 5, lo=1, hi=300)
DB_REPLICA_DOWN_SECONDS = env_int("DB_REPLICA_DOWN_SECONDS", 30, lo=1, hi=3600)
DB_REPLICA_EXCLUDE_APPS = tuple(x.strip() for x in (os.getenv(
    "DB_REPLICA_EXCLUDE_APPS", "sessions,auth,admin,contenttypes,authtoken,login,django_otp,otp_totp,otp_static,token_blacklist"
) or "").split(",") if x.strip())
if DB_REPLICA_ENABLED:
    if not DATABASE_URL:
        raise ImproperlyConfigured("DB_REPLICA_ENABLED=true needs DATABASE_URL (the primary) to be set too.")
    from urllib.parse import urlparse as _urlparse_replica

    _rep = _urlparse_replica(DB_REPLICA_URL)
    if _rep.scheme.split("+")[0] not in ("postgres", "postgresql") or not _rep.hostname:
        raise ImproperlyConfigured(
            "DB_REPLICA_ENABLED=true but DB_REPLICA_URL is missing or not a postgres:// URL "
            "(postgres://USER:PASSWORD@REPLICA_HOST:5432/DBNAME). Set it, or set DB_REPLICA_ENABLED=false."
        )
    DATABASES["replica"] = {
        **DATABASES["default"],
        "NAME": _rep.path[1:] or DATABASES["default"]["NAME"],
        "USER": _rep.username or DATABASES["default"]["USER"],
        "PASSWORD": _rep.password if _rep.password is not None else DATABASES["default"]["PASSWORD"],
        "HOST": _rep.hostname,
        "PORT": _rep.port or 5432,
        "TEST": {"MIRROR": "default"},  # tests: the replica alias is the primary
    }
    DATABASE_ROUTERS = ["core.db_routing.ReplicaRouter"]
    MIDDLEWARE.insert(MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
                      "core.db_routing.ReplicaReadMiddleware")

# P-C8 -- default Cache-Control for responses that set none (see common/api_cache_headers.py). false = old behaviour.
API_CACHE_DEFAULT_PRIVATE = env_bool("API_CACHE_DEFAULT_PRIVATE", True)

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
            # T0.2 — SafeRedisChannelLayer = RedisChannelLayer + har payload pe json_safe()
            # (UUID/datetime -> str), warna msgpack "can not serialize 'UUID' object" deta hai.
            "BACKEND": "common.ws_payload.SafeRedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# T0.3 — Notes (message/user_notes.py) close-friends audience lookup. `post.CloseFriend` me
# list ka maalik field `owner` hai (pehle default 'user' tha => FieldError => notes hide).
NOTES_CLOSE_FRIEND_MODEL = "post.CloseFriend"
NOTES_CLOSE_FRIEND_OWNER_FIELD = "owner"
NOTES_CLOSE_FRIEND_MEMBER_FIELD = "friend"

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    # Task 14: moderators/admins (is_staff / is_superuser) need 14+ chars, mixed case, digit,
    # symbol. No effect on normal students. Also re-checked at admin login (common/admin_site.py).
    {'NAME': 'common.password_policy.StaffStrongPasswordValidator'},
]

# Migrations for login / user_profile / leaderboard already record BigAutoField ids; without this setting
# Django resolves those models to AutoField and `makemigrations --check` (CI) reports 17 phantom
# 'Alter field id' migrations (and models.W042 warnings). Matches the existing DB schema -> NO schema change.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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

# T0.3 (B3) — private media. See common/private_media.py.
# Files under these upload prefixes are NEVER served from /media/; their URLs are signed + expiring.
# PRIVATE_MEDIA_PREFIXES = (...)         # override the default list (common/private_media.py)
# P1 — Notes / Resource posts (post/resource_files.py)
POST_RESOURCE_MAX_MB_PDF = int(os.getenv("POST_RESOURCE_MAX_MB_PDF", "25"))
POST_RESOURCE_MAX_MB_DOC = int(os.getenv("POST_RESOURCE_MAX_MB_DOC", "25"))
POST_RESOURCE_MAX_MB_IMAGE = int(os.getenv("POST_RESOURCE_MAX_MB_IMAGE", "15"))
POST_RESOURCE_MAX_MB_VIDEO = int(os.getenv("POST_RESOURCE_MAX_MB_VIDEO", "200"))
# Antivirus (optional): set one of these to scan every upload with clamd. Unreachable scanner => upload refused
# unless POST_RESOURCE_SCAN_FAIL_OPEN is true.
POST_RESOURCE_CLAMAV_HOST = os.getenv("POST_RESOURCE_CLAMAV_HOST", "")
POST_RESOURCE_CLAMAV_PORT = int(os.getenv("POST_RESOURCE_CLAMAV_PORT", "3310"))
POST_RESOURCE_CLAMAV_SOCKET = os.getenv("POST_RESOURCE_CLAMAV_SOCKET", "")
POST_RESOURCE_SCAN_FAIL_OPEN = os.getenv("POST_RESOURCE_SCAN_FAIL_OPEN", "false").lower() == "true"

PRIVATE_MEDIA_URL_TTL = int(os.getenv("PRIVATE_MEDIA_URL_TTL", str(4 * 3600)))      # seconds a signed URL stays valid (+ up to one bucket)
PRIVATE_MEDIA_URL_BUCKET = int(os.getenv("PRIVATE_MEDIA_URL_BUCKET", "3600"))      # URL is stable within a bucket -> client caches still hit
# nginx offload: set to the `internal` location alias, e.g. "/protected-media" (see deploy/PRIVATE_MEDIA.md)
PRIVATE_MEDIA_ACCEL_PREFIX = os.getenv("PRIVATE_MEDIA_ACCEL_PREFIX", "")
os.makedirs(MEDIA_ROOT, exist_ok=True)

# Chunked-upload temp storage — deliberately OUTSIDE MEDIA_ROOT. MEDIA_ROOT
# is served (directly by Django in DEBUG via serve_media_with_range in
# urls.py, and by nginx/S3 in production) — keeping partial chunks out of
# it means a half-uploaded (unvalidated) file can never become reachable
# mid-upload. See tuitionclass/chunked_upload_views.py.
CHUNKED_UPLOAD_TMP_ROOT = BASE_DIR / 'tmp' / 'chunked_uploads'
os.makedirs(CHUNKED_UPLOAD_TMP_ROOT, exist_ok=True)

# H20 round 1: the Flutter app no longer uses the legacy POST /post/chunked/{init,complete,status}/ routes (every post goes
# through the upload queue, /post/uploads/...). Old installed app versions still do, so the routes stay ON by default and
# log a deprecation warning. Set POST_LEGACY_CHUNKED_ENABLED=false once the old versions are force-updated: the three
# routes then answer 410 `legacy_chunked_removed`. (The shared comment/chunked/chunk/ route is NOT affected.)
POST_LEGACY_CHUNKED_ENABLED = os.getenv("POST_LEGACY_CHUNKED_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

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
            "BACKEND": "common.storage_backends.PrivateAwareS3Storage",  # T0.3: signed URLs for private prefixes
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "common.storage_backends.PrivateAwareFileSystemStorage",  # T0.3: signed URLs for private prefixes
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

# V1 — video playback pipeline (post.tasks.process_video_for_streaming).
# Every new video upload is probed in the background and, if a phone might not
# play it (H.265/HEVC, 10-bit/HDR, 4K, non-AAC audio, moov atom at the end,
# .mov/.avi container), converted to MP4 / H.264 / AAC / +faststart. Needs
# `ffmpeg` and `ffprobe` on the Celery worker's PATH (see deploy/systemd/
# learnscroll-celery-worker.service). Dev without a worker: either run with
# CELERY_TASK_ALWAYS_EAGER=true or run `python manage.py process_videos --sync`.
VIDEO_TRANSCODE_ENABLED = os.getenv("VIDEO_TRANSCODE_ENABLED", "True") == "True"
VIDEO_TRANSCODE_MAX_SIDE = int(os.getenv("VIDEO_TRANSCODE_MAX_SIDE", "1280"))  # longer side, px
VIDEO_TRANSCODE_CRF = int(os.getenv("VIDEO_TRANSCODE_CRF", "23"))  # x264 quality, lower = better/larger
VIDEO_TRANSCODE_PRESET = os.getenv("VIDEO_TRANSCODE_PRESET", "veryfast")
VIDEO_TRANSCODE_TIMEOUT_SECONDS = int(os.getenv("VIDEO_TRANSCODE_TIMEOUT_SECONDS", "1800"))
# The converted file gets a NEW name; the original is deleted this long after the
# swap (so a client holding the old URL for a moment doesn't 404), or never if
# VIDEO_TRANSCODE_KEEP_ORIGINAL=True.
VIDEO_TRANSCODE_KEEP_ORIGINAL = os.getenv("VIDEO_TRANSCODE_KEEP_ORIGINAL", "False") == "True"
VIDEO_ORIGINAL_DELETE_DELAY_SECONDS = int(os.getenv("VIDEO_ORIGINAL_DELETE_DELAY_SECONDS", str(6 * 3600)))

# Failed / stuck conversions (post/tasks.py retry_failed_video_processing, beat every 10 min). A `failed` video goes back
# in the queue after attempts x BACKOFF minutes until MAX_ATTEMPTS is used up (then the owner gets ONE notification with
# a Retry action = POST /post/media/<id>/retry-processing/, at most MANUAL_RETRY_LIMIT times per video). A video that
# sits in pending / processing for STUCK_MINUTES (worker died) is queued again.
VIDEO_PROCESSING_MAX_ATTEMPTS = int(os.getenv("VIDEO_PROCESSING_MAX_ATTEMPTS", "3"))
VIDEO_PROCESSING_RETRY_BACKOFF_MINUTES = int(os.getenv("VIDEO_PROCESSING_RETRY_BACKOFF_MINUTES", "10"))
VIDEO_PROCESSING_STUCK_MINUTES = int(os.getenv("VIDEO_PROCESSING_STUCK_MINUTES", "120"))  # minimum 10
VIDEO_PROCESSING_MANUAL_RETRY_LIMIT = int(os.getenv("VIDEO_PROCESSING_MANUAL_RETRY_LIMIT", "5"))

# Seek-bar scrub preview (post/services.py plan_video_sprite): ONE jpeg grid of small frames per video, one frame every
# ceil(duration / MAX_FRAMES) seconds, kept in PostMedia.metadata["sprite"] and served as `sprite` by the API. Built after
# the MP4 is ready (best effort). Old videos: `python manage.py process_videos --sprite --sync`.
VIDEO_SPRITE_ENABLED = os.getenv("VIDEO_SPRITE_ENABLED", "True") == "True"
VIDEO_SPRITE_MAX_FRAMES = int(os.getenv("VIDEO_SPRITE_MAX_FRAMES", "100"))
VIDEO_SPRITE_COLS = int(os.getenv("VIDEO_SPRITE_COLS", "10"))
VIDEO_SPRITE_THUMB_WIDTH = int(os.getenv("VIDEO_SPRITE_THUMB_WIDTH", "160"))  # px per tile
VIDEO_SPRITE_TIMEOUT_SECONDS = int(os.getenv("VIDEO_SPRITE_TIMEOUT_SECONDS", "300"))

# Video lectures (round 4) - automatic Hindi / English captions (post/tasks.py generate_video_captions + poll_video_caption_jobs).
# Needs an S3 bucket for AWS Transcribe's output (and, on local-disk media storage, for a temporary copy of the video).
LECTURE_AUTO_CAPTIONS_ENABLED = os.getenv("LECTURE_AUTO_CAPTIONS_ENABLED", "true").lower() in ("1", "true", "yes")
CAPTIONS_S3_BUCKET = os.getenv("CAPTIONS_S3_BUCKET", "")            # falls back to AWS_STORAGE_BUCKET_NAME when empty
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_S3_REGION_NAME", "ap-south-1"))
LECTURE_AUTO_CAPTION_MAX_SECONDS = int(os.getenv("LECTURE_AUTO_CAPTION_MAX_SECONDS", str(3 * 3600)))
LECTURE_CAPTION_JOB_TIMEOUT_HOURS = int(os.getenv("LECTURE_CAPTION_JOB_TIMEOUT_HOURS", "6"))

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

# T0.4 — Smart reply (chat quick-reply chips) best-effort feature hai.
#   AI_SMART_REPLY_ENABLED          false => endpoint turant 200 {suggestions: []} (Gemini call hi nahi)
#   AI_SMART_REPLY_TIMEOUT          Gemini call ka hard deadline (seconds)
#   AI_SMART_REPLY_BACKOFF_SECONDS  fail/timeout ke baad itni der Gemini skip (offline par har
#                                   naye message pe 4s ka intezaar na ho)
AI_SMART_REPLY_ENABLED = os.getenv("AI_SMART_REPLY_ENABLED", "true").lower() in ("1", "true", "yes", "on")
AI_SMART_REPLY_TIMEOUT = float(os.getenv("AI_SMART_REPLY_TIMEOUT", "4"))
AI_SMART_REPLY_BACKOFF_SECONDS = int(os.getenv("AI_SMART_REPLY_BACKOFF_SECONDS", "30"))

# Composer AI helpers (post/ai_assist.py + post/ai_assist_guard.py): caption, tags, 3-line summary, alt text,
# hi<->en translate, step hints. The cost guard counts in the CACHES backend (Redis in production) - no model, no migration.
#   AI_ASSIST_ENABLED               false => every AI helper answers 503 `ai_disabled` (the free `quality` tips still work)
#   AI_ASSIST_DISABLED_FEATURES     comma list to switch single helpers off, e.g. "alt_text,translate"
#   AI_ASSIST_GLOBAL_DAILY_CALLS    all users together per local day - the real money cap
#   AI_ASSIST_USER_DAILY_TOTAL      one user, all helpers together, per local day
#   AI_ASSIST_DAILY_LIMITS          per-feature per-user per-day overrides; env AI_ASSIST_DAILY_LIMITS_JSON='{"alt_text": 10}'
#   AI_ASSIST_BACKOFF_SECONDS       after a failed / timed-out model call the model is skipped this long (503 `ai_busy`)
#   AI_ASSIST_TIMEOUT               hard deadline of one model call (seconds)
#   AI_ASSIST_CACHE_TTL             same input -> same answer for this long (seconds); a cache hit costs no quota
#   AI_ASSIST_IMAGE_FOR_MINORS      false (default) => photo-to-alt-text AI is off for accounts under 18
#   AI_ASSIST_MIN_IMAGE_SIDE / _DARK_MEAN / _BLUR_VARIANCE   thresholds of the free photo-quality tips (600 px / 45 / 30)
AI_ASSIST_ENABLED = os.getenv("AI_ASSIST_ENABLED", "true").lower() in ("1", "true", "yes", "on")
AI_ASSIST_DISABLED_FEATURES = [f.strip() for f in os.getenv("AI_ASSIST_DISABLED_FEATURES", "").split(",") if f.strip()]
AI_ASSIST_GLOBAL_DAILY_CALLS = int(os.getenv("AI_ASSIST_GLOBAL_DAILY_CALLS", "20000"))
AI_ASSIST_USER_DAILY_TOTAL = int(os.getenv("AI_ASSIST_USER_DAILY_TOTAL", "100"))
try:
    import json as _json_ai_assist
    AI_ASSIST_DAILY_LIMITS = _json_ai_assist.loads(os.getenv("AI_ASSIST_DAILY_LIMITS_JSON", "") or "{}")
    if not isinstance(AI_ASSIST_DAILY_LIMITS, dict):
        AI_ASSIST_DAILY_LIMITS = {}
except ValueError:
    AI_ASSIST_DAILY_LIMITS = {}  # a typo in the env var must not stop the server: the guard's defaults apply
AI_ASSIST_BACKOFF_SECONDS = int(os.getenv("AI_ASSIST_BACKOFF_SECONDS", "20"))
AI_ASSIST_TIMEOUT = float(os.getenv("AI_ASSIST_TIMEOUT", "12"))
AI_ASSIST_CACHE_TTL = int(os.getenv("AI_ASSIST_CACHE_TTL", str(24 * 3600)))
AI_ASSIST_IMAGE_FOR_MINORS = os.getenv("AI_ASSIST_IMAGE_FOR_MINORS", "false").lower() in ("1", "true", "yes", "on")
# P-F6 - AI quality loop (aiquality app): 👍/👎 + "report wrong answer", prompt-version log, cost guard.
#   AI_PROMPT_VERSIONS / env AI_PROMPT_VERSIONS_JSON   {"doubt.answer": "v2"} - bump when a prompt text changes (aiquality/prompts.py)
#   AI_PRICE_INPUT_PER_M_USD / AI_PRICE_OUTPUT_PER_M_USD   USD per 1M tokens, used for the cost ESTIMATE (defaults = Gemini 2.5 Flash list price)
#   AI_DAILY_BUDGET_USD            0 = no budget; > 0 => dashboard bar + one critical log at 80 % / 100 % (aiquality.check_budget)
#   AI_QUALITY_MIN_VOTES / AI_QUALITY_ALERT_DOWN_RATE   a feature+prompt version is flagged when it has >= N votes and >= this 👎 share
#   AI_QUALITY_AUTO_REMOVE_REPORTS  N different students report a cached feed-post AI result wrong => it is deleted (0 = never)
#   AI_CALL_LOG_RETENTION_DAYS / AI_FEEDBACK_RETENTION_DAYS   pruning (reviewed feedback only; unreviewed 👎 is kept)
AI_PRICE_INPUT_PER_M_USD = float(os.getenv("AI_PRICE_INPUT_PER_M_USD", "0.30"))
AI_PRICE_OUTPUT_PER_M_USD = float(os.getenv("AI_PRICE_OUTPUT_PER_M_USD", "2.50"))
AI_DAILY_BUDGET_USD = float(os.getenv("AI_DAILY_BUDGET_USD", "0"))
AI_QUALITY_MIN_VOTES = int(os.getenv("AI_QUALITY_MIN_VOTES", "10"))
AI_QUALITY_ALERT_DOWN_RATE = float(os.getenv("AI_QUALITY_ALERT_DOWN_RATE", "0.30"))
AI_QUALITY_AUTO_REMOVE_REPORTS = int(os.getenv("AI_QUALITY_AUTO_REMOVE_REPORTS", "3"))
AI_CALL_LOG_RETENTION_DAYS = int(os.getenv("AI_CALL_LOG_RETENTION_DAYS", "90"))
AI_FEEDBACK_RETENTION_DAYS = int(os.getenv("AI_FEEDBACK_RETENTION_DAYS", "365"))


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
    "filters": {
        # X6b - ?token= / ?ticket= / JWTs never reach a log line (LearnScroll/log_filters.py)
        "redact_secrets": {"()": "LearnScroll.log_filters.RedactSecretsFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose", "filters": ["redact_secrets"]},
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

# P-G1 -- COIN CASH-OUT KILL SWITCH. Coin -> real-money withdrawal is still an open business / tax decision (GST invoice,
# TDS 194R / 194BA, KYC / PAN, minimum limit), so it ships OFF. Default is FALSE: unset env == OFF == safe.
#   OFF: POST /profile/coin-withdrawals/ and the staff "processing" / "success" actions answer 403 {"code": "feature_disabled"};
#        the app config + wallet summary tell the Flutter app to hide the withdraw UI ("Coming soon").
#   Unchanged while OFF: coin purchase, spending, refunds, withdrawal HISTORY / receipts, and staff "reject"
#        (it only refunds the coins, so requests made before the switch was OFF can still be unwound).
# Turn it ON only after the checklist in deploy/COIN_CASHOUT_REENABLE.md is signed off. Read at call time (common.feature_flags).
COIN_CASHOUT_ENABLED = os.getenv("COIN_CASHOUT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

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

# X6b -- WebSocket auth without the JWT in the URL (login/ws_ticket.py, LearnScroll/ws_auth.py).
#   WS_TICKET_TTL_SECONDS    life of the one-time ticket from POST /login/auth/ws-ticket/ (5..120, default 30)
#   WS_ALLOW_QUERY_TOKEN     P-B2: DEFAULT FALSE = only `?ticket=` is accepted; the JWT never goes into a URL.
#                            true  = the old `?token=<JWT>` works again (only for old app builds that still send it; the
#                            current app NEVER does). It is a temporary env switch for a staged rollout, not a setting to keep:
#                            `manage.py release_check` and `check_security_settings --production` FAIL while it is true.
WS_TICKET_TTL_SECONDS = int(os.getenv("WS_TICKET_TTL_SECONDS", "30"))
WS_ALLOW_QUERY_TOKEN = os.getenv("WS_ALLOW_QUERY_TOKEN", "false").strip().lower() in ("1", "true", "yes", "on")

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
        "channel_verify": "20/hour",  # N8 - e-mail verification code send / check (core/channel_views.py)
        "data_rights_write": "20/hour",  # S5 - export request, deletion cancel, consent change (login/data_rights_views.py)
        "data_rights_download": "20/hour",  # S5 - export ZIP download
        "profile_username_check": "30/min",  # P1 - GET /profile/username/check/ (debounced while typing)
        "profile_achievements": "60/min",  # P3 - GET /profile/profile/<username>/achievements/
        "profile_layout": "60/min",  # P5 - GET /profile/profile/<username>/layout/
        "profile_featured": "60/min",  # P6 - /profile/profile/<username>/featured/, /profile/featured/, /profile/featured/candidates/
        "role_settings": "60/min",  # S12 - teacher defaults, parent child-limit (user_profile/role_settings_views.py)
        "post_poll_vote": "60/min",  # poll / quiz votes
        "post_gif_search": "60/min",  # GET /post/gifs/search/ (story GIF sticker, debounced while typing)
        "post_doubt_answer": "30/min",   # POST /post/<id>/answers/ (text / steps / photos / video)
        "post_doubt_similar": "90/min",  # GET /post/doubts/similar/ (debounced while typing)
        "search_unified": "90/min",  # P-F7 - GET /core/search/unified/ (debounced while typing, ~1 call per 450 ms)
        "search_trending": "30/min",  # P-F7 - GET /core/search/trending/ (opened once per search screen)
        "user": "100/min",
        # T3.8 — install/app_open pings from the app (anonymous allowed; keyed by IP / user)
        "funnel_events": "120/min",
        "anon": "20/min",
        # L1 gaps 10-11 (login/session_views.py): logout is cheap + idempotent; the device
        # list / per-device logout / logout-all are user-driven and never need to be fast.
        "logout": "30/min",
        "session_manage": "30/min",
        "ws_ticket": "60/min",  # X6b - POST /login/auth/ws-ticket/ (one per socket (re)connect)
        # S3 (login/security_views.py): 2FA code check at login (no auth yet -> keyed by IP) + security centre writes.
        "two_factor": "10/min",
        # S10 (login/connected_views.py): connections reads / writes (re-auth, link, change) + QR sign-in.
        "connections": "60/min",
        "connections_write": "20/hour",
        "qr_login": "30/min",
        "qr_poll": "120/min",
        "password_change": "30/hour",  # S3 - POST /login/auth/change-password/ (current password is checked there)
        # X6 (login/sensitive.py SensitiveWriteThrottle - counts WRITES only, a refusal is audited). Every scope used by
        # a throttle MUST be listed here, otherwise DRF raises ImproperlyConfigured (HTTP 500) on the first request.
        "profile_update": "60/hour",  # PATCH /profile/update/ (username / DOB / private account / photo ...)
        "privacy_write": "120/hour",  # PATCH /profile/privacy/ (S4 who-can-message / call / tag ... controls)
        "contact_match": "20/hour",  # POST /profile/contacts/match/ (S4b find friends by phone / e-mail)
        "wallet_settings_write": "30/hour",  # PATCH /profile/wallet/settings/ (spend limits, confirm toggle)
        "withdrawal_request": "10/hour",  # POST /profile/coin-withdrawals/
        "ai_study": "20/min",
        # T3 — `ClassroomCreateGroupView` & co. (tuitionclass/classroom_chat_views.py)
        # always used throttle_scope="classroom_group_create" but the rate was
        # never defined here -> ScopedRateThrottle raised ImproperlyConfigured
        # (HTTP 500) on every call. Defined now.
        "classroom_group_create": "20/min",
        # Help & feedback (support/throttles.py) — writes only.
        "support_ticket": "10/hour",
        "support_message": "40/hour",
        "support_bug": "10/day",
        "support_feature": "10/day",
        "support_vote": "60/min",
        "support_faq": "120/hour",  # S11b FAQ "was this helpful?"
        # Classroom scopes used by tuitionclass/views.py via ScopedRateThrottle —
        # a scope with no rate here raises ImproperlyConfigured on first request.
        "classroom_trial_join": "10/min",
        "session_reaction": "120/min",
        "session_caption": "120/min",
        "chat_read": "120/min",
        # X12 live safety (tuitionclass/live_moderation_views.py) - each scope is used by ScopedRateThrottle there, so a
        # scope missing here would be an ImproperlyConfigured 500 on the first call. New reports are ALSO capped per
        # class / per hour by live_abuse.consume_report_budget; this is only the request-rate floor.
        "live_report": "10/min",         # POST sessions/<id>/moderation/report/
        "live_mod_action": "60/min",     # POST moderation/act/, queue resolve, chat-filter
        "live_notice_ack": "30/min",     # POST sessions/<id>/recording-notice/ack/
        "parent_message_create": "10/min",
        "send_otp": "5/min",
        "verify_otp": "10/min",
        # L1 gap 18 -- GET /login/auth/captcha/ (each call draws an image, so keep it low).
        "captcha": "30/min",
        # P-B3 -- open (no-login) endpoints; classes in common/open_throttles.py. Keyed by user id, else by IP.
        "login": "20/min",              # POST /login/auth/login/ (a per-identifier lockout also exists)
        "signup": "10/min",             # POST signup
        "google_auth": "20/min",        # POST google sign-in / sign-up
        "public_read": "240/min",       # comments / replies / categories / ad config / sound policy
        "freesound_search": "30/min",   # GET /post/music/search/ (logged-in only now)
        "unsubscribe": "30/min",        # e-mail unsubscribe link
        "channel_webhook": "600/min",   # SES / SNS / SMS provider bounces (shared-secret protected)
        "token_refresh": "120/min",     # POST /login/auth/token/refresh/ (JWT refresh; per user id or IP)
        "gateway_webhook": "600/min",   # coin-purchase confirm webhook (HMAC protected)
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
        # LV1 — host presence ping (app sends ~every 20s = 3/min; headroom for retries).
        "session_heartbeat": "12/min",
        # LV1 — home polls GET post/stories/live/ every ~5s (12/min); headroom for resume bursts.
        "story_live": "30/min",
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
        "copyright_termination_appeal": "3/hour",
        "copyright_transparency": "60/hour",
        # P14-BE — user_profile ActivityView (aggregates 7 days + two post lists,
        # so a modest rate) and ActivityHeartbeatView (client beats every
        # ~30-60 s while foregrounded; 12/min leaves headroom for 2 devices).
        "profile_activity": "30/min",
        "profile_activity_heartbeat": "12/min",
        "profile_streak_freeze": "10/min",           # StreakFreezeBuyView
        "profile_daily_goal": "60/min",              # DailyGoalView
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
        "livekit_token_refresh": "30/hour",   # C13
        "study_room_moderation": "60/min",    # S4 host controls
        "study_room_hand": "30/min",          # S4 raise hand
        "study_room_report": "10/hour",       # S4 report user in room
        "study_room_block": "30/hour",        # S8 block / unblock from room
        "call_telemetry": "120/hour",         # X1 client call telemetry (connect / summary)
        "call_ops": "30/min",                 # X1 GET /message/calls/ops/ (Prometheus scrape)
        "study_room_rules": "60/hour",        # S8 rules accept
        "study_room_join": "60/min",          # X2 join (waiting-room 4s loop ~15/min)
        "study_room_state": "180/min",        # X2 whiteboard state GET/PUT autosave
        "study_room_telemetry": "120/hour",   # X1 client study-room telemetry (connect / summary)
        "study_room_activity": "150/hour",    # S5 idle-time heartbeat (~1/min while active)
        "study_room_review": "120/min",       # S4 staff review of study-room reports
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
        # T1.5 — DoubtSimilarThrottle (message/throttles.py) -> "similar doubt"
        # suggestions while a student types a new doubt. A scope with no rate
        # here is a guaranteed ImproperlyConfigured (500) — see the notes above.
        "doubt_similar": "60/min",
        # T1.8 — core.views.AppConfigView (public, per-IP). Generous: campus
        # Wi-Fi/NAT puts many students behind one IP and the app calls this
        # on every launch + resume.
        "app_config": "300/min",
        # IP-level safety net (used alongside the per-user rates above, not
        # instead of them — see MessageSendIPThrottle/CallInitiateIPThrottle
        # in throttles.py for why per-user alone isn't enough).
        "message_send_ip": "120/min",
        "call_initiate_ip": "120/min",        # X2: CGNAT / college WiFi (per-user 10/min alag lagta hai)
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
        "ai_ask_doubt_image": "8/min",               # AskAIDoubtImageThrottle -> AskAIDoubtView photo doubts
        "ai_study_buddy": "20/min",                  # StudyBuddyThrottle -> StudyBuddyView
        "ai_study_buddy_pdf": "5/min",               # StudyBuddyPdfThrottle -> StudyBuddyView (PDF input)
        "post_ai_study": "20/min",                   # PostAiStudyThrottle -> PostAiStudyAPIView (Feature 1)
        "post_ai_assist": "20/min",                  # PostAiAssistThrottle -> PostAiAssistAPIView (composer AI helpers, burst)
        "post_ai_assist_image": "8/min",             # PostAiAssistImageThrottle -> alt_text (picture goes to the model)
        "post_ai_assist_quality": "30/min",          # PostAiAssistQualityThrottle -> quality tips (no AI, but decodes pictures)
        "social_read": "240/min",                     # social_learning SocialReadThrottle (P-F5)
        "social_write": "60/min",                     # social_learning SocialWriteThrottle: timer controls / claim / goal / prefs
        "social_nudge": "20/hour",                    # social_learning SocialNudgeThrottle (+ 2 per day per pair rule)
        "social_partner_request": "20/day",           # social_learning SocialPartnerRequestThrottle
        "revision_read": "120/min",                   # spaced_repetition RevisionReadThrottle: due queue / stats / config (P-F8)
        "revision_review": "240/min",                 # spaced_repetition RevisionReviewThrottle: one POST per graded card (P-F8)
        "ai_feedback": "40/min",                      # aiquality AiFeedbackThrottle -> POST /ai-quality/feedback/ (P-F6)
        "ai_feedback_report": "30/hour",              # aiquality AiFeedbackReportThrottle -> "report wrong answer" only (P-F6)
        # T1.4 (D7) — Doubt Queue write/read limits (message/throttles.py,
        # wired in DoubtQuestionViewSet.get_throttles). Settings is the single
        # source of truth for these rates (the classes deliberately set only
        # `scope`), so ops can retune without a code deploy.
        "doubt_create": "20/hour",                   # DoubtCreateThrottle -> new doubt (text or photo)
        "doubt_create_image": "10/hour",             # DoubtImageUploadThrottle -> doubt WITH photo (on top of doubt_create)
        "doubt_answer": "60/hour",                   # DoubtAnswerThrottle -> post an answer
        "doubt_upvote": "120/min",                   # DoubtUpvoteThrottle -> upvote / un-upvote
        "doubt_action": "60/min",                    # DoubtActionThrottle -> best / verify / reveal / delete-answer
        "doubt_read": "120/min",                     # DoubtReadThrottle -> list / detail / answers list
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
        # COMMUNICATION — NoticeViewSet.emergency (see CampusEmergencyBroadcastThrottle).
        "campus_emergency_broadcast": "6/hour",
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
        # NEW — T4 §F: CampusDoubtViewSet.create/.reply (see
        # campus/throttles.py::CampusDoubtPostThrottle). Landed in the same
        # change as the throttle_classes= wiring in views.py — same bug
        # class as every NOTE above (a scope with no rate = guaranteed
        # ImproperlyConfigured/500 on the first request).
        "campus_doubt_post": "30/hour",
        # NEW — Homework Diary: HomeworkDiaryViewSet.create (see
        # campus/throttles.py::CampusHomeworkDiaryPostThrottle). Same rule as
        # every scope above: the rate MUST land with the throttle wiring.
        "campus_homework_diary_post": "60/hour",
        # NEW — Class resources: ClassResourceViewSet.create / file-replacing edits
        # and ResourceFolderViewSet.create (see campus/throttles.py::
        # CampusResourceUploadThrottle / CampusResourceFolderThrottle).
        "campus_resource_upload": "60/hour",
        "campus_resource_folder": "120/hour",
        "campus_substitute_assign": "120/hour",
        "campus_staff_leave_apply": "20/day",
        # NEW — Admission: AdmissionViewSet writes (see campus/throttles.py::CampusAdmissionWriteThrottle).
        "campus_admission_write": "600/hour",
        # NEW — Results sheet: ResultEntryViewSet.publish/.bulk/.import (see
        # campus/throttles.py::CampusResultsPublishThrottle and
        # CampusResultsImportThrottle). Rates land with the throttle wiring,
        # same rule as every scope above.
        "campus_results_publish": "60/hour",
        "campus_results_import": "120/hour",
        # FINANCE — see campus/throttles.py::CampusFinanceExportThrottle / CampusFinanceReconcileThrottle
        "campus_finance_export": "60/hour",
        # REPORTS / EXPORT — campus.report_views.ReportExportView (attendance / marks / fees; CSV, Excel, PDF)
        "campus_report_export": "60/hour",
        # Safety: backups / admin consent entry / deletion decisions, restore, and user-side requests + consent changes
        "campus_safety_write": "30/hour",
        "campus_safety_restore": "10/hour",
        # Campus Settings (campus/settings_views.py)
        "campus_settings_write": "120/hour",
        "campus_data_request": "60/day",
        "campus_finance_reconcile": "30/hour",
        # NEW — Parents se baat (PTM + teacher<->parent chat): see
        # campus/throttles.py::CampusPTMBookThrottle / CampusPTMManageThrottle /
        # CampusParentChatSendThrottle. Rates land with the throttle wiring.
        "campus_ptm_book": "60/hour",
        "campus_ptm_manage": "120/hour",
        "campus_parent_chat_send": "120/hour",
        # NEW — Recorded classes + notes: campus/throttles.py::CampusRecordingControlThrottle
        # (start / stop / hide a class recording) and CampusNoteWriteThrottle (class
        # notes + private student notes). Rates land with the throttle wiring.
        "campus_recording_control": "60/hour",
        "campus_note_write": "300/hour",
        # T3.4 — Doubts hub (doubts/throttles.py). Reads are cheap list/summary
        # queries; writes are ALSO limited by the source app's own bucket
        # (doubt_create / doubt_answer / campus_doubt_post ...), the tighter wins.
        "doubts_hub_read": "120/min",
        "doubts_hub_write": "30/min",
        # NOTE (fix — CRITICAL, same bug class as the scopes above):
        # assignments/throttling.py::assigmentsPublicPageThrottle sets
        # scope = "assigments_public_page" for the one AllowAny surface in the
        # assigments app (PublicSubmissionView), but this dict never had that
        # key — DRF raises ImproperlyConfigured on the very first request, so
        # the public share URL of a submission was a guaranteed 500.
        "assigments_public_page": "60/min",
        # Public, unauthenticated surfaces of the advanced testseries/assignments
        # features (see testseries/throttling.py, assignments/throttling.py).
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
        # H10: GET /post/feed/ (post.views.HomeFeedView, post.feed_resilience.FeedHomeThrottle). Replaces the global
        # `user` default for this view. A real scroller makes a few requests/min (page 1 + a page every few
        # seconds of scrolling + pull-to-refresh); 60/min per user leaves room for several devices and retries,
        # and stops a script that hammers the (most expensive) endpoint. Env FEED_HOME_THROTTLE e.g. "90/min".
        "feed_home": os.getenv("FEED_HOME_THROTTLE", "60/min"),
        # GET /post/feed/ops/ (post.views.FeedOpsAPIView): Prometheus scrapes it every 15-60 s; per-IP/user cheap reads.
        "feed_ops": "30/min",
        # N10: GET core/notification-ops/ (core.views.NotificationOpsView) - Prometheus scrape + staff dashboards.
        "notification_ops": "30/min",
        # X3: product analytics - app config read, client events (batches of <= 20), staff / scrape-token read.
        "product_analytics_config": "30/min",
        "product_events": "60/min",
        "product_analytics_ops": "30/min",
        # GET /post/counts/ (live like/comment counts poll; H9: adaptive 20s->40s->90s on the client + ETag/304,
        # so an idle client is ~0.7 req/min, an active one up to ~3; 30/min leaves room
        # for resume/refresh bursts + several devices). See post.views.PostCountsAPIView.
        "post_counts": "30/min",
        # POST /post/share-item/ (share a class / test series into the feed).
        "post_create": "20/hour",
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
    # FIX: DRF treats `?format=<x>` as a renderer override and raises Http404 when no renderer is called <x>.
    # Our own endpoints use that query param for something else (webhooks ?format=ses|sendgrid|msg91|meta,
    # ops scrape ?format=prom, campus exports ?format=csv|xlsx|pdf) - so every one of them answered 404 before
    # the view code ran. None = never negotiate on the query string (Accept header only).
    "URL_FORMAT_OVERRIDE": None,
    # L1 -- how many reverse proxies sit in front of Django (load balancer, CDN...).
    # DRF's get_ident() (throttles AND the login/OTP abuse limits in login/) uses it to
    # pick the real client IP out of X-Forwarded-For. Unset = DRF's default behaviour.
    # Set NUM_PROXIES in production, or a client-supplied X-Forwarded-For can dodge per-IP limits.
    "NUM_PROXIES": int(os.getenv("NUM_PROXIES")) if os.getenv("NUM_PROXIES", "").strip() else None,
}

# ---------------------------------------------------------------------------
# T1.12 (R11) — JWT signing key is now SEPARATE from Django's SECRET_KEY.
#
# SIMPLE_JWT used to sign every access/refresh token with SECRET_KEY itself.
# SECRET_KEY also protects sessions, password-reset links, signed cookies and
# copyrights/claimant.py's HMAC — so one leak (or one rotation) of that key
# meant forged logins for every user, and rotating it for any other reason
# logged everybody out. A dedicated key limits the blast radius both ways.
#
#   Generate:  python -c "import secrets; print(secrets.token_urlsafe(64))"
#   Set:       JWT_SIGNING_KEY=<that value>   (in .env / hosting env vars)
#
# Production (DEBUG=False) REFUSES TO BOOT without a distinct, >=32-char key.
# In DEBUG a stable key is derived from SECRET_KEY so local dev needs no setup.
# ⚠️ Switching keys invalidates every already-issued token once — all users are
# asked to log in again on the next app open. That is expected; ship it in a
# normal release window.
# Both JWTAuthentication (REST) and LearnScroll/ws_auth.py (WebSocket) read
# SIMPLE_JWT["SIGNING_KEY"], so this one setting covers both.
JWT_SIGNING_KEY = os.getenv("JWT_SIGNING_KEY", "").strip()
if not JWT_SIGNING_KEY:
    if DEBUG and SECRET_KEY:
        import hashlib as _hashlib
        import hmac as _hmac
        JWT_SIGNING_KEY = _hmac.new(
            SECRET_KEY.encode(), b"learnscroll-jwt-signing-v1", _hashlib.sha256
        ).hexdigest()
    else:
        raise ImproperlyConfigured(
            "JWT_SIGNING_KEY is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(64))\"` "
            "and add it to the environment (must differ from SECRET_KEY)."
        )
elif not DEBUG and (JWT_SIGNING_KEY == SECRET_KEY or len(JWT_SIGNING_KEY) < 32):
    raise ImproperlyConfigured(
        "JWT_SIGNING_KEY must be at least 32 characters and different from SECRET_KEY."
    )

# L1 gap 8 -- the access token is SHORT-lived (was 1 day): a leaked one is useless within
# minutes, and revoked sessions (logout / password change / "sign this device out") stop
# working almost immediately. The app refreshes silently (AuthService.getToken/getValidToken),
# so users never notice. The refresh token ROTATES on every use and the old one is blacklisted
# (gap 9); the 10-day inactivity limit still lives in AuthToken (login/models.py).
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "15"))
# Live sessions (devices) kept per user; a login beyond this ends the least recently used one.
AUTH_MAX_SESSIONS_PER_USER = int(os.getenv("AUTH_MAX_SESSIONS_PER_USER", "10"))

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=JWT_ACCESS_TOKEN_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    # Rolling out: OLD app builds ignore the rotated `refresh` in the response and would be
    # logged out on their 2nd refresh. Until the new app is widely installed, deploy with
    # JWT_ROTATE_REFRESH_TOKENS=0 (and JWT_ACCESS_TOKEN_MINUTES=1440), then flip both back.
    "ROTATE_REFRESH_TOKENS": os.getenv("JWT_ROTATE_REFRESH_TOKENS", "1").strip().lower() not in ("0", "false", "no"),
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
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

# T1.8 — force-update / maintenance defaults for `GET /core/app-config/`
# (core/app_config.py). A `core.AppConfig` row (Django admin) overrides each
# value per platform without a redeploy; these are only the fallbacks.
# Versions are dotted numeric ("1.4.2"). Blank min => nobody is ever forced.
APP_MIN_SUPPORTED_VERSION = os.getenv("APP_MIN_SUPPORTED_VERSION", "")
APP_LATEST_VERSION = os.getenv("APP_LATEST_VERSION", "")
APP_UPDATE_MESSAGE = os.getenv("APP_UPDATE_MESSAGE", "")
APP_UPDATE_MESSAGE_HI = os.getenv("APP_UPDATE_MESSAGE_HI", "")
APP_MAINTENANCE_MODE = os.getenv("APP_MAINTENANCE_MODE", "False") == "True"
APP_MAINTENANCE_MESSAGE = os.getenv("APP_MAINTENANCE_MESSAGE", "")

# T5 - feed video preview length (seconds). The app plays this much of a feed video (muted autoplay), then shows
# "Watch full"; the full player / reels have no cutoff. Clamped to 3..120 in core/app_config.py.
FEED_PREVIEW_SECONDS = int(os.getenv("FEED_PREVIEW_SECONDS", "15") or 15)

# P3-4 - max video length (seconds) the editor allows for a feed post / a story. Defaults = the old app constants (90 / 60).
# Clamped to 5..600 in core/app_config.py. Product owner can change these without an app release.
POST_VIDEO_MAX_SECONDS = int(os.getenv("POST_VIDEO_MAX_SECONDS", "90") or 90)
STORY_VIDEO_MAX_SECONDS = int(os.getenv("STORY_VIDEO_MAX_SECONDS", "60") or 60)
# P3-follow-up - same knob for a reel (was the app constant kReelMaxSeconds = 180). Clamped to 5..600 in core/app_config.py.
REEL_VIDEO_MAX_SECONDS = int(os.getenv("REEL_VIDEO_MAX_SECONDS", "180") or 180)
APP_MAINTENANCE_MESSAGE_HI = os.getenv("APP_MAINTENANCE_MESSAGE_HI", "")

# P-J2 - remote kill switches / staged rollout (common/feature_flags.py, common/feature_gate.py).
# The normal way to flip a feature is Django admin -> Core -> Feature flags (no deploy). FEATURE_FLAGS_OFF is the emergency
# lever that works even when the database is unreachable: FEATURE_FLAGS_OFF=live,ai_features  (restart needed).
# Keys: live, reels_upload, coin_purchase, ai_features, cashout.
FEATURE_FLAGS_OFF = os.getenv("FEATURE_FLAGS_OFF", "")
# Optional hard min-version gate: an API call from a build older than min_supported_version gets 426 UPDATE_REQUIRED (the app then
# shows the blocking "Update now" dialog). Default OFF - turn it on only after the force-update test plan (deploy/FORCE_UPDATE_TEST_PLAN.md).
APP_HARD_MIN_VERSION_GATE = os.getenv("APP_HARD_MIN_VERSION_GATE", "false").strip().lower() in ("1", "true", "yes", "on")

# ONE public web base for EVERY share link (class, test series, profile, parent
# invite, certificate). It must be the domain that serves /.well-known/assetlinks.json
# and the landing pages in common/web_links.py, and must equal the Flutter build's
# --dart-define=WEB_HOST. Set it in the environment for production.
PUBLIC_WEB_BASE_URL = os.getenv("PUBLIC_WEB_BASE_URL", "https://learnscroll.app").rstrip("/")
TUITIONCLASS_WEB_BASE_URL = os.getenv("TUITIONCLASS_WEB_BASE_URL", PUBLIC_WEB_BASE_URL).rstrip("/")
# The app registers learnscroll:// (DeepLinkService.scheme) - the old default
# "tuitionclass" was never handled by the app.
TUITIONCLASS_DEEP_LINK_SCHEME = os.getenv("TUITIONCLASS_DEEP_LINK_SCHEME", "learnscroll")

# ---------------------------------------------------------------------------
# X12 - live class safety (tuitionclass/live_safety.py, live_abuse.py, live_moderation.py, live_push.py).
# Every knob is optional and read with getattr(settings, NAME, default), so deleting a line here just means "default".
# ---------------------------------------------------------------------------
# Word filter for live chat: off | flag | mask | block. A session can override it (ClassSession.chat_filter_mode).
# 🔥 C1 — call lifecycle (message/tasks.py::sweep_stale_calls)
CALL_RING_TIMEOUT_SECONDS = int(os.getenv("CALL_RING_TIMEOUT_SECONDS", "45"))
# Call ki hard cap (started_at + ye) — sweeper isse purani ONGOING call 'max_duration' se end karta hai.
# 🔥 C13 — LiveKit token TTL 2h hai, isliye client `calls/<id>/refresh-token/` se silently naya token
# leta hai; refresh isi cap tak chalta hai, aur cap se LIVEKIT_SESSION_WARN_SECONDS pehle warning aati hai.
CALL_STALE_ONGOING_SECONDS = int(os.getenv("CALL_STALE_ONGOING_SECONDS", str(3 * 3600)))
# C14 leftover: ENDED participant ka WS reconnect is window (default 60s, C3 ki 30s reconnect grace se bada) ke andar hi wapas ONGOING (blip); uske baad REST accept/join.
CALL_WS_REJOIN_WINDOW_SECONDS = int(os.environ.get('CALL_WS_REJOIN_WINDOW_SECONDS', '60'))
# 🔥 C13 — LiveKit token / session knobs (livekit_utils.py getattr se padhta hai).
LIVEKIT_CALL_TOKEN_TTL_SECONDS = int(os.getenv("LIVEKIT_CALL_TOKEN_TTL_SECONDS", str(2 * 3600)))
LIVEKIT_STUDY_ROOM_TOKEN_TTL_SECONDS = int(os.getenv("LIVEKIT_STUDY_ROOM_TOKEN_TTL_SECONDS", str(8 * 3600)))
# Ek user ke ek continuous study-room session (join se) ki hard cap; uske baad refresh 410.
LIVEKIT_STUDY_ROOM_MAX_SECONDS = int(os.getenv("LIVEKIT_STUDY_ROOM_MAX_SECONDS", str(12 * 3600)))
# Hard cap se itne seconds pehle client "session khatam hone wala hai" warning dikhaye.
LIVEKIT_SESSION_WARN_SECONDS = int(os.getenv("LIVEKIT_SESSION_WARN_SECONDS", "300"))

# S3 — study room whiteboard (`StudyRoomStateView`) guards.
STUDY_ROOM_STATE_MAX_BYTES = int(os.getenv("STUDY_ROOM_STATE_MAX_BYTES", str(2 * 1024 * 1024)))   # PUT payload cap (413)
STUDY_ROOM_STATE_MAX_PAGES = int(os.getenv("STUDY_ROOM_STATE_MAX_PAGES", "200"))
# True => board saved ho chuka ho to PUT me `base_version` / `If-Match` zaroori (warna 428).
STUDY_ROOM_STATE_REQUIRE_VERSION = os.getenv("STUDY_ROOM_STATE_REQUIRE_VERSION", "1") not in ("0", "false", "False")
STUDY_ROOM_SNAPSHOT_MIN_INTERVAL_SECONDS = int(os.getenv("STUDY_ROOM_SNAPSHOT_MIN_INTERVAL_SECONDS", "300"))
STUDY_ROOM_SNAPSHOT_KEEP = int(os.getenv("STUDY_ROOM_SNAPSHOT_KEEP", "20"))
STUDY_ROOM_UNDO_WINDOW_SECONDS = int(os.getenv("STUDY_ROOM_UNDO_WINDOW_SECONDS", str(24 * 3600)))   # clear ke baad undo
# S4 — host controls (study_room_roles.py). Sab optional.
STUDY_ROOM_DEFAULT_MAX_PARTICIPANTS = int(os.getenv("STUDY_ROOM_DEFAULT_MAX_PARTICIPANTS", "100"))   # LiveKit room hard cap + default join cap
STUDY_ROOM_MAX_PARTICIPANTS_LIMIT = int(os.getenv("STUDY_ROOM_MAX_PARTICIPANTS_LIMIT", "500"))        # host jitna max set kar sakta hai
STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM = os.getenv("STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM", "1") not in ("0", "false", "False")   # public group ke naye session me waiting room default
# S5 — presence + attendance minutes (message/study_room_presence.py). LiveKit webhook (/message/livekit/webhook/ ya purana
# tuitionclass URL) configured hona zaroori hai; warna STUDY_ROOM_PRESENCE_ENABLED=0 rakho (purana "join = attendance").
STUDY_ROOM_PRESENCE_ENABLED = os.getenv("STUDY_ROOM_PRESENCE_ENABLED", "1") not in ("0", "false", "False")
STUDY_ROOM_STREAK_MIN_MINUTES = int(os.getenv("STUDY_ROOM_STREAK_MIN_MINUTES", "10"))   # din streak me tabhi jab itne minute room me; 0 = join kaafi
STUDY_ROOM_PRESENCE_STALE_SECONDS = int(os.getenv("STUDY_ROOM_PRESENCE_STALE_SECONDS", str(2 * 3600)))   # itna purana open interval sweeper verify kare
STUDY_ROOM_PRESENCE_STALE_CREDIT_SECONDS = int(os.getenv("STUDY_ROOM_PRESENCE_STALE_CREDIT_SECONDS", "3600"))   # leave webhook kho gaya to max credit
STUDY_ROOM_PRESENCE_SWEEP_BATCH = int(os.getenv("STUDY_ROOM_PRESENCE_SWEEP_BATCH", "200"))
# S5 leftover — idle time. ON => client heartbeat (`study-room/<id>/activity/`) bhejne wale rooms me sirf ACTIVE time minutes me
# jaata hai (idle baithna nahi). Heartbeat na bhejne wala purana client poora connected time pata rehta hai.
STUDY_ROOM_IDLE_FILTER_ENABLED = os.getenv("STUDY_ROOM_IDLE_FILTER_ENABLED", "1") not in ("0", "false", "False")
STUDY_ROOM_ACTIVITY_PING_SECONDS = int(os.getenv("STUDY_ROOM_ACTIVITY_PING_SECONDS", "60"))   # client ping interval (pehle ping ka credit)
STUDY_ROOM_ACTIVITY_GAP_SECONDS = int(os.getenv("STUDY_ROOM_ACTIVITY_GAP_SECONDS", "150"))   # 2 pings ke beech itna gap tak active; zyada => idle
# S8 — discovery -> room safety (message/study_room_safety.py, post/minor_privacy.py). Sab optional.
STUDY_ROOM_RULES_REQUIRED_PUBLIC = os.getenv("STUDY_ROOM_RULES_REQUIRED_PUBLIC", "1") not in ("0", "false", "False")   # public group study room: pehle rules accept (group admin/mod exempt)
STUDY_ROOM_REPORT_AUTO_REMOVE_THRESHOLD = int(os.getenv("STUDY_ROOM_REPORT_AUTO_REMOVE_THRESHOLD", "0"))   # itne ALAG reporters (same session) => user room se auto-remove; 0 = off
STUDY_ROOM_REPORT_AUTO_REMOVE_WINDOW_MINUTES = int(os.getenv("STUDY_ROOM_REPORT_AUTO_REMOVE_WINDOW_MINUTES", "60"))
MINOR_PUBLIC_ROOM_MASK_IDENTITY = os.getenv("MINOR_PUBLIC_ROOM_MASK_IDENTITY", "1") not in ("0", "false", "False")   # public room me minor ka naam/photo adult strangers se chhupao
MINOR_PUBLIC_GROUP_JOIN_APPROVAL = os.getenv("MINOR_PUBLIC_GROUP_JOIN_APPROVAL", "1") not in ("0", "false", "False")   # minor public group me admin approval se hi aaye
MINOR_PUBLIC_ROOM_CAMERA_OFF = os.getenv("MINOR_PUBLIC_ROOM_CAMERA_OFF", "1") not in ("0", "false", "False")   # public room me minor ka camera default OFF (client hint)
# S5 — app-wide study-time leaderboard (scope_type=study_time, scope_id khali). Default OFF: bachon ka padhai-time sabko
# dikhane se pehle product/privacy decision. Per-room board (scope_id = conversation) hamesha sirf us room ke members ko.
STUDY_TIME_GLOBAL_BOARD_ENABLED = os.getenv("STUDY_TIME_GLOBAL_BOARD_ENABLED", "0") in ("1", "true", "True")
CALL_SWEEP_BATCH = int(os.getenv("CALL_SWEEP_BATCH", "200"))
# 🔥 C7 — busy rule. False (default): callee dusri call me ho to caller ko 409 `callee_busy`
# ("Line busy"), ring nahi. True: callee ki chalti call ke upar ring jaati hai +
# push/WS me `call_waiting` flag (client ka call-waiting banner). Ek call ki ring
# ke dauran doosri ring (ringing elsewhere) har haal me busy maani jaati hai.
CALL_WAITING_ENABLED = os.getenv("CALL_WAITING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

# 🔥 C5 — killed-state incoming calls
# Ring push (FCM `ttl` + APNs `apns-expiration`) itne second baad expire. Phone
# offline tha to purani ring baad me achanak "incoming call" na dikhaye.
# Client ke 30s ring timeout ke barabar rakho.
CALL_PUSH_TTL_SECONDS = int(os.getenv("CALL_PUSH_TTL_SECONDS", "30"))

# 🔥 C11 — group call scale (fan-out)
# Group call me pehle itne members ko hi RING hota hai (online wale pehle). Baaki members ko
# ring/push nahi jaata — chat me "Join" banner dikhta hai (GET /message/calls/history/active/
# + POST /message/calls/<id>/action/ {"action": "join"}). 1:1 par koi asar nahi.
CALL_GROUP_RING_CAP = max(int(os.getenv("CALL_GROUP_RING_CAP", "10")), 1)
# Ek group call (LiveKit room) me max kitne log. `join` isi par 409 `call_full` deta hai aur
# LiveKit room `max_participants` bhi yahi hota hai (livekit_utils.ensure_call_room).
CALL_GROUP_MAX_PARTICIPANTS = max(int(os.getenv("CALL_GROUP_MAX_PARTICIPANTS", "50")), 2)
# Group call ki push + bell-notification Celery me jaati hai (request me nahi). Ek task itne
# recipients sambhalta hai (bade group me kai chhote parallel tasks).
CALL_PUSH_CHUNK = max(int(os.getenv("CALL_PUSH_CHUNK", "100")), 1)
# False => group ring push purane tareeke se request ke andar (debug ke liye). Default True.
CALL_GROUP_PUSH_ASYNC = os.getenv("CALL_GROUP_PUSH_ASYNC", "true").strip().lower() in ("1", "true", "yes", "on")
# LiveKit CreateRoom (max_participants set karne wala) call ka max wait (sec). Fail => call phir bhi chalti hai.
CALL_ROOM_CREATE_TIMEOUT_SECONDS = float(os.getenv("CALL_ROOM_CREATE_TIMEOUT_SECONDS", "3"))

# 🔥 C12 — missed-call push + chat bubble (message/missed_calls.py). Koi migration nahi.
# Missed-call data-only push (type='missed_call'). False => sirf bell row + chat bubble.
CALL_MISSED_PUSH_ENABLED = os.getenv("CALL_MISSED_PUSH_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
# Kabhi connect na hui call ka chat me "Missed call" bubble (ek call = ek system message).
CALL_MISSED_BUBBLE_ENABLED = os.getenv("CALL_MISSED_BUBBLE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
# Missed-call push kitni der tak valid (phone offline tha to online aane par bhi dikhe). Default 24h.
CALL_MISSED_PUSH_TTL_SECONDS = max(int(os.getenv("CALL_MISSED_PUSH_TTL_SECONDS", str(24 * 3600))), 60)
# False => missed-call kaam sweeper/request ke andar sync (debug / broker ke bina). Default True (Celery).
CALL_MISSED_NOTIFY_ASYNC = os.getenv("CALL_MISSED_NOTIFY_ASYNC", "true").strip().lower() in ("1", "true", "yes", "on")
# iOS PushKit/VoIP push (message/voip_push.py). Sab set na hon to iOS bhi purane
# FCM-only path par chalta hai (killed state me unreliable). Details:
# STATUS_C5_KILLED_STATE_CALLS.md
APNS_VOIP_KEY_PATH = os.getenv("APNS_VOIP_KEY_PATH", "")   # AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID = os.getenv("APNS_KEY_ID", "")
APNS_TEAM_ID = os.getenv("APNS_TEAM_ID", "")
APNS_BUNDLE_ID = os.getenv("APNS_BUNDLE_ID", "")           # e.g. com.learnscroll.app
# Xcode debug build = sandbox, TestFlight/App Store = production.
APNS_USE_SANDBOX = os.getenv("APNS_USE_SANDBOX", "true" if DEBUG else "false").strip().lower() in ("1", "true", "yes", "on")

LIVE_CHAT_FILTER_DEFAULT = os.getenv("LIVE_CHAT_FILTER_DEFAULT", "mask")
# Flood guard: more than MAX lines inside WINDOW seconds -> 429 chat_flood (host tier exempt).
LIVE_CHAT_FLOOD_WINDOW_SECONDS = int(os.getenv("LIVE_CHAT_FLOOD_WINDOW_SECONDS", "10"))
LIVE_CHAT_FLOOD_MAX = int(os.getenv("LIVE_CHAT_FLOOD_MAX", "6"))
# The same line again inside this many seconds is a double tap (rejected, not a strike).
LIVE_DUP_GRACE_SECONDS = int(os.getenv("LIVE_DUP_GRACE_SECONDS", "3"))
# Strikes (blocked line / flood / repeated line): LIMIT inside WINDOW -> automatic chat mute; every further auto mute
# of the same person in the same class doubles the time (x2, x4, x8), capped by MAX.
LIVE_STRIKE_WINDOW_SECONDS = int(os.getenv("LIVE_STRIKE_WINDOW_SECONDS", "600"))
LIVE_STRIKE_LIMIT = int(os.getenv("LIVE_STRIKE_LIMIT", "3"))
LIVE_AUTO_MUTE_MINUTES = int(os.getenv("LIVE_AUTO_MUTE_MINUTES", "5"))
LIVE_AUTO_MUTE_MAX_MINUTES = int(os.getenv("LIVE_AUTO_MUTE_MAX_MINUTES", "60"))
# NEW reports one person may file (per class / per hour). child-safety reports are never limited.
LIVE_REPORT_MAX_PER_SESSION = int(os.getenv("LIVE_REPORT_MAX_PER_SESSION", "5"))
LIVE_REPORT_MAX_PER_HOUR = int(os.getenv("LIVE_REPORT_MAX_PER_HOUR", "20"))
# Minors: allow | with_notice | never (on stage). with_notice = only after the recording notice was acknowledged.
LIVE_MINOR_STAGE_POLICY = os.getenv("LIVE_MINOR_STAGE_POLICY", "with_notice")
# False = a class that ever had a minor in it can only be saved class-only (no public / followers clip).
LIVE_PUBLIC_RECORDING_WITH_MINORS = os.getenv("LIVE_PUBLIC_RECORDING_WITH_MINORS", "0") == "1"
LIVE_MINOR_CACHE_SECONDS = int(os.getenv("LIVE_MINOR_CACHE_SECONDS", "60"))
# "recording is ready" push: how many attendees at most (same cap as the live-now push).
LIVE_NOTIFY_MAX_AUDIENCE = int(os.getenv("LIVE_NOTIFY_MAX_AUDIENCE", "5000"))
# X12 L-D #3: optional separate Redis DB for the live-chat hot keys (slow mode, flood / strike counters, mute lookup).
# Unset = they share CACHES["default"] (Redis already when REDIS_URL is set). See tuitionclass/live_cache.py.
if os.getenv("LIVE_REDIS_URL"):
    CACHES["live"] = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("LIVE_REDIS_URL"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "live",
    }
# X12 L-D #4: how long (seconds) a "is this person chat-muted?" answer is cached per (class, person). The answer is also
# written / deleted the moment a moderator mutes / unmutes, so on a shared Redis this is only the safety net. 0 = no
# cache (one DB query per chat message, the old behaviour).
LIVE_MUTE_CACHE_SECONDS = int(os.getenv("LIVE_MUTE_CACHE_SECONDS", "20"))
# X12 L-D #1: listener mode as the platform default. False (default) = old behaviour: every student token can publish.
# True = a class starts STAGE-ONLY when it goes LIVE: plain students can listen, chat, raise a hand - they publish only
# once the host puts them on stage. The host can still switch it off per class (More sheet -> Stage only).
LIVE_STAGE_ONLY_DEFAULT = os.getenv("LIVE_STAGE_ONLY_DEFAULT", "0") == "1"
# False = the older tuitionclass pushes (waitlist, refund, reminders ...) ignore quiet hours / Do Not Disturb again.
TUITIONCLASS_PUSH_RESPECT_QUIET_HOURS = os.getenv("TUITIONCLASS_PUSH_RESPECT_QUIET_HOURS", "1") == "1"
if not globals().get("DEBUG", True) and any(h in PUBLIC_WEB_BASE_URL for h in ("example.com", "localhost", "127.0.0.1")):
    import warnings as _w
    _w.warn(
        f"PUBLIC_WEB_BASE_URL={PUBLIC_WEB_BASE_URL!r} is a placeholder: share links and "
        "referral commission links will not open the app. Set PUBLIC_WEB_BASE_URL.",
        stacklevel=1,
    )

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

# P-B1 -- API docs lock. /api/schema/, /swagger/, /redoc/ (and the duplicate /tuitionclass/schema/ + /tuitionclass/schema/docs/)
# list every endpoint, field and permission of the API: a free map for an attacker and an expensive request to render.
# drf-spectacular's own default for SERVE_PERMISSIONS is AllowAny, so before this block all five URLs were public.
#   API_DOCS_PUBLIC=false (DEFAULT)  only logged-in STAFF (is_staff) can open them. Two ways in: a browser session
#                                    (log in at /admin/, then open /swagger/) or a staff JWT in the Authorization header.
#                                    Anonymous -> 401, non-staff -> 403.
#   API_DOCS_PUBLIC=true             open to everyone. Local / throw-away staging only: `manage.py release_check` and
#                                    `manage.py check_security_settings --production` both FAIL while it is true.
# Read here (not via _env_bool, which is defined further down this file). The routes themselves stay mounted
# (LearnScroll/urls.py) - the lock is the permission class, so there is one rule for all five URLs.
API_DOCS_PUBLIC = os.getenv("API_DOCS_PUBLIC", "false").strip().lower() in ("1", "true", "yes", "on")

SPECTACULAR_SETTINGS = {
    'TITLE': 'LearnScroll API',
    'DESCRIPTION': 'LearnScroll Production API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    # P-B1: who may open the schema / Swagger UI / Redoc pages (see the block above).
    'SERVE_PERMISSIONS': (
        ['rest_framework.permissions.AllowAny'] if API_DOCS_PUBLIC
        else ['rest_framework.permissions.IsAdminUser']
    ),
    # JWT first so an anonymous caller gets 401 + WWW-Authenticate (not a bare 403); SessionAuthentication second so a
    # staff member who logged in at /admin/ can use Swagger UI in the browser (a browser cannot send a Bearer header).
    # Both only authenticate; IsAdminUser above decides. Session auth enforces CSRF on unsafe methods only - these views are GET.
    'SERVE_AUTHENTICATION': [
        'login.authentication.SlidingSessionAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
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
# T1 - web + android + ios ke ID tokens ka `aud` alag hota hai: GOOGLE_CLIENT_IDS="web.apps...,android.apps...,ios.apps..."
# (comma list). GOOGLE_CLIENT_ID (purana, single) fallback ke taur par chalta rahta hai - login/google_verify.py dono merge karta hai.
GOOGLE_CLIENT_IDS = [x.strip() for x in os.environ.get("GOOGLE_CLIENT_IDS", "").split(",") if x.strip()]
# Google ke certs fetch / verify ke har HTTP call ka timeout (seconds) aur certs cache ki umar. Dev PC pe network atke
# to worker latakne ki jagah 5 s me 503 `google_unreachable` milta hai.
GOOGLE_VERIFY_TIMEOUT = float(os.environ.get("GOOGLE_VERIFY_TIMEOUT", "5"))
GOOGLE_CERTS_CACHE_SECONDS = int(os.environ.get("GOOGLE_CERTS_CACHE_SECONDS", "3600"))
FREESOUND_API_KEY = os.environ.get('FREESOUND_API_KEY')
# Story studio F3 - GIF sticker. Giphy API key for GET /post/gifs/search/ (post/views.py GifSearchAPIView); stays on the
# server. Unset = the endpoint answers 503 "not configured" and the app hides nothing else.
GIPHY_API_KEY = os.environ.get('GIPHY_API_KEY')
# Hosts a stored GIF url may point at (exact host or any subdomain) - post/story_gifs.py. Default: Giphy only.
# STORY_GIF_ALLOWED_HOSTS = ("giphy.com",)
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

# T2.7 (Loop 7) — COST-FREE referral rewards (no coins leave the platform):
#   REFERRAL_REWARD_FREEZE_REFERRER / _REFEREE -- streak-freeze tokens handed
#       to each side when a referral is redeemed (capped by
#       STREAK_FREEZE_MAX_TOKENS, so a full holder simply gets 0).
#   REFERRER_BADGE_FIRST_AT / REFERRER_BADGE_COMMUNITY_AT -- how many
#       redeemed referrals earn the "referrer_first" / "referrer_5" badges.
#   FOUNDING_MEMBER_UNTIL (ISO date, "" = no date limit) and
#   FOUNDING_MEMBER_MAX_AWARDS -- the founding window: while it is open, a
#       referee who redeems a code, a referrer who reaches
#       FOUNDING_MEMBER_REFERRALS_NEEDED referrals, or a student who joins a
#       campus via an invite code gets the "founding_member" badge.
REFERRAL_REWARD_FREEZE_REFERRER = int(os.environ.get("REFERRAL_REWARD_FREEZE_REFERRER", 1))
REFERRAL_REWARD_FREEZE_REFEREE = int(os.environ.get("REFERRAL_REWARD_FREEZE_REFEREE", 1))
REFERRER_BADGE_FIRST_AT = int(os.environ.get("REFERRER_BADGE_FIRST_AT", 1))
REFERRER_BADGE_COMMUNITY_AT = int(os.environ.get("REFERRER_BADGE_COMMUNITY_AT", 5))
FOUNDING_MEMBER_UNTIL = os.environ.get("FOUNDING_MEMBER_UNTIL", "")
FOUNDING_MEMBER_MAX_AWARDS = int(os.environ.get("FOUNDING_MEMBER_MAX_AWARDS", 1000))
# T2.8 — students needed for the last step of the campus pilot checklist
# (campus/pilot_kit.py).
CAMPUS_PILOT_TARGET_STUDENTS = int(os.environ.get("CAMPUS_PILOT_TARGET_STUDENTS", 10))

# SAFETY (campus/backup.py, campus/data_deletion.py, campus/consent.py) -- all optional, defaults shown.
#   CAMPUS_BACKUP_RETENTION_DAYS    a backup is deleted after this many days (0 = keep until the count limit)
#   CAMPUS_BACKUP_MAX_PER_CAMPUS    newest N backups kept per campus (0 = no count limit)
#   CAMPUS_BACKUP_MAX_ROWS          refuse a backup bigger than this many rows
#   CAMPUS_BACKUP_MAX_UNPACKED_MB   restore refuses a file that unpacks to more than this (zip-bomb guard)
#   CAMPUS_BACKUP_SCHEDULE_DAYS     nightly job backs a campus up when its last scheduled backup is older than this
#   CAMPUS_DELETION_SLA_DAYS        promised answer time for a data-deletion request (shown as its due date)
#   CAMPUS_CONSENT_POLICY_VERSION   bump when the privacy text changes -> every earlier "granted" shows "needs update"
CAMPUS_BACKUP_RETENTION_DAYS = int(os.environ.get("CAMPUS_BACKUP_RETENTION_DAYS", 90))
# Soft-deleted campus records (trash) stay restorable this many days; `manage.py purge_soft_deleted --execute`
# erases the older ones for real. See campus/soft_delete.py.
CAMPUS_SOFT_DELETE_RETENTION_DAYS = int(os.environ.get("CAMPUS_SOFT_DELETE_RETENTION_DAYS", 90))
CAMPUS_BACKUP_MAX_PER_CAMPUS = int(os.environ.get("CAMPUS_BACKUP_MAX_PER_CAMPUS", 20))
CAMPUS_BACKUP_MAX_ROWS = int(os.environ.get("CAMPUS_BACKUP_MAX_ROWS", 300000))
CAMPUS_BACKUP_MAX_UNPACKED_MB = int(os.environ.get("CAMPUS_BACKUP_MAX_UNPACKED_MB", 400))
CAMPUS_BACKUP_SCHEDULE_DAYS = int(os.environ.get("CAMPUS_BACKUP_SCHEDULE_DAYS", 7))
CAMPUS_DELETION_SLA_DAYS = int(os.environ.get("CAMPUS_DELETION_SLA_DAYS", 30))
CAMPUS_CONSENT_POLICY_VERSION = os.environ.get("CAMPUS_CONSENT_POLICY_VERSION", "2026-10")
FOUNDING_MEMBER_REFERRALS_NEEDED = int(os.environ.get("FOUNDING_MEMBER_REFERRALS_NEEDED", 3))

# TASK G10 (growth_and_feature_tasks.md — certificates as a share loop):
# general app web base URL, used to build a `?ref=<code>` signup link that
# rides along on a certificate share card (testseries/certificate_share_card.py,
# views_advanced.py::certificate_share_card). Same `getattr(settings, ...,
# default)`-with-sane-fallback shape `Classroom.referral_urls()` already uses
# for `TUITIONCLASS_WEB_BASE_URL` — this is the app-wide equivalent, not scoped
# to one classroom, since a certificate's referral link should land a new
# signup on the general signup screen, not inside someone else's classroom.
APP_WEB_BASE_URL = os.environ.get("APP_WEB_BASE_URL", PUBLIC_WEB_BASE_URL)

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
# Homework Diary: how many days before `due_date` the "homework due" push goes
# out (campus.tasks.send_homework_diary_due_reminders). 1 = the evening before.
CAMPUS_HOMEWORK_DIARY_REMINDER_DAYS_BEFORE = int(os.environ.get("CAMPUS_HOMEWORK_DIARY_REMINDER_DAYS_BEFORE", 1))
# Class resources (content share) — all optional, defaults shown.
#   * file size limits live in campus/resource_rules.py (CAMPUS_RESOURCE_MAX_MB 25 /
#     CAMPUS_RESOURCE_VIDEO_MAX_MB 200 / CAMPUS_RESOURCE_THUMB_MAX_MB 2);
#   * push anti-spam: once the same teacher has pushed this many resources into
#     ONE folder within the window, further uploads are still visible in the
#     app but no longer buzz the students/parents (campus/resource_push.py);
#   * "copy to other sections": max target sections per request.
CAMPUS_RESOURCE_PUSH_BURST_LIMIT = int(os.environ.get("CAMPUS_RESOURCE_PUSH_BURST_LIMIT", 3))
CAMPUS_RESOURCE_PUSH_BURST_MINUTES = int(os.environ.get("CAMPUS_RESOURCE_PUSH_BURST_MINUTES", 10))
CAMPUS_RESOURCE_COPY_MAX_SECTIONS = int(os.environ.get("CAMPUS_RESOURCE_COPY_MAX_SECTIONS", 10))

# Live class (one-tap go-live + automatic attendance) — all optional, defaults
# shown; the rules live in campus/live_class.py.
#   * Go live is allowed from EARLY minutes before the period starts until
#     LATE_GRACE minutes after it ends; an ended class can be re-opened for REOPEN minutes.
#   * A stay with no heartbeat for PRESENCE_TTL seconds counts as closed at its last
#     sign of life; a LIVE session whose TEACHER heartbeat is silent for STALE minutes
#     is auto-ended by the beat sweeper; MAX_HOURS is a hard cap on any live class.
#   * A session shorter than ATTENDANCE_MIN_SESSION minutes marks nobody absent.
#   * Per-campus switches (auto on/off, min % stayed, late-after minutes) are fields
#     on `campus.Campus`, editable by admin / principal.
CAMPUS_GO_LIVE_EARLY_MINUTES = int(os.environ.get("CAMPUS_GO_LIVE_EARLY_MINUTES", 15))
CAMPUS_GO_LIVE_LATE_GRACE_MINUTES = int(os.environ.get("CAMPUS_GO_LIVE_LATE_GRACE_MINUTES", 15))
CAMPUS_LIVE_REOPEN_MINUTES = int(os.environ.get("CAMPUS_LIVE_REOPEN_MINUTES", 60))
CAMPUS_LIVE_PRESENCE_TTL_SECONDS = int(os.environ.get("CAMPUS_LIVE_PRESENCE_TTL_SECONDS", 90))
CAMPUS_LIVE_STALE_MINUTES = int(os.environ.get("CAMPUS_LIVE_STALE_MINUTES", 5))
CAMPUS_LIVE_MAX_HOURS = int(os.environ.get("CAMPUS_LIVE_MAX_HOURS", 6))
CAMPUS_LIVE_ATTENDANCE_MIN_SESSION_MINUTES = int(os.environ.get("CAMPUS_LIVE_ATTENDANCE_MIN_SESSION_MINUTES", 5))

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
# T2.9: also read by core.tasks.send_streak_at_risk_reminders (it does nothing
# before this server-local hour); the beat entry fires every 30 min from here.
STREAK_REMINDER_HOUR = int(os.environ.get("STREAK_REMINDER_HOUR", 22))
# P-F3_F4 habit loop (core/habit_loop.py). SMART TIMING: the streak warning goes out HABIT_LEAD_HOURS before the hour
# the user usually finishes studying (median of their last 14 days), clamped to [HABIT_EARLIEST_HOUR, HABIT_LATEST_HOUR];
# users with < 3 days of history keep STREAK_REMINDER_HOUR. HABIT_SMART_TIMING=0 -> everyone gets STREAK_REMINDER_HOUR.
# Revision reminder: user-local HABIT_REVISION_HOUR (+ 3 h window), at most one per HABIT_REVISION_EVERY_DAYS, only for
# decks with >= HABIT_REVISION_MIN_ITEMS items saved >= 2 days ago. HABIT_WEEKLY_EMAIL=0 turns the weekly report e-mail off.
HABIT_LOOP = {
    "smart_timing": os.environ.get("HABIT_SMART_TIMING", "1") not in ("0", "false", "False", ""),
    "lead_hours": int(os.environ.get("HABIT_LEAD_HOURS", 1)),
    "earliest_hour": int(os.environ.get("HABIT_EARLIEST_HOUR", 18)),
    "latest_hour": int(os.environ.get("HABIT_LATEST_HOUR", 22)),
    "revision_hour": int(os.environ.get("HABIT_REVISION_HOUR", 18)),
    "revision_every_days": int(os.environ.get("HABIT_REVISION_EVERY_DAYS", 3)),
    "revision_min_items": int(os.environ.get("HABIT_REVISION_MIN_ITEMS", 3)),
    "weekly_email": os.environ.get("HABIT_WEEKLY_EMAIL", "1") not in ("0", "false", "False", ""),
}
STREAK_MILESTONE_DAYS = tuple(
    int(d) for d in os.environ.get("STREAK_MILESTONE_DAYS", "7,30,100").split(",") if d.strip()
)
STREAK_MILESTONE_BONUS_COINS = {
    7: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_7", 20)),
    30: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_30", 100)),
    100: int(os.environ.get("STREAK_MILESTONE_BONUS_COINS_100", 500)),
}

# Streak freeze + daily goal (see user_profile.models.StreakManager).
# STREAK_FREEZE_COST_COINS   -- ek freeze token ki coin keemat.
# STREAK_FREEZE_MAX_TOKENS   -- ek saath max kitne tokens rakh sakte hain
#                               (ye max missed din bhi hai jo ek baar me bach sakte hain).
# STREAK_DAILY_GOAL_OPTIONS  -- allowed daily-goal minutes; STREAK_GOAL_FREEZE_EVERY --
#                               har itne goals pe ek free freeze token (0 = band).
STREAK_FREEZE_COST_COINS = int(os.environ.get("STREAK_FREEZE_COST_COINS", 50))
STREAK_FREEZE_MAX_TOKENS = int(os.environ.get("STREAK_FREEZE_MAX_TOKENS", 2))
STREAK_DAILY_GOAL_OPTIONS = tuple(
    int(m) for m in os.environ.get("STREAK_DAILY_GOAL_OPTIONS", "5,10,15,20,30,45,60").split(",") if m.strip()
)
STREAK_GOAL_FREEZE_EVERY = int(os.environ.get("STREAK_GOAL_FREEZE_EVERY", 7))

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

# T0.4 (B4) — coin top-up pricing, owned by the SERVER (user_profile/payments.py).
# Packs the app may buy. For the Rs 1 live test add `1` temporarily (COIN_PURCHASE_PACKS=1,100,250,...)
# and remove it afterwards. Rate = INR charged per coin (keep in sync with the app's kCoinToInrRate,
# or better: show the `amount` the /buy-coin/order/ response returns).
COIN_PURCHASE_PACKS = os.getenv("COIN_PURCHASE_PACKS", "100,250,500,1000,2500")
COIN_PURCHASE_INR_PER_COIN = os.getenv("COIN_PURCHASE_INR_PER_COIN", "1")
RAZORPAY_API_BASE = os.getenv("RAZORPAY_API_BASE", "https://api.razorpay.com/v1")

# FEE-5 — campus fees paid online (UPI / card) through the same Razorpay account (campus/fee_gateway.py).
# Needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET; webhook: https://<host>/campus/fee-payments/webhook/razorpay/
# (events payment.captured, order.paid, refund.processed; same RAZORPAY_WEBHOOK_SECRET).
CAMPUS_FEE_ONLINE_ENABLED = os.getenv("CAMPUS_FEE_ONLINE_ENABLED", "1").lower() not in ("0", "false", "no")
CAMPUS_FEE_ONLINE_METHODS = os.getenv("CAMPUS_FEE_ONLINE_METHODS", "upi,card")
CAMPUS_FEE_ONLINE_MAX_PENDING = int(os.getenv("CAMPUS_FEE_ONLINE_MAX_PENDING", "5"))
CAMPUS_FEE_ONLINE_MIN_AMOUNT = os.getenv("CAMPUS_FEE_ONLINE_MIN_AMOUNT", "1")

# P-G2 — reconcile job (common/payment_reconcile.py, Celery task user_profile.reconcile_payments, every 15 min):
# asks Razorpay about PENDING online rows (coin top-ups + campus fees) whose webhook never arrived.
PAYMENT_RECONCILE_ENABLED = os.getenv("PAYMENT_RECONCILE_ENABLED", "1").lower() not in ("0", "false", "no")
PAYMENT_RECONCILE_MIN_AGE_MINUTES = int(os.getenv("PAYMENT_RECONCILE_MIN_AGE_MINUTES", "10"))  # never race the webhook
PAYMENT_RECONCILE_MAX_AGE_DAYS = int(os.getenv("PAYMENT_RECONCILE_MAX_AGE_DAYS", "7"))        # older: reported, not touched
PAYMENT_RECONCILE_EXPIRE_HOURS = int(os.getenv("PAYMENT_RECONCILE_EXPIRE_HOURS", "168"))      # no attempt at all -> FAILED
PAYMENT_RECONCILE_BATCH = int(os.getenv("PAYMENT_RECONCILE_BATCH", "200"))

# P-G2 — emailed coin top-up receipt (PDF attached). Transactional; needs EMAIL_* below. 0 = off.
COIN_RECEIPT_EMAIL_ENABLED = os.getenv("COIN_RECEIPT_EMAIL_ENABLED", "1").lower() not in ("0", "false", "no")


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
# P-C3: every task gets a time limit (a hung task must not hold a worker slot forever). Heavy tasks set their own (post/tasks.py,
# login/tasks.py, tuitionclass save_recording_clip). soft raises SoftTimeLimitExceeded inside the task, hard kills the child.
CELERY_TASK_SOFT_TIME_LIMIT = env_int("CELERY_TASK_SOFT_TIME_LIMIT", 600, lo=30)
CELERY_TASK_TIME_LIMIT = max(env_int("CELERY_TASK_TIME_LIMIT", 660, lo=60), CELERY_TASK_SOFT_TIME_LIMIT + 30)
# acks_late + prefetch 4 lets one worker hoard 4 messages while it runs a slow one -> prefetch 1.
CELERY_WORKER_PREFETCH_MULTIPLIER = env_int("CELERY_WORKER_PREFETCH_MULTIPLIER", 1, lo=1)
# failed-task alert (LearnScroll/celery_ops.py) and the single-beat guard (LearnScroll/beat_guard.py)
CELERY_FAIL_ALERT_THRESHOLD = env_int("CELERY_FAIL_ALERT_THRESHOLD", 5, lo=1)
CELERY_FAIL_ALERT_WINDOW_SECONDS = env_int("CELERY_FAIL_ALERT_WINDOW_SECONDS", 900, lo=60)
BEAT_SINGLE_INSTANCE_GUARD = env_bool("BEAT_SINGLE_INSTANCE_GUARD", True)
BEAT_LOCK_TTL = env_int("BEAT_LOCK_TTL", 60, lo=10)

# Cross-app side effects (notifications, chat-group sync — see
# core/async_utils.py + core/tasks.py) are enqueued from inside web requests,
# so an unreachable Redis must fail FAST (then core.async_utils falls back to
# running the task inline) instead of hanging the request on kombu's default
# multi-second connection retries.
CELERY_BROKER_CONNECTION_TIMEOUT = 2
# visibility_timeout: Redis re-delivers an un-acked message after this long. With acks_late it MUST be longer than the longest
# task (save_recording_clip = 5 h), else a still-running task is delivered a second time. 6 h.
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 2, "visibility_timeout": env_int("CELERY_VISIBILITY_TIMEOUT", 6 * 3600, lo=3600)}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Under `manage.py test` / pytest there is no worker: run tasks inline so
# tests never try to reach Redis. Also switchable via env for local dev
# without a worker (CELERY_TASK_ALWAYS_EAGER=true).
import sys as _sys  # noqa: E402

TESTING = "test" in _sys.argv or "pytest" in _sys.modules
CELERY_TASK_ALWAYS_EAGER = TESTING or os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = False

# ---------------------------------------------------------------------------
# Task 14 -- admin / moderator security (common/admin_site.py, common/password_policy.py)
#   ADMIN_REQUIRE_2FA            TOTP code needed for /admin/ (default: on whenever DEBUG=False).
#                                Enrol each moderator: python manage.py setup_staff_2fa <username>
#   STAFF_PASSWORD_MIN_LENGTH    minimum staff password length (default 14)
#   ADMIN_LOGIN_MAX_FAILURES     failed logins before a username is locked (default 5)
#   ADMIN_LOGIN_LOCKOUT_SECONDS  lock duration / failure window (default 900 = 15 min)
# ---------------------------------------------------------------------------
ADMIN_REQUIRE_2FA = os.getenv("ADMIN_REQUIRE_2FA", "").strip().lower() in ("1", "true", "yes") if os.getenv("ADMIN_REQUIRE_2FA") else (not DEBUG and not TESTING)
STAFF_PASSWORD_MIN_LENGTH = int(os.getenv("STAFF_PASSWORD_MIN_LENGTH", "14"))
ADMIN_LOGIN_MAX_FAILURES = int(os.getenv("ADMIN_LOGIN_MAX_FAILURES", "5"))
ADMIN_LOGIN_LOCKOUT_SECONDS = int(os.getenv("ADMIN_LOGIN_LOCKOUT_SECONDS", "900"))

# P-B6 -- admin hardening.
#   ADMIN_URL        where the Django admin lives (default "admin/"). Set a private path in production, e.g.
#                    ADMIN_URL=ops-9f3k2/ ; slashes at the ends are normalised. `manage.py release_check` warns while it is "admin/".
#   CSP_PUBLIC_MODE  Content-Security-Policy on public HTML pages (SEO / share links / legal): enforce | report-only | off
#   CSP_ADMIN_MODE   same for the admin; default report-only, switch to enforce after one look at the browser console on staging
#   CSP_REPORT_URI   optional endpoint that collects violation reports
ADMIN_URL = (os.getenv("ADMIN_URL", "admin/").strip().strip("/") or "admin") + "/"
CSP_PUBLIC_MODE = os.getenv("CSP_PUBLIC_MODE", "enforce").strip().lower()
CSP_ADMIN_MODE = os.getenv("CSP_ADMIN_MODE", "report-only").strip().lower()
CSP_REPORT_URI = os.getenv("CSP_REPORT_URI", "").strip()
OTP_TOTP_ISSUER = "LearnScroll"  # name shown in the authenticator app

# P-B8 -- admin IP allow-list (common/admin_ip_allowlist.py).
#   ADMIN_IP_ALLOWLIST  comma / space separated IPs or CIDRs allowed to reach /<ADMIN_URL>. EMPTY = off (default).
#                       Set but all entries invalid = everybody refused (fails closed). Blocked requests get a plain 404.
#   ADMIN_NUM_PROXIES   trusted reverse proxies in front of Django (nginx = 1). Unset -> falls back to NUM_PROXIES; both
#                       unset -> only REMOTE_ADDR is used and X-Forwarded-For is ignored. Also used by the admin login lockout.
ADMIN_IP_ALLOWLIST = os.getenv("ADMIN_IP_ALLOWLIST", "").strip()
ADMIN_NUM_PROXIES = int(os.getenv("ADMIN_NUM_PROXIES")) if os.getenv("ADMIN_NUM_PROXIES", "").strip().isdigit() else None

from celery.schedules import crontab  # noqa: E402

# ---------------------------------------------------------------------------
# N6 - reminder throttling (core/notification_reminders.py reads both at call time).
#   NOTIF_REMINDER_DAILY_CAP        max NON-urgent reminder pushes per user per local day (streak-at-risk, weekly recap,
#                                   study digest, PTM reminder). 0 = cap off. Urgent ones (class starts, test /
#                                   assignment / homework due) are never capped. The bell row is always written.
#   NOTIF_REMINDER_COLLAPSE_MINUTES the same reminder TYPE is not pushed twice inside this window. 0 = off.
# ---------------------------------------------------------------------------
NOTIF_REMINDER_DAILY_CAP = int(os.environ.get("NOTIF_REMINDER_DAILY_CAP", 3))
NOTIF_REMINDER_COLLAPSE_MINUTES = int(os.environ.get("NOTIF_REMINDER_COLLAPSE_MINUTES", 240))

# ---------------------------------------------------------------------------
# N7 - rich notifications (core/notification_rich.py reads this at call time).
#   NOTIF_PUSH_MARK_READ_BUTTON  "Mark as read" button on reply / informational pushes. Default ON; set to 0 / false / off
#                                to stop adding it (Accept / Decline, Follow back, reminders, locked types never get it).
# ---------------------------------------------------------------------------
NOTIF_PUSH_MARK_READ_BUTTON = os.environ.get("NOTIF_PUSH_MARK_READ_BUTTON", "1").strip().lower() not in ("0", "false", "no", "off")


# --- N8: e-mail / SMS / WhatsApp channels (core/channel_policy.py) ---------------------------------------------------
#   Per-event e-mail needs a VERIFIED address; SMS / WhatsApp need User.phone_verified + a consent row for the number.
#   NOTIF_EMAIL_DAILY_CAP / NOTIF_SMS_DAILY_CAP / NOTIF_WHATSAPP_DAILY_CAP   per user, rolling 24 h (locked / safety types skip it)
#   NOTIF_SMS_GLOBAL_DAILY_CAP / NOTIF_WHATSAPP_GLOBAL_DAILY_CAP             platform cost guard per rolling 24 h (0 = no limit)
#   NOTIF_EMAIL_SOFT_BOUNCE_LIMIT        soft bounces within 7 days before the address is suppressed
#   NOTIF_CONSENT_TEXT_VERSION           bump when the wording of the consent text in the app changes
#   NOTIF_EMAIL_CODE_TTL_SECONDS / _RESEND_SECONDS / _MAX_ATTEMPTS / _MAX_SENDS_PER_DAY   verification code limits
#   NOTIF_WEBHOOK_SECRET                 header X-Webhook-Secret for core/channel-webhooks/<provider>/ ("" = endpoint OFF)
#   NOTIF_SMS_APPEND_STOP / NOTIF_SMS_MAX_SEGMENTS                           SMS wording (set APPEND_STOP=0 if your DLT text has it)
#   MSG91_WHATSAPP_TEMPLATE_NAME_HI / _EN  per-language approved WhatsApp templates (falls back to MSG91_WHATSAPP_TEMPLATE_NAME)
#   SITE_URL (already above) is REQUIRED for the unsubscribe link in e-mails.
# N9 - notification centre extras:
#   NOTIFICATION_HISTORY_DAYS          how long a normal notification stays in the bell (default 30). Older rows are deleted
#                                      by the daily task core.prune_notification_history (read, unread and archived alike).
#   NOTIFICATION_LOCKED_HISTORY_DAYS   same for locked / safety types (emergency, copyright, withdrawals ...) - default 90,
#                                      never shorter than the normal window.
#   (the older `manage.py cleanup_notifications` command keeps its own read 30d / unread 90d rule - it is a manual tool.)
NOTIFICATION_HISTORY_DAYS = int(os.environ.get("NOTIFICATION_HISTORY_DAYS", 30))
NOTIFICATION_LOCKED_HISTORY_DAYS = int(os.environ.get("NOTIFICATION_LOCKED_HISTORY_DAYS", 90))


def _env_bool(name, default):
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


NOTIF_EMAIL_DAILY_CAP = int(os.environ.get("NOTIF_EMAIL_DAILY_CAP", 10))
NOTIF_SMS_DAILY_CAP = int(os.environ.get("NOTIF_SMS_DAILY_CAP", 3))
NOTIF_WHATSAPP_DAILY_CAP = int(os.environ.get("NOTIF_WHATSAPP_DAILY_CAP", 3))
NOTIF_SMS_GLOBAL_DAILY_CAP = int(os.environ.get("NOTIF_SMS_GLOBAL_DAILY_CAP", 0))
NOTIF_WHATSAPP_GLOBAL_DAILY_CAP = int(os.environ.get("NOTIF_WHATSAPP_GLOBAL_DAILY_CAP", 0))
NOTIF_EMAIL_SOFT_BOUNCE_LIMIT = int(os.environ.get("NOTIF_EMAIL_SOFT_BOUNCE_LIMIT", 3))
NOTIF_CONSENT_TEXT_VERSION = os.environ.get("NOTIF_CONSENT_TEXT_VERSION", "2026-10")
NOTIF_EMAIL_CODE_TTL_SECONDS = int(os.environ.get("NOTIF_EMAIL_CODE_TTL_SECONDS", 600))
NOTIF_EMAIL_CODE_RESEND_SECONDS = int(os.environ.get("NOTIF_EMAIL_CODE_RESEND_SECONDS", 60))
NOTIF_EMAIL_CODE_MAX_ATTEMPTS = int(os.environ.get("NOTIF_EMAIL_CODE_MAX_ATTEMPTS", 5))
NOTIF_EMAIL_CODE_MAX_SENDS_PER_DAY = int(os.environ.get("NOTIF_EMAIL_CODE_MAX_SENDS_PER_DAY", 5))
NOTIF_WEBHOOK_SECRET = os.environ.get("NOTIF_WEBHOOK_SECRET", "")
NOTIF_SMS_APPEND_STOP = _env_bool("NOTIF_SMS_APPEND_STOP", "1")
NOTIF_SMS_MAX_SEGMENTS = int(os.environ.get("NOTIF_SMS_MAX_SEGMENTS", 2))
MSG91_WHATSAPP_TEMPLATE_NAME_HI = os.environ.get("MSG91_WHATSAPP_TEMPLATE_NAME_HI", "")
MSG91_WHATSAPP_TEMPLATE_NAME_EN = os.environ.get("MSG91_WHATSAPP_TEMPLATE_NAME_EN", "")

CELERY_BEAT_SCHEDULE = {
    # L1 gaps 9/11 -- drop dead AuthToken rows + expired blacklist rows (login/tasks.py).
    "login-cleanup-stale-sessions": {
        "task": "login.cleanup_stale_sessions",
        "schedule": crontab(hour=3, minute=30),
    },
    # L1 gap 19 -- drop auth audit rows older than AUTH_AUDIT_RETENTION_DAYS (login/tasks.py).
    "login-purge-auth-audit-logs": {
        "task": "login.purge_auth_audit_logs",
        "schedule": crontab(hour=4, minute=15),
    },
    # S5 -- DPDP data rights (login/tasks.py): expire old export ZIPs / build late ones, and erase accounts whose
    # 30-day deletion grace has ended (+ one "3 days left" e-mail).
    "login-sweep-data-exports": {
        "task": "login.sweep_data_exports",
        "schedule": crontab(minute="*/30"),
    },
    "login-run-due-account-deletions": {
        "task": "login.run_due_account_deletions",
        "schedule": crontab(hour=2, minute=10),
    },
    # S10 -- stale QR sign-in requests (login/connected_accounts.purge_old_qr_requests).
    "login-purge-old-qr-requests": {
        "task": "login.purge_old_qr_requests",
        "schedule": crontab(minute=20),
    },
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
    # LV1 — a LIVE session whose host app stopped heartbeating for
    # LIVE_STALE_COMPLETE_MINUTES gets completed (the story-tray ring itself
    # already drops after LIVE_HEARTBEAT_TTL_SECONDS without needing this).
    "tuitionclass-sweep-stale-live-sessions": {
        "task": "tuitionclass.sweep_stale_live_sessions",
        "schedule": crontab(minute="*"),
    },
    # X12 L-C - reminders for scheduled standalone lives ("starts in 15 minutes").
    "tuitionclass-send-standalone-live-reminders": {
        "task": "tuitionclass.send_standalone_live_reminders",
        "schedule": crontab(minute="*"),
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
    # T3 — classroom chat group == class participants. Daily full sweep
    # (adds missing / removes extra members, recreates a missing group) ...
    "core-reconcile-classroom-groups-daily": {
        "task": "core.reconcile_classroom_groups",
        "schedule": crontab(hour=3, minute=30),
    },
    # ... and a cheap frequent sweep limited to classrooms where a pass
    # expired in the last hour, so an expired student leaves the group within
    # minutes instead of waiting for the daily run.
    "core-reconcile-classroom-groups-expired": {
        "task": "core.reconcile_classroom_groups",
        "schedule": crontab(minute="*/15"),
        "kwargs": {"expired_within_minutes": 60},
    },
    # [T4 §D] campus section chat group == section roster (daily sweep; the
    # per-change sync is dispatched from campus.roster after each roster edit).
    "core-reconcile-section-groups-daily": {
        "task": "core.reconcile_section_groups",
        "schedule": crontab(hour=3, minute=45),
    },
    # Sweeps abandoned chunked uploads (client crashed/closed mid-upload)
    # and reclaims their temp disk usage — see
    # tuitionclass/tasks.py:cleanup_stale_chunked_uploads for exactly what it
    # checks. Hourly is enough since the staleness window itself is 6h.
    "tuitionclass-cleanup-stale-chunked-uploads": {
        "task": "tuitionclass.cleanup_stale_chunked_uploads",
        "schedule": crontab(minute=0),
    },
    # X12 (auto recording): "Save as lecture / reel / post" jobs (tuitionclass/recording_save.py). Re-queues a clip whose
    # Celery message was lost and fails one stuck pending / processing for hours, so the save screen never spins for ever.
    "tuitionclass-sweep-recording-saves": {
        "task": "tuitionclass.sweep_recording_saves",
        "schedule": crontab(minute="*/10"),
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
    # 🔥 C1 — stuck/ghost calls sweeper (ring timeout server-side). 30s
    # cadence: client ring timeout 30s hai, server 45s pe safety-net.
    "message-sweep-stale-calls": {
        "task": "message.sweep_stale_calls",
        "schedule": 30.0,
    },
    # X1 — call SLO fast-burn check (message/call_ops.py): connect-success % < CALL_SLO over the last 15 min =>
    # one ERROR log + Sentry message. Cheap (a few cache reads).
    "message-call-slo-watch": {
        "task": "message.call_slo_watch",
        "schedule": crontab(minute="*/5"),
    },
    # S5 — study room: leave webhook kho gaya ho to khule presence intervals band (minutes attendance me).
    "message-sweep-stale-study-presence": {
        "task": "message.sweep_stale_study_presence",
        "schedule": crontab(minute="*/10"),
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
    # N3 — FCM device-token hygiene (stale 60d / lagataar fail / orphan session / per-user 10+). Roz 04:30, DB-only, idempotent.
    "message-cleanup-stale-device-tokens": {
        "task": "message.cleanup_stale_device_tokens",
        "schedule": crontab(hour=4, minute=30),
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
    # P9 - profile visit rows older than PROFILE_VISIT_RETENTION_DAYS (user_profile/profile_insights.py), daily.
    "user-profile-prune-profile-visits": {
        "task": "user_profile.tasks.prune_old_profile_visits",
        "schedule": crontab(hour=3, minute=40),
    },
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
    #
    # T2.9 — NOW every 30 minutes from STREAK_REMINDER_HOUR to 23:30 (not one
    # shot at the hour): `core.tasks.send_streak_at_risk_reminders` writes the
    # bell row once per user per day and retries the PUSH each run while the
    # user is in quiet hours / DND, so a user whose quiet window ends at 22:30
    # still gets it — and nobody is pushed during their quiet hours, with push
    # off, or with the type muted. The task is idempotent per user per day.
    # H18 - study digest: hourly; each user is handled once per local day at their own hour (content_digest_hour in
    # their timezone), and pushes held back by quiet hours / DND are retried every run. Tune / disable with
    # settings.CONTENT_DIGEST (see core/content_digest.py DEFAULTS).
    "core-send-content-digests": {
        "task": "core.send_content_digests",
        "schedule": crontab(minute=7),
    },
    # S8 - the student's own study reminder (Settings > Learning): every 15 min, each user is handled once per local
    # day inside a 3 h window after the time they chose; quiet-hours pushes are retried every run (core/study_reminders.py).
    "core-send-study-reminders": {
        "task": "core.send_study_reminders",
        "schedule": crontab(minute="*/15"),
    },
    # N5 - low-priority roll-up (likes / reactions / "new post from ..." -> one summary push per user per day).
    # N8 - drop channel send-log rows older than 35 days (the caps only look back 24 h).
    "core-prune-channel-send-log": {
        "task": "core.prune_channel_send_log",
        "schedule": crontab(hour=3, minute=15),
    },
    # N10 - push SLO watch (delivery success %, unexplained skips) every 10 minutes.
    "core-notification-slo-watch": {
        "task": "core.notification_slo_watch",
        "schedule": crontab(minute="*/10"),
    },
    # N9 - notification history 30 days (+ expired class / chat / post mutes).
    "core-prune-notification-history": {
        "task": "core.prune_notification_history",
        "schedule": crontab(hour=3, minute=20),
    },
    "core-send-notification-rollups": {
        "task": "core.send_notification_rollups",
        "schedule": crontab(minute=37),
    },
    "user-profile-send-streak-risk-reminders": {
        "task": "core.send_streak_at_risk_reminders",
        "schedule": crontab(
            # P-F3_F4: smart timing can move a user's warning before STREAK_REMINDER_HOUR (never before HABIT_LOOP earliest_hour)
            hour=f"{min(STREAK_REMINDER_HOUR, HABIT_LOOP['earliest_hour']) if HABIT_LOOP['smart_timing'] else STREAK_REMINDER_HOUR}-23",
            minute="*/30",
        ),
    },
    # P-F6 - AI quality loop: prune old call logs daily; cost-guard budget alert every 30 min (no-op while AI_DAILY_BUDGET_USD=0).
    # P-F5 - social learning (social_learning/tasks.py)
    "social-season-tick": {"task": "social_learning.season_tick", "schedule": crontab(minute="*/15")},
    "social-pomodoro-sweep": {"task": "social_learning.pomodoro_sweep", "schedule": crontab(minute="*/10")},
    "social-partner-daily": {"task": "social_learning.partner_daily", "schedule": crontab(hour=0, minute=20)},
    "social-partner-evening": {"task": "social_learning.partner_evening", "schedule": crontab(hour=19, minute=30)},
    "aiquality-prune-logs": {"task": "aiquality.prune_logs", "schedule": crontab(hour=3, minute=40)},
    "aiquality-check-budget": {"task": "aiquality.check_budget", "schedule": crontab(minute="*/30")},
    # P-F3_F4 - revision-deck reminder (core/habit_loop.py): every 15 min, each eligible user at most once per
    # HABIT_REVISION_EVERY_DAYS days inside a 3 h window after their local HABIT_REVISION_HOUR.
    "core-send-revision-reminders": {
        "task": "core.send_revision_reminders",
        "schedule": crontab(minute="*/15"),
    },
    # TASK G2 (growth_and_feature_tasks.md — recap screen) —
    # user_profile.tasks.generate_weekly_recaps. Sunday only (day_of_week=0
    # in crontab's Sun=0 convention), at RECAP_GENERATION_HOUR (default
    # 23:00) — deliberately once a week, not more often: the task is
    # idempotent per (user, week_start) via WeeklyRecap's unique constraint
    # (update_or_create in recap.py), so a re-run within the same week just
    # recomputes and re-notifies, which is harmless but pointless to do on
    # a tighter schedule.
    # H15 — post.creator_feedback.send_creator_feedback: weekly "N read / M saved / K helpful" to authors of
    # Notes / Resource posts. Monday morning; idempotent (skips an author who got one in the last 6 days).
    "post-creator-feedback-weekly": {
        "task": "post.creator_feedback.send_creator_feedback",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),
    },

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
    # FINANCE — accrue late fines first so the 08:00 reminder below quotes the right balance.
    "campus-accrue-late-fines": {
        "task": "campus.tasks.accrue_late_fines",
        "schedule": crontab(hour=7, minute=30),
    },
    "campus-send-fee-due-reminders": {
        "task": "campus.tasks.send_fee_due_reminders",
        "schedule": crontab(hour=8, minute=0),
    },
    # P-F14 — weekly parent report (attendance + marks trend + fees): Sunday 18:00 (CELERY_TIMEZONE). One send per
    # parent per ISO week, so a re-run is harmless. Kill switch: CAMPUS_PARENT_WEEKLY_ENABLED=false.
    "campus-send-parent-weekly-summaries": {
        "task": "campus.tasks.send_parent_weekly_summaries",
        "schedule": crontab(hour=int(os.environ.get("CAMPUS_PARENT_WEEKLY_HOUR", 18)), minute=0, day_of_week=0),
    },
    # SAFETY — nightly campus backups (a campus is only backed up when it has none newer than
    # CAMPUS_BACKUP_SCHEDULE_DAYS) and a daily nudge for data-deletion requests past their due date.
    "campus-run-scheduled-backups": {
        "task": "campus.tasks.run_scheduled_campus_backups",
        "schedule": crontab(hour=2, minute=30),
    },
    # ADMISSION — one push per campus whose admins have follow-ups due.
    "campus-admission-followup-digest": {
        "task": "campus.tasks.send_admission_followup_digest",
        "schedule": crontab(hour=9, minute=30),
    },
    "campus-warn-overdue-deletion-requests": {
        "task": "campus.tasks.warn_overdue_deletion_requests",
        "schedule": crontab(hour=9, minute=0),
    },
    # COMMUNICATION — publish scheduled notices whose time has come (claims each one atomically, so
    # overlapping runs / a manual "send now" can never push a notice twice).
    "campus-publish-due-notices": {
        "task": "campus.tasks.publish_due_notices",
        "schedule": crontab(minute="*"),
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
    # PUSH — platform-wide assignment-due sweep (personal + tuitionclass
    # assignments; campus-sourced ones are excluded inside the sweep because
    # campus-send-assigments-due-reminders above owns them). This function +
    # management command existed but nothing ever scheduled them, so no
    # assignment reminder was ever sent. Idempotent per (submission, due_date)
    # via a cache marker, so an extra run is harmless. Staggered after the
    # two campus jobs above.
    "assignments-send-due-reminders": {
        "task": "assignments.send_due_reminders",
        "schedule": crontab(hour=8, minute=45),
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
    # T4 §G — "online class starts in 5 minutes" push. Every minute (the
    # task claims each session/occurrence atomically — `reminder_sent_at`
    # / `TimetableReminderLog` — so a re-run or overlapping worker can
    # never double-send). Lead time: CAMPUS_CLASS_REMINDER_LEAD_MINUTES
    # (default 5). Timestamps are resolved in TIME_ZONE (= Celery's
    # CELERY_TIMEZONE), same convention TimeSlot rows are authored in.
    "campus-send-class-start-reminders": {
        "task": "campus.tasks.send_class_start_reminders",
        "schedule": crontab(minute="*"),
    },
    "campus-prune-timetable-reminder-logs": {
        "task": "campus.tasks.prune_timetable_reminder_logs",
        "schedule": crontab(hour=3, minute=15),
    },
    # Live class (one-tap go-live + auto attendance): every minute — auto-end a
    # LIVE session whose teacher app went silent (CAMPUS_LIVE_STALE_MINUTES, 5),
    # force-end anything older than CAMPUS_LIVE_MAX_HOURS (6) and rebuild
    # attendance that never completed. Idempotent (see campus/live_class.py).
    "campus-sweep-live-sessions": {
        "task": "campus.tasks.sweep_live_sessions",
        "schedule": crontab(minute="*"),
    },
    # Homework Diary — "homework due tomorrow" push to the section's students
    # and parents. Once a day, early evening (before dinner / study time).
    # Each entry is claimed atomically (`due_reminder_sent_at`), so a re-run
    # or an overlapping worker never double-sends. Days ahead:
    # CAMPUS_HOMEWORK_DIARY_REMINDER_DAYS_BEFORE (default 1) — read at task
    # call time; the crontab hour itself needs a beat restart to change.
    "campus-send-homework-diary-due-reminders": {
        "task": "campus.tasks.send_homework_diary_due_reminders",
        "schedule": crontab(hour=17, minute=0),
    },
    # Parents se baat — PTM "tomorrow / in an hour" reminders. Every 5 minutes;
    # each reminder is claimed atomically (`PTMBooking.reminder_*_sent_at`), so
    # an overlapping worker or a re-run never double-sends.
    "campus-send-ptm-reminders": {
        "task": "campus.tasks.send_ptm_reminders",
        "schedule": crontab(minute="*/5"),
    },
    # Parents se baat — one "parent messages waiting" push per teacher when the
    # campus chat window opens. Every 10 minutes; the task only acts on the tick
    # in which a campus's chat_start_time falls (campus.tasks.
    # PARENT_CHAT_DIGEST_TICK_MINUTES must match this 10).
    "campus-send-parent-chat-morning-digest": {
        "task": "campus.tasks.send_parent_chat_morning_digest",
        "schedule": crontab(minute="*/10"),
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
    # N6 - "test closing soon" push for IN_PROGRESS attempts: 24h and 1h before deadline_at (bands + dedupe in the task).
    "testseries-send-test-deadline-reminders": {
        "task": "testseries.send_test_deadline_reminders",
        "schedule": crontab(minute="*/10"),
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
    # Scheduled posts go live (post.tasks.publish_scheduled_posts) - every minute.
    "post-publish-scheduled": {
        "task": "post.tasks.publish_scheduled_posts",
        "schedule": 60.0,
    },
    # Doubt post upgrade: unsolved doubt bounties go back to the asker after DOUBT_BOUNTY_EXPIRY_DAYS (hourly).
    "post-refund-doubt-bounties": {
        "task": "post.tasks.refund_stale_doubt_bounties",
        "schedule": 3600.0,
    },
    # H20 - chunks of abandoned uploads, old idempotency records, old deleted drafts. Daily 03:40 (off-peak).
    "post-purge-stale-uploads": {
        "task": "post.tasks.purge_stale_uploads",
        "schedule": crontab(hour=3, minute=40),
    },
    # H23 - duplicate-content memory of the publish gate (post/publish_gate.py). Daily 03:50.
    "post-purge-publish-fingerprints": {
        "task": "post.tasks.purge_publish_fingerprints",
        "schedule": crontab(hour=3, minute=50),
    },
    # Video lectures (round 4): collect finished speech-to-text jobs of auto captions (post/tasks.py), every 2 minutes.
    "post-poll-video-captions": {
        "task": "post.tasks.poll_video_caption_jobs",
        "schedule": 120.0,
    },
    # Failed / stuck video conversions are retried with a back-off (post/tasks.py retry_failed_video_processing).
    "post-retry-failed-video-processing": {
        "task": "post.tasks.retry_failed_video_processing",
        "schedule": 600.0,
    },
    "post-prune-old-events": {
        "task": "post.tasks.prune_old_post_events",
        "schedule": crontab(hour=3, minute=15),
    },
    # H10 - the other two write-heavy feed tables (post/feed_retention.py): "seen" markers older than the seen
    # window (+7 d), then PII (ip / user agent) of old PostView rows. Staggered between the event prune (03:15)
    # and the leaderboard job (03:30); each run is time-budgeted (FEED_RETENTION["max_seconds"]).
    "post-prune-seen-views": {
        "task": "post.tasks.prune_seen_post_views",
        "schedule": crontab(hour=3, minute=20),
    },
    "post-scrub-view-pii": {
        "task": "post.tasks.scrub_post_view_pii",
        "schedule": crontab(hour=3, minute=25),
    },
    # H10 - feed SLO fast-burn check (post/feed_ops.py): availability / p95 / Redis-down / fallback-failed over the
    # last 15 min. Cheap (a few cache reads); one ERROR + Sentry message when something is wrong.
    "post-feed-slo-watch": {
        "task": "post.tasks.feed_slo_watch",
        "schedule": crontab(minute="*/5"),
    },
    # T1 item 9 — post.tasks.feed_daily_metrics: yesterday's feed summary (impressions, CTR, dwell,
    # sessions, show-fewer rate, diversity, per A/B variant) logged + cached. 02:45 = BEFORE the 03:15
    # prune job, so the day's PostEvent rows are certainly still there.
    "post-feed-daily-metrics": {
        "task": "post.tasks.feed_daily_metrics",
        "schedule": crontab(hour=2, minute=45),
    },
    # F2 - stories. Expired stories are already invisible live (every listing filters expires_at__gt=now); this sweep
    # soft-deletes them (batch UPDATE) so they leave the live set / the active-cap count and land in the owner's
    # archive. Every 10 min keeps a story's tail short; the weekly purge removes rows soft-deleted > 30 days ago
    # (stories in a Highlight are never purged). Both tasks are idempotent.
    "post-expire-old-stories": {
        "task": "post.tasks.expire_old_stories",
        "schedule": crontab(minute="*/10"),
    },
    "post-purge-ancient-stories": {
        "task": "post.tasks.hard_delete_ancient_stories",
        "schedule": crontab(hour=3, minute=5, day_of_week=0),  # Sunday 03:05, off-peak
    },
    # --- copyrights app automations (copyrights/tasks.py) ---
    "copyrights-auto-restore-counter-notices": {
        "task": "copyrights.auto_restore_counter_notices",
        "schedule": 3600.0,  # hourly: counter-notice period over + no court action -> content back
    },
    "copyrights-expire-strikes": {
        "task": "copyrights.expire_strikes",
        "schedule": 6 * 3600.0,
    },
    "copyrights-escalate-stale-claims": {
        "task": "copyrights.escalate_stale_claims",
        "schedule": 3 * 3600.0,
    },
    "copyrights-expire-needs-info": {
        "task": "copyrights.expire_needs_info",
        "schedule": 12 * 3600.0,
    },
    "child-safety-warn-overdue-cases": {
        "task": "user_profile.warn_overdue_child_safety_cases",
        "schedule": 3600.0,  # hourly: child-safety case still open after its SLA -> warn staff
    },
    "payment-reconcile": {
        "task": "user_profile.reconcile_payments",
        "schedule": 900.0,  # P-G2: every 15 min - settle PENDING coin / fee orders whose webhook was lost
    },
    "report-sla-sweep": {
        "task": "user_profile.sweep_report_sla",
        "schedule": 3600.0,  # hourly: open user reports past their review deadline -> mark breached + alert staff once
    },
    "copyrights-warn-due-legal-orders": {
        "task": "copyrights.warn_due_legal_orders",
        "schedule": 3600.0,  # hourly: court / government order deadline warnings
    },
    "copyrights-purge-personal-data": {
        "task": "copyrights.purge_personal_data",
        "schedule": crontab(hour=4, minute=0),  # daily, off-peak: DPDP Act - erase claimant data past its retention period
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
# NOTE: "assigments" is the app LABEL (the package dir is now `assignments/`, T3.10).
CONFIG_DRIFT_APPS = ["user_profile", "core", "assigments", "testseries", "campus", "support", "doubts"]

# Escape hatches for the same command — intentionally left empty. Per
# §11's own guidance, only add an entry here once a specific check has
# actually flagged something that's genuinely deliberate (not a bug the
# check is right to catch) — not preemptively for apps just added above.
CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}

# T3.5 (R10) — acknowledged stale-comment WARNINGS (fingerprints, not line numbers).
# `check_config_drift --strict` fails on anything NOT listed there, so the backlog
# can't grow while it is worked down. Errors (ALLOWED_HOSTS wildcard, DEBUG) are
# never baselined. Regenerate after cleaning up comments:
#   python manage.py check_config_drift --write-baseline config_drift_baseline.json
CONFIG_DRIFT_BASELINE = BASE_DIR / "config_drift_baseline.json"


# =====================================================================
# TESTSERIES — ADVANCED CONFIG  (see testseries/policy.py, access.py, live.py)
# Every value has a sane default in code; these lines just make the knobs
# visible. Env overrides are for staging / experiments.
# =====================================================================

# Who may charge for a test series.
#   mode "required"  -> must be paid   "optional" -> creator chooses
#         "forbidden" -> always free
# Product rule (T3, create-flow upgrade): a test series a user creates on their own
# (individual) may be FREE or PAID - the creator chooses in the app (default mode
# "optional"; a paid one still needs price >= TESTSERIES_MIN_PRICE_COINS). A campus
# one AND a tuition-class one are always FREE (TASK 9.1 — this is hard-coded in
# testseries/policy.py::ALWAYS_FREE_SOURCES, so the "campus" / "tuitionclass"
# entries below cannot make them paid). To go back to "individuals must charge" set
# TESTSERIES_INDIVIDUAL_PRICING=required (the app then shows the server's message
# next to the Free/Paid switch).
TESTSERIES_PRICING_POLICY = {
    "individual": {
        "mode": os.getenv("TESTSERIES_INDIVIDUAL_PRICING", "optional"),
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

# T2 — who may EDIT the questions of a campus / class series besides its creator
# (class-teacher / subject-teacher / admin of the section; teacher / co-teacher /
# moderator of the classroom). Same dotted-path pattern as TESTSERIES_CONTEXT_ACCESS.
# Fail-closed: a missing / broken resolver means "creator only".
TESTSERIES_CONTEXT_EDITORS = {
    "section": "campus.bridge.user_editable_testseries_context_ids",
    "classroom": "tuitionclass.bridge.user_editable_testseries_context_ids",
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
# ASSIGNMENTS — PUBLISHING (assignments/views.py: publish / explore / join)
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
# HOME FEED DIVERSITY RE-RANK (post/feed_diversity.py) - T1 Part 1
# Reorders ONE page so one author / one post type / one category does not
# clump. Never drops posts, so count + pagination are unchanged. Any key
# omitted falls back to feed_diversity.DEFAULT_DIVERSITY. Env
# FEED_DIVERSITY_ENABLED=0 switches the whole stage off.
#   author_gap       min positions between posts of one author   (<=1 off)
#   max_consecutive  max same post_type in a row                 (0 off)
#   category_gap     min positions between same category          (<=1 off)
#   lookahead        how far down a post may be pulled up         (<=1 off)
# =====================================================================
FEED_DIVERSITY = {
    "enabled": os.getenv("FEED_DIVERSITY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "author_gap": _env_int("FEED_DIVERSITY_AUTHOR_GAP", 3),
    "max_consecutive": _env_int("FEED_DIVERSITY_MAX_CONSECUTIVE", 2),
    "category_gap": _env_int("FEED_DIVERSITY_CATEGORY_GAP", 2),
    "lookahead": _env_int("FEED_DIVERSITY_LOOKAHEAD", 8),
}

# =====================================================================
# HOME FEED AUTHOR CAP (post/feed_diversity.py cap_pools, feed_mix.apply_author_caps)
# T1 Part 2. Applied when the ranked pools are built, so one prolific author
# cannot fill the whole ranked list.
#   *_soft_cap          posts of one author beyond this are DEMOTED to the tail
#                       of their pool (never dropped) (<=0 off)
#   discovery_hard_cap  recommended + trending only: posts of one author beyond
#                       this are DROPPED (0 = off, default). Dropping changes
#                       `count`, so it is opt-in. Following is never dropped.
# Env: FEED_AUTHOR_CAP_ENABLED=0 switches the stage off.
# =====================================================================
FEED_AUTHOR_CAP = {
    "enabled": os.getenv("FEED_AUTHOR_CAP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "following_soft_cap": _env_int("FEED_AUTHOR_CAP_FOLLOWING_SOFT", 5),
    "discovery_soft_cap": _env_int("FEED_AUTHOR_CAP_DISCOVERY_SOFT", 2),
    "discovery_hard_cap": _env_int("FEED_AUTHOR_CAP_DISCOVERY_HARD", 0),
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
# H10 - FEED RESILIENCE / SLO / RETENTION
# FEED_RESILIENCE (post/feed_resilience.py): GET /post/feed/ never 500s because ranking, Redis or a slow query broke.
#   enabled              master switch (env FEED_RESILIENCE_ENABLED=0/1); off -> errors propagate as before
#   pool_timeout_ms      PostgreSQL statement_timeout around the ranking queries (env FEED_POOL_TIMEOUT_MS, 0 = off)
#   lock_*               cache-stampede lock: parallel page-1 calls of one user build the pools once; a follower waits at
#                        most lock_wait_seconds for the builder, then builds itself (env FEED_LOCK_ENABLED=0/1)
#   fallback_*           the simple feed served when ranking fails: newest posts of followed accounts + recent public
# FEED_SLO (post/feed_ops.py): availability >= 99.5 %, p95 <= 800 ms; checked every 5 min over the last 15 min
#   (alert_window_minutes) once there are alert_min_requests requests (celery beat "post-feed-slo-watch").
# FEED_OPS_SCRAPE_TOKEN: bearer token Prometheus uses on GET /post/feed/ops/?format=prom (staff users may also read it).
# FEED_RETENTION (post/feed_retention.py): PostEvent / PostView(seen) / PostView PII lifetime, see that docstring.
# =====================================================================
FEED_RESILIENCE = {
    "enabled": os.getenv("FEED_RESILIENCE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "pool_timeout_ms": _env_int("FEED_POOL_TIMEOUT_MS", 4000),
    "lock_enabled": os.getenv("FEED_LOCK_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"),
    "lock_ttl_seconds": _env_int("FEED_LOCK_TTL_SECONDS", 20),
    "lock_wait_seconds": _env_float("FEED_LOCK_WAIT_SECONDS", 3.0),
}
FEED_SLO = {
    "availability_pct": _env_float("FEED_SLO_AVAILABILITY_PCT", 99.5),
    "p95_ms": _env_int("FEED_SLO_P95_MS", 800),
    "alert_window_minutes": _env_int("FEED_SLO_ALERT_WINDOW_MINUTES", 15),
    "alert_min_requests": _env_int("FEED_SLO_ALERT_MIN_REQUESTS", 50),
}
FEED_OPS_SCRAPE_TOKEN = os.getenv("FEED_OPS_SCRAPE_TOKEN", "")
# N10 - notification observability (core/notification_ops.py). delivery_success_pct = delivered / (delivered + failed),
# dead tokens (uninstalled apps) excluded; judged only when the window has >= alert_min_attempts pushes (beat
# "core-notification-slo-watch" every 10 min). NOTIFICATION_OPS_SCRAPE_TOKEN = bearer token for
# GET /core/notification-ops/?format=prom (empty => FEED_OPS_SCRAPE_TOKEN is used).
NOTIFICATION_SLO = {
    "delivery_success_pct": _env_float("NOTIFICATION_SLO_DELIVERY_SUCCESS_PCT", 97.0),
    "alert_window_minutes": _env_int("NOTIFICATION_SLO_ALERT_WINDOW_MINUTES", 15),
    "alert_min_attempts": _env_int("NOTIFICATION_SLO_ALERT_MIN_ATTEMPTS", 50),
    "max_not_allowed_pct": _env_float("NOTIFICATION_SLO_MAX_UNEXPLAINED_SKIP_PCT", 5.0),
}
NOTIFICATION_OPS_SCRAPE_TOKEN = os.getenv("NOTIFICATION_OPS_SCRAPE_TOKEN", "")
# X1 — call observability (message/call_ops.py). connect_success_pct: alert when LOWER over `alert_window_minutes`
# with at least `alert_min_attempts` connect attempts (celery beat "message-call-slo-watch").
# CALL_OPS_SCRAPE_TOKEN: bearer token for GET /message/calls/ops/?format=prom (empty => FEED_OPS_SCRAPE_TOKEN is used).
CALL_SLO = {
    "connect_success_pct": _env_float("CALL_SLO_CONNECT_SUCCESS_PCT", 95.0),
    "alert_window_minutes": _env_int("CALL_SLO_ALERT_WINDOW_MINUTES", 15),
    "alert_min_attempts": _env_int("CALL_SLO_ALERT_MIN_ATTEMPTS", 20),
}
CALL_OPS_SCRAPE_TOKEN = os.getenv("CALL_OPS_SCRAPE_TOKEN", "")

# X3 - privacy-safe product analytics + A/B experiments (core/product_analytics.py, core/experiments.py).
# * Aggregate counters only (no user id / device id / free text), counted ONLY for users whose latest `analytics`
#   consent (S5 Data & privacy switch) is granted. REQUIRE_OPT_IN=True => "never asked" is NOT collected (DPDP).
# * Minors are never counted and never put in an experiment unless the *_INCLUDE_MINORS flags are switched on.
# * PRODUCT_ANALYTICS_SCRAPE_TOKEN: bearer for GET /core/analytics/product/?format=prom (empty => FEED_OPS_SCRAPE_TOKEN).
# * EXPERIMENTS: per-key overrides merged over core.experiments.DEFAULT_EXPERIMENTS, e.g.
#     EXPERIMENTS = {"notif_copy_streak_at_risk_v1": {"enabled": True, "rollout_pct": 20, "salt": "r2"}}
#   or just env EXPERIMENTS_ON="notif_copy_streak_at_risk_v1". EXPERIMENTS_ENABLED=0 stops every experiment at once.
PRODUCT_ANALYTICS_ENABLED = os.getenv("PRODUCT_ANALYTICS_ENABLED", "1").lower() in ("1", "true", "yes")
PRODUCT_ANALYTICS_REQUIRE_OPT_IN = os.getenv("PRODUCT_ANALYTICS_REQUIRE_OPT_IN", "1").lower() in ("1", "true", "yes")
PRODUCT_ANALYTICS_INCLUDE_MINORS = False
PRODUCT_ANALYTICS_SCRAPE_TOKEN = os.getenv("PRODUCT_ANALYTICS_SCRAPE_TOKEN", "")
EXPERIMENTS_ENABLED = os.getenv("EXPERIMENTS_ENABLED", "1").lower() in ("1", "true", "yes")
EXPERIMENTS_INCLUDE_MINORS = False
EXPERIMENTS = {}
FEED_RETENTION = {
    "post_event_days": _env_int("FEED_RETENTION_POST_EVENT_DAYS", 30),
    "seen_days": _env_int("FEED_RETENTION_SEEN_DAYS", 60),
    "post_view_pii_days": _env_int("FEED_RETENTION_POST_VIEW_PII_DAYS", 90),
    "batch_size": _env_int("FEED_RETENTION_BATCH_SIZE", 5000),
    "max_seconds": _env_int("FEED_RETENTION_MAX_SECONDS", 900),
}

# H2 (loadtest/README.md): staging-only counters for the feed load test - Redis hit-rate of the feed caches and
# SQL statements per feed page (post/feed_stats.py). Wraps every feed request in query capture, so keep it OFF
# everywhere except the staging workers you are load-testing. Read with `manage.py feed_stats`.
FEED_LOADTEST_STATS = os.getenv("FEED_LOADTEST_STATS", "0").strip().lower() in ("1", "true", "yes", "on")

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
#   include_own_last_tier small user base: when the pool is still < min_pool after every media tier,
#                         the caller's OWN public videos are appended LAST (never ahead of other people's;
#                         env REELS_INCLUDE_OWN_LAST_TIER=0/1, default on). Off = own reels are never served
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
    "include_own_last_tier": os.getenv("REELS_INCLUDE_OWN_LAST_TIER", "1").strip().lower() not in ("0", "false", "no", "off"),
}

# ---------------------------------------------------------------------------
# T1 Parts 3-5 - exploration, A/B bucket, extra signals, educational lens,
# candidate cache. Every block is a dict merged over the defaults in the named
# module, so you only list what you want to change. The env switches turn a
# whole stage off without a deploy of code.
# ---------------------------------------------------------------------------
def _flag(name, default="1"):
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


# post/feed_experiment.py - stable A/B buckets. One "default" variant = no change.
# T1 item 7: a variant may override these sections (each merged over its settings block below):
#   mix -> FEED_MIX_RATIOS   diversity -> FEED_DIVERSITY   author_cap -> FEED_AUTHOR_CAP
#   quality -> FEED_QUALITY  explore -> FEED_EXPLORE  signals -> FEED_SIGNALS  context -> FEED_CONTEXT
# Example split (overrides are merged over FEED_EXPLORE / FEED_SIGNALS / FEED_CONTEXT):
#   "variants": [
#       {"name": "control", "weight": 50, "overrides": {}},
#       {"name": "more_explore", "weight": 50, "overrides": {"explore": {"share": 0.15}}},
#   ]
FEED_EXPERIMENT = {
    "enabled": _flag("FEED_EXPERIMENT_ENABLED"),
    "name": "feed_adv_v1",  # change the name to re-shuffle everyone into new buckets
    "buckets": 100,
    "variants": [{"name": "default", "weight": 100, "overrides": {}}],
}

# post/feed_explore.py - exploration slots (new creators / test audience).
FEED_EXPLORE = {
    "enabled": _flag("FEED_EXPLORE_ENABLED"),
    "share": 0.08,
    "new_viewer_share": 0.15,
}

# post/feed_signals.py - dwell / tap / save / share / quick-skip signals.
FEED_SIGNALS = {
    "enabled": _flag("FEED_SIGNALS_ENABLED"),
    "half_life_days": 7.0,
}

# post/feed_context.py - educational lens + campus / class context.
FEED_CONTEXT = {
    "enabled": _flag("FEED_CONTEXT_ENABLED"),
    "study_hours": (16, 23),  # local time (TIME_ZONE)
}

# post/feed_cache.py - per-user candidate cache (Redis when REDIS_URL is set).
FEED_CANDIDATE_CACHE = {
    "enabled": _flag("FEED_CANDIDATE_CACHE_ENABLED"),
    "ttl_seconds": int(os.getenv("FEED_CANDIDATE_CACHE_TTL", "30")),
}

# =====================================================================
# post/feed_quality.py - QUALITY / SAFETY GATES  (T1 item 6)
#   reported_hard   reported_count >= this -> post is out of EVERY source incl. following (0 = off)
#   reported_soft   reported_count >= this -> demoted to the tail of discovery pools (0 = off)
#   min_text_chars  plain text post, no media, shorter than this -> demoted (0 = off)
#   max_links / max_char_run / max_caps_ratio / max_hashtags / repeat_word_ratio -> "spam"
#   duplicate_max   same author + same text more than this many times in duplicate_window_days
#                   -> "repeated_text"
#   spam_action     what spam / repeated_text become: "drop" (default) or "demote"
# Discovery pools (recommended + trending + exploration) only - following is an explicit choice.
# Env: FEED_QUALITY_ENABLED=0 switches every gate off, =1 forces them on (also under tests).
# Under `manage.py test` / pytest (TESTING) the gates default to OFF: older feed tests create many
# tiny / same-text posts ("p0", "hello") the spam rules would -- correctly -- drop.
# post/tests_feed_quality.py switches them on with override_settings.
# Blocked / muted / hidden authors are enforced elsewhere (feed_mix + services), always on.
# =====================================================================
FEED_QUALITY = {
    "enabled": _flag("FEED_QUALITY_ENABLED", "0" if TESTING else "1"),
    "reported_hard": _env_int("FEED_QUALITY_REPORTED_HARD", 10),
    "reported_soft": _env_int("FEED_QUALITY_REPORTED_SOFT", 3),
    "min_text_chars": _env_int("FEED_QUALITY_MIN_TEXT_CHARS", 15),
    "duplicate_max": _env_int("FEED_QUALITY_DUPLICATE_MAX", 2),
    "duplicate_window_days": _env_int("FEED_QUALITY_DUPLICATE_WINDOW_DAYS", 7),
    "spam_action": os.getenv("FEED_QUALITY_SPAM_ACTION", "drop").strip().lower(),
}


# ---------------------------------------------------------------------------
# Auto-moderation (common/moderation.py, user_profile/automod.py)
# Posts, comments, stories, bios and DMs are screened on save and, if they
# look abusive/spammy, added to the admin review queue
# (user_profile.AutoModerationFlag). FLAG-ONLY: nothing is hidden/deleted.
#   AUTOMOD_SCREEN_DMS   screen private messages too (default True). Turn off
#                        if you decide DMs must never be scanned.
#   AUTOMOD_AI_ENABLED   optional AI second pass (Celery, after the word-list
#                        pass). Needs ANTHROPIC_API_KEY. Default off.
#   MODERATION_PROFANITY_WORDS  override/extend the word list without a deploy
#                        (falls back to TUITIONCLASS_PROFANITY_WORDS).
# ---------------------------------------------------------------------------
AUTOMOD_SCREEN_DMS = os.getenv("AUTOMOD_SCREEN_DMS", "true").lower() == "true"
AUTOMOD_AI_ENABLED = os.getenv("AUTOMOD_AI_ENABLED", "false").lower() == "true"
AUTOMOD_AI_MODEL = os.getenv("AUTOMOD_AI_MODEL", "claude-haiku-5-5")
AUTOMOD_AI_TIMEOUT = int(os.getenv("AUTOMOD_AI_TIMEOUT", "5"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# COPYRIGHTS app (copyrights/services.py has the full explanation).
#   COPYRIGHT_AUTO_HOLD            hide content the moment a COMPLETE notice is filed (reversible)
#   COPYRIGHT_COUNTER_WAIT_DAYS    days before content is auto-restored after a counter-notice
#   COPYRIGHT_STRIKE_DAYS          how long a strike counts
#   COPYRIGHT_RESTRICT_AT / _REVIEW_AT   active strikes -> uploads blocked / termination review
#   COPYRIGHT_SLA_HOURS            untouched claims are escalated to L2 after this
#   COPYRIGHT_NEEDS_INFO_DAYS      claim closed if the claimant never answers
#   COPYRIGHT_CLAIMS_PER_DAY / COPYRIGHT_BAD_FAITH_LIMIT   abuse limits for claimants
#   COPYRIGHT_HIGH_REACH_FOLLOWERS claims against bigger accounts need an L2 decision
# REPORT_* = automation on user reports (user_profile/report_automation.py).
# ---------------------------------------------------------------------------
COPYRIGHT_AUTO_HOLD = os.getenv("COPYRIGHT_AUTO_HOLD", "true").lower() == "true"
COPYRIGHT_COUNTER_WAIT_DAYS = int(os.getenv("COPYRIGHT_COUNTER_WAIT_DAYS", 21))  # Copyright Act s.52(1)(c): 21 days - confirm with your lawyer
COPYRIGHT_STRIKE_DAYS = int(os.getenv("COPYRIGHT_STRIKE_DAYS", 180))
COPYRIGHT_RESTRICT_AT = int(os.getenv("COPYRIGHT_RESTRICT_AT", 2))
COPYRIGHT_REVIEW_AT = int(os.getenv("COPYRIGHT_REVIEW_AT", 3))
COPYRIGHT_SLA_HOURS = int(os.getenv("COPYRIGHT_SLA_HOURS", 48))
COPYRIGHT_NEEDS_INFO_DAYS = int(os.getenv("COPYRIGHT_NEEDS_INFO_DAYS", 14))
COPYRIGHT_CLAIMS_PER_DAY = int(os.getenv("COPYRIGHT_CLAIMS_PER_DAY", 10))
COPYRIGHT_BAD_FAITH_LIMIT = int(os.getenv("COPYRIGHT_BAD_FAITH_LIMIT", 3))
COPYRIGHT_HIGH_REACH_FOLLOWERS = int(os.getenv("COPYRIGHT_HIGH_REACH_FOLLOWERS", 10000))
REPORT_HOLD_THRESHOLD_SEVERE = int(os.getenv("REPORT_HOLD_THRESHOLD_SEVERE", 3))
REPORT_HOLD_THRESHOLD_ANY = int(os.getenv("REPORT_HOLD_THRESHOLD_ANY", 8))
REPORT_UNRELIABLE_DISMISSED = int(os.getenv("REPORT_UNRELIABLE_DISMISSED", 10))
# --- brigading guard (user_profile/report_automation.analyse_reports) ---
REPORT_NEW_ACCOUNT_DAYS = int(os.getenv("REPORT_NEW_ACCOUNT_DAYS", 3))
REPORT_NEW_ACCOUNT_WEIGHT = float(os.getenv("REPORT_NEW_ACCOUNT_WEIGHT", 0.5))
REPORT_PERSISTENT_DISMISSED = int(os.getenv("REPORT_PERSISTENT_DISMISSED", 2))
REPORT_BURST_MIN = int(os.getenv("REPORT_BURST_MIN", 10))
REPORT_BURST_MINUTES = int(os.getenv("REPORT_BURST_MINUTES", 10))
REPORT_NEW_SHARE = float(os.getenv("REPORT_NEW_SHARE", 0.7))
REPORT_BRIGADE_MODE = os.getenv("REPORT_BRIGADE_MODE", "review")  # "review" or "off"
# --- review deadlines for reports (user_profile/report_sla.py), hours ---
REPORT_SLA_HOURS_SEVERE = int(os.getenv("REPORT_SLA_HOURS_SEVERE", 24))   # nudity / violence / self-harm / hate / harassment + anything auto-held
REPORT_SLA_HOURS_NORMAL = int(os.getenv("REPORT_SLA_HOURS_NORMAL", 72))
REPORT_SLA_SOON_HOURS = int(os.getenv("REPORT_SLA_SOON_HOURS", 4))        # "due soon" bucket in the staff summary
# --- server-side text / media filters on user content (P-F15, point 3) ---
#   COMMENT_TEXT_ACTION  what happens to a comment with profanity / adult content:
#                          "hold" (default) the comment is created but hidden + the author gets a notice with an Appeal button
#                          "flag" only queue it for a moderator (the old behaviour)   "off" no screening of comments
#   DM_NSFW_ACTION       adult-content text in a DM: "flag" (default, never hides a private message) or "off"
#   MEDIA_NSFW_PROVIDER  optional dotted path "pkg.module.func": func(name, file=None, path=None) -> {"nsfw": True, "score": 0.97,
#                          "label": "porn"} | None. Without it nothing is held on a guess (image bytes are not decoded here).
#   MEDIA_NSFW_ACTION    "hold" (default: provider says nsfw -> post held + owner notice + appeal) | "flag" | "off"
#   MEDIA_NSFW_MIN_SCORE a provider answer below this score is only flagged, never held (default 0.85)
COMMENT_TEXT_ACTION = os.getenv("COMMENT_TEXT_ACTION", "hold").lower()
DM_NSFW_ACTION = os.getenv("DM_NSFW_ACTION", "flag").lower()
MEDIA_NSFW_PROVIDER = os.getenv("MEDIA_NSFW_PROVIDER", "")
MEDIA_NSFW_ACTION = os.getenv("MEDIA_NSFW_ACTION", "hold").lower()
MEDIA_NSFW_MIN_SCORE = float(os.getenv("MEDIA_NSFW_MIN_SCORE", 0.85))
# --- appeals (user_profile/appeals.py) ---
APPEALS_PER_DAY = int(os.getenv("APPEALS_PER_DAY", 5))
# --- child safety (user_profile/child_safety.py) - confirm the numbers with your lawyer ---
CHILD_SAFETY_SLA_HOURS = int(os.getenv("CHILD_SAFETY_SLA_HOURS", 24))
CHILD_SAFETY_PRESERVE_DAYS = int(os.getenv("CHILD_SAFETY_PRESERVE_DAYS", 180))
# --- strike / termination appeals (copyrights/appeals) ---
COPYRIGHT_APPEAL_DAYS = int(os.getenv("COPYRIGHT_APPEAL_DAYS", 30))  # a strike can be appealed within this many days of issue
COPYRIGHT_REAPPEAL_DAYS = int(os.getenv("COPYRIGHT_REAPPEAL_DAYS", 30))  # wait after a rejected termination appeal
# --- DPDP Act 2023: how long a claimant's personal data is kept (copyrights/retention.py) ---
#   s.8(7): erase once the purpose is over unless a law says keep. The Act gives NO fixed number for this - these are
#   OUR defaults, confirm with your lawyer. 0 = never erase (not recommended: system check copyrights.W009).
#   COPYRIGHT_PII_RETENTION_DAYS         closed claim (rejected / withdrawn / restored): name, e-mail, organisation,
#                                        signature + proof files erased this many days after the last change
#   COPYRIGHT_PII_RETENTION_UPHELD_DAYS  same for UPHELD / bad-faith claims (appeals, strike history, 3-year limitation)
#   COPYRIGHT_EMAIL_CODE_RETENTION_DAYS  e-mail verification codes (never below COPYRIGHT_EMAIL_VERIFY_DAYS + 1)
#   COPYRIGHT_PII_PURGE_BATCH            max claims erased per daily run
COPYRIGHT_PII_RETENTION_DAYS = int(os.getenv("COPYRIGHT_PII_RETENTION_DAYS", 365))
COPYRIGHT_PII_RETENTION_UPHELD_DAYS = int(os.getenv("COPYRIGHT_PII_RETENTION_UPHELD_DAYS", 1095))
COPYRIGHT_EMAIL_CODE_RETENTION_DAYS = int(os.getenv("COPYRIGHT_EMAIL_CODE_RETENTION_DAYS", 45))
COPYRIGHT_PII_PURGE_BATCH = int(os.getenv("COPYRIGHT_PII_PURGE_BATCH", 500))
# --- moderator dashboard (copyrights/dashboard.py, /admin/copyrights/copyrightclaim/dashboard/) ---
#   COPYRIGHT_DASH_SOON_HOURS  a review deadline with less than this many hours left shows as "due soon" (default 6)
#   COPYRIGHT_DASH_PAGE_SIZE   rows per page (default 25, max 100). The deadline itself = COPYRIGHT_SLA_HOURS (48) /
#                              COPYRIGHT_PUBLISHER_SLA_HOURS (12), the same numbers the Celery escalation uses.
COPYRIGHT_DASH_SOON_HOURS = int(os.getenv("COPYRIGHT_DASH_SOON_HOURS", 6))
COPYRIGHT_DASH_PAGE_SIZE = int(os.getenv("COPYRIGHT_DASH_PAGE_SIZE", 25))

# ---------------------------------------------------------------------------
# LEGAL / LAUNCH settings used by the public Copyright Policy page (copyrights/policy.py).
# Set these in the production environment - `manage.py check` warns while they are empty.
#   Grievance Officer (IT Rules 2021) - name, email are required; GRIEVANCE_ACK_HOURS (24) and
#   GRIEVANCE_RESOLVE_DAYS (15) are only the numbers printed on the page - confirm with your lawyer.
#   DMCA_AGENT_* : only if you have US users (optional).
#   LEGAL_ORDERS_EMAIL : where courts / authorities send removal orders.
#   LEGAL_ORDER_SLA_HOURS (36) / LEGAL_ORDER_WARN_HOURS (12): deadline of a legal order, warning window.
# ---------------------------------------------------------------------------
APP_NAME = os.getenv("APP_NAME", "LearnScroll")
COMPANY_LEGAL_NAME = os.getenv("COMPANY_LEGAL_NAME", "")
COPYRIGHT_EMAIL = os.getenv("COPYRIGHT_EMAIL", "")
LEGAL_ORDERS_EMAIL = os.getenv("LEGAL_ORDERS_EMAIL", "")
GRIEVANCE_OFFICER_NAME = os.getenv("GRIEVANCE_OFFICER_NAME", "")
GRIEVANCE_OFFICER_DESIGNATION = os.getenv("GRIEVANCE_OFFICER_DESIGNATION", "Grievance Officer")
GRIEVANCE_OFFICER_EMAIL = os.getenv("GRIEVANCE_OFFICER_EMAIL", "")
GRIEVANCE_OFFICER_PHONE = os.getenv("GRIEVANCE_OFFICER_PHONE", "")
GRIEVANCE_OFFICER_ADDRESS = os.getenv("GRIEVANCE_OFFICER_ADDRESS", "")
GRIEVANCE_ACK_HOURS = int(os.getenv("GRIEVANCE_ACK_HOURS", 24))
GRIEVANCE_RESOLVE_DAYS = int(os.getenv("GRIEVANCE_RESOLVE_DAYS", 15))
DMCA_AGENT_NAME = os.getenv("DMCA_AGENT_NAME", "")
DMCA_AGENT_EMAIL = os.getenv("DMCA_AGENT_EMAIL", "")
DMCA_AGENT_PHONE = os.getenv("DMCA_AGENT_PHONE", "")
DMCA_AGENT_ADDRESS = os.getenv("DMCA_AGENT_ADDRESS", "")
LEGAL_ORDER_SLA_HOURS = int(os.getenv("LEGAL_ORDER_SLA_HOURS", 36))
LEGAL_ORDER_WARN_HOURS = int(os.getenv("LEGAL_ORDER_WARN_HOURS", 12))

# ---------------------------------------------------------------------------
# COPYRIGHTS: claimant checks, media handling, notifications (copyrights/claimant.py, media.py, notify.py)
#   COPYRIGHT_REQUIRE_EMAIL_VERIFICATION / _EMAIL_VERIFY_DAYS   notice address must be verified
#   COPYRIGHT_NEW_ACCOUNT_DAYS, _NEW_CLAIMANT_PER_DAY            young accounts: 1 notice/day, never auto-held
#   COPYRIGHT_AUTO_HOLDS_PER_DAY                                  brake on automatic holds per claimant
#   COPYRIGHT_EVIDENCE_MAX_MB / _MAX_FILES                        proof uploads
#   COPYRIGHT_MEDIA_ASYNC        heavy work (copy evidence, move files, hash uploads, e-mails) runs in Celery after commit
#   COPYRIGHT_QUARANTINE_PREFIX  removed files are moved to <prefix>/<original name>
#   COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID / COPYRIGHT_CDN_PATH_PREFIX  invalidate removed paths on the CDN
#   COPYRIGHT_EMAILS_ENABLED, SITE_URL                            e-mail notices (+ link in the footer)
# ---------------------------------------------------------------------------
COPYRIGHT_REQUIRE_EMAIL_VERIFICATION = os.getenv("COPYRIGHT_REQUIRE_EMAIL_VERIFICATION", "true").lower() == "true"
COPYRIGHT_EMAIL_VERIFY_DAYS = int(os.getenv("COPYRIGHT_EMAIL_VERIFY_DAYS", 30))
COPYRIGHT_NEW_ACCOUNT_DAYS = int(os.getenv("COPYRIGHT_NEW_ACCOUNT_DAYS", 7))
COPYRIGHT_NEW_CLAIMANT_PER_DAY = int(os.getenv("COPYRIGHT_NEW_CLAIMANT_PER_DAY", 1))
COPYRIGHT_AUTO_HOLDS_PER_DAY = int(os.getenv("COPYRIGHT_AUTO_HOLDS_PER_DAY", 3))
# Trusted tiers (TrustedClaimant): "verified" = held at once; "publisher" = fast track (queue priority HIGH,
# shorter review deadline, higher limits). Caps stop a hijacked publisher account from hiding the platform (0 = no cap).
COPYRIGHT_TRUSTED_HOLDS_PER_DAY = int(os.getenv("COPYRIGHT_TRUSTED_HOLDS_PER_DAY", 50))
COPYRIGHT_PUBLISHER_HOLDS_PER_DAY = int(os.getenv("COPYRIGHT_PUBLISHER_HOLDS_PER_DAY", 200))
COPYRIGHT_PUBLISHER_CLAIMS_PER_DAY = int(os.getenv("COPYRIGHT_PUBLISHER_CLAIMS_PER_DAY", 100))
COPYRIGHT_PUBLISHER_SLA_HOURS = int(os.getenv("COPYRIGHT_PUBLISHER_SLA_HOURS", 12))
# Public transparency report (copyrights/transparency.py): GET /copyright/transparency/ and /legal/transparency-report/.
# Counts only (claims, removals, restored, counter-notices, strikes, appeals, court orders). false = the public
# endpoints answer 404 (the management command copyright_transparency_report still works).
COPYRIGHT_TRANSPARENCY_PUBLIC = os.getenv("COPYRIGHT_TRANSPARENCY_PUBLIC", "true").lower() == "true"
COPYRIGHT_TRANSPARENCY_CACHE_SECONDS = int(os.getenv("COPYRIGHT_TRANSPARENCY_CACHE_SECONDS", 600))
COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS = int(os.getenv("COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS", 180))
COPYRIGHT_EVIDENCE_MAX_MB = int(os.getenv("COPYRIGHT_EVIDENCE_MAX_MB", 10))
COPYRIGHT_EVIDENCE_MAX_FILES = int(os.getenv("COPYRIGHT_EVIDENCE_MAX_FILES", 5))
COPYRIGHT_MEDIA_ASYNC = os.getenv("COPYRIGHT_MEDIA_ASYNC", "true").lower() == "true"
COPYRIGHT_QUARANTINE_PREFIX = os.getenv("COPYRIGHT_QUARANTINE_PREFIX", "copyright_quarantine")
COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID = os.getenv("COPYRIGHT_CLOUDFRONT_DISTRIBUTION_ID", "")
COPYRIGHT_CDN_PATH_PREFIX = os.getenv("COPYRIGHT_CDN_PATH_PREFIX", "")

# --- automatic re-upload detection (copyrights/fingerprint.py, services.precheck_upload) ---
#   COPYRIGHT_REJECT_EXACT_REUPLOAD  refuse (403 code copyright_reupload_blocked) a file whose bytes equal a removed
#                                    file; False = only hold it after saving
#   COPYRIGHT_FP_ENABLED             perceptual fingerprints (image / video / audio / PDF) of REMOVED files; a similar
#                                    new upload is HELD and sent to level-2 review (never removed automatically)
#   needs on the worker image: ffmpeg + ffprobe (video), chromaprint `fpcalc` (audio: apt install libchromaprint-tools),
#   pypdf (PDF, in requirements.txt). A missing tool only skips that kind and logs an error.
#   Tuning (defaults are conservative - tune on real removed/uploaded samples before lowering):
#   _IMAGE_MAX_DISTANCE (bits of 64), _VIDEO_FRAMES / _VIDEO_FRAME_DISTANCE / _VIDEO_MATCH_RATIO,
#   _AUDIO_MAX_BER / _AUDIO_MIN_SECONDS / _AUDIO_SECONDS / _AUDIO_MAX_SCAN, _PDF_MIN_JACCARD / _PDF_MIN_WORDS / _PDF_MAX_PAGES
COPYRIGHT_REJECT_EXACT_REUPLOAD = os.getenv("COPYRIGHT_REJECT_EXACT_REUPLOAD", "true").lower() == "true"
COPYRIGHT_FP_ENABLED = os.getenv("COPYRIGHT_FP_ENABLED", "true").lower() == "true"
COPYRIGHT_FP_IMAGE_MAX_DISTANCE = int(os.getenv("COPYRIGHT_FP_IMAGE_MAX_DISTANCE", 5))
COPYRIGHT_FP_VIDEO_FRAMES = int(os.getenv("COPYRIGHT_FP_VIDEO_FRAMES", 12))
COPYRIGHT_FP_VIDEO_FRAME_DISTANCE = int(os.getenv("COPYRIGHT_FP_VIDEO_FRAME_DISTANCE", 8))
COPYRIGHT_FP_VIDEO_MATCH_RATIO = float(os.getenv("COPYRIGHT_FP_VIDEO_MATCH_RATIO", 0.6))
COPYRIGHT_FP_AUDIO_MAX_BER = float(os.getenv("COPYRIGHT_FP_AUDIO_MAX_BER", 0.30))
COPYRIGHT_FP_AUDIO_MIN_SECONDS = int(os.getenv("COPYRIGHT_FP_AUDIO_MIN_SECONDS", 15))
COPYRIGHT_FP_PDF_MIN_JACCARD = float(os.getenv("COPYRIGHT_FP_PDF_MIN_JACCARD", 0.6))
COPYRIGHT_EMAILS_ENABLED = os.getenv("COPYRIGHT_EMAILS_ENABLED", "true").lower() == "true"
SITE_URL = os.getenv("SITE_URL", "")

# T0.8 (R1) — public legal pages (common/legal_pages.py). Starter text ships with a DRAFT banner:
# have counsel review it, then set LEGAL_PAGES_DRAFT_BANNER=false. Or host elsewhere and set the *_URL vars (302).
LEGAL_ENTITY_NAME = os.getenv("LEGAL_ENTITY_NAME", "LearnScroll")
LEGAL_JURISDICTION = os.getenv("LEGAL_JURISDICTION", "")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "")
TERMS_URL = os.getenv("TERMS_URL", "")
DELETE_ACCOUNT_URL = os.getenv("DELETE_ACCOUNT_URL", "")
LEGAL_PAGES_DRAFT_BANNER = os.getenv("LEGAL_PAGES_DRAFT_BANNER", "true").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# L1 -- login / OTP abuse limits (login/login_guard.py, login/otp_service.py).
# All counters live in the shared CACHES backend (Redis in production), so they hold
# across gunicorn workers. Every value can be overridden from the environment.
# ---------------------------------------------------------------------------
# OTP / reset-code SENDING, per target (email or phone):
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "30"))   # min gap between two codes
OTP_MAX_SENDS_PER_HOUR = int(os.getenv("OTP_MAX_SENDS_PER_HOUR", "3"))              # codes per target per hour
OTP_IP_MAX_SENDS_PER_HOUR = int(os.getenv("OTP_IP_MAX_SENDS_PER_HOUR", "20"))       # codes per IP per hour (all targets)
# Password-login lockout (wrong passwords):
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))                      # per identifier + IP
LOGIN_ACCOUNT_MAX_FAILURES = int(os.getenv("LOGIN_ACCOUNT_MAX_FAILURES", "15"))     # per identifier, all IPs
LOGIN_IP_MAX_FAILURES = int(os.getenv("LOGIN_IP_MAX_FAILURES", "30"))               # per IP, all identifiers
LOGIN_FAILURE_WINDOW_SECONDS = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "900"))  # failures counted within this window
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))              # how long a lock lasts

# ---------------------------------------------------------------------------
# L1 gap 18 -- disposable e-mail / virtual phone block (login/disposable.py)
#   DISPOSABLE_BLOCK_ENABLED          master switch (default on)
#   DISPOSABLE_EMAIL_EXTRA_DOMAINS    comma separated, added to the built-in list
#   DISPOSABLE_EMAIL_LIST_FILE        path to a text file, one domain per line (e.g. the public
#                                     "disposable-email-domains" list) -- re-read when it changes
#   DISPOSABLE_EMAIL_ALLOWED_DOMAINS  never blocked, even if listed (false-positive escape hatch)
#   BLOCKED_PHONE_PREFIXES            comma separated digit prefixes of virtual ranges to refuse
#   PHONE_LOOKUP_PROVIDER             "" (off, default) or "twilio" -> carrier line-type lookup
#   PHONE_BLOCKED_LINE_TYPES          line types refused by the lookup (default nonFixedVoip,voip)
#   TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN   only needed for the lookup
# And captcha (login/captcha.py):
#   CAPTCHA_MODE        off | adaptive | always   (default adaptive; OFF under tests)
#   CAPTCHA_PROVIDER    builtin (default, no keys) | turnstile | hcaptcha | recaptcha
#   CAPTCHA_SITE_KEY / CAPTCHA_SECRET_KEY   for turnstile / hcaptcha / recaptcha
#   CAPTCHA_FAIL_OPEN   let requests through if the remote provider is down (default true)
# ---------------------------------------------------------------------------
DISPOSABLE_BLOCK_ENABLED = os.getenv("DISPOSABLE_BLOCK_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
DISPOSABLE_EMAIL_EXTRA_DOMAINS = [d.strip().lower() for d in os.getenv("DISPOSABLE_EMAIL_EXTRA_DOMAINS", "").split(",") if d.strip()]
DISPOSABLE_EMAIL_ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("DISPOSABLE_EMAIL_ALLOWED_DOMAINS", "").split(",") if d.strip()]
DISPOSABLE_EMAIL_LIST_FILE = os.getenv("DISPOSABLE_EMAIL_LIST_FILE", "").strip()
BLOCKED_PHONE_PREFIXES = [p.strip() for p in os.getenv("BLOCKED_PHONE_PREFIXES", "").split(",") if p.strip()]
PHONE_LOOKUP_PROVIDER = os.getenv("PHONE_LOOKUP_PROVIDER", "").strip().lower()
PHONE_BLOCKED_LINE_TYPES = os.getenv("PHONE_BLOCKED_LINE_TYPES", "nonFixedVoip,voip")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

CAPTCHA_MODE = os.getenv("CAPTCHA_MODE", "off" if TESTING else "adaptive").strip().lower()
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "builtin").strip().lower()
CAPTCHA_SITE_KEY = os.getenv("CAPTCHA_SITE_KEY", "")
CAPTCHA_SECRET_KEY = os.getenv("CAPTCHA_SECRET_KEY", "")
CAPTCHA_FAIL_OPEN = os.getenv("CAPTCHA_FAIL_OPEN", "true").strip().lower() in ("1", "true", "yes", "on")
CAPTCHA_TTL_SECONDS = int(os.getenv("CAPTCHA_TTL_SECONDS", "180"))                           # a challenge image stays valid this long
CAPTCHA_RISK_WINDOW_SECONDS = int(os.getenv("CAPTCHA_RISK_WINDOW_SECONDS", "3600"))          # adaptive volume window
CAPTCHA_SEND_OTP_FREE_REQUESTS = int(os.getenv("CAPTCHA_SEND_OTP_FREE_REQUESTS", "2"))      # send-otp calls per IP/window before asking
CAPTCHA_FORGOT_FREE_REQUESTS = int(os.getenv("CAPTCHA_FORGOT_FREE_REQUESTS", "2"))          # forgot-password calls per IP/window before asking
CAPTCHA_LOGIN_AFTER_FAILURES = int(os.getenv("CAPTCHA_LOGIN_AFTER_FAILURES", "3"))          # wrong passwords before login asks

# ---------------------------------------------------------------------------
# L1 gap 19 -- audit log of login / OTP / password-reset / session events
# (login.models.AuthAuditLog, written by login/audit.py, read-only in the admin)
#   AUTH_AUDIT_ENABLED               master switch (default on)
#   AUTH_AUDIT_RETENTION_DAYS        rows older than this are purged nightly by
#                                    login.purge_auth_audit_logs (default 180; 0 = keep forever)
#   AUTH_AUDIT_NOISE_DEDUPE_SECONDS  attacker-triggerable "blocked" events (captcha asked, OTP rate-limited,
#                                    login locked, refresh refused) from the same IP + identifier are written
#                                    once per this window, so they can't be used to fill the table
# ---------------------------------------------------------------------------
AUTH_AUDIT_ENABLED = os.getenv("AUTH_AUDIT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
AUTH_AUDIT_RETENTION_DAYS = int(os.getenv("AUTH_AUDIT_RETENTION_DAYS", "180"))
AUTH_AUDIT_NOISE_DEDUPE_SECONDS = int(os.getenv("AUTH_AUDIT_NOISE_DEDUPE_SECONDS", "60"))

# ---------------------------------------------------------------------------
# S5 -- DPDP Act 2023 data rights (login/data_rights.py, login/data_rights_views.py)
#   DATA_EXPORT_EXPIRY_DAYS          how long the "Download my data" ZIP stays available (default 7)
#   DATA_EXPORT_MIN_INTERVAL_HOURS   one new export request per this many hours (default 24; 0 = no limit)
#   DATA_EXPORT_MAX_ROWS             rows per section inside the ZIP (default 50000)
#   ACCOUNT_DELETION_GRACE_DAYS      days between "Delete account" and the real erase (default 30; 0 = erase at once)
#   ACCOUNT_DELETION_REMINDER_DAYS   one "your account is deleted soon" e-mail this many days before (default 3; 0 = off)
#   PRIVACY_NOTICE_VERSION           stamped on every consent row (default "2026-10")
# ---------------------------------------------------------------------------
DATA_EXPORT_EXPIRY_DAYS = int(os.getenv("DATA_EXPORT_EXPIRY_DAYS", "7"))
DATA_EXPORT_MIN_INTERVAL_HOURS = int(os.getenv("DATA_EXPORT_MIN_INTERVAL_HOURS", "24"))
DATA_EXPORT_MAX_ROWS = int(os.getenv("DATA_EXPORT_MAX_ROWS", "50000"))
ACCOUNT_DELETION_GRACE_DAYS = int(os.getenv("ACCOUNT_DELETION_GRACE_DAYS", "30"))
ACCOUNT_DELETION_REMINDER_DAYS = int(os.getenv("ACCOUNT_DELETION_REMINDER_DAYS", "3"))
PRIVACY_NOTICE_VERSION = os.getenv("PRIVACY_NOTICE_VERSION", "2026-10")

# ---------------------------------------------------------------------------------------------------------------
# S3 -- change-password re-auth.
#   CHANGE_PASSWORD_REQUIRE_CURRENT   true (default): POST /login/auth/change-password/ needs `current_password` for
#                                     accounts that have a password (Google-only accounts have none). Set it to false
#                                     ONLY for the few days before the app build that sends the field is in the stores,
#                                     then turn it back on.
# ---------------------------------------------------------------------------------------------------------------
CHANGE_PASSWORD_REQUIRE_CURRENT = os.getenv("CHANGE_PASSWORD_REQUIRE_CURRENT", "true").strip().lower() not in ("0", "false", "no", "off")

# P9 - Profile insights (user_profile/profile_insights.py): how long ProfileVisit rows (one per profile / viewer / day)
# are kept. `user_profile.tasks.prune_old_profile_visits` deletes older ones every day. The app shows 7 / 30 / 90 days.
PROFILE_VISIT_RETENTION_DAYS = int(os.getenv("PROFILE_VISIT_RETENTION_DAYS", "90"))


# =====================================================================================================================
# P-F5 - social learning (social_learning app). Full list + meaning: social_learning/conf.py
# =====================================================================================================================
SOCIAL_LEARNING_ENABLED = os.getenv("SOCIAL_LEARNING_ENABLED", "true").lower() not in ("0", "false", "off", "no")
SOCIAL_POMODORO_PARTICIPANTS_CAN_CONTROL = os.getenv("SOCIAL_POMODORO_PARTICIPANTS_CAN_CONTROL", "false").lower() in ("1", "true", "on", "yes")
SOCIAL_POMODORO_MIN_PRESENCE_FRACTION = float(os.getenv("SOCIAL_POMODORO_MIN_PRESENCE_FRACTION", "0.8"))
SOCIAL_POMODORO_DAILY_CREDIT_CAP = int(os.getenv("SOCIAL_POMODORO_DAILY_CREDIT_CAP", "16"))
SOCIAL_PARTNER_MAX_ACTIVE = int(os.getenv("SOCIAL_PARTNER_MAX_ACTIVE", "3"))
SOCIAL_PARTNER_MAX_PENDING_OUT = int(os.getenv("SOCIAL_PARTNER_MAX_PENDING_OUT", "5"))
SOCIAL_PARTNER_MANUAL_MAX_MINUTES = int(os.getenv("SOCIAL_PARTNER_MANUAL_MAX_MINUTES", "120"))
SOCIAL_PARTNER_AUTO_NUDGE = os.getenv("SOCIAL_PARTNER_AUTO_NUDGE", "true").lower() not in ("0", "false", "off", "no")
SOCIAL_SEASON_DAYS = int(os.getenv("SOCIAL_SEASON_DAYS", "28"))
SOCIAL_SEASON_AUTO_CREATE = os.getenv("SOCIAL_SEASON_AUTO_CREATE", "true").lower() not in ("0", "false", "off", "no")


# =====================================================================================================================
# P-F8 - spaced repetition (spaced_repetition app). Full list + meaning: spaced_repetition/conf.py
# =====================================================================================================================
SRS_ENABLED = os.getenv("SRS_ENABLED", "true").lower() not in ("0", "false", "off", "no")
SRS_NEW_PER_DAY = int(os.getenv("SRS_NEW_PER_DAY", "10"))
SRS_NEW_MIN_AGE_HOURS = int(os.getenv("SRS_NEW_MIN_AGE_HOURS", "0"))
SRS_MAX_QUEUE = int(os.getenv("SRS_MAX_QUEUE", "30"))
SRS_MAX_INTERVAL_DAYS = int(os.getenv("SRS_MAX_INTERVAL_DAYS", "365"))
SRS_RELEARN_MINUTES = int(os.getenv("SRS_RELEARN_MINUTES", "10"))
