"""
P-B8 - optional IP allow-list for the Django admin (where every copyright takedown / strike / termination happens).

    ADMIN_IP_ALLOWLIST   comma / space separated IPs or CIDRs, e.g. "203.0.113.7, 198.51.100.0/24, 2001:db8::/32".
                         EMPTY (default) = feature OFF, nothing changes.
    ADMIN_NUM_PROXIES    how many trusted reverse proxies sit in front of Django (nginx = 1, CDN + nginx = 2). Falls back
                         to NUM_PROXIES (the DRF setting) when unset. Unset / unknown = only REMOTE_ADDR is trusted.

How the client IP is found (the one thing that can go wrong behind a proxy):
  * no proxy count set   -> REMOTE_ADDR only. X-Forwarded-For is IGNORED: anyone can type that header.
  * proxy count N >= 1   -> the N-th address from the RIGHT of X-Forwarded-For (each trusted proxy appends the address it
                            saw; everything left of that is client-supplied and not trusted). Same rule DRF's NUM_PROXIES
                            uses for throttling, so the login throttles and this list agree.
  * proxy count 0        -> REMOTE_ADDR.

Rules:
  * Applies to the admin path only (/<ADMIN_URL>...), login page included. Other URLs are never touched.
  * A blocked request gets a plain 404 (not 403): the private admin path is not confirmed to a scanner.
  * Fails CLOSED: if the list is set but NO entry is valid (typo), nobody is let in (never "off by accident").
  * The IP check is IN ADDITION to 2FA (ADMIN_REQUIRE_2FA), password policy and the login lockout, never instead of them.
  * Locked yourself out? Unset ADMIN_IP_ALLOWLIST and restart. `manage.py admin_ip_check <ip>` answers "would this IP
    get in?" with the live settings.

The top of this module is pure Python (no Django import) so core/release_check.py can reuse parse_allowlist().
"""
from __future__ import annotations

import ipaddress
import logging
import re
from functools import lru_cache
from typing import Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("security.admin")

_SPLIT = re.compile(r"[,\s;]+")


def parse_allowlist(raw: Optional[str]) -> Tuple[list, list]:
    """-> (networks, invalid_entries). "10.0.0.5" = /32, "10.0.0.5/24" is accepted (host bits are dropped)."""
    nets, bad = [], []
    for part in _SPLIT.split((raw or "").strip()):
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            bad.append(part)
    return nets, bad


def is_enabled(raw: Optional[str]) -> bool:
    """True as soon as the setting has ANY text (even if all of it is invalid: then everything is refused)."""
    return bool((raw or "").strip())


def has_open_entry(networks: Sequence) -> bool:
    """A /0 entry (0.0.0.0/0 or ::/0) lets everybody in - the list would do nothing."""
    return any(n.prefixlen == 0 for n in networks)


def parse_num_proxies(value) -> Optional[int]:
    """"" / None / not a number / negative -> None (= trust only REMOTE_ADDR)."""
    if value is None:
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def client_ip(meta: Mapping, num_proxies: Optional[int]) -> str:
    remote = (meta.get("REMOTE_ADDR") or "").strip()
    if not num_proxies:  # None or 0
        return remote
    xff = (meta.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if not xff:
        return remote
    addrs = [a.strip() for a in xff.split(",") if a.strip()]
    if not addrs:
        return remote
    return addrs[-min(num_proxies, len(addrs))]


def _normalise(ip: str):
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped is not None:  # "::ffff:1.2.3.4" is 1.2.3.4
        return addr.ipv4_mapped
    return addr


def is_allowed(ip: str, networks: Sequence) -> bool:
    addr = _normalise(ip)
    if addr is None:
        return False
    return any(addr.version == n.version and addr in n for n in networks)


@lru_cache(maxsize=16)
def _parsed(raw: str):
    return parse_allowlist(raw)


def settings_num_proxies(settings) -> Optional[int]:
    own = parse_num_proxies(getattr(settings, "ADMIN_NUM_PROXIES", None))
    if own is not None:
        return own
    return parse_num_proxies((getattr(settings, "REST_FRAMEWORK", None) or {}).get("NUM_PROXIES"))


def decide(settings, meta: Mapping) -> Tuple[bool, str, str]:
    """(allowed, ip, reason) for one request with the live settings. reason: "off" | "allowed" | "not_listed" | "bad_list"."""
    raw = getattr(settings, "ADMIN_IP_ALLOWLIST", "") or ""
    if not is_enabled(raw):
        return True, "", "off"
    ip = client_ip(meta, settings_num_proxies(settings))
    nets, _bad = _parsed(raw)
    if not nets:
        return False, ip, "bad_list"
    return (True, ip, "allowed") if is_allowed(ip, nets) else (False, ip, "not_listed")


class AdminIPAllowlistMiddleware:
    """Put right after SecurityMiddleware (before sessions / auth: a blocked IP costs no DB query)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        raw = getattr(settings, "ADMIN_IP_ALLOWLIST", "") or ""
        if is_enabled(raw):
            prefix = "/" + (getattr(settings, "ADMIN_URL", "admin/") or "admin/").lstrip("/")
            path = request.path_info or ""
            if path == prefix.rstrip("/") or path.startswith(prefix):
                allowed, ip, reason = decide(settings, request.META)
                if not allowed:
                    logger.warning("admin blocked by IP allow-list: ip=%s reason=%s method=%s", ip or "?", reason,
                                   request.method)
                    from django.http import HttpResponseNotFound

                    return HttpResponseNotFound(b"", content_type="text/plain")
        return self.get_response(request)
