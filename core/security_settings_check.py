"""X6 -- verify the LIVE Django settings (CSRF / hosts / cookies / CORS / middleware) the way an attacker would look.

    from core.security_settings_check import check_settings, settings_snapshot
    findings = check_settings(settings_snapshot(django_settings))

``core/release_check.py`` (X1) checks the ENVIRONMENT FILE before boot. This module checks the SETTINGS THAT ACTUALLY
LOADED (an env value can be ignored, overridden further down settings.py, or never read at all) -- that is the
difference between "the .env says false" and "the running server really is false".

Pure Python: no Django import, so it is unit-tested with plain dicts (core/tests_security_settings_check.py).
``manage.py check_security_settings`` runs it on the real settings. Finding / ERROR / WARN are the X1 ones.
Written by reading the code only - not run.
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from core.release_check import ERROR, WARN, Finding

# Names read from django.conf.settings by settings_snapshot().
SNAPSHOT_KEYS = (
    "DEBUG",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "CSRF_COOKIE_SECURE",
    "CSRF_COOKIE_SAMESITE",
    "CSRF_USE_SESSIONS",
    "SESSION_COOKIE_SECURE",
    "SESSION_COOKIE_HTTPONLY",
    "SESSION_COOKIE_SAMESITE",
    "SECURE_SSL_REDIRECT",
    "SECURE_PROXY_SSL_HEADER",
    "SECURE_HSTS_SECONDS",
    "SECURE_HSTS_INCLUDE_SUBDOMAINS",
    "SECURE_CONTENT_TYPE_NOSNIFF",
    "SECURE_REFERRER_POLICY",
    "X_FRAME_OPTIONS",
    "CORS_ALLOW_ALL_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "CORS_ALLOW_CREDENTIALS",
    "MIDDLEWARE",
    "REST_FRAMEWORK",
    "USE_X_FORWARDED_HOST",
    "WS_ALLOW_QUERY_TOKEN",
    "DATABASES",  # P-C1: engine of the default DB
    "SPECTACULAR_SETTINGS",  # P-B1: SERVE_PERMISSIONS of /swagger/, /redoc/, /api/schema/
    "ADMIN_IP_ALLOWLIST",  # P-B8
    "ADMIN_REQUIRE_2FA",  # P-B8: the live value, not the env text
)

CSRF_MW = "django.middleware.csrf.CsrfViewMiddleware"
SECURITY_MW = "django.middleware.security.SecurityMiddleware"
CLICKJACK_MW = "django.middleware.clickjacking.XFrameOptionsMiddleware"
CORS_MW = "corsheaders.middleware.CorsMiddleware"
SESSION_AUTH = "rest_framework.authentication.SessionAuthentication"

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]*]+$")  # "*" / ".example.com" are judged separately (prod only)


def settings_snapshot(django_settings: Any) -> dict:
    """The keys above from a Django settings object (missing ones are simply absent from the dict)."""
    return {k: getattr(django_settings, k) for k in SNAPSHOT_KEYS if hasattr(django_settings, k)}


def _seq(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def _host_of(origin: str) -> str:
    return (urlparse(origin).hostname or "").lower()


def _host_allowed(host: str, allowed: Sequence[str]) -> bool:
    for a in allowed:
        a = a.lower()
        if a == "*" or a == host:
            return True
        if a.startswith(".") and (host == a[1:] or host.endswith(a)):
            return True
    return False


def check_settings(cfg: Mapping[str, Any], *, production: Optional[bool] = None) -> List[Finding]:
    """Every finding (does not stop at the first). ``production`` defaults to ``not cfg['DEBUG']``.

    In a dev run (DEBUG on) cookie / HTTPS rules are downgraded to warnings: a laptop has no TLS.
    """
    f: List[Finding] = []
    debug = bool(cfg.get("DEBUG", False))
    prod = (not debug) if production is None else production

    def err(k, m):
        f.append(Finding(ERROR if prod else WARN, k, m))

    def hard(k, m):  # wrong in every environment
        f.append(Finding(ERROR, k, m))

    def warn(k, m):
        f.append(Finding(WARN, k, m))

    # --- host header ----------------------------------------------------------------------------------------
    hosts = _seq(cfg.get("ALLOWED_HOSTS"))
    if prod and not hosts:
        hard("ALLOWED_HOSTS", "is empty in production - Django would refuse every request (or, with DEBUG, accept any).")
    for h in hosts:
        if prod and (h == "*" or h.startswith(".")):
            hard("ALLOWED_HOSTS", f"'{h}' is a wildcard - a forged Host header (password-reset / absolute-URL poisoning) would be accepted. List exact hosts.")
        elif "://" in h or "/" in h or not _HOST_RE.match(h):
            hard("ALLOWED_HOSTS", f"'{h}' must be a bare host name (no scheme, path or spaces).")
    if cfg.get("USE_X_FORWARDED_HOST"):
        warn("USE_X_FORWARDED_HOST", "true - the proxy MUST overwrite X-Forwarded-Host, otherwise a client can choose the Host Django trusts.")

    # --- WebSocket token (X6b) --------------------------------------------------------------------------------
    # P-B2: was a warning while old app builds were in the field; the default is now false and `true` is a hard FAIL.
    if prod and cfg.get("WS_ALLOW_QUERY_TOKEN") is True:
        hard("WS_ALLOW_QUERY_TOKEN", "true - sockets accept the JWT in the URL (?token=), which lands in proxy / access logs. "
                                     "Set WS_ALLOW_QUERY_TOKEN=false (default); clients use /login/auth/ws-ticket/.")

    # --- CSRF -----------------------------------------------------------------------------------------------
    mw = _seq(cfg.get("MIDDLEWARE"))
    if CSRF_MW not in mw:
        hard("MIDDLEWARE", f"{CSRF_MW} is missing - cookie-authenticated POSTs (admin, web forms) have no CSRF protection.")
    if SECURITY_MW not in mw:
        hard("MIDDLEWARE", f"{SECURITY_MW} is missing - HTTPS redirect, HSTS and nosniff headers are not applied.")
    elif [m for m in mw[: mw.index(SECURITY_MW)] if m != CORS_MW]:
        warn("MIDDLEWARE", "SecurityMiddleware should be at the very top (CorsMiddleware may sit above it).")
    if CLICKJACK_MW not in mw:
        warn("MIDDLEWARE", f"{CLICKJACK_MW} is missing - pages can be framed (clickjacking).")
    if CORS_MW in mw and mw.index(CORS_MW) > (mw.index(CSRF_MW) if CSRF_MW in mw else 10**6):
        warn("MIDDLEWARE", "CorsMiddleware should come before CsrfViewMiddleware.")

    origins = _seq(cfg.get("CSRF_TRUSTED_ORIGINS"))
    for o in origins:
        parsed = urlparse(o)
        if not parsed.scheme or not parsed.hostname:
            hard("CSRF_TRUSTED_ORIGINS", f"'{o}' must be a full origin like https://api.example.com (scheme + host, no path).")
            continue
        if parsed.path not in ("", "/") or parsed.query:
            hard("CSRF_TRUSTED_ORIGINS", f"'{o}' must not carry a path or query.")
        if prod and parsed.scheme != "https":
            err("CSRF_TRUSTED_ORIGINS", f"'{o}' is not https - a network attacker can forge an http origin.")
        if parsed.hostname.startswith("*") and prod:
            # Django allows *.example.com; a bare "*" host is never valid, a subdomain wildcard is a conscious risk.
            if parsed.hostname == "*":
                hard("CSRF_TRUSTED_ORIGINS", f"'{o}' trusts every site.")
            else:
                warn("CSRF_TRUSTED_ORIGINS", f"'{o}' trusts every sub-domain - one hijacked sub-domain can then forge requests.")
    if prod and hosts and not origins:
        warn("CSRF_TRUSTED_ORIGINS", "empty - https POSTs from your own web pages (admin login) fail with CSRF 403 behind a TLS proxy.")
    if prod and origins and hosts and "*" not in hosts:
        for o in origins:
            h = _host_of(o)
            if h and not h.startswith("*") and not _host_allowed(h, hosts):
                warn("CSRF_TRUSTED_ORIGINS", f"'{o}' is not covered by ALLOWED_HOSTS - one of the two lists is stale.")

    if not cfg.get("CSRF_COOKIE_SECURE", False):
        err("CSRF_COOKIE_SECURE", "false - the CSRF cookie would travel over plain http.")
    if not cfg.get("SESSION_COOKIE_SECURE", False):
        err("SESSION_COOKIE_SECURE", "false - the session cookie would travel over plain http.")
    if not cfg.get("SESSION_COOKIE_HTTPONLY", True):
        hard("SESSION_COOKIE_HTTPONLY", "false - JavaScript (any XSS) could read the session cookie.")
    for name in ("SESSION_COOKIE_SAMESITE", "CSRF_COOKIE_SAMESITE"):
        v = cfg.get(name, "Lax")
        if v in (None, False, "None", "none"):
            err(name, "None - the cookie is sent on cross-site requests; use Lax (or Strict).")

    # --- rest framework: session auth means CSRF is enforced; JWT only means it is not needed -----------------
    rf = cfg.get("REST_FRAMEWORK") or {}
    auth = _seq(rf.get("DEFAULT_AUTHENTICATION_CLASSES"))
    if SESSION_AUTH in auth and CSRF_MW not in mw:
        hard("REST_FRAMEWORK", "SessionAuthentication is on but CsrfViewMiddleware is off - cookie-auth API calls are forgeable.")
    perms = _seq(rf.get("DEFAULT_PERMISSION_CLASSES"))
    if "rest_framework.permissions.AllowAny" in perms:
        hard("REST_FRAMEWORK", "DEFAULT_PERMISSION_CLASSES contains AllowAny - a view that forgets permission_classes becomes public.")
    if not perms:
        warn("REST_FRAMEWORK", "DEFAULT_PERMISSION_CLASSES is empty (DRF then defaults to AllowAny).")

    # --- database engine (P-C1) ---------------------------------------------------------------------------------
    # Only judged when the snapshot carries DATABASES. settings.py already refuses to boot without DATABASE_URL, so this
    # catches the other route: a DATABASES override further down / a settings module that hard-codes SQLite.
    if prod and "DATABASES" in cfg:
        for alias, db in (cfg.get("DATABASES") or {}).items():
            engine = str((db or {}).get("ENGINE", ""))
            if engine.endswith("sqlite3"):
                hard("DATABASES", f"'{alias}' uses SQLite in production - single writer, 'database is locked' under load. Use PostgreSQL (DATABASE_URL).")

    # --- API docs (P-B1) -------------------------------------------------------------------------------------
    # Only judged when the snapshot carries SPECTACULAR_SETTINGS. drf-spectacular's own default (key missing) and an
    # empty list both mean "everyone", so they count as open. In a dev run (DEBUG on) this is only a warning.
    if "SPECTACULAR_SETTINGS" in cfg:
        serve = _seq((cfg.get("SPECTACULAR_SETTINGS") or {}).get("SERVE_PERMISSIONS"))
        if not serve or any(p.endswith(".AllowAny") for p in serve):
            err("SPECTACULAR_SETTINGS", "SERVE_PERMISSIONS lets anonymous users open /swagger/, /redoc/ and /api/schema/ "
                                        "(the whole API map). Unset API_DOCS_PUBLIC (default false) so only staff can.")

    # --- CSP + admin URL (P-B6) --------------------------------------------------------------------------------
    mw = _seq(cfg.get("MIDDLEWARE")) if "MIDDLEWARE" in cfg else None
    if mw is not None and "common.csp.ContentSecurityPolicyMiddleware" not in mw:
        err("MIDDLEWARE", "common.csp.ContentSecurityPolicyMiddleware is missing - admin and public HTML pages get no Content-Security-Policy.")
    if "CSP_PUBLIC_MODE" in cfg and str(cfg.get("CSP_PUBLIC_MODE")).lower() == "off":
        err("CSP_PUBLIC_MODE", "off - public HTML pages are served without a Content-Security-Policy.")
    if prod and "ADMIN_URL" in cfg and str(cfg.get("ADMIN_URL")).strip("/").lower() == "admin":
        warn("ADMIN_URL", "still 'admin/' - set ADMIN_URL to a private path in production.")

    # --- admin IP allow-list + 2FA (P-B8) ---------------------------------------------------------------------
    if mw is not None and str(cfg.get("ADMIN_IP_ALLOWLIST") or "").strip() \
            and "common.admin_ip_allowlist.AdminIPAllowlistMiddleware" not in mw:
        err("MIDDLEWARE", "ADMIN_IP_ALLOWLIST is set but common.admin_ip_allowlist.AdminIPAllowlistMiddleware is missing - "
                          "the list is NOT enforced.")
    if prod and "ADMIN_REQUIRE_2FA" in cfg and not cfg.get("ADMIN_REQUIRE_2FA"):
        err("ADMIN_REQUIRE_2FA", "false in production - staff (copyright takedowns, strikes, terminations) can sign in to the admin "
                                 "with a password only.")

    # --- CORS -----------------------------------------------------------------------------------------------
    cors_all = bool(cfg.get("CORS_ALLOW_ALL_ORIGINS", False))
    cors_origins = _seq(cfg.get("CORS_ALLOWED_ORIGINS"))
    if cors_all:
        err("CORS_ALLOW_ALL_ORIGINS", "true - any website can call the API from a visitor's browser.")
        if cfg.get("CORS_ALLOW_CREDENTIALS"):
            hard("CORS_ALLOW_CREDENTIALS", "true together with allow-all origins - credentialed cross-site reads for every site.")
    for o in cors_origins:
        if o == "*" or o == "null":
            hard("CORS_ALLOWED_ORIGINS", f"'{o}' lets every site (or sandboxed iframes) read responses.")
        elif prod and not o.startswith("https://"):
            err("CORS_ALLOWED_ORIGINS", f"'{o}' is not https.")

    # --- transport headers ----------------------------------------------------------------------------------
    if not cfg.get("SECURE_SSL_REDIRECT", False):
        err("SECURE_SSL_REDIRECT", "false - http requests are served instead of redirected (the proxy must send X-Forwarded-Proto).")
    if cfg.get("SECURE_SSL_REDIRECT") and not cfg.get("SECURE_PROXY_SSL_HEADER"):
        warn("SECURE_PROXY_SSL_HEADER", "unset while SECURE_SSL_REDIRECT is on - behind a TLS-terminating proxy this loops forever.")
    hsts = int(cfg.get("SECURE_HSTS_SECONDS", 0) or 0)
    if hsts <= 0:
        err("SECURE_HSTS_SECONDS", "0 - browsers are never told to insist on https.")
    elif hsts < 60 * 60 * 24 * 30:
        warn("SECURE_HSTS_SECONDS", f"{hsts}s is under 30 days.")
    if not cfg.get("SECURE_CONTENT_TYPE_NOSNIFF", True):
        warn("SECURE_CONTENT_TYPE_NOSNIFF", "false - browsers may sniff uploads into executable types.")
    xfo = str(cfg.get("X_FRAME_OPTIONS", "DENY")).upper()
    if xfo not in ("DENY", "SAMEORIGIN"):
        warn("X_FRAME_OPTIONS", f"'{xfo}' - use DENY (or SAMEORIGIN).")
    if not cfg.get("SECURE_REFERRER_POLICY"):
        warn("SECURE_REFERRER_POLICY", "unset - full URLs (with ids / tokens in the path) can leak in the Referer header.")

    if debug:
        # DEBUG itself: an error only when the caller says this is production (--production / DJANGO_ENV).
        if production:
            hard("DEBUG", "true in production - stack traces, settings and SQL are shown to anyone who triggers an error.")
    return f
