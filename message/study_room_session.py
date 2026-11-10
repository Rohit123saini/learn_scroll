"""
message/study_room_session.py

S2 — Study room ka "current session" DB me (`StudyRoomSession`), cache me nahi.

Pehle `StudyRoomJoinView` current session ka suffix sirf `cache` me rakhta tha
(`studyroom:current_session:<conv>`, 7 din). Redis flush ya LocMem cache (har worker ka apna cache)
me alag-alag log alag-alag LiveKit rooms me chale jaate the — whiteboard / sticky notes bhi alag.
Ab sach ek hi jagah hai: DB. Conversation pe ek hi ACTIVE row (`ended_at IS NULL`) ho sakti hai
(partial unique constraint), isliye do worker ek saath join karein to bhi ek hi room milta hai.

Rules:
  * `new_session=True`  -> purana active session `ended_at` se band, naya row (naya room name).
  * `new_session=False` -> active session ho aur 7 din se purana na ho to wahi; warna naya banta hai.
  * Rolling deploy / pehli baar: agar is conversation ki koi row hi nahi, to purana cache suffix
    (ya cache bhi khali ho to purana plain `study_<conv>` naam) ek baar adopt karte hain taaki jo log
    abhi room me hain wahi room chalta rahe. Uske baad cache kabhi nahi padha jata.
"""

import logging
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Conversation, StudyRoomSession

logger = logging.getLogger(__name__)

# Purane cache pointer ka TTL (7 din) — wahi semantic: itna purana session "current" nahi mana jata.
SESSION_MAX_AGE = timedelta(days=7)


def legacy_cache_key(conversation_id):
    return f"studyroom:current_session:{conversation_id}"


def build_room_name(conversation_id, suffix=None):
    return f"study_{conversation_id}_{suffix}" if suffix else f"study_{conversation_id}"


def _active_qs(conversation_id):
    return StudyRoomSession.objects.filter(conversation_id=conversation_id, ended_at__isnull=True)


def get_active_session(conversation_id, now=None):
    """Taaza (7 din se purana nahi) active session, ya None."""
    now = now or timezone.now()
    session = _active_qs(conversation_id).order_by('-started_at').first()
    if session is not None and now - session.started_at < SESSION_MAX_AGE:
        return session
    return None


def _legacy_suffix(conversation_id):
    try:
        suffix = cache.get(legacy_cache_key(conversation_id))
    except Exception:
        suffix = None
    return str(suffix) if suffix else None


def _clear_legacy_suffix(conversation_id):
    try:
        cache.delete(legacy_cache_key(conversation_id))
    except Exception:
        logger.warning("legacy study room cache pointer delete failed conv=%s", conversation_id)


def resolve_session(conversation, user, new_session=False, now=None, extra_defaults=None):
    """T2.2 - public entry: `_resolve_session_once` ko transient "database is locked" par 3 baar retry karta hai.

    Poora kaam ek `transaction.atomic()` me hota hai (lock par rollback), isliye retry se double session nahi banta.
    """
    from common.db_retry import DEFAULT_WRITE_RETRIES, retry_on_db_lock

    return retry_on_db_lock(
        lambda: _resolve_session_once(conversation, user, new_session, now, extra_defaults),
        retries=DEFAULT_WRITE_RETRIES,
    )


def _resolve_session_once(conversation, user, new_session=False, now=None, extra_defaults=None):
    """
    Returns (session, started_new: bool). Caller ko `conversation` (active member check ke baad) dena hai.
    `started_new` True tab jab is call me naya row bana (caller fresh-board purge isi pe karta hai
    jab `new_session` maanga gaya ho).

    S4 — `extra_defaults` (dict) naye row ke saath hi save hota hai (jaise public group me
    `waiting_room_enabled=True`), taaki row banne aur setting lagne ke beech koi dusra joiner na ghus sake.
    """
    now = now or timezone.now()
    extra_defaults = dict(extra_defaults or {})
    conversation_id = conversation.id

    if not new_session:
        current = get_active_session(conversation_id, now)
        if current is not None:
            return current, False

    try:
        with transaction.atomic():
            # Conversation row pe lock: do worker ek saath naya session na banayein (Postgres).
            # Partial unique constraint dusri safety hai (SQLite me lock no-op hota hai).
            Conversation.objects.select_for_update().filter(id=conversation_id).first()

            if not new_session:
                current = get_active_session(conversation_id, now)
                if current is not None:
                    return current, False

            suffix = None
            adopt_legacy = False
            if not new_session and not StudyRoomSession.all_objects.filter(
                conversation_id=conversation_id
            ).exists():
                # Is conversation ki pehli DB row — purane cache wale room ko adopt karo (ek baar).
                suffix = _legacy_suffix(conversation_id)
                adopt_legacy = True
            if not adopt_legacy:
                suffix = uuid.uuid4().hex[:12]
            room_name = build_room_name(conversation_id, suffix)

            # Purana active (ya 7 din se purana) session band.
            _active_qs(conversation_id).update(ended_at=now)

            session, created = StudyRoomSession.objects.get_or_create(
                room_name=room_name,
                defaults={
                    "conversation_id": conversation_id,
                    "started_by": user,
                    "started_at": now,
                    **extra_defaults,
                },
            )
            if not created:
                # Bahut hi rare collision (same room_name) — naya suffix, kabhi ended row revive nahi.
                session = StudyRoomSession.objects.create(
                    conversation_id=conversation_id,
                    room_name=build_room_name(conversation_id, uuid.uuid4().hex[:12]),
                    started_by=user,
                    started_at=now,
                    **extra_defaults,
                )
            if adopt_legacy:
                _clear_legacy_suffix(conversation_id)
            return session, True
    except IntegrityError:
        # Race: kisi aur request ne usi pal active session bana diya — wahi use karo.
        current = get_active_session(conversation_id, timezone.now())
        if current is not None:
            return current, False
        raise
