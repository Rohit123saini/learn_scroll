"""
tuitionclass/recording_tools.py  (X12 - auto recording)

Helpers shared by the recording features. Two layers, same split as office_convert.py / post/lectures.py:

  1. PURE functions (no DB, no ffmpeg, no request) - chapter offsets, clip validation, shifting chapters into a clip,
     peak concurrent viewers, timeline buckets, watch-ping clamping, question detection. Unit-tested without a database
     (test_recording_auto.py).
  2. Small DB helpers that use them - `add_chapter`, `seed_start_chapter`, `build_insights`, `viewer_rows`. Models are
     imported inside the functions so this module stays importable while migrations load.

Timeline rule: every offset (chapter, replay chat, peak viewers) is measured from ClassSession.recording_started_at,
i.e. from second 0 of the recording video. A session that was never recorded has no timeline, so no chapter is made.
"""
import logging
import re
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_CHAPTERS_PER_SESSION = 120
MAX_VIDEO_CHAPTERS = 60              # post.lectures.MAX_CHAPTERS - a published video holds at most this many
MIN_CHAPTER_GAP_SECONDS = 3          # two markers closer than this (same title) are one marker
CHAPTER_TITLE_MAX = 120

MIN_CLIP_SECONDS = 5
MAX_REEL_SECONDS = 120               # a reel is at most 2 minutes
MAX_CLIP_SECONDS = 4 * 3600          # a lecture / post clip is at most 4 hours (one egress file is never longer)
DURATION_SLACK_SECONDS = 2           # egress duration and our own estimate can differ by a second or two

MAX_WATCH_PING_SECONDS = 90          # one replay ping can never add more than this
FIRST_PING_MAX_SECONDS = 30
WATCH_COMPLETE_RATIO = 0.95
WATCH_COMPLETE_TAIL_SECONDS = 10

INSIGHT_BUCKETS = 24
TOP_QUESTIONS = 5

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_QUESTION_WORDS = {
    "what", "why", "how", "when", "where", "which", "who", "whom", "whose", "can", "could", "is", "are", "do", "does",
    "kya", "kyu", "kyun", "kyon", "kaise", "kaisa", "kaun", "kab", "kahan", "kitna", "kitne", "kaunsa", "kaunsi",
}


