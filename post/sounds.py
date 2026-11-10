"""
post/sounds.py

X7 - REEL SOUNDS: licence decision, credit, takedown, "reels with this sound".

DECISION (X7 a): the app keeps ONLY two kinds of audio.

    1. CC0 sounds from the Freesound proxy (`GET /post/music/search/`). CC0 needs no credit by law, we still show one.
    2. The user's OWN audio (their recording / a file on the phone). The reel is stored with `copyright_flag` and the user
       must confirm the rights (post/reel_meta.py). Such audio is NEVER offered to other people as a reusable sound.

Why not a licensed library (b)? It needs a partner contract, per-play royalty reporting and a legal review; none of that
exists yet, and a CC0 library plus clear takedown is the version that can ship now. Everything below is written by
PROVIDER so a licensed library can be added later without touching the screens: only `SOURCES_ALLOWED`, the search proxy
and `credit_for` need a new branch.

What this file does
  * policy()              what the app allows (sent to the app: `GET /post/sounds/policy/`).
  * credit_for(post)      the credit shown on a reel: {kind, id, name, artist, license, source, credit_url, reusable,
                          removed, uses}. `kind`: cc0 | user_audio | original. A blocked sound shows `removed: true` and
                          no name / artist (so a takedown never leaves the title on screen).
  * blocked_ids() / is_blocked(id)   cached set of blocked sound ids (5 min; every block / restore clears it). Used by the
                          search proxy, reel_meta (new reels) and story_music (new stories).
  * block_sound() / restore_sound()  the takedown flow (staff): registry row, owners told (in-app + push, NO e-mail),
                          optionally the reels using it go to the review queue (`audio_copyright` flag). Reels are not
                          hidden or deleted: a CC0 reel is the user's own work, only its credit is replaced.
  * report_sound()        a user says "this is not copyright-free"; one row per (user, sound); staff see counts.
  * reels_using_qs()      the reels a viewer may watch that use the sound (the "Reels with this sound" page).

Nothing here runs a migration or an external call; the only network call of the sound feature (Freesound search) stays in
post/views.py.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PROVIDER_FREESOUND = "freesound"
KIND_CC0 = "cc0"
KIND_USER_AUDIO = "user_audio"
KIND_ORIGINAL = "original"

SOURCES_ALLOWED = (KIND_CC0, KIND_USER_AUDIO)      # decision (a). A licensed library would add "licensed" here.
SOUND_ID_MAX = 40
NOTE_MAX = 300
REPORT_REASONS = ("copyright", "wrong_license", "offensive", "other")
MAX_OWNERS_TOLD = 500                              # one takedown never fans out to more notifications than this
MAX_REELS_HELD = 500
BLOCKED_CACHE_KEY = "post:sounds:blocked:v1"
BLOCKED_CACHE_SECONDS = 300
REPORTS_HINT_AT = 3                                # staff list marks a sound "needs a look" from this many reports

BLOCKED_CODE = "sound_blocked"                     # client-facing error code (reel create 400 / story create 400)
BLOCKED_TEXT = "This sound was removed and can't be used. Pick another sound."


class SoundError(ValueError):
    """A takedown / report rule was broken. `str(exc)` is the staff / client text, `code` a stable machine code."""

    def __init__(self, message, code="sound_error"):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------------------------------------------------- policy
def policy():
    """What the app may offer. Plain data for the app's "Sound sources" screen and the music sheet note."""
    return {
        "mode": "cc0_and_own_audio",
        "sources": [
            {"kind": KIND_CC0, "label": "Freesound (CC0)", "license": "CC0",
             "attribution_required": False, "reusable": True},
            {"kind": KIND_USER_AUDIO, "label": "Your own audio", "license": "user_audio",
             "attribution_required": False, "reusable": False, "needs_rights_confirm": True},
        ],
        "licensed_library": False,
        "takedown_contact": (getattr(settings, "COPYRIGHT_EMAIL", "") or ""),
    }


def credit_url(sound_id):
    """Public page of a Freesound sound (short URL, works for any id)."""
    return f"https://freesound.org/s/{sound_id}/"


