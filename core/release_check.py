"""X1 — release config checker (pure Python: NO Django import, so CI / a laptop can run it before anything boots).

    from core.release_check import check_env, parse_env_file
    findings = check_env(parse_env_file(open(".env.production").read()))

Every finding is `Finding(level, key, message)`; level is "error" (do not ship) or "warn" (ship only knowingly).
Used by `manage.py release_check` and tested by `core/tests_release_check.py`. Written by reading the code only - not run.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional

ERROR, WARN = "error", "warn"
_TRUE = ("1", "true", "yes", "on")
_PLACEHOLDER_RE = re.compile(r"(PUT_A_|PASSWORD@HOST|USER:PASSWORD|xxxxxxxx|<key>|<org>|changeme|example\.com|YOUR_|XXXXXXXXXX)", re.I)
_SHA256_RE = re.compile(r"^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$")
_IOS_APP_ID_RE = re.compile(r"^[A-Z0-9]{10}\.[A-Za-z0-9.\-]+$")


@dataclass(frozen=True)
class Finding:
    level: str
    key: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():5}] {self.key}: {self.message}"


def truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in _TRUE


def parse_env_file(text: str) -> Dict[str, str]:
    """KEY=VALUE lines. `# comment` lines and ` # trailing comment` are dropped; surrounding quotes are removed."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        val = val.strip()
        if val[:1] in ("'", '"') and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key.strip()] = val
    return out


