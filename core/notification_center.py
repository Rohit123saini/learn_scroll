"""N9 - notification-center extras (one module, pure helpers + small DB helpers; nothing here sends a push).

    1. HISTORY      30-day retention. `prune_history()` is what the daily Celery task `core.prune_notification_history`
                    runs. Safety / locked types live longer (they are records, not chatter).
    2. SCOPE MUTE   "Mute THIS class / chat / post for 8h". `NotificationScopeMute` rows; `push_muted_for_row()` is the
                    one check every row-push goes through, `filter_unmuted_users()` is the multi-recipient twin (chat
                    fan-out, live-class audience). Only the buzz is dropped - the bell row stays.
    3. WHY          `why_for(row)` -> "Why am I seeing this?" line (en + hi) + which switch controls it. Pure (type + data
                    only, ZERO queries) so it is safe inside a 100-row list.
    4. SOUND        per Android channel (= notification category) sound + vibration choice, validated here, stored on
                    `NotificationPreference.sound_prefs`, shipped to the phone inside the push (`snd`, `vib`) and by the
                    preferences API.

Likha gaya sirf code padh kar - chalaya nahi.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------------------
# 1. History retention
# ---------------------------------------------------------------------------------------------------------------------
HISTORY_DAYS_DEFAULT = 30
LOCKED_HISTORY_DAYS_DEFAULT = 90  # safety / copyright / money-failed alerts are kept longer than chatter
_DELETE_BATCH = 2000
_MAX_BATCHES_PER_RUN = 50  # 100k rows per run; the daily beat catches up on the next run


def history_days() -> int:
    """How many days a normal notification stays in the bell (settings.NOTIFICATION_HISTORY_DAYS, default 30)."""
    try:
        return max(1, int(getattr(settings, "NOTIFICATION_HISTORY_DAYS", HISTORY_DAYS_DEFAULT)))
    except (TypeError, ValueError):
        return HISTORY_DAYS_DEFAULT


def locked_history_days() -> int:
    """Same for locked / safety types. Never shorter than the normal window."""
    try:
        days = int(getattr(settings, "NOTIFICATION_LOCKED_HISTORY_DAYS", LOCKED_HISTORY_DAYS_DEFAULT))
    except (TypeError, ValueError):
        days = LOCKED_HISTORY_DAYS_DEFAULT
    return max(days, history_days())


def history_policy() -> dict:
    """What the app shows under the bell ("Notifications 30 din tak rehti hain")."""
    return {"history_days": history_days(), "locked_history_days": locked_history_days()}


def _delete_in_batches(qs, *, max_batches=None) -> tuple[int, bool]:
    """-> (deleted, more_left). Bounded: one run never holds a huge transaction / lock."""
    from .models import Notification

    # read the module value at CALL time (a default argument is frozen at import, so ops / tests could not tune it)
    max_batches = _MAX_BATCHES_PER_RUN if max_batches is None else max_batches
    total = 0
    for _ in range(max_batches):
        ids = list(qs.values_list("pk", flat=True)[:_DELETE_BATCH])
        if not ids:
            return total, False
        Notification.objects.filter(pk__in=ids).delete()
        total += len(ids)
    return total, True


def prune_history(now=None, *, dry_run=False) -> dict:
    """Delete notifications older than the history window.

        normal types   older than history_days()          (read, unread and archived alike - a 30 day history is a
                                                           history, not an inbox that grows for ever)
        locked types   older than locked_history_days()

    Also removes expired scope mutes. Returns counts; never raises past the caller (the Celery task logs).
    """
    from .models import Notification, NotificationScopeMute

    now = now or timezone.now()
    locked = sorted(Notification.locked_types())
    normal_cut = now - timedelta(days=history_days())
    locked_cut = now - timedelta(days=locked_history_days())

    normal = Notification.objects.filter(created_at__lt=normal_cut).exclude(notif_type__in=locked)
    locked_old = Notification.objects.filter(created_at__lt=locked_cut, notif_type__in=locked)
    expired_mutes = NotificationScopeMute.objects.filter(muted_until__isnull=False, muted_until__lte=now)

    if dry_run:
        return {
            "dry_run": True, "normal": normal.count(), "locked": locked_old.count(), "mutes": expired_mutes.count(),
            "history_days": history_days(), "locked_history_days": locked_history_days(),
        }

    n_normal, more1 = _delete_in_batches(normal)
    n_locked, more2 = _delete_in_batches(locked_old)
    n_mutes, _ = expired_mutes.delete()
    return {
        "dry_run": False, "normal": n_normal, "locked": n_locked, "mutes": n_mutes, "more": bool(more1 or more2),
        "history_days": history_days(), "locked_history_days": locked_history_days(),
    }


# ---------------------------------------------------------------------------------------------------------------------
# 2. Scope mute (class / chat / post)
# ---------------------------------------------------------------------------------------------------------------------
SCOPE_POST = "post"
SCOPE_CLASS = "class"
SCOPE_CONVERSATION = "conversation"
SCOPE_SECTION = "section"  # N9b - a campus section (the school class a student / parent / teacher belongs to)
SCOPE_TYPES = (SCOPE_POST, SCOPE_CLASS, SCOPE_CONVERSATION, SCOPE_SECTION)

#: duration presets the app offers. None = until the user unmutes. A raw `minutes` is also accepted (1 min .. 1 year).
MUTE_PRESETS = {"1h": 60, "8h": 8 * 60, "24h": 24 * 60, "7d": 7 * 24 * 60, "forever": None}
MAX_MUTE_MINUTES = 365 * 24 * 60
MAX_ACTIVE_MUTES = 500  # one user cannot grow the table without bound
_LABEL_MAX = 120


def _s(value) -> str:
    return "" if value is None else str(value).strip()


def _present(value) -> bool:
    return _s(value) not in ("", "None", "null", "0")


def scopes_for(notif_type, data=None, *, classroom_id=None, session_id=None, **_ignored) -> list[tuple[str, str]]:
    """The things a notification belongs to, most specific first: [("conversation", "<uuid>"), ("class", "12"), ...].
    Pure - reads the row's own fields only. (`session_id` is accepted for call-site symmetry; a live session is muted
    through its class.)"""
    d = data if isinstance(data, dict) else {}
    out: list[tuple[str, str]] = []
    if _present(d.get("conversation_id")):
        out.append((SCOPE_CONVERSATION, _s(d["conversation_id"])))
    if _present(classroom_id):
        out.append((SCOPE_CLASS, _s(classroom_id)))
    elif _present(d.get("classroom_id")):
        out.append((SCOPE_CLASS, _s(d["classroom_id"])))
    if _present(d.get("section_id")):  # N9b - every campus push carries data.section_id
        out.append((SCOPE_SECTION, _s(d["section_id"])))
    if _present(d.get("post_id")):
        out.append((SCOPE_POST, _s(d["post_id"])))
    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def scope_mutable(notif_type) -> bool:
    """Types a scope mute may silence. Locked (safety / copyright / money failed), quiet-bypass (calls, emergencies,
    parent approval) and NEVER_MUTE types always get through."""
    from .models import Notification, NotificationPreference

    t = _s(notif_type)
    if t in Notification.locked_types():
        return False
    if t in NotificationPreference.NEVER_MUTE_TYPES or t in NotificationPreference.QUIET_BYPASS_TYPES:
        return False
    return True


def active_mutes(user_id, now=None):
    from .models import NotificationScopeMute

    now = now or timezone.now()
    return NotificationScopeMute.objects.filter(user_id=user_id).filter(
        models_q_active(now)
    )


def models_q_active(now):
    from django.db.models import Q

    return Q(muted_until__isnull=True) | Q(muted_until__gt=now)


def muted_scope_set(user_id, candidates, now=None) -> set[tuple[str, str]]:
    """Which of `candidates` [(type, id), ...] are muted for `user_id` right now. ONE query."""
    from django.db.models import Q

    if user_id is None or not candidates:
        return set()
    cond = Q()
    for st, sid in candidates:
        cond |= Q(scope_type=st, scope_id=sid)
    rows = active_mutes(user_id, now).filter(cond).values_list("scope_type", "scope_id")
    return {(a, b) for a, b in rows}


def push_muted_for_row(row, now=None) -> bool:
    """True when the user silenced a class / chat / post this notification belongs to. Never raises (a failing check
    must not swallow a push - fail OPEN)."""
    try:
        if row is None or not scope_mutable(row.notif_type):
            return False
        cands = scopes_for(row.notif_type, row.data, classroom_id=row.classroom_id, session_id=row.session_id)
        return bool(muted_scope_set(row.recipient_id, cands, now))
    except Exception:
        logger.exception("scope-mute check failed (notification=%s)", getattr(row, "id", None))
        return False


def filter_unmuted_users(user_ids, scope_type, scope_id, notif_type=None, now=None) -> list:
    """Multi-recipient twin of push_muted_for_row: `user_ids` minus everybody who muted (scope_type, scope_id).
    ONE query for the whole audience. Fail-open."""
    ids = [u for u in dict.fromkeys(user_ids) if u is not None]
    if not ids or not _present(scope_id):
        return ids
    try:
        if notif_type is not None and not scope_mutable(notif_type):
            return ids
        muted = set(
            active_mutes_q(now).filter(user_id__in=ids, scope_type=scope_type, scope_id=_s(scope_id))
            .values_list("user_id", flat=True)
        )
        return [u for u in ids if u not in muted]
    except Exception:
        logger.exception("scope-mute audience filter failed (%s:%s)", scope_type, scope_id)
        return ids


def active_mutes_q(now=None):
    from .models import NotificationScopeMute

    return NotificationScopeMute.objects.filter(models_q_active(now or timezone.now()))


def bell_hidden_user_ids(user_ids, notif_type, data=None, classroom_id=None, now=None) -> set:
    """N9b - recipients (of `user_ids`) who muted a scope this notification belongs to WITH `hide_bell`: no bell row is
    written for them. ONE query for the whole audience (the scopes are the same for every recipient of one fan-out).
    Locked / safety / call types are never hidden (scope_mutable). Fail-open: an error means "write the row"."""
    ids = [u for u in dict.fromkeys(user_ids) if u is not None]
    if not ids:
        return set()
    try:
        if not scope_mutable(notif_type):
            return set()
        cands = scopes_for(notif_type, data, classroom_id=classroom_id)
        if not cands:
            return set()
        from django.db.models import Q

        cond = Q()
        for st, sid in cands:
            cond |= Q(scope_type=st, scope_id=sid)
        return set(
            active_mutes_q(now).filter(user_id__in=ids, hide_bell=True).filter(cond).values_list("user_id", flat=True)
        )
    except Exception:
        logger.exception("bell-hide check failed (type=%s)", notif_type)
        return set()


class MuteError(ValueError):
    """Bad input for set_mute (the view turns it into a 400 with `code`)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_duration(duration=None, minutes=None):
    """-> timedelta | None (forever). `duration` preset wins; else `minutes`; neither = forever."""
    if duration not in (None, ""):
        if duration not in MUTE_PRESETS:
            raise MuteError("bad_duration", f"duration must be one of: {', '.join(MUTE_PRESETS)}.")
        mins = MUTE_PRESETS[duration]
        return None if mins is None else timedelta(minutes=mins)
    if minutes in (None, ""):
        return None
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        raise MuteError("bad_minutes", "minutes must be a whole number.")
    if not 1 <= mins <= MAX_MUTE_MINUTES:
        raise MuteError("bad_minutes", f"minutes must be between 1 and {MAX_MUTE_MINUTES}.")
    return timedelta(minutes=mins)


