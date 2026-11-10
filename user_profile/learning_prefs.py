"""
user_profile/learning_prefs.py - S8: Learning preferences, ek jagah.

Kya hai: student ki padhai se judi saari choices ek hi screen se - exam target / class / subjects / exam date /
Exam Mode (pehle se), daily goal (pehle se, `Streak`), aur NAYA: study reminder time + days, focus-mode schedule,
recommended content level, content languages. Yeh file sirf PURE logic rakhti hai (validation, schedule maths,
feed hashtags, summary) taaki serializer / feed / reminder task / tests sab ek hi jagah se kaam lein.

Kaun kya padhta hai:
  * feed           post/feed_exam.py (focus window = Exam Mode, level + language hashtags) + feed_context.py (points)
  * notifications  core/models.py NotificationPreference.focus_active (focus window => sirf urgent push),
                   core/study_reminders.py (study reminder push)
  * summary        GET /profile/learning/ (UserPreference + Streak ko jodkar "ye choices feed / notifications ko
                   aise badalti hain" wali lines)

Rules:
  * Weekdays Python ke weekday() jaise: 0 = Monday ... 6 = Sunday. Khaali list = har din.
  * Sab time user ke LOCAL time me hain (NotificationPreference.timezone, N5 sync) - server ka time nahi.
  * Koi bhi function kharab data par raise nahi karta jab wo sirf PADH raha ho (feed / push kabhi nahi tootna chahiye);
    validation functions (`normalize_*`, `parse_hhmm`) ValueError dete hain, serializer unhe 400 bana deta hai.
"""
from __future__ import annotations

import datetime
from typing import Iterable, List, Optional, Sequence

MAX_FOCUS_WINDOWS = 3
MAX_CONTENT_LANGUAGES = 3
#: Reminder us time se is-itne minute ke andar hi bheja jaata hai (DND / quiet hours me deri ho to bhi) - uske baad
#: "aaj chhoot gaya", purana reminder kabhi raat 11 baje nahi aata.
REMINDER_GRACE_MINUTES = 180

LEVELS = ("beginner", "intermediate", "advanced")

#: Post hashtags (lower-case, no '#') jo ek level ka signal dete hain. Feed ka boost inhi se match hota hai
#: (exam tags jaisa - Post me alag "level" column nahi hai).
LEVEL_TAGS = {
    "beginner": ["beginner", "beginners", "basics", "basic", "foundation", "starter", "ncertbasics"],
    "intermediate": ["intermediate", "practice", "pyq", "revision", "midlevel"],
    "advanced": ["advanced", "jeeadvanced", "olympiad", "hots", "deepdive", "tough"],
}

#: Language code -> hashtag spellings that mark content as being in that language.
LANGUAGE_TAGS = {
    "en": ["english", "englishmedium"],
    "hi": ["hindi", "hinglish", "hindimedium"],
    "bn": ["bengali", "bangla"],
    "mr": ["marathi"],
    "ta": ["tamil"],
    "te": ["telugu"],
    "gu": ["gujarati"],
    "kn": ["kannada"],
    "ml": ["malayalam"],
    "pa": ["punjabi"],
    "ur": ["urdu"],
    "or": ["odia", "oriya"],
    "as": ["assamese"],
}