class ClipError(ValueError):
    """Carries a machine `code` and a user-safe `message` (the view turns it into a 400)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ----------------------------------------------------------------------------- chapters (pure)
def clean_title(raw, limit=CHAPTER_TITLE_MAX):
    return " ".join(_CONTROL.sub(" ", str(raw or "")).split())[:limit]


def chapter_offset(started_at, now=None):
    """Whole seconds from the start of the recording to `now`, or None when no recording timeline exists."""
    if started_at is None:
        return None
    now = now or timezone.now()
    seconds = int((now - started_at).total_seconds())
    return max(seconds, 0)


def shift_chapters(chapters, start, end, fallback_title="Start", limit=MAX_VIDEO_CHAPTERS):
    """Session chapters [(offset, title), ...] -> chapters of the clip [start, end): [{'start': s, 'title': t}, ...].

    * markers inside the clip move back by `start`;
    * the marker that was ACTIVE when the clip begins (the last one before it) becomes the clip's 0:00 chapter, so a
      clip cut in the middle of a presentation still opens with its name;
    * a video player needs a chapter at 0:00 - when there is none a `fallback_title` one is added;
    * same second twice keeps the last title; at most `limit` chapters."""
    ordered = sorted(((int(o), clean_title(t)) for o, t in chapters if clean_title(t)), key=lambda x: x[0])
    inside = {}
    active = None
    for offset, title in ordered:
        if offset < start:
            active = title
        elif offset < end:
            inside[offset - start] = title
    if 0 not in inside:
        inside[0] = active or clean_title(fallback_title) or "Start"
    out = [{"start": s, "title": t} for s, t in sorted(inside.items())]
    return out[:limit]


# ----------------------------------------------------------------------------- clip validation (pure)
def estimated_duration(recording_duration_seconds, recording_started_at, actual_end):
    """Best known length of the recording file in seconds, or None. The egress webhook's own duration wins; otherwise
    `actual_end - recording_started_at` (the room's end closes the egress, so they match to within a few seconds)."""
    if recording_duration_seconds:
        return int(recording_duration_seconds)
    if recording_started_at is not None and actual_end is not None and actual_end > recording_started_at:
        return int((actual_end - recording_started_at).total_seconds())
    return None


def clamp_clip(kind, start, end, duration=None):
    """Validate the trim range of a save request -> (start, end) ints. Raises ClipError.

    A range that runs past the file is pulled back to the file's end (within DURATION_SLACK_SECONDS it is accepted as
    is); a reel longer than MAX_REEL_SECONDS is REFUSED (never silently cut - the teacher chose that range)."""
    try:
        start_i, end_i = int(float(start)), int(float(end))
    except (TypeError, ValueError):
        raise ClipError("invalid_range", "Start and end must be numbers of seconds.")
    if start_i < 0:
        raise ClipError("invalid_range", "Start cannot be negative.")
    if end_i <= start_i:
        raise ClipError("invalid_range", "End must be after start.")
    if duration:
        if start_i >= duration:
            raise ClipError("range_outside_recording", "The start is after the end of the recording.")
        if end_i > duration + DURATION_SLACK_SECONDS:
            end_i = int(duration)
    length = end_i - start_i
    if length < MIN_CLIP_SECONDS:
        raise ClipError("clip_too_short", f"A clip must be at least {MIN_CLIP_SECONDS} seconds long.")
    if kind == "reel" and length > MAX_REEL_SECONDS:
        raise ClipError("reel_too_long", f"A reel can be at most {MAX_REEL_SECONDS} seconds ({MAX_REEL_SECONDS // 60} minutes).")
    if length > MAX_CLIP_SECONDS:
        raise ClipError("clip_too_long", "This clip is too long. Pick a shorter range.")
    return start_i, end_i


def post_visibility(choice):
    """RecordingSave.visibility -> (Post.visibility, class_gated). `class_only` is the safe default for anything unknown:
    an unrecognised value can only ever make the clip MORE private, never public."""
    mapping = {
        "public": ("public", False),
        "followers": ("connections", False),
        "class_only": ("class_campus", True),
    }
    return mapping.get(str(choice or "").strip().lower(), mapping["class_only"])


# ----------------------------------------------------------------------------- replay watch (pure)
def clamp_watch_delta(delta, last_ping_at, now, first=False):
    """Seconds a replay ping may add to `watched_seconds`: what the client claims, never more than the wall clock allows
    (2x for fast playback + 2 s of slack), never more than MAX_WATCH_PING_SECONDS, and a first ping counts little."""
    try:
        claimed = max(int(float(delta)), 0)
    except (TypeError, ValueError):
        return 0
    cap = FIRST_PING_MAX_SECONDS if (first or last_ping_at is None) else MAX_WATCH_PING_SECONDS
    if last_ping_at is not None:
        elapsed = max((now - last_ping_at).total_seconds(), 0)
        cap = min(cap, int(elapsed * 2) + 2)
    return min(claimed, cap)


def watch_completed(position, duration):
    if not duration:
        return False
    return position >= duration * WATCH_COMPLETE_RATIO or position >= max(duration - WATCH_COMPLETE_TAIL_SECONDS, 0)


# ----------------------------------------------------------------------------- viewers over time (pure)
def merge_intervals(intervals):
    """[(start, end), ...] -> merged, sorted, non-overlapping (touching intervals join)."""
    cleaned = sorted((s, e) for s, e in intervals if s is not None and e is not None and e >= s)
    out = []
    for s, e in cleaned:
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def peak_concurrent(per_user_intervals):
    """{user_id: [(join, leave), ...]} -> (peak, peak_at). One person who rejoined is counted once at any instant.
    A leave and a join at the same instant do not overlap (leave is processed first)."""
    events = []
    for intervals in per_user_intervals.values():
        for s, e in merge_intervals(intervals):
            events.append((s, 1))
            events.append((e, -1))
    if not events:
        return 0, None
    events.sort(key=lambda ev: (ev[0], ev[1]))   # -1 sorts before +1 at the same time
    current = peak = 0
    peak_at = None
    for at, delta in events:
        current += delta
        if current > peak:
            peak, peak_at = current, at
    return peak, peak_at


def timeline_buckets(per_user_intervals, t0, t1, buckets=INSIGHT_BUCKETS):
    """Viewers present at the middle of each of `buckets` equal slices of [t0, t1] -> [{'t': offset_seconds, 'viewers': n}]."""
    if t0 is None or t1 is None or t1 <= t0 or buckets < 1:
        return []
    merged = {uid: merge_intervals(iv) for uid, iv in per_user_intervals.items()}
    span = (t1 - t0).total_seconds()
    step = span / buckets
    out = []
    for i in range(buckets):
        mid = t0 + timedelta(seconds=step * i + step / 2)
        n = sum(1 for iv in merged.values() if any(s <= mid <= e for s, e in iv))
        out.append({"t": int(step * i), "viewers": n})
    return out


def is_question(text):
    """Heuristic for a chat line that is a question: has a '?' or opens with a question word (English / Hinglish)."""
    t = str(text or "").strip()
    if not t:
        return False
    if "?" in t or "\uff1f" in t:
        return True
    first = re.split(r"[\s,.!:;]+", t.lower(), maxsplit=1)[0]
    return first in _QUESTION_WORDS


# ----------------------------------------------------------------------------- DB helpers
def add_chapter(session, kind, title, *, user=None, ref_type="", ref_id=None):
    """Drop a marker at "now" on the recording timeline. Returns the SessionChapter or None.

    Best-effort and silent by design: it is called from the present / poll views and must never fail them. No marker is
    made unless a recording is running (egress_id + recording_started_at) - without a video there is no timeline."""
    try:
        from .models import SessionChapter
        from .realtime import broadcast_to_session

        if not getattr(session, "egress_id", "") or getattr(session, "recording_started_at", None) is None:
            return None
        offset = chapter_offset(session.recording_started_at)
        if offset is None:
            return None
        title = clean_title(title)
        if not title:
            return None
        qs = SessionChapter.objects.filter(session=session)
        if qs.count() >= MAX_CHAPTERS_PER_SESSION:
            return None
        if ref_id is not None and qs.filter(ref_type=ref_type, ref_id=ref_id).exists():
            return None
        last = qs.order_by("-offset_seconds", "-id").first()
        if last is not None and last.title == title and offset - last.offset_seconds < MIN_CHAPTER_GAP_SECONDS:
            return None
        chapter = SessionChapter.objects.create(
            session=session, kind=kind, title=title, offset_seconds=offset, ref_type=ref_type or "",
            ref_id=ref_id, created_by=user if getattr(user, "pk", None) else None,
        )
        try:
            broadcast_to_session(session.id, "chapter.added", {"id": chapter.id, "kind": kind, "title": title, "offset_seconds": offset})
        except Exception:
            logger.exception("chapter.added broadcast failed for session %s", session.id)
        return chapter
    except Exception:
        logger.exception("add_chapter failed for session %s (%s)", getattr(session, "id", None), kind)
        return None


def seed_start_chapter(session):
    """The recording just started: put "Class started" at 0:00 (idempotent)."""
    try:
        from .models import SessionChapter

        if SessionChapter.objects.filter(session=session, kind=SessionChapter.Kind.START).exists():
            return None
        return SessionChapter.objects.create(
            session=session, kind=SessionChapter.Kind.START, title="Class started", offset_seconds=0,
        )
    except Exception:
        logger.exception("seed_start_chapter failed for session %s", getattr(session, "id", None))
        return None


def reset_timeline(session):
    """A NEW recording is starting on this session: the old markers belong to the old video, so they go (the new
    recording gets its own \"Class started\" at 0:00). Never raises."""
    try:
        from .models import SessionChapter

        SessionChapter.objects.filter(session=session).delete()
    except Exception:
        logger.exception("reset_timeline failed for session %s", getattr(session, "id", None))


def restamp_chapters(session, delta_seconds):
    """The recording's true 0:00 turned out to be `delta_seconds` LATER than the stamp chapters were measured from
    (LiveKit's egress takes a few seconds to warm up after the API call) -> move every marker back by that much so it
    still lands on the same frame. Never below 0. Never raises."""
    try:
        from .models import SessionChapter

        delta = int(delta_seconds)
        if delta <= 0:
            return 0
        moved = 0
        for ch in SessionChapter.objects.filter(session=session, offset_seconds__gt=0):
            new = max(ch.offset_seconds - delta, 0)
            if new != ch.offset_seconds:
                SessionChapter.objects.filter(pk=ch.pk).update(offset_seconds=new)
                moved += 1
        return moved
    except Exception:
        logger.exception("restamp_chapters failed for session %s", getattr(session, "id", None))
        return 0


def timeline_start(session):
    """Moment that is 0:00 of the video (the recording's start), else the class's own start, else None."""
    return getattr(session, "recording_started_at", None) or getattr(session, "actual_start", None)


def _participant_intervals(session, include_hosts=False, include_trial=True):
    """{user_id: [(join, leave), ...]} from SessionParticipant. An open row ends at the session's end (or now)."""
    from .models import SessionParticipant

    end_default = session.actual_end or timezone.now()
    rows = session.participants.all().only("user_id", "role", "is_trial", "joined_at", "left_at")
    out = {}
    for p in rows:
        if p.role == SessionParticipant.Role.HOST and not include_hosts:
            continue
        if p.is_trial and not include_trial:
            continue
        if p.joined_at is None:
            continue
        out.setdefault(p.user_id, []).append((p.joined_at, p.left_at or end_default))
    return out


def build_insights(session):
    """Everything the insights screen shows, as one JSON-able dict (see the module docstring of recording_views)."""
    from django.db.models import Avg, Count, Sum

    from .models import ChatMessage, ClassQuery, LivePoll, PollResponse, RecordingWatch, SessionChapter

    t0 = timeline_start(session)
    end = session.actual_end or timezone.now()
    per_user = _participant_intervals(session)
    peak, peak_at = peak_concurrent(per_user)

    def _off(dt):
        return int((dt - t0).total_seconds()) if (dt is not None and t0 is not None) else None

    watch_seconds = []
    for intervals in per_user.values():
        total = sum((e - s).total_seconds() for s, e in merge_intervals(intervals))
        watch_seconds.append(total)
    avg_live = int(sum(watch_seconds) / len(watch_seconds)) if watch_seconds else 0
    scheduled = max((session.scheduled_end - session.scheduled_start).total_seconds(), 1)

    duration = estimated_duration(session.recording_duration_seconds, session.recording_started_at, session.actual_end)

    watches = RecordingWatch.objects.filter(session=session)
    agg = watches.aggregate(n=Count("id"), total=Sum("watched_seconds"), avg=Avg("watched_seconds"))
    watchers = int(agg["n"] or 0)
    completed = watches.filter(completed=True).count()

    # questions: written doubts tied to this session + chat lines that read like a question (not the teacher's own)
    queries = list(
        ClassQuery.objects.filter(session=session).select_related("asked_by").order_by("-upvote_count", "-created_at")
    )
    teacher_id = session.classroom.teacher_id
    chat = list(
        ChatMessage.objects.filter(session=session, is_deleted=False).select_related("sender").order_by("sent_at")
    )
    chat_questions = [m for m in chat if m.sender_id != teacher_id and is_question(m.message)]
    top = [
        {
            "source": "query", "id": q.id, "text": q.question[:300], "by": q.asked_by.get_full_name() or q.asked_by.username,
            "upvotes": q.upvote_count, "offset_seconds": _off(q.created_at), "answered": q.status == ClassQuery.Status.ANSWERED,
        }
        for q in queries[:TOP_QUESTIONS]
    ]
    room = TOP_QUESTIONS - len(top)
    if room > 0:
        for m in chat_questions[:room]:
            top.append({
                "source": "chat", "id": m.id, "text": m.message[:300], "by": m.sender.get_full_name() or m.sender.username,
                "upvotes": 0, "offset_seconds": _off(m.sent_at), "answered": False,
            })

    polls = LivePoll.objects.filter(session=session)
    poll_count = polls.count()
    poll_responses = PollResponse.objects.filter(poll__session=session).count()

    return {
        "session_id": session.id,
        "has_recording": bool(session.recording_url),
        "recording_started_at": session.recording_started_at.isoformat() if session.recording_started_at else None,
        "duration_seconds": duration,
        "live": {
            "attendees": len(per_user),
            "peak_viewers": peak,
            "peak_at_seconds": _off(peak_at),
            "avg_watch_seconds": avg_live,
            "avg_watch_percent": round(min(avg_live / scheduled, 1.0) * 100, 1),
            "timeline": timeline_buckets(per_user, t0, end),
        },
        "replay": {
            "watchers": watchers,
            "total_watch_seconds": int(agg["total"] or 0),
            "avg_watch_seconds": int(agg["avg"] or 0),
            "completed": completed,
            "completion_rate": round(completed / watchers * 100, 1) if watchers else 0.0,
        },
        "questions": {
            "count": len(queries) + len(chat_questions),
            "written": len(queries),
            "answered": sum(1 for q in queries if q.status == ClassQuery.Status.ANSWERED),
            "in_chat": len(chat_questions),
            "top": top,
        },
        "chat": {"messages": len(chat), "chatters": len({m.sender_id for m in chat})},
        "polls": {"count": poll_count, "responses": poll_responses},
        "chapters": SessionChapter.objects.filter(session=session).count(),
        "generated_at": timezone.now().isoformat(),
    }


def viewer_rows(session):
    """One row per person who attended live or watched the replay (for the teacher's viewer list):
    [{'user': User, 'is_trial', 'attendance_status', 'live_seconds', 'joined_at', 'replay_watched_seconds',
      'replay_completed', 'replay_last_watched_at'}], most active first."""
    from .models import RecordingWatch, SessionAttendance, SessionParticipant

    rows = {}
    end_default = session.actual_end or timezone.now()
    parts = session.participants.filter(role=SessionParticipant.Role.STUDENT).select_related("user")
    intervals = {}
    for p in parts:
        if p.joined_at is None:
            continue
        entry = rows.setdefault(p.user_id, {
            "user": p.user, "is_trial": bool(p.is_trial), "attendance_status": "", "live_seconds": 0, "joined_at": p.joined_at,
            "replay_watched_seconds": 0, "replay_completed": False, "replay_last_watched_at": None,
        })
        if p.joined_at < entry["joined_at"]:
            entry["joined_at"] = p.joined_at
        entry["is_trial"] = entry["is_trial"] and bool(p.is_trial)
        intervals.setdefault(p.user_id, []).append((p.joined_at, p.left_at or end_default))
    for uid, iv in intervals.items():
        rows[uid]["live_seconds"] = int(sum((e - s).total_seconds() for s, e in merge_intervals(iv)))

    status = dict(SessionAttendance.objects.filter(session=session).values_list("student_id", "status"))
    for uid, st in status.items():
        if uid in rows:
            rows[uid]["attendance_status"] = st

    for w in RecordingWatch.objects.filter(session=session).select_related("user"):
        entry = rows.setdefault(w.user_id, {
            "user": w.user, "is_trial": False, "attendance_status": status.get(w.user_id, ""), "live_seconds": 0,
            "joined_at": None, "replay_watched_seconds": 0, "replay_completed": False, "replay_last_watched_at": None,
        })
        entry["replay_watched_seconds"] = w.watched_seconds
        entry["replay_completed"] = w.completed
        entry["replay_last_watched_at"] = w.last_watched_at

    return sorted(rows.values(), key=lambda r: (-(r["live_seconds"] + r["replay_watched_seconds"]), str(r["user"].pk)))
