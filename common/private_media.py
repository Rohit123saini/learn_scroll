"""
T0.3 (B3) — private media: signed, expiring URLs.

PROBLEM: every uploaded file used to be reachable by anyone who knew (or guessed) the
URL — `/media/<path>` was mounted without auth and returned `Cache-Control: public,
max-age=31536000`. For class videos/notes, assignment & test attachments, submissions,
certificates, evidence and support screenshots that is a data leak.

DESIGN
  * Paths are classified by prefix (`PRIVATE_MEDIA_PREFIXES`). Everything else
    (posts/, stories/, profile/, comment_media/, classroom_covers/) stays public and keeps
    the long public cache.
  * A private file's `.url` (the storage's `url()`, which EVERY serializer already calls)
    returns `/pmedia/<path>?exp=<unix>&sig=<hmac>` instead of `/media/<path>`. So the URL
    is only ever minted inside a response the permission layer already authorised
    (enrolled student, owner, teacher ...) — no per-serializer edits needed.
  * `/pmedia/` verifies the HMAC + expiry, then streams (local disk), X-Accel-Redirects
    (nginx) or 302s to a short-lived S3 presigned URL. No/invalid signature -> 401/403.
  * `/media/<private path>` is refused outright (token or not).
  * `exp` is rounded to a bucket boundary, so the SAME url is returned for ~1h. That
    keeps client-side image/video caches effective while still expiring.

Pure functions below are Django-settings-aware but I/O free.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from django.conf import settings

DEFAULT_PRIVATE_PREFIXES = (
    "class_materials/",
    "post_resources/",          # P1: files of Notes/Resource posts
    "assigmentss/",
    "assigments_submissions/",
    "assigments/",
    "certificates/",
    "campus/",
    "testseries/",
    "legal_orders/",
    "copyright_evidence/",
    "support/",
)

PRIVATE_URL_PREFIX = "/pmedia/"


def private_prefixes() -> tuple:
    return tuple(getattr(settings, "PRIVATE_MEDIA_PREFIXES", DEFAULT_PRIVATE_PREFIXES))


def is_private_path(name: str) -> bool:
    n = (name or "").lstrip("/").replace("\\", "/")
    if n.startswith("media/"):
        n = n[len("media/"):]
    return n.startswith(private_prefixes())


def _ttl() -> int:
    return int(getattr(settings, "PRIVATE_MEDIA_URL_TTL", 4 * 3600))


def _bucket() -> int:
    return max(1, int(getattr(settings, "PRIVATE_MEDIA_URL_BUCKET", 3600)))


def _key() -> bytes:
    # Derived, so a leaked signature can't be used for anything else signed with SECRET_KEY.
    return hashlib.sha256(b"private-media-v1:" + settings.SECRET_KEY.encode()).digest()


def _sig(path: str, exp: int) -> str:
    return hmac.new(_key(), f"{path}\n{exp}".encode(), hashlib.sha256).hexdigest()[:40]


def _norm(path: str) -> str:
    n = (path or "").lstrip("/").replace("\\", "/")
    return n[len("media/"):] if n.startswith("media/") else n


def make_signature(path: str, now: float | None = None) -> tuple[int, str]:
    now = time.time() if now is None else now
    path = _norm(path)
    b = _bucket()
    exp = int((now // b) * b) + b + _ttl()
    return exp, _sig(path, exp)


def build_signed_path(name: str, now: float | None = None) -> str:
    """`class_materials/a b.pdf` -> `/pmedia/class_materials/a%20b.pdf?exp=..&sig=..`"""
    path = _norm(name)
    exp, sig = make_signature(path, now)
    return f"{PRIVATE_URL_PREFIX}{quote(path)}?exp={exp}&sig={sig}"


def verify_signature(path: str, exp, sig, now: float | None = None) -> str:
    """Returns 'ok' | 'missing' | 'invalid' | 'expired'."""
    if not exp or not sig:
        return "missing"
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return "invalid"
    if not hmac.compare_digest(_sig(_norm(path), exp_i), str(sig)):
        return "invalid"
    if (time.time() if now is None else now) > exp_i:
        return "expired"
    return "ok"
