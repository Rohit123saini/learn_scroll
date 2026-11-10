"""
tuitionclass/standalone_live.py  -  X12 L-C: "Go Live" from the "+" hub WITHOUT a classroom

DECISION (md L-C, option a): every creator gets one hidden "personal classroom" (Classroom.is_personal) the first
time they go live, and a standalone live is an ordinary ClassSession of that classroom (ClassSession.live_standalone).
So nothing of the live room is copied: LiveKit, chat, polls, whiteboard, recording, moderation (X12 L-B), reports,
passes all work unchanged. What this module adds on top:

    1. the hidden personal classroom                       personal_classroom_for()
    2. create a live now / scheduled (+ validation)        create_live()
    3. who may see / join it (audience)                    resolve_audience(), open_access_for(), upcoming_for()
    4. reminders ("Remind me", "starts in N minutes")      set_reminder(), send_due_reminders()
    5. minor rule: a minor's live is for their own class   policy_for(), resolve_audience()

Audience of a live (ClassSession.live_audience):
    class      members of the personal classroom (= pass holders / staff) only        (the old behaviour)
    pass       same people, announced as "paid pass" (the pass itself is sold with the usual ClassPass flow)
    followers  accepted followers of the host may join without a pass
    public     everybody (not blocked either way) may join without a pass; NEVER for a minor host

The join gate stays views/sessions.py::_perform_join - it only gained one more way to pass `_has_full_room_access`
(open_access_for below). Bans, kicks, capacity, recording notice, live safety rules are all still checked there.

Pure helpers (clean_title, clean_topic, clean_duration, parse_start, resolve_audience, reminder_due) touch no database
so they can be unit-tested without one (test_standalone_live.py). Everything that does touch it never swallows errors
silently except the push fan-out, which must never break a request.
"""
import logging
import zoneinfo
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------------------------------------------
AUDIENCE_CLASS = "class"
AUDIENCE_PASS = "pass"
AUDIENCE_FOLLOWERS = "followers"
AUDIENCE_PUBLIC = "public"
ALL_AUDIENCES = (AUDIENCE_FOLLOWERS, AUDIENCE_PUBLIC, AUDIENCE_PASS, AUDIENCE_CLASS)
OPEN_AUDIENCES = (AUDIENCE_FOLLOWERS, AUDIENCE_PUBLIC)  # join without a pass
MINOR_AUDIENCES = (AUDIENCE_PASS, AUDIENCE_CLASS)  # what a minor host may pick when MINOR_BLOCK_PUBLIC_LIVE is on

MIN_TITLE_LEN = 3
MAX_TITLE_LEN = 150
MAX_TOPIC_LEN = 120
DEFAULT_DURATION_MINUTES = 60
MIN_DURATION_MINUTES = 10
MAX_DURATION_MINUTES = 240
MIN_LEAD_SECONDS = 120          # a scheduled live must start at least 2 minutes from now (else: "go live now")
MAX_LEAD_DAYS = 30
MAX_UPCOMING_PER_HOST = 10
REMINDER_LEAD_MINUTES = 15
REMINDER_GRACE_MINUTES = 5      # a reminder is still sent up to 5 minutes after the start time (late beat run)
STALE_GO_NOW_MINUTES = 10
THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEZONE = "Asia/Kolkata"

PUSH_TYPE_REMINDER = "live_reminder"
PUSH_TYPE_CANCELLED = "live_cancelled"