# ---------------------------------------------------------------------------------------------------------------------
# validation (raise ValueError)
# ---------------------------------------------------------------------------------------------------------------------
def parse_hhmm(value) -> datetime.time:
    """'18:30' / '18:30:00' / datetime.time -> datetime.time (seconds hata di jaati hain). Kharab -> ValueError."""
    if isinstance(value, datetime.time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if not isinstance(value, str):
        raise ValueError("time 'HH:MM' hona chahiye.")
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError("time 'HH:MM' hona chahiye.")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time 00:00 se 23:59 ke beech hona chahiye.")
    return datetime.time(hh, mm)


def fmt_hhmm(t: datetime.time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def normalize_days(value) -> List[int]:
    """Weekday list -> sorted, unique ints 0..6. [] = har din. Kharab -> ValueError."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("days ek list honi chahiye (0 = Monday ... 6 = Sunday).")
    out = set()
    for d in value:
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6:
            raise ValueError("days me sirf 0 (Monday) se 6 (Sunday) tak ke numbers hone chahiye.")
        out.add(d)
    return sorted(out)


def normalize_focus_windows(value) -> List[dict]:
    """Focus schedule -> canonical [{"days": [...], "start": "HH:MM", "end": "HH:MM"}], max MAX_FOCUS_WINDOWS,
    duplicate windows hata di jaati hain. start == end (0 minute) refuse. end < start = raat paar (valid)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("focus_schedule ek list honi chahiye.")
    out: List[dict] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Har focus window {days, start, end} hona chahiye.")
        days = normalize_days(raw.get("days", []))
        start = parse_hhmm(raw.get("start"))
        end = parse_hhmm(raw.get("end"))
        if start == end:
            raise ValueError("Focus window ka start aur end alag hone chahiye.")
        win = {"days": days, "start": fmt_hhmm(start), "end": fmt_hhmm(end)}
        if win not in out:
            out.append(win)
    if len(out) > MAX_FOCUS_WINDOWS:
        raise ValueError(f"Zyada se zyada {MAX_FOCUS_WINDOWS} focus windows rakh sakte ho.")
    return out


def normalize_languages(value, supported: Iterable[str]) -> List[str]:
    """Content languages -> lower-case primary codes, unique, order kept, max MAX_CONTENT_LANGUAGES, sirf `supported`."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("content_languages ek list honi chahiye.")
    ok = {str(c).lower() for c in supported}
    out: List[str] = []
    for raw in value:
        code = str(raw or "").strip().lower().split("-")[0]
        if code not in ok:
            raise ValueError(f"Language '{raw}' supported nahi hai.")
        if code not in out:
            out.append(code)
    if len(out) > MAX_CONTENT_LANGUAGES:
        raise ValueError(f"Zyada se zyada {MAX_CONTENT_LANGUAGES} languages chun sakte ho.")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# time zone + schedule maths (kabhi raise nahi)
# ---------------------------------------------------------------------------------------------------------------------
def tzinfo_for(name):
    """IANA naam -> tzinfo; kharab / khaali -> None (caller Django ka current timezone use karega)."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / ValueError / OSError
        return None


def tz_from_name(name):
    """NotificationPreference.timezone naam -> tzinfo. "UTC" ek default hai jo kisi ne choose nahi kiya, isliye khaali /
    "UTC" / kharab -> None (caller server timezone use karta hai, content_digest jaisa)."""
    if not name or name == "UTC":
        return None
    return tzinfo_for(name)


def user_tz(user_id):
    """User ka tzinfo (NotificationPreference.timezone, N5 auto-sync). Pata na chale to None."""
    try:
        from core.models import NotificationPreference

        name = NotificationPreference.objects.filter(user_id=user_id).values_list("timezone", flat=True).first()
    except Exception:  # noqa: BLE001
        return None
    return tz_from_name(name)


def local_now(now=None, tz=None) -> datetime.datetime:
    """Aware `now` ko user ke local time me badlo (tz None = Django current timezone)."""
    from django.utils import timezone

    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, datetime.timezone.utc)
    return now.astimezone(tz) if tz is not None else timezone.localtime(now)


def focus_window_active(windows, now=None, tz=None) -> bool:
    """Kya `windows` me se koi abhi chal rahi hai? Raat paar wali window (22:00 -> 06:00) ka subah wala hissa
    PICHLE din ki window ka hota hai (days = jis din window SHURU hoti hai). Kharab window chup-chaap skip."""
    if not windows:
        return False
    local = local_now(now, tz)
    today = local.weekday()
    yesterday = (today - 1) % 7
    t = datetime.time(local.hour, local.minute)
    for w in windows:
        try:
            days = normalize_days(w.get("days", []))
            start, end = parse_hhmm(w.get("start")), parse_hhmm(w.get("end"))
        except (ValueError, AttributeError, TypeError):
            continue
        every = not days
        if start == end:
            continue
        if start < end:
            if (every or today in days) and start <= t < end:
                return True
        else:  # raat paar
            if (every or today in days) and t >= start:
                return True
            if (every or yesterday in days) and t < end:
                return True
    return False


def study_reminder_due(enabled, reminder_time, days, last_on, local, grace_minutes: int = REMINDER_GRACE_MINUTES) -> bool:
    """Kya `local` (user ka local datetime) par study reminder bananaa chahiye? Aaj ke weekday me, time aane ke baad,
    `grace_minutes` ke andar, aur aaj pehle nahi bana (`last_on` = last local date)."""
    if not enabled or reminder_time is None:
        return False
    if last_on == local.date():
        return False
    try:
        wanted = normalize_days(days)
    except ValueError:
        wanted = []
    if wanted and local.weekday() not in wanted:
        return False
    now_min = local.hour * 60 + local.minute
    rem_min = reminder_time.hour * 60 + reminder_time.minute
    delta = now_min - rem_min
    return 0 <= delta <= int(grace_minutes)


# ---------------------------------------------------------------------------------------------------------------------
# feed hashtags
# ---------------------------------------------------------------------------------------------------------------------
def level_tags(level: str) -> List[str]:
    return list(LEVEL_TAGS.get((level or "").strip().lower(), []))


def language_tags(codes: Optional[Sequence[str]]) -> List[str]:
    out: List[str] = []
    for c in codes or ():
        out += LANGUAGE_TAGS.get(str(c).strip().lower().split("-")[0], [])
    return list(dict.fromkeys(out))


def preference_tags(level: str, languages: Optional[Sequence[str]]) -> List[str]:
    """Level + language hashtags jo feed me +points dete hain (feed_context.POINTS_PREF)."""
    return list(dict.fromkeys(level_tags(level) + language_tags(languages)))


# ---------------------------------------------------------------------------------------------------------------------
# summary for GET /profile/learning/
# ---------------------------------------------------------------------------------------------------------------------
def _safe_days(value) -> List[int]:
    """Padhne ke liye: kharab stored data par summary 500 na ho."""
    try:
        return normalize_days(value or [])
    except ValueError:
        return []


def completeness(pref) -> dict:
    """Kitna setup pura hai: {"percent": 0..100, "missing": [field, ...]} - screen "80% - add exam date" dikhata hai."""
    checks = (
        ("exam_target", bool(pref.exam_target)),
        ("class_level", bool(pref.class_level)),
        ("focus_subjects", bool(pref.focus_subjects)),
        ("exam_date", pref.exam_date is not None),
        ("content_level", bool(pref.content_level)),
        ("content_languages", bool(pref.content_languages)),
        ("study_reminder", bool(pref.study_reminder_enabled)),
    )
    done = sum(1 for _, ok in checks if ok)
    return {"percent": round(100 * done / len(checks)), "missing": [name for name, ok in checks if not ok]}


def drives(pref, now=None, tz=None) -> dict:
    """\"Ye choices kya badalti hain\" - CODES (client apni bhasha me likhta hai). feed / notifications alag list."""
    feed: List[str] = []
    notif: List[str] = []
    if pref.exam_target:
        feed.append("exam_target")
    if pref.class_level:
        feed.append("class_level")
    if pref.focus_subjects:
        feed.append("focus_subjects")
    if pref.content_level:
        feed.append("content_level")
    if pref.content_languages:
        feed.append("content_languages")
    if pref.exam_mode_active:
        feed.append("exam_mode")
        notif.append("exam_mode_priority_only")
    if pref.focus_schedule_enabled and pref.focus_schedule:
        feed.append("focus_schedule")
        notif.append("focus_schedule_priority_only")
    if pref.study_reminder_enabled:
        notif.append("study_reminder")
    return {"feed": feed, "notifications": notif}


def build_summary(pref, streak=None, now=None, tz=None) -> dict:
    """Poori Learning screen ek response me. `streak` None ho to daily goal default (10) dikhta hai."""
    from django.conf import settings

    goal = int(getattr(streak, "daily_goal_minutes", None) or 10)
    goal_options = list(getattr(settings, "STREAK_DAILY_GOAL_OPTIONS", (5, 10, 15, 20, 30, 45, 60)))
    today = local_now(now, tz).date()
    days_to_exam = None
    if pref.exam_date is not None:
        diff = (pref.exam_date - today).days
        days_to_exam = diff if diff >= 0 else None
    schedule_active = pref.focus_schedule_active(now=now, tz=tz)
    return {
        "profile": {
            "exam_target": pref.exam_target,
            "class_level": pref.class_level,
            "focus_subjects": list(pref.focus_subjects or []),
            "exam_date": pref.exam_date.isoformat() if pref.exam_date else None,
            "days_to_exam": days_to_exam,
            "exam_mode": bool(pref.exam_mode),
            "exam_mode_active": bool(pref.exam_mode_active),
        },
        "daily_goal": {"minutes": goal, "options": goal_options},
        "study_reminder": {
            "enabled": bool(pref.study_reminder_enabled),
            "time": fmt_hhmm(pref.study_reminder_time),
            "days": _safe_days(pref.study_reminder_days),
        },
        "focus_schedule": {
            "enabled": bool(pref.focus_schedule_enabled),
            "windows": list(pref.focus_schedule or []),
            "active_now": bool(schedule_active),
            "max_windows": MAX_FOCUS_WINDOWS,
        },
        "focus_now": bool(pref.exam_mode_active or schedule_active),
        "content": {
            "level": pref.content_level,
            "levels": list(LEVELS),
            "languages": list(pref.content_languages or []),
            "language_options": [c for c in getattr(settings, "SUPPORTED_LANGUAGES", ("en", "hi")) if c in LANGUAGE_TAGS],
            "max_languages": MAX_CONTENT_LANGUAGES,
        },
        "drives": drives(pref, now=now, tz=tz),
        "completeness": completeness(pref),
    }