def parse_num_proxies_safe(v: Optional[str]) -> Optional[int]:
    """int >= 0 or None (P-B8 helper that does not import Django-side modules)."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _csv(v: Optional[str]) -> List[str]:
    return [p.strip() for p in (v or "").split(",") if p.strip()]


def _is_placeholder(v: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(v or ""))


def check_env(env: Mapping[str, str], file_exists: Callable[[str], bool] = os.path.exists,
              default_fcm_path: str = "firebase-service-account.json") -> List[Finding]:
    """Rules for a PRODUCTION environment. Returns every finding (does not stop at the first)."""
    e = {k: (v or "").strip() for k, v in env.items()}
    f: List[Finding] = []

    def err(k, m): f.append(Finding(ERROR, k, m))
    def warn(k, m): f.append(Finding(WARN, k, m))
    def need(k, why=""):
        v = e.get(k, "")
        if not v:
            err(k, f"is empty. {why}".strip())
        elif _is_placeholder(v):
            err(k, "still has the placeholder value from env.production.example.")
        return v

    # --- core / Django -------------------------------------------------------------------------------
    if truthy(e.get("DEBUG")) or "DEBUG" not in e:
        err("DEBUG", "must be explicitly False in production (missing counts as unsafe).")

    sk = e.get("SECRET_KEY", "")
    if not sk or _is_placeholder(sk):
        err("SECRET_KEY", "missing or placeholder.")
    elif len(sk) < 50 or len(set(sk)) < 5 or sk.startswith("django-insecure"):
        err("SECRET_KEY", "must be >= 50 chars, >= 5 distinct chars, not 'django-insecure-...' (manage.py check --deploy W009).")

    jwt = e.get("JWT_SIGNING_KEY", "")
    if len(jwt) < 32 or _is_placeholder(jwt):
        err("JWT_SIGNING_KEY", "missing / placeholder / shorter than 32 chars (app refuses to boot).")
    elif jwt == sk:
        err("JWT_SIGNING_KEY", "must differ from SECRET_KEY.")

    hosts = _csv(e.get("ALLOWED_HOSTS"))
    if not hosts:
        err("ALLOWED_HOSTS", "is empty (app refuses to boot).")
    for h in hosts:
        if h == "*" or h.startswith("."):
            err("ALLOWED_HOSTS", f"'{h}' is a wildcard - list exact hosts.")
        elif "://" in h or "/" in h:
            err("ALLOWED_HOSTS", f"'{h}' must be a bare host name (no scheme / path).")
        elif h in ("localhost", "127.0.0.1", "0.0.0.0") or _is_placeholder(h):
            warn("ALLOWED_HOSTS", f"'{h}' looks like a dev / placeholder host.")
    origins = _csv(e.get("CSRF_TRUSTED_ORIGINS"))
    for o in origins:
        if not o.startswith("https://"):
            err("CSRF_TRUSTED_ORIGINS", f"'{o}' must start with https://")
    if hosts and not origins:
        warn("CSRF_TRUSTED_ORIGINS", "empty - browser POSTs (admin login, web forms) from your https host will fail with CSRF 403.")
    if e.get("SECURE_SSL_REDIRECT") and not truthy(e.get("SECURE_SSL_REDIRECT")):
        err("SECURE_SSL_REDIRECT", "must not be false in production (and the proxy must send X-Forwarded-Proto).")

    # --- data stores ---------------------------------------------------------------------------------
    db = e.get("DATABASE_URL", "")
    if not db:
        err("DATABASE_URL", "is empty - settings.py refuses to boot with DEBUG=False and no DATABASE_URL (P-C1: SQLite is dev-only).")
    elif not db.startswith(("postgres://", "postgresql://")):
        err("DATABASE_URL", "must be a PostgreSQL URL (postgres://...).")
    elif _is_placeholder(db):
        err("DATABASE_URL", "still has the placeholder USER:PASSWORD@HOST.")
    for k in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        v = e.get(k, "")
        if not v:
            err(k, "is empty - cache / channels / Celery would fall back to in-memory (breaks with >1 worker).")
        elif not v.startswith(("redis://", "rediss://")):
            err(k, "must be redis:// or rediss://")
        elif _is_placeholder(v) or "://HOST" in v:  # deploy/env.production.example ships `redis://HOST:6379/0`
            err(k, "still has the placeholder HOST.")

    # --- media: S3 + CDN -----------------------------------------------------------------------------
    if truthy(e.get("USE_S3_STORAGE")):
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_STORAGE_BUCKET_NAME"):
            need(k, "USE_S3_STORAGE=true needs it.")
    else:
        warn("USE_S3_STORAGE", "false - uploads live on the server disk (fine for one box, lost on a rebuild; use S3 for scale).")
    if truthy(e.get("MEDIA_CDN_ENABLED")):
        if not e.get("MEDIA_CDN_URL", "").startswith("https://"):
            err("MEDIA_CDN_URL", "MEDIA_CDN_ENABLED=true needs an https:// MEDIA_CDN_URL (app refuses to boot otherwise).")
    else:
        warn("MEDIA_CDN_ENABLED", "false - media is served from the origin without CDN caching.")

    # --- push: FCM + APNs ----------------------------------------------------------------------------
    cred = e.get("FIREBASE_CREDENTIALS_PATH") or e.get("FCM_SERVICE_ACCOUNT_JSON_PATH") or default_fcm_path
    if not file_exists(cred):
        err("FIREBASE_CREDENTIALS_PATH", f"service-account JSON not found at '{cred}' - no push (and no call ring) will be sent.")
    apns = ("APNS_VOIP_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID")
    have = [k for k in apns if e.get(k)]
    if not have:
        warn("APNS_VOIP_KEY_PATH", "APNs VoIP not configured - iOS killed-state calls fall back to FCM only (unreliable).")
    else:
        for k in apns:
            if not e.get(k) or _is_placeholder(e.get(k, "")):
                err(k, "APNs is partly configured - all four APNS_* values are needed.")
        if e.get("APNS_VOIP_KEY_PATH") and not file_exists(e["APNS_VOIP_KEY_PATH"]):
            err("APNS_VOIP_KEY_PATH", f"file not found at '{e['APNS_VOIP_KEY_PATH']}'.")
    if truthy(e.get("APNS_USE_SANDBOX")):
        err("APNS_USE_SANDBOX", "must be false for TestFlight / App Store builds.")

    # --- LiveKit -------------------------------------------------------------------------------------
    need("LIVEKIT_API_KEY", "calls / study rooms / live classes need it.")
    need("LIVEKIT_API_SECRET")
    ws = e.get("LIVEKIT_WS_URL", "")
    if not ws.startswith("wss://") or _is_placeholder(ws) or re.search(r"//(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", ws):
        err("LIVEKIT_WS_URL", "must be wss://<your public livekit domain> (not ws://, localhost, private IP or the example domain).")

    # --- payments ------------------------------------------------------------------------------------
    rk = need("RAZORPAY_KEY_ID")
    if rk.startswith("rzp_test_"):
        err("RAZORPAY_KEY_ID", "is a TEST key (rzp_test_...) - live money needs rzp_live_...")
    need("RAZORPAY_KEY_SECRET")
    need("RAZORPAY_WEBHOOK_SECRET", "without it payment webhooks are rejected and coins never credit.")

    # --- observability -------------------------------------------------------------------------------
    dsn = e.get("SENTRY_DSN", "")
    if not dsn:
        warn("SENTRY_DSN", "empty - no crash / Celery failure alerts.")
    elif not dsn.startswith("https://") or _is_placeholder(dsn):
        err("SENTRY_DSN", "is not a real https DSN.")
    rate = e.get("SENTRY_TRACES_SAMPLE_RATE", "")
    if rate:
        try:
            if not 0.0 <= float(rate) <= 1.0:
                err("SENTRY_TRACES_SAMPLE_RATE", "must be between 0 and 1.")
            elif float(rate) > 0.5:
                warn("SENTRY_TRACES_SAMPLE_RATE", "above 0.5 gets expensive at scale (0.05-0.2 is typical).")
        except ValueError:
            err("SENTRY_TRACES_SAMPLE_RATE", "is not a number.")
    if not e.get("HEALTHCHECK_TOKEN"):
        warn("HEALTHCHECK_TOKEN", "empty - /healthz/deep/ is disabled (Celery / backup / disk probes off).")

    # --- security / staff ----------------------------------------------------------------------------
    if truthy(e.get("WS_ALLOW_QUERY_TOKEN")):  # P-B2
        err("WS_ALLOW_QUERY_TOKEN", "true - WebSocket sockets accept the JWT in the URL (?token=) and it ends up in proxy / access logs. "
                                    "Unset it or set false (clients use the one-time ticket from /login/auth/ws-ticket/).")
    if truthy(e.get("API_DOCS_PUBLIC")):  # P-B1
        err("API_DOCS_PUBLIC", "true - /swagger/, /redoc/ and /api/schema/ would show every endpoint to anyone. "
                               "Leave it unset / false in production (staff can still open the docs after logging in at /admin/).")
    if "ADMIN_REQUIRE_2FA" in e and not truthy(e.get("ADMIN_REQUIRE_2FA")):
        warn("ADMIN_REQUIRE_2FA", "false - staff can sign in without a second factor.")
    if (e.get("ADMIN_URL") or "admin").strip().strip("/").lower() == "admin":  # P-B6
        warn("ADMIN_URL", "unset or 'admin' - bots probe /admin/ all day. Set a private path, e.g. ADMIN_URL=ops-9f3k2/ (2FA + lockout stay on either way).")
    # --- P-B8: admin IP allow-list (common/admin_ip_allowlist.py). Empty = off, which is allowed. -----------------------
    _al = (e.get("ADMIN_IP_ALLOWLIST") or "").strip()
    if _al:
        from common.admin_ip_allowlist import has_open_entry, parse_allowlist, parse_num_proxies

        _nets, _bad = parse_allowlist(_al)
        if _bad:
            err("ADMIN_IP_ALLOWLIST", f"invalid entr{'y' if len(_bad) == 1 else 'ies'} {', '.join(_bad)} - use IPs / CIDRs like "
                                      "203.0.113.7 or 198.51.100.0/24. A list with no valid entry refuses EVERYONE (admin unreachable).")
        elif has_open_entry(_nets):
            warn("ADMIN_IP_ALLOWLIST", "contains a /0 entry (0.0.0.0/0 or ::/0) - every address is allowed, the list does nothing.")
        _px = [parse_num_proxies(e.get(k)) for k in ("ADMIN_NUM_PROXIES", "NUM_PROXIES")]
        if all(p is None for p in _px):
            warn("ADMIN_IP_ALLOWLIST", "set but neither ADMIN_NUM_PROXIES nor NUM_PROXIES is - only the direct connection address is "
                                       "checked. Behind nginx / a load balancer that is the PROXY, so the list would match the proxy, "
                                       "not the visitor. Set ADMIN_NUM_PROXIES=1 (nginx) and test with `manage.py admin_ip_check`.")
        for _k in ("ADMIN_NUM_PROXIES",):
            if (e.get(_k) or "").strip() and parse_num_proxies(e.get(_k)) is None:
                err(_k, f"'{e.get(_k)}' is not a whole number (>= 0).")
    elif (e.get("ADMIN_NUM_PROXIES") or "").strip() and parse_num_proxies_safe(e.get("ADMIN_NUM_PROXIES")) is None:
        err("ADMIN_NUM_PROXIES", f"'{e.get('ADMIN_NUM_PROXIES')}' is not a whole number (>= 0).")
    # --- P-C3: Sentry sampling, DB connection reuse, Celery limits, settings profile -------------------------------
    _sr = (e.get("SENTRY_TRACES_SAMPLE_RATE") or "").strip()
    if _sr:
        try:
            _srf = float(_sr)
            if not 0.0 <= _srf <= 1.0:
                err("SENTRY_TRACES_SAMPLE_RATE", f"{_sr} is outside 0..1.")
            elif _srf > 0.5:
                warn("SENTRY_TRACES_SAMPLE_RATE", f"{_sr} - tracing more than half of all requests burns Sentry quota; 0.1 is the default.")
        except ValueError:
            err("SENTRY_TRACES_SAMPLE_RATE", f"'{_sr}' is not a number (settings.py would silently use 0.1).")
    for _k in ("DB_CONN_MAX_AGE", "CELERY_TASK_SOFT_TIME_LIMIT", "CELERY_TASK_TIME_LIMIT", "CELERY_FAIL_ALERT_THRESHOLD", "BEAT_LOCK_TTL"):
        _v = (e.get(_k) or "").strip()
        if _v and (not _v.lstrip("-").isdigit() or int(_v) < 0):
            err(_k, f"'{_v}' is not a whole number >= 0 (settings.py would silently use the default).")
    if truthy(e.get("DB_DISABLE_SERVER_SIDE_CURSORS")) is False and "6432" in (e.get("DATABASE_URL") or ""):
        warn("DB_DISABLE_SERVER_SIDE_CURSORS", "DATABASE_URL looks like pgbouncer (port 6432) - set DB_DISABLE_SERVER_SIDE_CURSORS=true, .iterator() breaks in transaction pooling.")
    # --- P-C8: read replica + API cache default ---------------------------------------------------------------
    if truthy(e.get("DB_REPLICA_ENABLED")):
        _ru = (e.get("DB_REPLICA_URL") or "").strip()
        if not _ru.startswith(("postgres://", "postgresql://")):
            err("DB_REPLICA_URL", "DB_REPLICA_ENABLED=true but DB_REPLICA_URL is missing / not postgres:// (settings.py refuses to boot).")
        elif _ru == (e.get("DATABASE_URL") or "").strip():
            warn("DB_REPLICA_URL", "same as DATABASE_URL - routing adds complexity and no capacity. Point it at a real replica or disable.")
        if not (e.get("DB_REPLICA_PATH_PREFIXES") or "").strip():
            warn("DB_REPLICA_PATH_PREFIXES", "empty - the replica is configured but no endpoint uses it. List the read-only prefixes, e.g. /post/feed/,/post/reels/")
    for _k in ("DB_REPLICA_PIN_SECONDS", "DB_REPLICA_DOWN_SECONDS"):
        _v = (e.get(_k) or "").strip()
        if _v and (not _v.isdigit() or int(_v) < 1):
            err(_k, f"'{_v}' is not a whole number >= 1 (settings.py would silently use the default).")
    if "API_CACHE_DEFAULT_PRIVATE" in e and not truthy(e.get("API_CACHE_DEFAULT_PRIVATE")):
        warn("API_CACHE_DEFAULT_PRIVATE", "false - API responses without their own Cache-Control carry no header; a 'cache everything' CDN rule could share them.")
    if (e.get("DJANGO_SETTINGS_MODULE") or "").endswith("settings_dev"):
        err("DJANGO_SETTINGS_MODULE", "LearnScroll.settings_dev in a production env file.")
    if not truthy(e.get("BEAT_SINGLE_INSTANCE_GUARD") or "true"):
        warn("BEAT_SINGLE_INSTANCE_GUARD", "false - nothing stops a second celery beat from firing every periodic task twice.")
    for _k in ("CSP_PUBLIC_MODE", "CSP_ADMIN_MODE"):  # P-B6
        _v = (e.get(_k) or "").strip().lower()
        if _v and _v not in ("enforce", "report-only", "off"):
            err(_k, f"'{_v}' is not one of enforce / report-only / off.")
        elif _v == "off":
            warn(_k, "off - no Content-Security-Policy header on this surface.")
    if (e.get("CSP_ADMIN_MODE") or "report-only").strip().lower() == "report-only":
        warn("CSP_ADMIN_MODE", "report-only - the admin CSP only reports. Check the browser console once on staging, then set enforce.")

    # --- legal / store -------------------------------------------------------------------------------
    se = e.get("SUPPORT_EMAIL", "")
    if not se or _is_placeholder(se):
        err("SUPPORT_EMAIL", "missing or example.com - Play requires a real address on /legal/delete-account/.")
    for k in ("GRIEVANCE_OFFICER_NAME", "GRIEVANCE_OFFICER_EMAIL"):
        if not e.get(k):
            warn(k, "empty - privacy policy page and copyrights check W001/W002 want it.")
    if truthy(e.get("LEGAL_PAGES_DRAFT_BANNER")) or "LEGAL_PAGES_DRAFT_BANNER" not in e:
        warn("LEGAL_PAGES_DRAFT_BANNER", "legal pages still show the DRAFT banner - set false after legal review.")

    # --- deep links ----------------------------------------------------------------------------------
    if not e.get("ANDROID_APP_PACKAGE"):
        warn("ANDROID_APP_PACKAGE", "empty - /.well-known/assetlinks.json answers 404, https links will not open the app.")
    prints = _csv(e.get("ANDROID_SHA256_CERT_FINGERPRINTS"))
    if not prints:
        warn("ANDROID_SHA256_CERT_FINGERPRINTS", "empty - add the PLAY APP SIGNING key's SHA-256 (and your upload key if you sideload).")
    for p in prints:
        if not _SHA256_RE.match(p):
            err("ANDROID_SHA256_CERT_FINGERPRINTS", f"'{p}' is not a SHA-256 fingerprint (32 hex pairs separated by ':').")
    ios = e.get("IOS_APP_ID", "")
    if not ios:
        warn("IOS_APP_ID", "empty - apple-app-site-association answers 404, Universal Links will not work.")
    elif not _IOS_APP_ID_RE.match(ios):
        err("IOS_APP_ID", "must look like TEAMID.com.bundle.id (10 upper-case letters / digits, a dot, the bundle id).")
    if not e.get("PLAY_STORE_URL"):
        warn("PLAY_STORE_URL", "empty - fallback page and app-update prompt have no store link.")
    if not e.get("GOOGLE_CLIENT_ID") and not e.get("GOOGLE_CLIENT_IDS"):
        warn("GOOGLE_CLIENT_ID", "empty (and GOOGLE_CLIENT_IDS too) - Google sign-in on the server cannot verify ID tokens.")

    # --- backups -------------------------------------------------------------------------------------
    if not e.get("BACKUP_REMOTE"):
        warn("BACKUP_REMOTE", "empty - the only backup lives on the same server as the database.")

    return f


def summarize(findings: List[Finding]) -> Dict[str, int]:
    return {"error": sum(1 for x in findings if x.level == ERROR), "warn": sum(1 for x in findings if x.level == WARN)}


def exit_code(findings: List[Finding], fail_on: str = ERROR) -> int:
    """0 = fine, 1 = something at or above `fail_on` ("error" or "warn")."""
    s = summarize(findings)
    return 1 if s["error"] or (fail_on == WARN and s["warn"]) else 0
