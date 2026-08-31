# message/link_preview.py
#
# 🔥 NAYA (ADVANCED FEATURE) — Link Previews.
#
# Jab koi text message me URL bhejta hai, WhatsApp/Telegram/iMessage jaisa
# ek chhota card dikhta hai (title, description, thumbnail). Ye module wahi
# karta hai: message text se pehla URL nikalta hai, us page ka OpenGraph
# (`og:title`, `og:description`, `og:image`) data fetch karta hai, aur
# result `Message.meta['link_preview']` me store kar deta hai — koi naya
# migration NAHI chahiye (`meta` pehle se hi JSONField hai).
#
# Kaam HAMESHA async chalta hai (Celery task, `tasks.py` me
# `generate_link_preview_task`) — kabhi bhi message-send request ko block
# nahi karta. Preview ready hone ke baad ek chhota `meta_update` WS event
# broadcast hota hai taaki khuli chat screen me card "pop in" ho jaaye,
# bina refresh ke.
#
# ⚠️ SSRF SAFETY — ye sabse important part hai. User-supplied URL ko
# server se fetch karna SSRF ka classic attack surface hai (attacker
# `http://169.254.169.254/...` jaisa cloud-metadata URL, ya internal
# `http://10.x.x.x:8000/admin` bhej ke internal network probe kar sakta
# hai). Isliye:
#   1. Sirf http/https scheme allowed.
#   2. Hostname resolve karke uska IP check karte hain — private/loopback/
#      link-local/multicast ranges par fetch NAHI karte.
#   3. Redirects manually follow karte hain (har hop pe wahi IP-check
#      dobara) — `requests` ka default redirect-follow ye check bypass kar
#      sakta hai agar seedha allow kar diya.
#   4. Response size aur time dono capped hain.

import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_MAX_BYTES = 300 * 1024        # 300KB — sirf <head> chahiye, poora page nahi
_TIMEOUT_SECONDS = 4
_MAX_REDIRECTS = 3
_CACHE_TTL = 60 * 60 * 24 * 7  # ek URL ka preview 7 din cache rehta hai

_OG_TAG_RE = re.compile(
    r'<meta[^>]+property=["\'](og:title|og:description|og:image)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_TITLE_TAG_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)


def extract_first_url(text: str) -> "str | None":
    """Message text me pehla http(s) URL dhoondta hai. Nahi mila to None."""
    if not text:
        return None
    match = _URL_RE.search(text)
    return match.group(0).rstrip('.,)>]') if match else None


def _is_safe_host(hostname: str) -> bool:
    """Hostname resolve karke check karta hai ki IP private/internal to nahi."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            return False
    return True


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.hostname:
        return False
    return _is_safe_host(parsed.hostname)


def fetch_link_preview(url: str) -> "dict | None":
    """
    URL ka OpenGraph preview fetch karta hai: {"title", "description",
    "image", "url"}. Kuch bhi fail ho (unsafe URL, timeout, 4xx/5xx, non-
    HTML content) to None return karta hai — caller (Celery task) is None
    ko gracefully handle karta hai, message-send kabhi fail nahi hota.
    """
    cache_key = f"link_preview:{url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None  # cached "no preview" bhi cache hota hai (negative cache)

    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_safe_url(current_url):
            cache.set(cache_key, {}, _CACHE_TTL)
            return None
        try:
            resp = requests.get(
                current_url,
                timeout=_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ChatLinkPreviewBot/1.0)"},
            )
        except requests.RequestException as e:
            logger.info("link_preview: fetch failed for %s: %s", current_url, e)
            cache.set(cache_key, {}, _CACHE_TTL)
            return None

        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get('Location')
            resp.close()
            if not location:
                cache.set(cache_key, {}, _CACHE_TTL)
                return None
            current_url = urljoin(current_url, location)
            continue

        if resp.status_code != 200 or 'text/html' not in (resp.headers.get('Content-Type') or ''):
            resp.close()
            cache.set(cache_key, {}, _CACHE_TTL)
            return None

        # bounded read — sirf <head> chahiye
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_BYTES or b'</head>' in chunk:
                break
        resp.close()
        html = b''.join(chunks).decode('utf-8', errors='ignore')
        break
    else:
        # too many redirects
        cache.set(cache_key, {}, _CACHE_TTL)
        return None

    og = {}
    for prop, content in _OG_TAG_RE.findall(html):
        og.setdefault(prop, content)

    title = og.get('og:title')
    if not title:
        title_match = _TITLE_TAG_RE.search(html)
        title = title_match.group(1).strip() if title_match else None

    if not title and not og.get('og:description'):
        cache.set(cache_key, {}, _CACHE_TTL)
        return None

    preview = {
        "url": current_url,
        "title": (title or '')[:200],
        "description": (og.get('og:description') or '')[:400],
        "image": urljoin(current_url, og['og:image']) if og.get('og:image') else None,
    }
    cache.set(cache_key, preview, _CACHE_TTL)
    return preview