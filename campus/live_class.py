# campus/live_class.py
"""
LIVE CLASS — one tap to go live from the timetable, attendance fills itself.

    timetable period  --(Go live)-->  CampusLiveSession (LIVE, room, push)
          students join / heartbeat  -->  CampusLiveParticipant rows
    teacher ends (or app dies)        -->  ENDED  -->  Attendance rows (source=auto)

The ONE place that owns these rules, so the HTTP layer (views.py), the Celery
sweeper (tasks.py) and the tests cannot drift apart. Pure ORM + the existing
`campus.bridge` door for push / video; no DRF, no Celery imports at module level.

1. ONE-TAP GO LIVE  (`go_live_from_entry`)
   * Only an ONLINE period; only on the day the weekly slot belongs to; from
     `CAMPUS_GO_LIVE_EARLY_MINUTES` (15) before it starts until
     `CAMPUS_GO_LIVE_LATE_GRACE_MINUTES` (15) after it ends.
   * Who: the period's own teacher, an assigned subject teacher / class teacher /
     moderator / admin (`can_manage_section_subject`) AND the substitute teacher
     that covers this period today (they are usually not an approved subject
     teacher of that section, so the normal permission would wrongly refuse them).
   * Idempotent: (timetable_entry, date) -> at most one non-cancelled session
     (DB partial unique constraint + `select_for_update`). Tapping twice, from
     two devices, or after an app restart just RESUMES the same room. A session a
     teacher had already scheduled by hand for the same class (same section +
     subject within `LIVE_SESSION_DEDUPE_WINDOW`) is ADOPTED, not duplicated.
   * An ENDED session can be re-opened for `CAMPUS_LIVE_REOPEN_MINUTES` (60) after
     it ended (teacher's app crashed and the sweeper closed it) — same room, no
     second "class is live" push, attendance simply rebuilds on the next end.

2. PRESENCE  (`record_join` / `heartbeat` / `record_leave`)
   Every join stamps a `CampusLiveParticipant` row; the app's heartbeat keeps
   `last_seen_at` fresh; a stay with no heartbeat for `CAMPUS_LIVE_PRESENCE_TTL_SECONDS`
   (90) is treated as closed at its last sign of life. A heartbeat from someone who
   may run the session also keeps the SESSION alive (`live_heartbeat_at`).

3. AUTO ATTENDANCE  (`build_attendance`)
   When a session ends, each ACTIVE student of the section gets ONE subject-wise
   `Attendance` row (date = day the class started, subject = the session's subject):
       present : stayed >= campus.live_attendance_min_percent % of the class
       late    : same, but first joined > campus.live_attendance_late_minutes after start
       absent  : never joined / stayed less than the minimum
   Rules that are deliberate, not accidents:
     * idempotent + safe to re-run; never overwrites a row a teacher edited
       (`Attendance.source == manual`) or an approved `leave`;
     * a session nobody joined, one shorter than
       `CAMPUS_LIVE_ATTENDANCE_MIN_SESSION_MINUTES` (5) or an OFFLINE one did not
       "happen" -> nobody is marked absent for it;
     * two live classes of the SAME subject on the same day share the one
       (student, date, subject) row: minutes add up and the better status wins;
     * parents of newly-absent children get the normal absent push
       (`attendance_push`, only for today, once per child per day) and a
       correction push when a rebuild turns absent into present;
     * `Campus.live_attendance_auto = False` switches the whole thing off.
   Teachers correct the result through the existing attendance API (edit window +
   reason + audit log); the first change flips the row to `source=manual`.

4. SWEEPER  (`sweep_live_sessions`, Celery beat every minute)
   * auto-ends a LIVE session whose teacher heartbeat went silent for
     `CAMPUS_LIVE_STALE_MINUTES` (5) — ended_at = the last heartbeat;
   * force-ends ANY live session older than `CAMPUS_LIVE_MAX_HOURS` (6) (also
     covers old apps that never send heartbeats);
   * rebuilds attendance for ended sessions whose build never completed;
   * recorded classes (campus/recordings.py): stops a recording that outlived its class,
     fails one stuck in "processing" (the LiveKit webhook never came).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import bridge, recordings
from .bridge import NotifTypes
from .class_schedule import LIVE_SESSION_DEDUPE_WINDOW, occurrence_start
from .models import (
    Attendance,
    CampusLiveParticipant,
    CampusLiveSession,
    ClassMode,
    StudentEnrollment,
    TimetableEntry,
)
from .permissions import can_manage_section_subject

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# knobs (all optional settings)
# --------------------------------------------------------------------------

def _minutes(name: str, default: int) -> timedelta:
    return timedelta(minutes=int(getattr(settings, name, default)))


def early_window() -> timedelta:
    return _minutes("CAMPUS_GO_LIVE_EARLY_MINUTES", 15)


def late_grace() -> timedelta:
    return _minutes("CAMPUS_GO_LIVE_LATE_GRACE_MINUTES", 15)


def reopen_window() -> timedelta:
    return _minutes("CAMPUS_LIVE_REOPEN_MINUTES", 60)


def stale_after() -> timedelta:
    return _minutes("CAMPUS_LIVE_STALE_MINUTES", 5)


def max_live_duration() -> timedelta:
    return timedelta(hours=int(getattr(settings, "CAMPUS_LIVE_MAX_HOURS", 6)))


def presence_ttl() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "CAMPUS_LIVE_PRESENCE_TTL_SECONDS", 90)))


def min_session_for_attendance() -> timedelta:
    return _minutes("CAMPUS_LIVE_ATTENDANCE_MIN_SESSION_MINUTES", 5)


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

class LiveClassError(Exception):
    """A business-rule failure with a stable machine `code` (the API turns it
    into a response whose `code` the app can switch on)."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------

