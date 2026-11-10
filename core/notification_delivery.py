"""
core/notification_delivery.py - N5: smart delivery (timezone, DND presets, priority roll-up, digest preview).

What lives here (the DECISION "may this push buzz right now?" itself lives on the model:
NotificationPreference.push_block_reason, so every existing send path - create + push helpers, streak reminders,
live-class pushes, study digest - gets it for free):

  sync_timezone(user, tz)         phone -> server zone sync. mode "auto" follows the device, "manual" ignores it.
  dnd_until_for_preset(...)       "1h" | "8h" | "24h" | "tomorrow" | "custom" -> the server-side deadline.
  build_rollup(...)               what the daily "quiet updates" summary would say (LOW-tier unread bell rows).
  digest_preview(user)            everything the settings screen's "aisa dikhega" sheet shows (roll-up + study digest +
                                  a fixed example, so the preview is useful even for a brand-new account).
  send_rollups(now)               hourly task body: ONE summary push per user per local day at `content_digest_hour`.

Focus / exam mode, priority tiers: see core/notification_priority.py. Nothing here ever writes a Notification row -
the roll-up is a push about rows that already exist.
"""
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

DND_PRESET_MINUTES = {"1h": 60, "8h": 8 * 60, "24h": 24 * 60}
DND_PRESETS = tuple(DND_PRESET_MINUTES) + ("tomorrow", "custom", "off")
DND_MAX = timedelta(days=7)
DEFAULT_WAKE = time(7, 0)           # "till tomorrow" with no quiet hours set: until 07:00 local
TOMORROW_MIN_AHEAD = timedelta(hours=1)  # "till tomorrow" at 06:30 means the NEXT morning, not in 30 minutes
SYNC_REFRESH = timedelta(hours=12)  # an unchanged zone is re-stamped at most this often (no write on every app open)
ROLLUP_LOOKBACK = timedelta(days=3)


# --------------------------------------------------------------------------------------------- timezone
def valid_timezone(name):
    """IANA name -> the same name, or None when it is not a real zone."""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name or len(name) > 64:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return name


def sync_timezone(user, tz_name, now=None) -> dict:
    """The app reports the phone's zone (login / app open). Auto mode follows it; manual mode keeps the user's pick.
    -> {applied, changed, timezone, mode, reason?}. Never raises for bad input (reason: "invalid")."""
    from .models import NotificationPreference

    now = now or timezone.now()
    name = valid_timezone(tz_name)
    pref = NotificationPreference.for_user(user)
    if name is None:
        return {"applied": False, "changed": False, "timezone": pref.timezone, "mode": pref.timezone_mode,
                "reason": "invalid"}
    if pref.timezone_mode == NotificationPreference.TimezoneMode.MANUAL:
        return {"applied": False, "changed": False, "timezone": pref.timezone, "mode": pref.timezone_mode,
                "reason": "manual", "device_timezone": name}
    changed = pref.timezone != name
    fresh = pref.timezone_synced_at is not None and now - pref.timezone_synced_at < SYNC_REFRESH
    if changed or not fresh:
        pref.timezone = name
        pref.timezone_synced_at = now
        pref.save(update_fields=["timezone", "timezone_synced_at", "updated_at"])
    return {"applied": True, "changed": changed, "timezone": name, "mode": pref.timezone_mode}


# --------------------------------------------------------------------------------------------- DND presets
def dnd_until_for_preset(pref, preset, now=None, custom_until=None):
    """-> aware datetime (UTC-comparable) or None ("off" clears). ValueError (message = user-facing) for bad input.
    All deadlines come from the SERVER clock (a wrong phone clock can't pause alerts for days); "custom" is checked
    to be in the future and at most 7 days ahead."""
    now = now or timezone.now()
    preset = (preset or "").strip().lower()
    if preset == "off":
        return None
    if preset in DND_PRESET_MINUTES:
        return now + timedelta(minutes=DND_PRESET_MINUTES[preset])
    if preset == "tomorrow":
        tz = pref._tzinfo()
        local = now.astimezone(tz)
        wake = pref.quiet_end or DEFAULT_WAKE
        wake = wake.replace(second=0, microsecond=0, tzinfo=None)
        candidate = datetime.combine(local.date(), wake, tzinfo=tz)
        if candidate - local < TOMORROW_MIN_AHEAD:
            candidate = datetime.combine(local.date() + timedelta(days=1), wake, tzinfo=tz)
        return candidate
    if preset == "custom":
        if custom_until is None:
            raise ValueError("dnd_custom_until is required for the custom preset.")
        if timezone.is_naive(custom_until):
            custom_until = timezone.make_aware(custom_until, pref._tzinfo())
        if custom_until <= now:
            raise ValueError("Pick a time in the future.")
        if custom_until - now > DND_MAX:
            raise ValueError("Pause can be at most 7 days.")
        return custom_until
    raise ValueError(f"Unknown pause preset (use: {', '.join(DND_PRESETS)}).")