class StandaloneLiveError(Exception):
    """A refusal with a stable `code` the app can switch on. Views turn it into {detail, code, ...extra}."""

    def __init__(self, code, message, status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = dict(extra or {})

    def payload(self):
        out = {"detail": self.message, "code": self.code}
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------------------------------------------
# pure helpers (no database)
# ---------------------------------------------------------------------------------------------------------------
def clean_title(raw) -> str:
    title = " ".join(str(raw or "").split())
    if len(title) < MIN_TITLE_LEN:
        raise StandaloneLiveError("title_required", f"Give your live a title (at least {MIN_TITLE_LEN} characters).")
    if len(title) > MAX_TITLE_LEN:
        raise StandaloneLiveError("title_too_long", f"The title can be at most {MAX_TITLE_LEN} characters.")
    return title


def clean_topic(raw) -> str:
    topic = " ".join(str(raw or "").split())
    if len(topic) > MAX_TOPIC_LEN:
        raise StandaloneLiveError("topic_too_long", f"The subject / topic can be at most {MAX_TOPIC_LEN} characters.")
    return topic


def clean_duration(raw) -> int:
    if raw in (None, ""):
        return DEFAULT_DURATION_MINUTES
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        raise StandaloneLiveError("invalid_duration", "Duration must be a number of minutes.")
    if minutes < MIN_DURATION_MINUTES or minutes > MAX_DURATION_MINUTES:
        raise StandaloneLiveError(
            "invalid_duration",
            f"Duration must be between {MIN_DURATION_MINUTES} and {MAX_DURATION_MINUTES} minutes.",
        )
    return minutes


def clean_timezone(raw) -> str:
    name = str(raw or "").strip() or DEFAULT_TIMEZONE
    try:
        zoneinfo.ZoneInfo(name)
    except Exception:
        raise StandaloneLiveError("invalid_timezone", "Unknown time zone.")
    return name


def parse_start(raw, tz_name, *, now):
    """`start_at` of the go-live sheet -> aware datetime, or None meaning "go live now".

    * empty / missing                      -> None (live now)
    * ISO-8601 with an offset ("...+05:30" / "Z") is taken as it is
    * ISO-8601 WITHOUT an offset is read in the host's zone `tz_name` (what the sheet's date + time pickers produce)
    * must be at least MIN_LEAD_SECONDS ahead and at most MAX_LEAD_DAYS ahead of `now`
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise StandaloneLiveError("invalid_start", "The start time is not valid.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zoneinfo.ZoneInfo(clean_timezone(tz_name)))
    if parsed < now + timedelta(seconds=MIN_LEAD_SECONDS):
        raise StandaloneLiveError(
            "schedule_too_soon",
            "Pick a start time at least a couple of minutes from now, or go live right away.",
        )
    if parsed > now + timedelta(days=MAX_LEAD_DAYS):
        raise StandaloneLiveError("schedule_too_far", f"You can schedule a live up to {MAX_LEAD_DAYS} days ahead.")
    return parsed


def resolve_audience(raw, *, host_is_minor_blocked) -> str:
    """Validated audience. `host_is_minor_blocked` = post.minor_privacy.public_live_blocked(host): such a host can
    only go live for their own class / pass holders (public and followers are refused with a clear code)."""
    audience = str(raw or "").strip().lower() or (AUDIENCE_PASS if host_is_minor_blocked else AUDIENCE_FOLLOWERS)
    if audience not in ALL_AUDIENCES:
        raise StandaloneLiveError("invalid_audience", "Choose who can watch: public, followers, class or paid pass.")
    if host_is_minor_blocked and audience in OPEN_AUDIENCES:
        raise StandaloneLiveError(
            "public_live_blocked_minors",
            "Live for everyone is not available for accounts under 18. You can go live for your own class.",
            status=403,
        )
    return audience


def reminder_due(scheduled_start, now) -> bool:
    """Is a "starts soon" reminder due for a session starting at `scheduled_start`?"""
    return (
        scheduled_start <= now + timedelta(minutes=REMINDER_LEAD_MINUTES)
        and scheduled_start >= now - timedelta(minutes=REMINDER_GRACE_MINUTES)
    )


def minutes_until(scheduled_start, now) -> int:
    return max(0, int((scheduled_start - now).total_seconds() // 60))


# ---------------------------------------------------------------------------------------------------------------
# policy (what the go-live sheet may offer THIS person)
# ---------------------------------------------------------------------------------------------------------------
def policy_for(user) -> dict:
    from post.minor_privacy import public_live_blocked

    blocked = bool(public_live_blocked(user))
    audiences = list(MINOR_AUDIENCES if blocked else ALL_AUDIENCES)
    return {
        "minor_restricted": blocked,
        "audiences": audiences,
        "default_audience": audiences[0],
        "min_lead_minutes": MIN_LEAD_SECONDS // 60,
        "max_lead_days": MAX_LEAD_DAYS,
        "default_duration_minutes": DEFAULT_DURATION_MINUTES,
        "min_duration_minutes": MIN_DURATION_MINUTES,
        "max_duration_minutes": MAX_DURATION_MINUTES,
        "max_upcoming": MAX_UPCOMING_PER_HOST,
        "title_max": MAX_TITLE_LEN,
        "topic_max": MAX_TOPIC_LEN,
        "reminder_lead_minutes": REMINDER_LEAD_MINUTES,
    }


# ---------------------------------------------------------------------------------------------------------------
# 1. personal classroom
# ---------------------------------------------------------------------------------------------------------------
def personal_classroom_for(user):
    """The creator's hidden personal classroom (created on first use, re-activated if it was ever closed)."""
    from .models import Classroom

    qs = Classroom.objects.filter(teacher=user, is_personal=True, is_deleted=False).order_by("id")
    existing = qs.first()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            existing.save(update_fields=["is_active", "updated_at"])
        return existing

    name = (user.get_full_name() or user.username or "Live").strip()
    created = Classroom.objects.create(
        teacher=user,
        is_personal=True,
        title=f"{name} - Live"[:150],
        classroom_type=Classroom.ClassroomType.INDIVIDUAL,
        chat_group_enabled=False,  # no class chat group for a hidden classroom
        recording_enabled=True,
        max_participants=int(getattr(settings, "STANDALONE_LIVE_MAX_PARTICIPANTS", 300)),
        is_active=True,
    )
    # Two first taps at once could both create one: keep the oldest, drop the other.
    winner = qs.first()
    if winner is not None and winner.id != created.id:
        created.delete()
        return winner
    return created


# ---------------------------------------------------------------------------------------------------------------
# 2. create
# ---------------------------------------------------------------------------------------------------------------
def _validate_thumbnail(thumbnail):
    if thumbnail is None:
        return None
    size = getattr(thumbnail, "size", 0) or 0
    if size > THUMBNAIL_MAX_BYTES:
        raise StandaloneLiveError("thumbnail_too_large", "The thumbnail can be at most 5 MB.")
    ctype = (getattr(thumbnail, "content_type", "") or "").lower()
    if ctype and not ctype.startswith("image/"):
        raise StandaloneLiveError("invalid_thumbnail", "The thumbnail must be an image.")
    return thumbnail


def create_live(user, *, title, topic="", audience=None, start_at=None, tz_name=None, duration_minutes=None,
                thumbnail=None, now=None):
    """Creates the ClassSession of a standalone live. `start_at` empty = "go live now": the row is created SCHEDULED
    and flips to LIVE (and notifies) when the host's own /join/ call goes through, exactly like a classroom session.
    Raises StandaloneLiveError."""
    from post.minor_privacy import public_live_blocked

    from .models import ClassSession

    now = now or timezone.now()
    title = clean_title(title)
    topic = clean_topic(topic)
    duration = clean_duration(duration_minutes)
    tz_name = clean_timezone(tz_name)
    audience = resolve_audience(audience, host_is_minor_blocked=bool(public_live_blocked(user)))
    start = parse_start(start_at, tz_name, now=now)
    thumbnail = _validate_thumbnail(thumbnail)
    go_now = start is None

    with transaction.atomic():
        classroom = personal_classroom_for(user)
        sessions = ClassSession.objects.select_for_update().filter(classroom=classroom, live_standalone=True)
        live_now = sessions.filter(status=ClassSession.Status.LIVE).first()
        if go_now:
            if live_now is not None:
                raise StandaloneLiveError(
                    "already_live", "You are already live.", status=409, extra={"session_id": live_now.id}
                )
            # Rows of earlier "go live now" taps that never started are not announcements: drop them.
            sessions.filter(live_go_now=True, status=ClassSession.Status.SCHEDULED, actual_start__isnull=True).delete()
            start = now
        else:
            upcoming = sessions.filter(status=ClassSession.Status.SCHEDULED, live_go_now=False, scheduled_start__gte=now).count()
            if upcoming >= MAX_UPCOMING_PER_HOST:
                raise StandaloneLiveError(
                    "too_many_scheduled",
                    f"You already have {MAX_UPCOMING_PER_HOST} scheduled lives. Cancel one first.",
                    status=409,
                )
        session = ClassSession.objects.create(
            classroom=classroom,
            scheduled_start=start,
            scheduled_end=start + timedelta(minutes=duration),
            live_standalone=True,
            live_go_now=go_now,
            live_audience=audience,
            live_title=title,
            live_topic=topic,
            live_timezone=tz_name,
            live_thumbnail=thumbnail,
        )
    return session


def cancel_live(session, user):
    """Host cancels a SCHEDULED standalone live; everyone who set a reminder is told. Returns the session."""
    from .models import ClassSession

    if not session.live_standalone or session.classroom.teacher_id != user.id:
        raise StandaloneLiveError("not_allowed", "Only the host can cancel this live.", status=403)
    if session.status != ClassSession.Status.SCHEDULED:
        raise StandaloneLiveError("not_scheduled", "Only a live that has not started can be cancelled.", status=409)
    subscribers = list(session.live_reminder_subs.values_list("user_id", flat=True))
    session.status = ClassSession.Status.CANCELLED
    session.save(update_fields=["status"])
    if subscribers:
        _notify(
            subscribers,
            "Live cancelled",
            f"\"{display_title(session)}\" was cancelled by the host.",
            push_type=PUSH_TYPE_CANCELLED,
            session=session,
        )
    return session


def display_title(session) -> str:
    return (session.live_title or "").strip() or session.classroom.title


# ---------------------------------------------------------------------------------------------------------------
# 3. audience: who may join / see
# ---------------------------------------------------------------------------------------------------------------
def _open_sessions(classroom):
    from .live_presence import fresh_cutoff
    from .models import ClassSession

    return ClassSession.objects.filter(classroom=classroom, live_standalone=True).filter(
        Q(status=ClassSession.Status.SCHEDULED)
        | Q(status=ClassSession.Status.LIVE, live_heartbeat_at__gte=fresh_cutoff())
    )


def _host_blocks(host_id, user_id) -> bool:
    try:
        from user_profile.services import blocked_user_ids

        return user_id in set(blocked_user_ids(host_id))
    except Exception:
        logger.exception("standalone_live: block lookup failed (host=%s user=%s)", host_id, user_id)
        return True  # when unsure, keep the door shut


def open_access_for(classroom, user) -> bool:
    """Extra way through `_has_full_room_access` (views/access.py): a viewer who needs no pass because an open
    standalone live of this personal classroom is for followers / everybody. Several open lives of one host with
    different audiences: ANY of them that includes the viewer lets them through. Never raises."""
    try:
        if not getattr(classroom, "is_personal", False) or user is None or not getattr(user, "is_authenticated", False):
            return False
        if user.id == classroom.teacher_id:
            return True
        audiences = set(_open_sessions(classroom).values_list("live_audience", flat=True))
        audiences &= set(OPEN_AUDIENCES)
        if not audiences:
            return False
        from post.minor_privacy import public_live_blocked

        if public_live_blocked(classroom.teacher):
            return False  # a minor's live is never open, whatever an old row says
        if _host_blocks(classroom.teacher_id, user.id):
            return False
        if AUDIENCE_PUBLIC in audiences:
            return True
        from user_profile.models import Follow

        return Follow.objects.filter(
            follower=user, following_id=classroom.teacher_id, status=Follow.Status.ACCEPTED
        ).exists()
    except Exception:
        logger.exception("standalone_live.open_access_for failed (classroom=%s)", getattr(classroom, "id", None))
        return False


def thumbnail_url(session):
    try:
        return session.live_thumbnail.url if session.live_thumbnail else ""
    except Exception:
        return ""


def upcoming_for(viewer, *, limit=20, now=None):
    """Live now + scheduled standalone lives `viewer` may see, soonest / live first. Plain dicts for the feed card.
    Same visibility rules as post/story_live.py: blocked / muted hosts hidden; a minor host only for own class."""
    from post.minor_privacy import minor_ids_among

    from .live_presence import fresh_cutoff
    from .models import ClassSession, LiveReminderSub
    from .views.access import _accessible_classroom_ids

    now = now or timezone.now()
    limit = max(1, min(int(limit or 20), 50))
    rows = list(
        ClassSession.objects.filter(
            live_standalone=True, classroom__is_active=True, classroom__is_deleted=False
        )
        .filter(
            Q(status=ClassSession.Status.LIVE, live_heartbeat_at__gte=fresh_cutoff())
            | Q(
                status=ClassSession.Status.SCHEDULED,
                live_go_now=False,
                scheduled_start__gte=now - timedelta(minutes=STALE_GO_NOW_MINUTES),
                scheduled_start__lte=now + timedelta(days=MAX_LEAD_DAYS),
            )
        )
        .select_related("classroom", "classroom__teacher")
        .order_by("scheduled_start")[:300]
    )
    if not rows:
        return []

    host_ids = {r.classroom.teacher_id for r in rows}
    hidden = set()
    following = set()
    members = set()
    minors = set()
    reminded = set()
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        from post.models import MutedAccount
        from user_profile.models import Follow
        from user_profile.services import blocked_user_ids

        hidden = set(blocked_user_ids(viewer))
        hidden |= set(
            MutedAccount.objects.filter(user=viewer, mute_stories=True, muted_user_id__in=host_ids).values_list("muted_user_id", flat=True)
        )
        following = set(
            Follow.objects.filter(
                follower=viewer, status=Follow.Status.ACCEPTED, following_id__in=host_ids
            ).values_list("following_id", flat=True)
        )
        members = _accessible_classroom_ids(viewer)
        reminded = set(
            LiveReminderSub.objects.filter(user=viewer, session_id__in=[r.id for r in rows]).values_list(
                "session_id", flat=True
            )
        )
    minors = minor_ids_among(host_ids)

    out = []
    for s in rows:
        host = s.classroom.teacher
        hid = s.classroom.teacher_id
        mine = viewer is not None and getattr(viewer, "id", None) == hid
        if not mine:
            if hid in hidden:
                continue
            is_member = s.classroom_id in members
            if s.live_audience == AUDIENCE_PUBLIC:
                ok = hid not in minors
            elif s.live_audience == AUDIENCE_FOLLOWERS:
                ok = hid not in minors and hid in following
            else:  # class / pass: members only
                ok = is_member
            if not ok:
                continue
        out.append({
            "session_id": s.id,
            "classroom_id": s.classroom_id,
            "title": display_title(s),
            "topic": s.live_topic,
            "audience": s.live_audience,
            "status": s.status,
            "is_live": s.status == ClassSession.Status.LIVE,
            "scheduled_start": s.scheduled_start.isoformat(),
            "scheduled_end": s.scheduled_end.isoformat(),
            "timezone": s.live_timezone,
            "thumbnail": thumbnail_url(s),
            "host_id": hid,
            "host_name": (host.get_full_name() or host.username or "").strip() if host else "",
            "is_mine": bool(mine),
            "reminded": s.id in reminded,
        })
    out.sort(key=lambda d: (0 if d["is_live"] else 1, d["scheduled_start"]))
    return out[:limit]


# ---------------------------------------------------------------------------------------------------------------
# 4. reminders
# ---------------------------------------------------------------------------------------------------------------
def set_reminder(session, user, on: bool) -> bool:
    """"Remind me" toggle for a SCHEDULED standalone live the viewer may see. Returns the new state."""
    from .models import ClassSession, LiveReminderSub

    if not session.live_standalone or session.status != ClassSession.Status.SCHEDULED:
        raise StandaloneLiveError("not_scheduled", "This live is not waiting to start.", status=409)
    if session.classroom.teacher_id == user.id:
        raise StandaloneLiveError("own_live", "You are the host of this live.", status=400)
    visible = {d["session_id"] for d in upcoming_for(user, limit=50)}
    if session.id not in visible:
        raise StandaloneLiveError("not_visible", "You cannot see this live.", status=403)
    if on:
        LiveReminderSub.objects.get_or_create(session=session, user=user)
    else:
        LiveReminderSub.objects.filter(session=session, user=user).delete()
    return bool(on)


def reminder_audience(session):
    """User ids that get the "starts soon" push: everybody who tapped Remind me + (followers / public lives) the host's
    accepted followers. Blocked / muted people and the host are never included. Capped by LIVE_NOTIFY_MAX_AUDIENCE."""
    host_id = session.classroom.teacher_id
    ids = list(session.live_reminder_subs.values_list("user_id", flat=True))
    if session.live_audience in OPEN_AUDIENCES:
        from user_profile.models import Follow

        ids += list(
            Follow.objects.filter(following_id=host_id, status=Follow.Status.ACCEPTED).values_list(
                "follower_id", flat=True
            )
        )
    skip = {host_id}
    try:
        from post.models import MutedAccount
        from user_profile.services import blocked_user_ids

        skip |= set(blocked_user_ids(host_id))
        skip |= set(MutedAccount.objects.filter(mute_stories=True, muted_user_id=host_id).values_list("user_id", flat=True))
    except Exception:
        logger.exception("standalone_live.reminder_audience: block / mute lookup failed (session=%s)", session.id)
    cap = int(getattr(settings, "LIVE_NOTIFY_MAX_AUDIENCE", 5000))
    out, seen = [], set()
    for uid in ids:  # reminder subscribers first, so a huge follower list can never crowd them out
        if uid in seen or uid in skip:
            continue
        seen.add(uid)
        out.append(uid)
        if len(out) >= cap:
            break
    return out


def _notify(user_ids, title, message, *, push_type, session):
    """live_push.notify (bell row always, push only where N9 quiet hours / preferences allow). Never raises."""
    try:
        from core.models import Notification

        from .live_push import notify

        return notify(
            user_ids, title, message, push_type=push_type,
            notif_type=Notification.NotifType.SESSION_REMINDER, session=session,
        )
    except Exception:
        logger.exception("standalone_live._notify failed (%s)", push_type)
        return {}


def send_due_reminders(now=None) -> int:
    """Celery beat (every minute): "<host> goes live in N minutes" for scheduled standalone lives. Idempotent - a
    conditional UPDATE claims `reminder_sent_at`, so a retry / second worker sends nothing twice. Returns how many
    lives were announced."""
    from .models import ClassSession

    now = now or timezone.now()
    ids = list(
        ClassSession.objects.filter(
            live_standalone=True,
            live_go_now=False,
            status=ClassSession.Status.SCHEDULED,
            reminder_sent_at__isnull=True,
            scheduled_start__lte=now + timedelta(minutes=REMINDER_LEAD_MINUTES),
            scheduled_start__gte=now - timedelta(minutes=REMINDER_GRACE_MINUTES),
        ).values_list("id", flat=True)[:200]
    )
    sent = 0
    for sid in ids:
        claimed = ClassSession.objects.filter(
            pk=sid, reminder_sent_at__isnull=True, status=ClassSession.Status.SCHEDULED
        ).update(reminder_sent_at=now)
        if not claimed:
            continue
        session = ClassSession.objects.filter(pk=sid).select_related("classroom", "classroom__teacher").first()
        if session is None:
            continue
        audience = reminder_audience(session)
        if not audience:
            continue
        host = session.classroom.teacher
        host_name = (host.get_full_name() or host.username or "").strip() if host else ""
        mins = minutes_until(session.scheduled_start, now)
        when = "in a few minutes" if mins <= 1 else f"in {mins} minutes"
        _notify(
            audience,
            f"{host_name or 'A creator'} goes live {when}",
            f"\"{display_title(session)}\" - tap to open.",
            push_type=PUSH_TYPE_REMINDER,
            session=session,
        )
        sent += 1
    return sent


def live_start_audience_extra(session):
    """Reminder subscribers of `session`: notify_session_live adds them to the "is live now" push, even when they
    do not follow the host."""
    return list(session.live_reminder_subs.values_list("user_id", flat=True))