def can_run_session(user, live: CampusLiveSession) -> bool:
    """May `user` end / heartbeat-as-host / see the attendance of this session?

    The session's own teacher (incl. a substitute — they are usually NOT an
    approved subject teacher of that section), whoever started it, or anyone
    `can_manage_section_subject` allows."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if live.teacher.is_active and live.teacher.user_id == user.id:
        return True
    if live.started_by_id and live.started_by_id == user.id:
        return True
    campus_id = live.section.school_class.campus_id
    return can_manage_section_subject(user, campus_id, live.section_id, live.subject_id)


def can_go_live_for_entry(user, entry: TimetableEntry, on_date) -> bool:
    from .substitutes import active_substitution

    if not user or not getattr(user, "is_authenticated", False):
        return False
    campus_id = entry.section.school_class.campus_id
    if entry.staff.is_active and entry.staff.user_id == user.id:
        return True
    if can_manage_section_subject(user, campus_id, entry.section_id, entry.subject_id):
        return True
    sub = active_substitution(entry.pk, on_date)
    return bool(sub and sub.substitute.is_active and sub.substitute.user_id == user.id)


# --------------------------------------------------------------------------
# video room helpers
# --------------------------------------------------------------------------

def ensure_room(live: CampusLiveSession, actor) -> bool:
    """Provision the video room name if the session has none yet (best-effort,
    same behaviour the old `start` action had). True when a room exists."""
    if live.room_id:
        return True
    if live.mode != ClassMode.ONLINE:
        return False
    try:
        with transaction.atomic():
            room_id = bridge.provision_video_room(live, actor=actor)
    except Exception:
        logger.exception("Video room provisioning failed for live session %s.", live.pk)
        return False
    if room_id:
        live.room_id = room_id
        live.save(update_fields=["room_id"])
        return True
    return False


def livekit_url() -> str:
    """WebSocket URL of the LiveKit server the app must connect to together with the
    token. Same env names `tuitionclass.livekit_utils` reads (`LIVEKIT_WS_URL`, alias
    `LIVEKIT_URL`) — read here directly because campus never imports tuitionclass.
    Empty string when video is not configured (the token mint then fails first)."""
    return (
        getattr(settings, "LIVEKIT_WS_URL", None)
        or os.getenv("LIVEKIT_WS_URL")
        or os.getenv("LIVEKIT_URL")
        or ""
    )


def mint_join_token(live: CampusLiveSession, user) -> str:
    """Per-participant LiveKit token. `LiveClassError(503)` when video is not
    available — callers decide whether that must roll the whole action back."""
    if not live.room_id:
        raise LiveClassError("no_room", "This session has no video room provisioned.", 503)
    # Local import: campus reaches core only through this single door (see views.join).
    from core.classroom_chat_bridge import generate_campus_session_token

    try:
        return generate_campus_session_token(live.room_id, user)
    except RuntimeError as exc:
        logger.exception("LiveKit not configured — cannot mint join token for session %s", live.pk)
        raise LiveClassError("video_unavailable", "Live video isn't configured on this deployment.", 503) from exc


# --------------------------------------------------------------------------
# start / announce
# --------------------------------------------------------------------------

def announce_live(live: CampusLiveSession) -> None:
    """The time-critical "class is live" push to the section's active students
    (honours each student's push toggle / muted types / quiet hours)."""
    recipients = StudentEnrollment.objects.filter(
        section=live.section, status=StudentEnrollment.Status.ACTIVE
    ).values_list("student", flat=True)
    bridge.notify(
        users=list(recipients),
        notif_type=NotifTypes.CAMPUS_SESSION_LIVE,
        title="Class is live",
        body=f"{live.subject.name} is live now.",
        data={
            "type": NotifTypes.CAMPUS_SESSION_LIVE,
            "live_session_id": str(live.pk),
            "section_id": str(live.section_id),
            "subject_id": str(live.subject_id),
            "mode": live.mode,
        },
        push=True,
    )


def start_session(live: CampusLiveSession, user, *, now=None, announce: bool = True, heartbeat: bool = False):
    """SCHEDULED -> LIVE (shared by the classic `start` action and Go live).

    `heartbeat=True` (Go live — the app that sends it also sends heartbeats)
    arms the silence sweeper; the classic action leaves `live_heartbeat_at`
    NULL so an old app that never pings is not auto-ended after 5 minutes."""
    now = now or timezone.now()
    live.status = CampusLiveSession.Status.LIVE
    live.started_at = live.started_at or now
    live.started_by = user
    live.ended_at = None
    fields = ["status", "started_at", "started_by", "ended_at"]
    if heartbeat:
        live.live_heartbeat_at = now
        fields.append("live_heartbeat_at")
    live.save(update_fields=fields)
    ensure_room(live, user)
    if announce:
        announce_live(live)
    return live


@dataclass
class GoLiveResult:
    session: CampusLiveSession
    created: bool = False      # a brand-new session row was made
    resumed: bool = False      # it was already LIVE — same room, nothing re-announced
    reopened: bool = False     # it had ENDED recently and was re-opened
    announce: bool = False     # caller should send the "class is live" push (after commit)


def _slot_window(entry: TimetableEntry, on_date):
    tz = timezone.get_current_timezone()
    return (
        occurrence_start(entry, on_date),
        datetime.combine(on_date, entry.time_slot.end_time, tzinfo=tz),
    )


def check_can_go_live(user, entry: TimetableEntry, now) -> object:
    """Validate everything about tapping Go live on `entry` right now and
    return the local date it applies to. Raises `LiveClassError`."""
    campus = entry.section.school_class.campus
    on_date = timezone.localtime(now).date()
    slot = entry.time_slot

    if not campus.is_active:
        raise LiveClassError("campus_inactive", "This campus is not active.", 403)
    if not can_go_live_for_entry(user, entry, on_date):
        raise LiveClassError("not_allowed", "You are not allowed to take this class live.", 403)
    if entry.mode != ClassMode.ONLINE:
        raise LiveClassError("offline_class", "This period is an offline class — there is no live room.")
    if slot.day_of_week != on_date.isoweekday():
        raise LiveClassError("not_today", "This period does not run today.")
    session = entry.session
    if (session.start_date and on_date < session.start_date) or (session.end_date and on_date > session.end_date):
        raise LiveClassError("outside_session", "This period is outside its academic session.")
    starts, ends = _slot_window(entry, on_date)
    if now < starts - early_window():
        raise LiveClassError(
            "too_early",
            f"Too early — you can go live {int(early_window().total_seconds() // 60)} minutes before the period starts.",
        )
    if now > ends + late_grace():
        raise LiveClassError("slot_over", "This period is already over.")
    return on_date


def go_live_from_entry(*, entry: TimetableEntry, user, now=None) -> GoLiveResult:
    """The one-tap action. Idempotent — see the module docstring. Call it inside
    `transaction.atomic()` together with the token mint so a video outage rolls
    the start back instead of notifying students about a class nobody can join."""
    from .substitutes import active_substitution

    now = now or timezone.now()
    on_date = check_can_go_live(user, entry, now)
    starts_at, _ends = _slot_window(entry, on_date)

    sub = active_substitution(entry.pk, on_date)
    teacher = sub.substitute if sub is not None else entry.staff

    with transaction.atomic():
        live = (
            CampusLiveSession.objects.select_for_update()
            .filter(timetable_entry=entry, occurrence_date=on_date)
            .exclude(status=CampusLiveSession.Status.CANCELLED)
            .first()
        )
        created = False
        if live is None:
            # A session the teacher already scheduled by hand for this very class.
            live = (
                CampusLiveSession.objects.select_for_update()
                .filter(
                    timetable_entry__isnull=True,
                    section_id=entry.section_id,
                    subject_id=entry.subject_id,
                    mode=ClassMode.ONLINE,
                    status=CampusLiveSession.Status.SCHEDULED,
                    scheduled_at__gte=starts_at - LIVE_SESSION_DEDUPE_WINDOW,
                    scheduled_at__lte=starts_at + LIVE_SESSION_DEDUPE_WINDOW,
                )
                .order_by("scheduled_at")
                .first()
            )
            if live is not None:
                live.timetable_entry = entry
                live.occurrence_date = on_date
                live.save(update_fields=["timetable_entry", "occurrence_date"])
        if live is None:
            try:
                with transaction.atomic():
                    live = CampusLiveSession.objects.create(
                        section=entry.section,
                        subject=entry.subject,
                        teacher=teacher,
                        scheduled_at=starts_at,
                        mode=ClassMode.ONLINE,
                        timetable_entry=entry,
                        occurrence_date=on_date,
                        # the class is starting right now — no "starts in 5 min" reminder
                        reminder_sent_at=now,
                    )
                created = True
            except IntegrityError:
                # lost a race with another tap: use the winner's row
                live = (
                    CampusLiveSession.objects.select_for_update()
                    .filter(timetable_entry=entry, occurrence_date=on_date)
                    .exclude(status=CampusLiveSession.Status.CANCELLED)
                    .get()
                )

        S = CampusLiveSession.Status
        if live.status == S.LIVE:
            fields = ["live_heartbeat_at"]
            live.live_heartbeat_at = now
            if not live.started_at:
                live.started_at = now
                fields.append("started_at")
            live.save(update_fields=fields)
            ensure_room(live, user)
            return GoLiveResult(session=live, created=created, resumed=not created, announce=False)

        if live.status == S.ENDED:
            if not live.ended_at or now - live.ended_at > reopen_window():
                raise LiveClassError("already_ended", "This class has already ended.", 409)
            start_session(live, user, now=now, announce=False, heartbeat=True)
            return GoLiveResult(session=live, reopened=True, announce=False)

        # SCHEDULED (fresh row or adopted)
        start_session(live, user, now=now, announce=False, heartbeat=True)
        return GoLiveResult(session=live, created=created, announce=True)


# --------------------------------------------------------------------------
# presence
# --------------------------------------------------------------------------

def _open_row(live, user):
    return (
        CampusLiveParticipant.objects.filter(session=live, user=user, left_at__isnull=True)
        .order_by("-joined_at")
        .first()
    )


def record_join(live: CampusLiveSession, user, *, now=None) -> CampusLiveParticipant:
    """Stamp that `user` is in the room. A still-fresh open stay is just
    refreshed (a re-fetched token is not a second join); a stale one is closed
    at its last sign of life and a new stay starts."""
    now = now or timezone.now()
    row = _open_row(live, user)
    if row is not None:
        if now - row.last_seen_at <= presence_ttl():
            row.last_seen_at = now
            row.save(update_fields=["last_seen_at"])
            return row
        row.left_at = row.last_seen_at
        row.save(update_fields=["left_at"])
    return CampusLiveParticipant.objects.create(session=live, user=user, joined_at=now, last_seen_at=now)


def heartbeat(live: CampusLiveSession, user, *, now=None) -> dict:
    """Keep-alive from the app (~every 20-30 s). Tells the app whether the
    session is still live, so a student's app learns the teacher ended it."""
    now = now or timezone.now()
    if live.status != CampusLiveSession.Status.LIVE:
        return {"live": False, "status": live.status}
    row = _open_row(live, user)
    if row is not None and now - row.last_seen_at <= presence_ttl():
        row.last_seen_at = now
        row.save(update_fields=["last_seen_at"])
    else:
        record_join(live, user, now=now)  # reconnect after a dropout
    recording_status = live.recording_status
    if _is_host(user, live):
        CampusLiveSession.objects.filter(pk=live.pk, status=CampusLiveSession.Status.LIVE).update(
            live_heartbeat_at=now
        )
        # Recorded classes: the teacher is inside the room now, so the video room exists.
        # Starts the recording by itself when the campus enabled it (once per session).
        if recordings.maybe_auto_start(live, user):
            recording_status = CampusLiveSession.RecordingStatus.RECORDING
    # `recording` lets every student's app show a "REC" chip (they know they are being recorded).
    return {
        "live": True,
        "status": live.status,
        "recording": recording_status == CampusLiveSession.RecordingStatus.RECORDING,
    }


def _is_host(user, live: CampusLiveSession) -> bool:
    """`can_run_session`, cached for 5 minutes per (session, user): every student's
    app heartbeats every ~30 s and each uncached check is several queries."""
    key = f"campus:live-host:{live.pk}:{user.id}"
    cached = cache.get(key)
    if cached is None:
        cached = bool(can_run_session(user, live))
        cache.set(key, cached, 300)
    return cached


def record_leave(live: CampusLiveSession, user, *, now=None) -> int:
    now = now or timezone.now()
    return CampusLiveParticipant.objects.filter(session=live, user=user, left_at__isnull=True).update(left_at=now)


def _close_open_participants(live: CampusLiveSession, ended_at) -> None:
    ttl = presence_ttl()
    for row in CampusLiveParticipant.objects.filter(session=live, left_at__isnull=True):
        row.left_at = max(row.joined_at, min(ended_at, row.last_seen_at + ttl))
        row.save(update_fields=["left_at"])


# --------------------------------------------------------------------------
# end
# --------------------------------------------------------------------------

def end_session(live: CampusLiveSession, *, now=None, ended_at=None):
    """-> ENDED, close open stays, build attendance. Idempotent. Returns
    `(session, changed)`. Like the classic `end` action it accepts any prior
    status (a never-started session simply has nobody to mark)."""
    now = now or timezone.now()
    with transaction.atomic():
        live = CampusLiveSession.objects.select_for_update().get(pk=live.pk)
        if live.status == CampusLiveSession.Status.ENDED:
            return live, False
        live.status = CampusLiveSession.Status.ENDED
        live.ended_at = min(ended_at or now, now)
        live.save(update_fields=["status", "ended_at"])
        _close_open_participants(live, live.ended_at)
    try:
        # Recorded classes: the class is over, stop the film (RECORDING -> PROCESSING).
        # Never blocks ending the class; the sweeper stops a leftover one.
        recordings.stop_recording(live, now=now)
    except Exception:
        logger.exception("Stopping the recording failed for live session %s.", live.pk)
    try:
        build_attendance(live.pk)
    except Exception:
        # Ending the class must never fail because of attendance; the sweeper retries.
        logger.exception("Auto attendance failed for live session %s.", live.pk)
    return live, True


# --------------------------------------------------------------------------
# auto attendance
# --------------------------------------------------------------------------

_RANK = {
    Attendance.Status.ABSENT: 0,
    Attendance.Status.LATE: 1,
    Attendance.Status.PRESENT: 2,
}


def _merged_seconds(intervals) -> float:
    """Total seconds covered by `(start, end)` pairs, overlaps counted once."""
    total = 0.0
    cur_start = cur_end = None
    for s, e in sorted(intervals):
        if cur_end is None or s > cur_end:
            if cur_end is not None:
                total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = s, e
        else:
            cur_end = max(cur_end, e)
    if cur_end is not None:
        total += (cur_end - cur_start).total_seconds()
    return total


def _session_window(live: CampusLiveSession, now):
    start = live.started_at or live.scheduled_at
    end = live.ended_at or now
    return start, end


def build_attendance(session_or_id, *, now=None) -> dict:
    """Create / refresh the subject-wise `Attendance` rows of an ENDED live
    session. Returns `{"ran", "reason", "created", "updated", "skipped_manual"}`.
    Safe to call twice; see the module docstring for the rules."""
    now = now or timezone.now()
    live = (
        CampusLiveSession.objects.select_related("section__school_class__campus", "subject")
        .filter(pk=getattr(session_or_id, "pk", session_or_id))
        .first()
    )
    result = {"ran": False, "reason": "", "created": 0, "updated": 0, "skipped_manual": 0}
    if live is None:
        result["reason"] = "not_found"
        return result
    if live.status != CampusLiveSession.Status.ENDED:
        result["reason"] = "not_ended"
        return result

    def _skip(reason):
        # Deliberate no-op: stamp it so the catch-up sweep never retries it.
        CampusLiveSession.objects.filter(pk=live.pk).update(attendance_built_at=now)
        result["reason"] = reason
        return result

    campus = live.section.school_class.campus
    if live.mode != ClassMode.ONLINE:
        return _skip("offline")
    if not campus.live_attendance_auto:
        return _skip("disabled")

    start, end = _session_window(live, now)
    duration = (end - start).total_seconds()
    if duration < min_session_for_attendance().total_seconds():
        return _skip("too_short")

    participants = list(CampusLiveParticipant.objects.filter(session=live))
    if not participants:
        return _skip("nobody_joined")

    by_user = {}
    for p in participants:
        by_user.setdefault(p.user_id, []).append(p)

    att_date = timezone.localtime(start).date()
    grace_cutoff = start + timedelta(minutes=campus.live_attendance_late_minutes)
    min_ratio = campus.live_attendance_min_percent / 100.0
    roster = list(
        StudentEnrollment.objects.filter(section=live.section, status=StudentEnrollment.Status.ACTIVE)
    )

    newly_absent, corrected = [], []
    with transaction.atomic():
        existing = {
            a.enrollment_id: a
            for a in Attendance.objects.select_for_update().filter(
                enrollment_id__in=[e.id for e in roster], date=att_date, subject=live.subject
            )
        }
        to_create, to_update = [], []
        for enr in roster:
            rows = by_user.get(enr.student_id, [])
            intervals, first_join = [], None
            for p in rows:
                s = max(p.joined_at, start)
                e = min(p.left_at or end, end)
                if e > s:
                    intervals.append((s, e))
                first_join = p.joined_at if first_join is None else min(first_join, p.joined_at)
            seconds = _merged_seconds(intervals)
            ratio = seconds / duration if duration else 0.0
            if not rows or ratio < min_ratio:
                new_status = Attendance.Status.ABSENT
            elif first_join and first_join > grace_cutoff:
                new_status = Attendance.Status.LATE
            else:
                new_status = Attendance.Status.PRESENT
            minutes = min(int(seconds // 60), 32000)

            row = existing.get(enr.id)
            if row is None:
                to_create.append(
                    Attendance(
                        enrollment=enr, date=att_date, subject=live.subject, status=new_status,
                        marked_by=None, source=Attendance.Source.AUTO,
                        minutes_present=minutes, live_session=live,
                    )
                )
                if new_status == Attendance.Status.ABSENT:
                    newly_absent.append(enr.id)
                continue
            if row.source == Attendance.Source.MANUAL:
                result["skipped_manual"] += 1  # teacher's word (or approved leave) wins
                continue
            old_status, old_minutes = row.status, row.minutes_present or 0
            if row.live_session_id and row.live_session_id != live.id:
                # another live class of the same subject earlier today: add up, best status wins
                minutes = min(old_minutes + minutes, 32000)
                if _RANK[old_status] > _RANK[new_status]:
                    new_status = old_status
            if (old_status, old_minutes, row.live_session_id) != (new_status, minutes, live.id):
                row.status = new_status
                row.minutes_present = minutes
                row.live_session = live
                to_update.append(row)
                if new_status == Attendance.Status.ABSENT and old_status != new_status:
                    newly_absent.append(enr.id)
                elif old_status == Attendance.Status.ABSENT and new_status != old_status:
                    corrected.append(enr.id)

        if to_create:
            Attendance.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            Attendance.objects.bulk_update(to_update, ["status", "minutes_present", "live_session"])
        CampusLiveSession.objects.filter(pk=live.pk).update(attendance_built_at=now)
    result.update(ran=True, created=len(to_create), updated=len(to_update), reason="built")

    # Pushes only for rows created/changed in THIS run, outside the transaction;
    # both helpers never raise and apply their own "only for today / once a day" rules.
    if newly_absent:
        from .attendance_push import notify_parents_of_absences

        notify_parents_of_absences(enrollment_ids=newly_absent, date=att_date, subject=live.subject)
    if corrected:
        from .attendance_push import notify_parents_of_corrections

        notify_parents_of_corrections(enrollment_ids=corrected, date=att_date, subject=live.subject)
    return result


# --------------------------------------------------------------------------
# read model for the teacher
# --------------------------------------------------------------------------

def _display_name(user) -> str:
    full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    return full or user.username


def attendance_report(live: CampusLiveSession) -> dict:
    """Roster of the session with each student's status / minutes / source and
    when they joined — for the teacher's "who came" screen."""
    start = live.started_at or live.scheduled_at
    att_date = timezone.localtime(start).date()
    roster = list(
        StudentEnrollment.objects.filter(section=live.section, status=StudentEnrollment.Status.ACTIVE)
        .select_related("student")
        .order_by("roll_number", "student__username")
    )
    marks = {
        a.enrollment_id: a
        for a in Attendance.objects.filter(
            enrollment_id__in=[e.id for e in roster], date=att_date, subject=live.subject
        )
    }
    stays = {}
    for p in CampusLiveParticipant.objects.filter(session=live):
        stays.setdefault(p.user_id, []).append(p)

    counts = {"present": 0, "late": 0, "absent": 0, "leave": 0, "unmarked": 0}
    students = []
    for enr in roster:
        a = marks.get(enr.id)
        rows = stays.get(enr.student_id, [])
        lefts = [p.left_at for p in rows if p.left_at]
        counts[a.status if a else "unmarked"] += 1
        students.append({
            "enrollment": str(enr.id),
            "student": enr.student_id,
            "name": _display_name(enr.student),
            "roll_number": enr.roll_number,
            "status": a.status if a else None,
            "source": a.source if a else None,
            "minutes_present": a.minutes_present if a else None,
            "first_joined_at": min((p.joined_at for p in rows), default=None),
            "last_left_at": max(lefts) if lefts else None,
            "joins": len(rows),
        })
    return {
        "session": str(live.pk),
        "date": att_date.isoformat(),
        "built": bool(live.attendance_built_at),
        "status": live.status,
        "counts": counts,
        "students": students,
    }


# --------------------------------------------------------------------------
# sweeper (Celery beat, every minute)
# --------------------------------------------------------------------------

def sweep_live_sessions(now=None) -> dict:
    now = now or timezone.now()
    S = CampusLiveSession.Status
    auto_ended = rebuilt = 0

    # 1) teacher heartbeat silent -> the class is over at the last sign of life
    silent = CampusLiveSession.objects.filter(
        status=S.LIVE, live_heartbeat_at__isnull=False, live_heartbeat_at__lt=now - stale_after()
    )
    for live in silent:
        _, changed = end_session(live, now=now, ended_at=live.live_heartbeat_at)
        auto_ended += int(changed)

    # 2) hard cap — also catches old apps that never send heartbeats
    cap = now - max_live_duration()
    too_long = CampusLiveSession.objects.filter(status=S.LIVE).filter(
        Q(started_at__lt=cap) | Q(started_at__isnull=True, scheduled_at__lt=cap)
    )
    for live in too_long:
        started = live.started_at or live.scheduled_at
        _, changed = end_session(live, now=now, ended_at=started + max_live_duration())
        auto_ended += int(changed)

    # 3) catch-up: ended recently but the attendance build never completed
    pending = CampusLiveSession.objects.filter(
        status=S.ENDED, attendance_built_at__isnull=True, ended_at__gte=now - timedelta(hours=24)
    )
    for live in pending:
        try:
            if build_attendance(live.pk, now=now).get("ran"):
                rebuilt += 1
        except Exception:
            logger.exception("Catch-up attendance build failed for live session %s.", live.pk)
    result = {"auto_ended": auto_ended, "attendance_rebuilt": rebuilt}
    # 4) recordings: stop a leftover one of an ended class; fail one stuck in processing
    try:
        result.update(recordings.sweep_recordings(now))
    except Exception:
        logger.exception("Recording sweep failed.")
    return result