# --------------------------------------------------------------------------------------------------- blocked sounds
def _blocked_from_db():
    from .models import SoundTakedown

    return frozenset(
        SoundTakedown.objects.filter(provider=PROVIDER_FREESOUND, status=SoundTakedown.Status.BLOCKED)
        .values_list("sound_id", flat=True)
    )


def blocked_ids():
    """Set of blocked Freesound ids (strings). Cached; fails OPEN (an unreadable cache / DB means "nothing blocked" for
    this call - and is logged), because a broken table must not stop every reel from being posted."""
    try:
        hit = cache.get(BLOCKED_CACHE_KEY)
        if hit is not None:
            return hit
    except Exception:
        logger.exception("sounds: cache read failed")
    try:
        ids = _blocked_from_db()
    except Exception:
        logger.exception("sounds: could not read blocked sounds")
        return frozenset()
    try:
        cache.set(BLOCKED_CACHE_KEY, ids, BLOCKED_CACHE_SECONDS)
    except Exception:
        logger.exception("sounds: cache write failed")
    return ids


def invalidate():
    try:
        cache.delete(BLOCKED_CACHE_KEY)
    except Exception:
        logger.exception("sounds: cache delete failed")


def is_blocked(sound_id):
    return str(sound_id or "").strip() in blocked_ids()


def clean_sound_id(value):
    """The id as text, or '' when it cannot be a Freesound id (digits only, <= SOUND_ID_MAX)."""
    if isinstance(value, bool) or value is None:
        return ""
    text = str(value).strip()
    return text if text.isdigit() and len(text) <= SOUND_ID_MAX else ""


# --------------------------------------------------------------------------------------------------------- the credit
def _reel_block(post):
    meta = post.metadata if isinstance(getattr(post, "metadata", None), dict) else {}
    reel = meta.get("reel")
    return reel if isinstance(reel, dict) else None


def credit_for(post, *, uses=None):
    """The sound credit of one reel, or None when the reel was not made in the reel studio / has no sound info.

    No query: reads the loaded post and the cached blocked set. `uses` (optional) is filled by the sound page only."""
    reel = _reel_block(post)
    if reel is None:
        return None
    music = reel.get("music") if isinstance(reel.get("music"), dict) else None
    if music:
        lic = music.get("license")
        sid = str(music.get("id") or "")
        if lic == "CC0":
            if sid in blocked_ids():
                return {"kind": KIND_CC0, "id": sid, "name": "", "artist": "", "license": "CC0", "source": "Freesound",
                        "credit_url": "", "reusable": False, "removed": True}
            out = {"kind": KIND_CC0, "id": sid, "name": music.get("name") or "", "artist": music.get("artist") or "",
                   "license": "CC0", "source": "Freesound", "credit_url": credit_url(sid), "reusable": True,
                   "removed": False}
            if uses is not None:
                out["uses"] = uses
            return out
        if lic == "user_audio":
            # Never reusable: the user only confirmed the rights for his / her own reel.
            return {"kind": KIND_USER_AUDIO, "id": sid, "name": music.get("name") or "", "artist": music.get("artist") or "",
                    "license": "user_audio", "source": "", "credit_url": "", "reusable": False, "removed": False}
        return None
    if reel.get("original_audio") is True and not reel.get("voiceover") is True:
        return {"kind": KIND_ORIGINAL, "id": "", "name": "", "artist": "", "license": "", "source": "",
                "credit_url": "", "reusable": False, "removed": False}
    return None


# ------------------------------------------------------- the sound's own Freesound record (for "Use this sound")
FREESOUND_SOUND_URL = "https://freesound.org/apiv2/sounds/{id}/"
META_CACHE_SECONDS = 6 * 3600
META_MISS_CACHE_SECONDS = 300


