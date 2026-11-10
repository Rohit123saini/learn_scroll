# login/ws_ticket.py
"""
X6b -- one-time WebSocket TICKET, so the long-lived-ish JWT never travels in a WebSocket URL.

Problem: ``wss://host/ws/chat/1/?token=<JWT>`` puts the access token in the URL, and URLs end up in proxy / CDN / server
access logs, crash reports and browser history.

Fix: the app first calls ``POST /login/auth/ws-ticket/`` (normal ``Authorization: Bearer`` header, never a URL) and
gets a random ticket that is
  * valid for WS_TICKET_TTL_SECONDS (default 30 s) -- just enough to open the socket,
  * SINGLE USE (redeeming deletes it), so a ticket copied out of a log is already dead,
  * bound to the user AND the login session (session_jti) it was issued for, so "log out this device" kills it too,
and then opens ``wss://host/ws/chat/1/?ticket=<ticket>``. ``LearnScroll.ws_auth.JWTAuthMiddleware`` redeems it.

Storage = the Django cache (Redis in production, REDIS_URL is mandatory when DEBUG is False). Only a SHA-256 of the
ticket is used as the key, so even a cache dump holds nothing that can be replayed. No model, no migration.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Optional, Tuple

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("security.ws_ticket")

_PREFIX = "wst:"


def ttl_seconds() -> int:
    try:
        return max(5, min(120, int(getattr(settings, "WS_TICKET_TTL_SECONDS", 30))))
    except (TypeError, ValueError):
        return 30


def _key(ticket: str) -> str:
    return _PREFIX + hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def issue(user_id, session_jti: Optional[str] = None) -> Tuple[str, int]:
    """New ticket for ``user_id`` (+ the login session it belongs to). Returns (ticket, ttl seconds)."""
    ticket = secrets.token_urlsafe(32)
    ttl = ttl_seconds()
    cache.set(_key(ticket), {"u": str(user_id), "s": session_jti or ""}, ttl)
    return ticket, ttl


def redeem(ticket: str) -> Optional[Tuple[str, Optional[str]]]:
    """Single use: returns (user_id, session_jti) the first time, None for unknown / expired / already used."""
    if not ticket or not isinstance(ticket, str) or len(ticket) > 200:
        return None
    key = _key(ticket)
    try:
        data = cache.get(key)
        if not data:
            return None
        # delete() reports whether THIS call removed it -> two sockets racing for one ticket: only one wins.
        if cache.delete(key) is False:
            return None
        return data.get("u"), (data.get("s") or None)
    except Exception:  # a cache outage must degrade to "not authenticated", never crash the handshake
        logger.warning("ws ticket redeem failed", exc_info=True)
        return None