# --------------------------------------------------------------------------------------------- roll-up
# bucket -> NotifType member names. A LOW type not listed lands in "other", so a new low type is still counted.
ROLLUP_BUCKETS = {
    "likes": ("POST_LIKED",),
    "reactions": ("STORY_REACTION",),
    "reposts": ("POST_REPOSTED",),
    "following": ("NEW_POST_FROM_FOLLOWED", "CLASSROOM_CREATED_BY_FOLLOWED", "TESTSERIES_CREATED_BY_FOLLOWED",
                  "FOLLOW_REQUEST_ACCEPTED"),
    "progress": ("STREAK_MILESTONE_REACHED", "CAMPUS_REWARD_EARNED", "REFERRAL_REWARD", "WEEKLY_RECAP_READY",
                 "CREATOR_FEEDBACK"),
}
_ROLLUP_ORDER = ("likes", "reactions", "reposts", "following", "progress", "other")

_WORDS = {
    "en": {
        "title": "Quiet updates",
        "likes": lambda n: f"{n} like{'s' if n != 1 else ''}",
        "reactions": lambda n: f"{n} reaction{'s' if n != 1 else ''}",
        "reposts": lambda n: f"{n} repost{'s' if n != 1 else ''}",
        "following": lambda n: f"{n} new from people you follow",
        "progress": lambda n: f"{n} progress update{'s' if n != 1 else ''}",
        "other": lambda n: f"{n} other",
        "example_title": "Quiet updates",
        "example_body": "12 likes · 3 reactions · 2 new from people you follow",
        "example_study_title": "Your study digest",
        "example_study_body": "Physics: 5 new notes · #kinematics: 3 new posts",
    },
    "hi": {
        "title": "छोटे अपडेट",
        "likes": lambda n: f"{n} लाइक",
        "reactions": lambda n: f"{n} रिएक्शन",
        "reposts": lambda n: f"{n} रीपोस्ट",
        "following": lambda n: f"फ़ॉलो किए लोगों से {n} नए",
        "progress": lambda n: f"{n} प्रोग्रेस अपडेट",
        "other": lambda n: f"{n} और",
        "example_title": "छोटे अपडेट",
        "example_body": "12 लाइक · 3 रिएक्शन · फ़ॉलो किए लोगों से 2 नए",
        "example_study_title": "आपका स्टडी डाइजेस्ट",
        "example_study_body": "Physics में 5 नए नोट्स · #kinematics पर 3 नई पोस्ट",
    },
}


def _bucket_of(notif_type: str, lookup: dict) -> str:
    return lookup.get(str(notif_type), "other")


def _bucket_lookup() -> dict:
    from .models import Notification

    out = {}
    for bucket, names in ROLLUP_BUCKETS.items():
        for name in names:
            member = getattr(Notification.NotifType, name, None)
            if member is not None:
                out[member.value] = bucket
    return out


def rollup_queryset(user_id, since):
    """Unread, not archived, LOW-tier bell rows created after `since`."""
    from . import notification_priority as prio
    from .models import Notification

    return Notification.objects.filter(
        recipient_id=user_id, notif_type__in=prio.low_types(), is_read=False,
        archived_at__isnull=True, created_at__gt=since,
    )


def rollup_since(pref, now):
    floor = now - ROLLUP_LOOKBACK
    return max(pref.last_rollup_at, floor) if pref.last_rollup_at else floor


def build_rollup(user_id, pref, now=None, language="en") -> dict:
    """-> {total, groups:[{key, count, label}], title, body, since}. total == 0 -> nothing to send."""
    now = now or timezone.now()
    since = rollup_since(pref, now)
    words = _WORDS["hi" if language == "hi" else "en"]
    lookup = _bucket_lookup()
    counts = {}
    rows = rollup_queryset(user_id, since).values("notif_type").annotate(n=Count("id"))
    for r in rows:
        key = _bucket_of(r["notif_type"], lookup)
        counts[key] = counts.get(key, 0) + int(r["n"])
    groups = [
        {"key": k, "count": counts[k], "label": words[k](counts[k])}
        for k in _ROLLUP_ORDER if counts.get(k)
    ]
    return {
        "total": sum(g["count"] for g in groups),
        "groups": groups,
        "title": words["title"],
        "body": " · ".join(g["label"] for g in groups),
        "since": since.isoformat(),
    }


def _language(user) -> str:
    from .content_digest import _language as lang

    return lang(user)