def _freesound_get(sound_id):
    """One call to Freesound for one sound. Separate function so tests can replace it. Returns the parsed dict or None."""
    try:
        import requests
    except ImportError:                                 # pragma: no cover - same guard as the search proxy
        logger.error("sounds: the 'requests' package isn't installed")
        return None
    key = getattr(settings, "FREESOUND_API_KEY", None)
    if not key:
        return None
    try:
        resp = requests.get(
            FREESOUND_SOUND_URL.format(id=sound_id),
            params={"token": key, "fields": "id,name,username,duration,previews,license,tags"}, timeout=6,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("sounds: Freesound lookup for %s failed", sound_id)
        return None


def freesound_meta(sound_id):
    """What "Use this sound" needs from Freesound: {name, artist, duration, preview_url, tags, cc0}. None when Freesound
    can't be reached (the page then simply has no "Use this sound" button). The server RE-CHECKS the licence here: a sound
    whose Freesound licence is no longer CC0 gets `cc0: False` and is not offered for reuse. Cached 6 h (misses 5 min).
    The Freesound token is never part of anything returned."""
    sid = clean_sound_id(sound_id)
    if not sid:
        return None
    key = f"post:sounds:meta:{sid}"
    try:
        hit = cache.get(key)
        if hit is not None:
            return hit or None
    except Exception:
        logger.exception("sounds: meta cache read failed")
    raw = _freesound_get(sid)
    if not raw:
        try:
            cache.set(key, {}, META_MISS_CACHE_SECONDS)
        except Exception:
            pass
        return None
    previews = raw.get("previews") if isinstance(raw.get("previews"), dict) else {}
    out = {
        "name": str(raw.get("name") or "")[:100],
        "artist": str(raw.get("username") or "")[:60],
        "duration": raw.get("duration") if isinstance(raw.get("duration"), (int, float)) and not isinstance(raw.get("duration"), bool) else None,
        "preview_url": previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or "",
        "tags": [str(t) for t in (raw.get("tags") or [])][:20] if isinstance(raw.get("tags"), list) else [],
        "cc0": "/zero/" in str(raw.get("license") or ""),   # http://creativecommons.org/publicdomain/zero/1.0/
    }
    try:
        cache.set(key, out, META_CACHE_SECONDS)
    except Exception:
        logger.exception("sounds: meta cache write failed")
    return out


def with_reuse_info(info):
    """`info` (sound_info) + `preview_url` / `duration` / `tags` when the sound may be reused right now. Never adds anything
    for a removed sound, and turns `reusable` off (+ `license_changed`) when Freesound no longer says CC0."""
    if not info or info.get("removed"):
        return info
    out = dict(info)
    meta = freesound_meta(out.get("id"))
    out.update({"preview_url": "", "duration": None, "tags": []})
    if meta is None:
        out["reusable"] = False                          # can't verify the licence now -> no reuse (page still opens)
        return out
    if not meta["cc0"]:
        logger.warning("sounds: %s is no longer CC0 on Freesound - reuse switched off", out.get("id"))
        out["reusable"] = False
        out["license_changed"] = True
        return out
    out.update({"preview_url": meta["preview_url"], "duration": meta["duration"], "tags": meta["tags"]})
    out["reusable"] = bool(meta["preview_url"])
    return out


# ------------------------------------------------------------------------------------------ reels with this sound
def _using_filter(qs, sound_id):
    return qs.filter(post_type="video", metadata__reel__music__id=str(sound_id), metadata__reel__music__license="CC0")


def reels_using_qs(user, sound_id):
    """Reels `user` may watch in Reels right now (public / own, approved, ...) that use the CC0 sound, newest first.
    Same visibility rule as the reels feed (reels.watchable_queryset), so the page can never show a reel the feed hides."""
    from . import reels

    return _using_filter(reels.page_queryset(user), sound_id).order_by("-created_at")


def uses_count(user, sound_id):
    return reels_using_qs(user, sound_id).count()


def sound_info(user, sound_id):
    """Header of the sound page: taken from the newest visible reel that uses it (the app only stores the sound's name /
    artist inside reels). None when no reel uses it any more AND it is not blocked (nothing to show)."""
    sid = clean_sound_id(sound_id)
    if not sid:
        return None
    blocked = sid in blocked_ids()
    newest = reels_using_qs(user, sid).first()
    if newest is None and not blocked:
        return None
    count = reels_using_qs(user, sid).count()
    if blocked:
        return {"id": sid, "kind": KIND_CC0, "name": "", "artist": "", "license": "CC0", "source": "Freesound",
                "credit_url": "", "reusable": False, "removed": True, "uses": count}
    credit = credit_for(newest, uses=count) or {}
    return credit


# ------------------------------------------------------------------------------------------------------- takedown
def _owners_and_posts(sound_id):
    """(post list, distinct owners) of every undeleted reel using the sound - regardless of who may watch it."""
    from .models import Post

    posts = list(
        _using_filter(Post.objects.filter(is_deleted=False), sound_id)
        .select_related("user").order_by("-created_at")[: MAX_REELS_HELD]
    )
    owners, seen = [], set()
    for p in posts:
        if p.user_id not in seen:
            seen.add(p.user_id)
            owners.append(p.user)
    return posts, owners[:MAX_OWNERS_TOLD]


def _tell_owner(user, sound_id, post_count, title):
    try:
        from copyrights import notify

        what = f"\"{title}\"" if title else "a sound"
        notify.send(
            user,
            "A sound in your reel was removed",
            f"{what} used in {post_count} of your reel{'s' if post_count != 1 else ''} was removed from the sound library. "
            "Your reel stays up; only the sound credit is hidden. If you think this is a mistake, write to us.",
            data={"role": "sound", "kind": "sound_removed", "sound_id": str(sound_id)},
        )
        return True
    except Exception:
        logger.exception("sounds: could not notify owner %s", getattr(user, "pk", None))
        return False


def _hold_reels(posts, sound_id):
    from user_profile.automod import record_flag

    held = 0
    for p in posts:
        flag = record_flag(
            target_type="post", target_id=p.pk, user_id=p.user_id,
            text=f"[sound:{sound_id}] this reel uses a sound that was removed - check the audio", reason="audio_copyright",
            source="wordlist",
        )
        held += 1 if flag is not None else 0
    return held


def _tell_sound_reporters(user_ids, outcome, sound_id):
    """P-F15: tell each reporter how their sound report ended. Best-effort, never raises."""
    try:
        from user_profile.report_automation import notify_outcome

        notify_outcome(user_ids, outcome, "sound", sound_id)
    except Exception:
        logger.exception("sound report outcome notification failed")


def block_sound(sound_id, *, by, reason="copyright", note="", title="", artist="", hold_reels=False):
    """Staff: stop the sound from being used. Idempotent (an already blocked sound returns its row, nothing else happens).

    Returns the SoundTakedown row. Raises SoundError for a bad id / reason."""
    from .models import SoundReport, SoundTakedown

    sid = clean_sound_id(sound_id)
    if not sid:
        raise SoundError("Sound id is invalid.", "sound_invalid")
    if reason not in {c for c, _ in SoundTakedown.Reason.choices}:
        raise SoundError("Unknown reason.", "sound_reason")
    note = (note or "").strip()[:NOTE_MAX]

    existing = SoundTakedown.objects.filter(provider=PROVIDER_FREESOUND, sound_id=sid, status=SoundTakedown.Status.BLOCKED).first()
    if existing is not None:
        return existing

    posts, owners = _owners_and_posts(sid)
    if not title and posts:
        music = (_reel_block(posts[0]) or {}).get("music") or {}
        title, artist = music.get("name", "") or "", artist or music.get("artist", "") or ""
    try:
        with transaction.atomic():
            row = SoundTakedown.objects.create(
                provider=PROVIDER_FREESOUND, sound_id=sid, title=(title or "")[:100], artist=(artist or "")[:60],
                reason=reason, note=note, created_by=by if getattr(by, "pk", None) else None,
            )
            _open = SoundReport.objects.filter(provider=PROVIDER_FREESOUND, sound_id=sid, status=SoundReport.Status.OPEN)
            _reporters = list(_open.values_list("user_id", flat=True))
            _open.update(status=SoundReport.Status.ACTIONED)
    except IntegrityError:           # a second staff member blocked it a moment earlier
        return SoundTakedown.objects.filter(provider=PROVIDER_FREESOUND, sound_id=sid, status="blocked").first()
    _tell_sound_reporters(_reporters, "actioned", sid)  # P-F15
    invalidate()

    by_owner = {}
    for p in posts:
        by_owner[p.user_id] = by_owner.get(p.user_id, 0) + 1
    told = sum(1 for u in owners if _tell_owner(u, sid, by_owner.get(u.pk, 1), title))
    held = _hold_reels(posts, sid) if hold_reels else 0
    SoundTakedown.objects.filter(pk=row.pk).update(owners_told=told, reels_held=held)
    row.owners_told, row.reels_held = told, held
    logger.info("sound %s blocked by %s (reason %s): %s owners told, %s reels held", sid, getattr(by, "pk", None), reason, told, held)
    return row


def restore_sound(sound_id, *, by, note=""):
    """Staff: the sound may be used again. Returns the number of rows restored (0 = it was not blocked).
    Reels that were sent to the review queue stay there - a moderator decides on each one."""
    from .models import SoundTakedown

    sid = clean_sound_id(sound_id)
    if not sid:
        raise SoundError("Sound id is invalid.", "sound_invalid")
    n = SoundTakedown.objects.filter(provider=PROVIDER_FREESOUND, sound_id=sid, status=SoundTakedown.Status.BLOCKED).update(
        status=SoundTakedown.Status.RESTORED, restored_at=timezone.now(),
        restored_by=by if getattr(by, "pk", None) else None,
        note=(note or "").strip()[:NOTE_MAX],
    )
    invalidate()
    return n


# --------------------------------------------------------------------------------------------------------- reports
def report_sound(user, sound_id, reason="copyright", note=""):
    """A user reports a sound. Returns (SoundReport, created). One row per (user, sound): a second report updates the
    reason / note and re-opens it. A blocked sound can still be reported (harmless, staff close it)."""
    from .models import SoundReport

    sid = clean_sound_id(sound_id)
    if not sid:
        raise SoundError("Sound id is invalid.", "sound_invalid")
    if reason not in REPORT_REASONS:
        raise SoundError("Unknown reason.", "sound_reason")
    note = (note or "").strip()[:NOTE_MAX]
    row, created = SoundReport.objects.get_or_create(
        user=user, provider=PROVIDER_FREESOUND, sound_id=sid, defaults={"reason": reason, "note": note},
    )
    if not created:
        row.reason, row.note, row.status = reason, note, SoundReport.Status.OPEN
        row.save(update_fields=["reason", "note", "status", "updated_at"])
    return row, created


def open_reports_summary(limit=50):
    """Staff list: sounds with open reports, most reported first: [{sound_id, reports, reasons, blocked, needs_look}]."""
    from django.db.models import Count

    from .models import SoundReport

    rows = list(
        SoundReport.objects.filter(provider=PROVIDER_FREESOUND, status=SoundReport.Status.OPEN)
        .values("sound_id").annotate(n=Count("id")).order_by("-n")[: max(1, min(int(limit), 200))]
    )
    if not rows:
        return []
    ids = [r["sound_id"] for r in rows]
    reasons = {}
    for sid, reason in SoundReport.objects.filter(
        provider=PROVIDER_FREESOUND, status=SoundReport.Status.OPEN, sound_id__in=ids
    ).values_list("sound_id", "reason"):
        reasons.setdefault(sid, {})
        reasons[sid][reason] = reasons[sid].get(reason, 0) + 1
    blocked = blocked_ids()
    return [
        {"sound_id": r["sound_id"], "reports": r["n"], "reasons": reasons.get(r["sound_id"], {}),
         "blocked": r["sound_id"] in blocked, "needs_look": r["n"] >= REPORTS_HINT_AT}
        for r in rows
    ]


def dismiss_reports(sound_id):
    """Staff: the reports were a false alarm. Returns how many were closed."""
    from .models import SoundReport

    sid = clean_sound_id(sound_id)
    if not sid:
        raise SoundError("Sound id is invalid.", "sound_invalid")
    _open = SoundReport.objects.filter(provider=PROVIDER_FREESOUND, sound_id=sid, status=SoundReport.Status.OPEN)
    _reporters = list(_open.values_list("user_id", flat=True))
    n = _open.update(status=SoundReport.Status.DISMISSED)
    _tell_sound_reporters(_reporters, "dismissed", sid)  # P-F15
    return n