def clean_scope(scope_type, scope_id) -> tuple[str, str]:
    st, sid = _s(scope_type).lower(), _s(scope_id)
    if st not in SCOPE_TYPES:
        raise MuteError("bad_scope_type", f"scope_type must be one of: {', '.join(SCOPE_TYPES)}.")
    if not sid or len(sid) > 40 or any(c in sid for c in " \t\r\n/\\'\""):
        raise MuteError("bad_scope_id", "scope_id is missing or malformed.")
    return st, sid


def set_mute(user, scope_type, scope_id, *, duration=None, minutes=None, label="", hide_bell=None, now=None):
    """Create / extend / shorten the mute (idempotent per (user, scope)). -> (row, created).

    `hide_bell` (N9b): True = new notifications of this class / chat / post are not even written to the bell while the
    mute lasts; False = only the buzz is dropped (default for a new mute); None = keep what the row already has."""
    from .models import NotificationScopeMute

    st, sid = clean_scope(scope_type, scope_id)
    delta = parse_duration(duration, minutes)
    now = now or timezone.now()
    until = None if delta is None else now + delta
    label = _s(label)[:_LABEL_MAX]
    existing = NotificationScopeMute.objects.filter(user=user, scope_type=st, scope_id=sid).first()
    if existing is None:
        if active_mutes(user.id, now).count() >= MAX_ACTIVE_MUTES:
            raise MuteError("too_many", f"You can mute at most {MAX_ACTIVE_MUTES} things at once.")
    defaults = {"muted_until": until}
    if label:
        defaults["label"] = label
    if hide_bell is not None:
        defaults["hide_bell"] = bool(hide_bell)
    row, created = NotificationScopeMute.objects.update_or_create(user=user, scope_type=st, scope_id=sid, defaults=defaults)
    return row, created


