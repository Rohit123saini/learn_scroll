"""P-B6 -- Content-Security-Policy for the HTML the backend serves itself (no extra package needed).

Only HTML responses get a policy; the JSON API is untouched. Two policies:

  * ADMIN  (paths under ``/<ADMIN_URL>``): scripts only from our own origin + inline <script> blocks that are listed
    by SHA-256 hash (the moderator dashboard has one). No inline event handlers, no remote scripts, no framing.
  * PUBLIC (SEO / share-link / legal / policy / unsubscribe pages): no scripts except our own inline blocks (hashed),
    JSON-LD is data and is not affected. Inline <style> and style="" are allowed (every page uses them),
    images may come from https:, nothing may frame the page, forms post only to ourselves.

Hashes are computed from the finished response body, so pages stay cacheable (a per-request nonce would not).
Swagger / ReDoc / schema pages are skipped (staff only, they load their own assets).

Settings (settings.py):
  CSP_PUBLIC_MODE   "enforce" (default) | "report-only" | "off"
  CSP_ADMIN_MODE    "report-only" (default, flip to "enforce" after one staging look) | "enforce" | "off"
  CSP_REPORT_URI    optional URL that receives violation reports
"""
import base64
import hashlib
import re

from django.conf import settings

_SCRIPT_RE = re.compile(rb"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.I | re.S)
_SKIP_PREFIXES = ("/swagger/", "/redoc/", "/api/schema/", "/tuitionclass/schema/")
_MODES = ("enforce", "report-only", "off")


def _mode(name, default):
    v = str(getattr(settings, name, default) or default).strip().lower()
    return v if v in _MODES else default


def admin_prefix():
    return "/" + str(getattr(settings, "ADMIN_URL", "admin/")).strip("/") + "/"


def inline_script_hashes(body: bytes):
    """sha256 source tokens for every inline, executable <script> block in ``body`` (src= and JSON-LD / data blocks skipped)."""
    out = []
    for m in _SCRIPT_RE.finditer(body):
        attrs = m.group("attrs").lower()
        if b"src=" in attrs:
            continue
        t = re.search(rb"type\s*=\s*[\"']?([^\"'\s>]+)", attrs)
        if t and t.group(1) not in (b"text/javascript", b"module", b"application/javascript"):
            continue  # application/ld+json etc. are never executed
        content = m.group("body")
        if not content.strip():
            continue
        digest = base64.b64encode(hashlib.sha256(content).digest()).decode()
        token = "'sha256-%s'" % digest
        if token not in out:
            out.append(token)
    return out


def build_policy(kind, script_hashes=()):
    scripts = " ".join(("'self'",) + tuple(script_hashes)) if kind == "admin" else " ".join(("'none'",) if not script_hashes else tuple(script_hashes))
    parts = [
        "default-src 'self'" if kind == "admin" else "default-src 'none'",
        "script-src " + scripts,
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:" if kind == "public" else "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'" if kind == "admin" else "connect-src 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
    uri = getattr(settings, "CSP_REPORT_URI", "")
    if uri:
        parts.append("report-uri " + uri)
    return "; ".join(parts)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            return self._apply(request, response)
        except Exception:  # a header must never turn a page into a 500
            return response

    def _apply(self, request, response):
        if getattr(response, "streaming", False) or "Content-Security-Policy" in response:
            return response
        if not str(response.get("Content-Type", "")).lower().startswith("text/html"):
            return response
        path = request.path
        if path.startswith(_SKIP_PREFIXES):
            return response
        kind, mode = ("admin", _mode("CSP_ADMIN_MODE", "report-only")) if path.startswith(admin_prefix()) \
            else ("public", _mode("CSP_PUBLIC_MODE", "enforce"))
        if mode == "off":
            return response
        policy = build_policy(kind, inline_script_hashes(response.content))
        response["Content-Security-Policy-Report-Only" if mode == "report-only" else "Content-Security-Policy"] = policy
        return response
