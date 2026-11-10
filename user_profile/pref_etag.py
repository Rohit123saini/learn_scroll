"""S2 — ETag helpers for `GET/PATCH /profile/preferences/me/`.

Kyu: do device par same account ho aur ek offline ho, to purana queued change naye server value ko
chupke se overwrite na kare. Client `If-Match: <etag>` bhejta hai; etag match na kare to server 412 deta hai
(current preferences ke saath) aur client khud decide karta hai kaun jeeta (field-wise, timestamp se).

ETag = weak validator, `updated_at` (auto_now) se bana — koi naya DB column / migration nahi chahiye.
"""
from __future__ import annotations

import re

_ETAG_TOKEN_RE = re.compile(r'(?:W/)?"([^"]*)"')


def make_etag(preference) -> str:
    """`W/"<microseconds since epoch>"` — same row, same updated_at => same etag."""
    ts = preference.updated_at
    micros = int(ts.timestamp() * 1_000_000) if ts is not None else 0
    return f'W/"{micros}"'


def _tokens(header_value: str) -> list[str]:
    return _ETAG_TOKEN_RE.findall(header_value or "")


def if_none_match_hits(header_value: str | None, etag: str) -> bool:
    """True => client ke paas already yahi version hai (GET par 304 de do)."""
    if not header_value:
        return False
    if header_value.strip() == "*":
        return True
    mine = _tokens(etag)
    return bool(mine) and mine[0] in _tokens(header_value)


def if_match_state(header_value: str | None, etag: str) -> str:
    """'absent' (purana client, header nahi bheja) | 'ok' | 'stale'."""
    if header_value is None or not header_value.strip():
        return "absent"
    if header_value.strip() == "*":
        return "ok"
    mine = _tokens(etag)
    return "ok" if mine and mine[0] in _tokens(header_value) else "stale"