def clear_mute(user, scope_type, scope_id) -> int:
    from .models import NotificationScopeMute

    st, sid = clean_scope(scope_type, scope_id)
    deleted, _ = NotificationScopeMute.objects.filter(user=user, scope_type=st, scope_id=sid).delete()
    return deleted


def mute_dict(row, now=None) -> dict:
    now = now or timezone.now()
    return {
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "label": row.label,
        "muted_until": row.muted_until.isoformat() if row.muted_until else None,
        "forever": row.muted_until is None,
        "hide_bell": bool(getattr(row, "hide_bell", False)),
        "active": row.is_active(now),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------------------------------------------------
# 3. "Why am I seeing this?"
# ---------------------------------------------------------------------------------------------------------------------
# group key (Notification.preference_groups()) -> (code, English, Hindi). `{who}` = "@username" or "Someone".
_WHY_BY_GROUP = {
    "classes": (
        "class_member",
        "You are a member of this class, so its updates reach you.",
        "आप इस क्लास के सदस्य हैं, इसलिए इसके अपडेट आपको मिलते हैं।",
    ),
    "assignments_tests": (
        "work_assigned",
        "This assignment / test is yours - you are enrolled where it was posted.",
        "यह असाइनमेंट / टेस्ट आपके लिए है - आप वहाँ जुड़े हैं जहाँ यह पोस्ट हुआ।",
    ),
    "messages_calls": (
        "conversation",
        "{who} wrote to you or in a chat you are part of.",
        "{who} ने आपको या आपके किसी चैट में लिखा है।",
    ),
    "social": (
        "social_activity",
        "This is activity on your account or on someone you follow.",
        "यह आपके अकाउंट या आपके फ़ॉलो किए व्यक्ति की गतिविधि है।",
    ),
    "learning": (
        "learning_nudge",
        "You keep learning reminders on, so we nudge you about your study progress.",
        "आपने लर्निंग रिमाइंडर चालू रखे हैं, इसलिए आपकी पढ़ाई की प्रगति पर याद दिलाते हैं।",
    ),
    "payments": (
        "money",
        "This is about your wallet, pass or fees.",
        "यह आपके वॉलेट, पास या फ़ीस से जुड़ा है।",
    ),
    "campus": (
        "campus_member",
        "You are linked to this campus (as student, parent or staff), so its updates reach you.",
        "आप इस कैंपस से जुड़े हैं (छात्र, अभिभावक या स्टाफ़), इसलिए इसके अपडेट आपको मिलते हैं।",
    ),
    "safety": (
        "safety",
        "This is a safety or account-rights notice. It is always delivered and cannot be turned off.",
        "यह सुरक्षा या अकाउंट-अधिकार की सूचना है। यह हमेशा भेजी जाती है और बंद नहीं हो सकती।",
    ),
    "other": (
        "account_update",
        "This is an update about your account.",
        "यह आपके अकाउंट का एक अपडेट है।",
    ),
}

# type-specific lines that are clearer than the group line. (type value -> (code, en, hi))
_WHY_BY_TYPE: dict[str, tuple[str, str, str]] = {}


def _build_type_overrides():
    from .models import Notification

    NT = Notification.NotifType

    def put(names, entry):
        for n in names:
            member = getattr(NT, n, None)
            if member is not None:
                _WHY_BY_TYPE[member.value] = entry

    put(("POST_LIKED", "POST_REPOSTED", "STORY_REACTION"), (
        "own_content",
        "{who} reacted to something you posted.",
        "{who} ने आपकी पोस्ट पर रिएक्ट किया।",
    ))
    put(("POST_COMMENTED",), (
        "own_content",
        "{who} commented on something you posted or on a thread you are in.",
        "{who} ने आपकी पोस्ट या आपके थ्रेड पर कमेंट किया।",
    ))
    put(("FOLLOW_REQUEST_RECEIVED",), (
        "follow_request",
        "{who} asked to follow you and your account needs your approval.",
        "{who} आपको फ़ॉलो करना चाहता है और आपके अकाउंट को आपकी मंज़ूरी चाहिए।",
    ))
    put(("FOLLOW_REQUEST_ACCEPTED",), (
        "follow_accepted",
        "{who} accepted the follow request you sent.",
        "{who} ने आपका भेजा फ़ॉलो रिक्वेस्ट मंज़ूर किया।",
    ))
    put(("NEW_POST_FROM_FOLLOWED",), (
        "following",
        "You follow {who}, so new posts from them show up here.",
        "आप {who} को फ़ॉलो करते हैं, इसलिए उनकी नई पोस्ट यहाँ दिखती हैं।",
    ))
    put(("MENTION", "STORY_MENTION", "POST_TAG"), (
        "mentioned",
        "{who} mentioned or tagged you.",
        "{who} ने आपको मेंशन या टैग किया।",
    ))
    put(("INCOMING_CALL",), (
        "call",
        "Someone called you. Call alerts are always delivered.",
        "किसी ने आपको कॉल किया। कॉल अलर्ट हमेशा भेजे जाते हैं।",
    ))
    put(("STREAK_AT_RISK", "STREAK_MILESTONE_REACHED"), (
        "streak",
        "You are on a study streak, so we remind you before it breaks and celebrate milestones.",
        "आपकी स्टडी स्ट्रीक चल रही है, इसलिए टूटने से पहले याद दिलाते हैं और माइलस्टोन मनाते हैं।",
    ))
    put(("STUDY_REMINDER",), (
        "study_reminder",
        "You set a daily study reminder in Settings > Learning, so we nudge you at the time you chose.",
        "आपने सेटिंग्स > लर्निंग में रोज़ की पढ़ाई की याद लगाई है, इसलिए चुने हुए समय पर हम याद दिलाते हैं।",
    ))
    put(("REVISION_DECK_REMINDER",), (
        "revision",
        "You saved items to your Revision deck, so we nudge you now and then (at most one reminder every few days).",
        "आपने रिवीज़न डेक में चीज़ें सेव की हैं, इसलिए हम समय-समय पर याद दिलाते हैं (कुछ दिनों में अधिकतम एक)।",
    ))
    put(("WEEKLY_RECAP_READY", "CONTENT_DIGEST"), (
        "digest",
        "You get a periodic recap / study digest. Change how often in Notification settings.",
        "आपको समय-समय पर रीकैप / स्टडी डाइजेस्ट मिलता है। कितनी बार - यह नोटिफिकेशन सेटिंग्स में बदलें।",
    ))
    put(("SESSION_REMINDER", "SESSION_LIVE", "CAMPUS_CLASS_STARTING", "CAMPUS_SESSION_LIVE"), (
        "class_live",
        "A class you are part of is starting or live now.",
        "आपकी किसी क्लास की लाइव शुरू होने वाली है या अभी चल रही है।",
    ))
    put(("TESTSERIES_DUE_SOON", "assigments_DUE_SOON", "assigments_DUE_REMINDER", "HOMEWORK_DIARY_DUE"), (
        "deadline",
        "You have a deadline coming up and deadline reminders are on.",
        "आपकी एक डेडलाइन पास है और डेडलाइन रिमाइंडर चालू हैं।",
    ))
    put(("DOUBT_ANSWERED", "QUERY_ANSWERED", "TESTSERIES_QUERY_ANSWERED"), (
        "your_question",
        "Someone answered a question you asked.",
        "किसी ने आपके पूछे सवाल का जवाब दिया।",
    ))
    put(("REFERRAL_REWARD",), (
        "referral",
        "Someone joined with your invite, so you earned a reward.",
        "आपके इन्वाइट से कोई जुड़ा, इसलिए आपको रिवॉर्ड मिला।",
    ))
    put(("PURCHASE_RECEIPT",), (
        "receipt",
        "You bought coins or a pass, so we send the receipt. Receipts are always delivered.",
        "आपने कॉइन या पास ख़रीदा, इसलिए रसीद भेजी गई। रसीद हमेशा भेजी जाती है।",
    ))
    put(("SUPPORT_REPLY",), (
        "support",
        "Our support team replied to your request.",
        "हमारी सपोर्ट टीम ने आपके अनुरोध का जवाब दिया।",
    ))


def _group_of(notif_type) -> str:
    from .models import Notification

    cache = getattr(_group_of, "_cache", None)
    if cache is None:
        cache = {}
        for key, types in Notification.preference_groups().items():
            for t in types:
                cache.setdefault(t, key)
        _group_of._cache = cache
    return cache.get(_s(notif_type), "other")


def why_for(row) -> dict:
    """{code, en, hi, group, locked, control, scope}  - NO database access.

    control: "locked"        cannot be switched off (safety / money failed / calls ...)
             "type_settings" switch it in Settings -> Notifications (the group / type matrix)
    scope:   {scope_type, scope_id} | None  the class / chat / post this belongs to (the app offers "mute this ...")"""
    from .models import Notification

    if not _WHY_BY_TYPE:
        _build_type_overrides()
    t = _s(row.notif_type)
    group = _group_of(t)
    code, en, hi = _WHY_BY_TYPE.get(t) or _WHY_BY_GROUP.get(group) or _WHY_BY_GROUP["other"]
    data = row.data if isinstance(row.data, dict) else {}
    who_en = f"@{data['actor_username']}" if _present(data.get("actor_username")) else "Someone"
    who_hi = f"@{data['actor_username']}" if _present(data.get("actor_username")) else "किसी ने"
    locked = not scope_mutable(t) or t in Notification.locked_types()
    scopes = scopes_for(t, data, classroom_id=getattr(row, "classroom_id", None))
    scope = None
    if not locked and scopes:
        scope = {"scope_type": scopes[0][0], "scope_id": scopes[0][1]}
    return {
        "code": code,
        "en": en.replace("{who}", who_en),
        "hi": hi.replace("{who}", who_hi),
        "group": group,
        "locked": bool(locked),
        "control": "locked" if locked else "type_settings",
        "scope": scope,
    }


# ---------------------------------------------------------------------------------------------------------------------
# 4. Sound / vibration per category (Android channel)
# ---------------------------------------------------------------------------------------------------------------------
SOUND_DEFAULT = "default"
SOUND_GENTLE = "gentle"  # sound plays, but no heads-up pop-over
SOUND_SILENT = "silent"
SOUND_CHOICES = (SOUND_DEFAULT, SOUND_GENTLE, SOUND_SILENT)
#: channels that can never be fully silent (calls ring; the default channel carries safety alerts). Vibration is free.
NEVER_SILENT_CHANNELS = frozenset({"calls", "notifications"})


def sound_channels() -> tuple:
    from message.push_channels import ALL_CHANNELS

    return tuple(ALL_CHANNELS)


def clean_sound_prefs(value, current=None) -> dict:
    """PATCH value {channel: {sound?, vibrate?} | null} merged into `current` -> new stored dict (deviations only).
    null / {} / all-default entry removes the channel. Raises ValueError(message) on bad input."""
    if not isinstance(value, dict):
        raise ValueError("sound_prefs must be an object {channel: {sound, vibrate}}.")
    valid = set(sound_channels())
    merged = {k: dict(v) for k, v in (current or {}).items() if isinstance(v, dict) and k in valid}
    for channel, entry in value.items():
        if channel not in valid:
            raise ValueError(f"Unknown channel '{channel}'.")
        if entry is None or entry == {}:
            merged.pop(channel, None)
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"'{channel}' must be an object {{sound, vibrate}} or null.")
        cur = merged.get(channel, {})
        for key, v in entry.items():
            if key == "sound":
                if v is None:
                    cur.pop("sound", None)
                    continue
                if v not in SOUND_CHOICES:
                    raise ValueError(f"sound must be one of: {', '.join(SOUND_CHOICES)}.")
                if v == SOUND_SILENT and channel in NEVER_SILENT_CHANNELS:
                    raise ValueError(f"'{channel}' alerts cannot be silent (calls and safety alerts must be heard).")
                cur["sound"] = v
            elif key == "vibrate":
                if v is None:
                    cur.pop("vibrate", None)
                    continue
                if not isinstance(v, bool):
                    raise ValueError("vibrate must be true or false.")
                cur["vibrate"] = v
            else:
                raise ValueError(f"Unknown key '{key}' (use: sound, vibrate).")
        if cur.get("sound") == SOUND_DEFAULT:
            cur.pop("sound")
        if cur.get("vibrate") is True:
            cur.pop("vibrate")
        if cur:
            merged[channel] = cur
        else:
            merged.pop(channel, None)
    return merged


def sound_for(pref, channel_id) -> tuple[str, bool]:
    """(sound, vibrate) the user chose for `channel_id` (defaults: ("default", True)). `pref` may be None."""
    entry = ((getattr(pref, "sound_prefs", None) or {}).get(_s(channel_id))) if pref is not None else None
    entry = entry if isinstance(entry, dict) else {}
    sound = entry.get("sound") if entry.get("sound") in SOUND_CHOICES else SOUND_DEFAULT
    if sound == SOUND_SILENT and _s(channel_id) in NEVER_SILENT_CHANNELS:
        sound = SOUND_DEFAULT  # a stale / hand-edited value can never silence a call or a safety alert
    vibrate = entry.get("vibrate") is not False
    return sound, vibrate


def push_sound_data(pref, channel_id) -> dict:
    """Strings for the push `data` map (FCM data is strings only). Empty when the user kept the defaults, so a normal
    push stays exactly as small as before."""
    sound, vibrate = sound_for(pref, channel_id)
    out = {}
    if sound != SOUND_DEFAULT:
        out["snd"] = sound
    if not vibrate:
        out["vib"] = "0"
    return out