def digest_preview(user, now=None) -> dict:
    """The settings sheet \"aisa dikhega\". Real numbers when there are any, plus a fixed example so a new account
    still sees what the summaries look like. Never raises: a failing section is simply empty."""
    from . import content_digest
    from .models import NotificationPreference

    now = now or timezone.now()
    pref = NotificationPreference.for_user(user)
    language = _language(user)
    words = _WORDS["hi" if language == "hi" else "en"]

    try:
        rollup = build_rollup(user.id, pref, now, language)
    except Exception:  # noqa: BLE001
        logger.exception("digest_preview: roll-up failed (user=%s)", user.id)
        rollup = {"total": 0, "groups": [], "title": words["title"], "body": "", "since": None}

    study = {"title": "", "message": "", "items": 0}
    try:
        items = content_digest.build_items(user, pref, content_digest.window_start(pref, now))
        if items:
            title, message = content_digest.compose(items, language)
            study = {"title": title, "message": message, "items": len(items)}
    except Exception:  # noqa: BLE001
        logger.exception("digest_preview: study digest failed (user=%s)", user.id)

    return {
        "language": language,
        "timezone": pref.timezone,
        "timezone_mode": pref.timezone_mode,
        "rollup": {**rollup, "enabled": pref.low_priority_rollup, "send_hour": int(pref.content_digest_hour)},
        "study": {
            **study,
            "frequency": pref.content_digest_frequency,
            "hour": int(pref.content_digest_hour),
        },
        "email_digest_frequency": pref.digest_frequency,
        "focus_active": pref.focus_active(),
        "focus_priority_only": pref.focus_priority_only,
        "example": {
            "rollup_title": words["example_title"], "rollup_body": words["example_body"],
            "study_title": words["example_study_title"], "study_body": words["example_study_body"],
        },
    }


# --------------------------------------------------------------------------------------------- the hourly task
def _due(pref, now) -> bool:
    from .content_digest import user_tz

    local = now.astimezone(user_tz(pref))
    if local.hour < int(pref.content_digest_hour):
        return False
    last = pref.last_rollup_at
    return last is None or last.astimezone(user_tz(pref)).date() < local.date()


def send_rollups(now=None, batch_size=500) -> dict:
    """Hourly. For everybody with low_priority_rollup on: once per local day, from `content_digest_hour` on, send ONE
    push \"12 likes · 3 reactions\" about the LOW-tier rows they have not read. Idempotent (`last_rollup_at`).
      * nothing to say           -> marked done for today, no push
      * push off                 -> marked done, no push (the rows stay in the bell)
      * focus / exam mode now    -> NOT marked: held back until focus ends, then ONE summary of what piled up
      * DND / quiet hours now    -> NOT marked: the next hourly run retries, so it goes out when the window ends
    Never raises."""
    from .models import Notification, NotificationPreference

    now = now or timezone.now()
    out = {"checked": 0, "sent": 0, "empty": 0, "dropped": 0, "waiting": 0}
    qs = NotificationPreference.objects.filter(low_priority_rollup=True, user__is_active=True).select_related("user")
    prefs = []
    for pref in qs.iterator(chunk_size=batch_size):
        if _due(pref, now):
            prefs.append(pref)
    NotificationPreference.prime_focus(prefs)
    for pref in prefs:
        out["checked"] += 1
        try:
            user = pref.user
            sample_type = Notification.NotifType.POST_LIKED
            if pref.is_quiet_now(now):
                out["waiting"] += 1
                continue
            if pref.focus_priority_only and pref.focus_active():
                # Exam / focus mode: the summary itself is not urgent, so it is held back - but NOT marked done, so the
                # likes collected meanwhile still roll up in ONE push once focus ends (ROLLUP_LOOKBACK caps how far back).
                out["waiting"] += 1
                continue
            if not pref.channel_allowed(sample_type, "push"):
                pref.last_rollup_at = now
                pref.save(update_fields=["last_rollup_at"])
                out["dropped"] += 1
                continue
            rollup = build_rollup(user.id, pref, now, _language(user))
            pref.last_rollup_at = now
            pref.save(update_fields=["last_rollup_at"])
            if not rollup["total"]:
                out["empty"] += 1
                continue
            _push_rollup(user.id, rollup, now, pref)
            out["sent"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("send_rollups failed (pref=%s)", getattr(pref, "pk", None))
    return out


def _push_rollup(user_id, rollup, now, pref):
    from message.push_channels import channel_for_notif_type
    from message.push_utils import send_rich_push

    from .content_digest import user_tz
    from .models import Notification

    day = now.astimezone(user_tz(pref)).date().isoformat()
    channel = channel_for_notif_type(Notification.NotifType.POST_LIKED)  # the "social" channel
    send_rich_push(
        [user_id], rollup["title"], rollup["body"],
        data={
            "type": "notification_rollup", "notif_type": "generic", "category": "other",
            # same keys core/notification_routes.route_for puts in every push: opens the inbox
            "route": "notifications", "target_type": "screen", "target_id": "",
            "rollup_total": str(rollup["total"]),
        },
        channel_id=channel, apns_thread_id=channel, apns_collapse_id=f"rollup-{day}",
    )
