"""
tuitionclass/live_moderation.py  -  X12 LIVE MODERATION ACTIONS

Everything a moderator (or the system) DOES to a person in a live class, plus the report intake and the moderators'
queue. One place, one audit trail: every action writes a LiveModerationEvent row (who / whom / what / when).

    log_event()            write one audit row (never raises)
    apply_chat_mute()      take someone's CHAT voice away for N minutes (they still watch and hear). Used by the
                           moderator action AND by live_abuse.register_strike (kind auto_mute)
    clear_chat_mute()      give it back
    warn()                 a warning (bell + push + live event to that person only)
    kick_participant()     remove from the LiveKit room + block rejoining THIS session (the logic that used to live
                           inside ClassSessionViewSet.kick - the view now calls this, so both paths stay identical)
    apply_classroom_ban()  permanent classroom ban incl. refund (same - ClassroomViewSet.ban calls this now)
    moderate()             the dispatcher behind POST sessions/<id>/moderation/act/
    file_report()          a participant reports a person / a chat line / the class
    resolve_report()       a moderator closes a report (actioned / dismissed)
    moderation_queue()     the moderators' to-do list (reports) - never shows WHO reported

RULES
    * Only real moderators (teacher / co-teacher / moderator, org staff - views.access._can_moderate_session) act. An
      on-stage co-host guest keeps its light powers (mute mic, delete chat) but cannot warn / chat-mute / kick / ban.
    * Nobody of the host tier can be warned / muted / kicked / banned through this path, and nobody can act on
      themselves. (A report AGAINST the teacher or staff goes to the platform's ContentReport queue only: it is not
      shown in the classroom queue and the accused is never notified.)
    * A reporter is never named - not in the queue, not in the push, not to the moderators.
    * Reports are mirrored into user_profile.ContentReport (the platform-wide queue, brigading guard, child-safety
      urgent path). A failure of that mirror never fails the classroom report.
"""

import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

logger = logging.getLogger(__name__)

User = get_user_model()

ACTIONS = ("warn", "chat_mute", "chat_unmute", "kick", "ban")
MUTE_MINUTES_DEFAULT = 10
MUTE_MINUTES_MAX = 24 * 60

# our report reason -> user_profile.ContentReport.Reason (the platform queue)
REPORT_REASONS = {
    "harassment": "harassment",
    "hate": "hate",
    "inappropriate": "nudity",
    "violence": "violence",
    "spam": "spam",
    "scam": "scam",
    "self_harm": "self_harm",
    "minor_safety": "child_safety",   # urgent queue, never rate limited
    "contact_info": "other",
    "other": "other",
}


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------
def _kind(name):
    from .models import LiveModerationEvent

    return LiveModerationEvent.Kind(name)


def log_event(session, kind, *, actor=None, target=None, reason="", detail="", chat_message_id=None,
              content_report_id=None):
    """One audit row. Never raises (a broken log must not undo a moderation action)."""
    try:
        from .models import LiveModerationEvent

        return LiveModerationEvent.objects.create(
            session=session, kind=_kind(kind), actor=actor, target_user=target,
            reason=str(reason or "")[:40], detail=str(detail or "")[:255],
            chat_message_id=chat_message_id, content_report_id=content_report_id,
        )
    except Exception:
        logger.exception("live_moderation.log_event failed (session %s kind %s)", getattr(session, "id", None), kind)
        return None


def count_events(session, target, kind) -> int:
    from .models import LiveModerationEvent

    try:
        return LiveModerationEvent.objects.filter(session=session, target_user=target, kind=kind).count()
    except Exception:
        logger.exception("live_moderation.count_events failed")
        return 0


# ---------------------------------------------------------------------------
# who can be acted on
# ---------------------------------------------------------------------------
def is_host_tier(session, user) -> bool:
    from .views.access import _can_moderate_session

    return bool(_can_moderate_session(session, user))


def ensure_moderator(session, user):
    if not (user.is_staff or is_host_tier(session, user)):
        raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can do this.")


def _target(session, actor, user_id):
    try:
        target = User.objects.filter(pk=int(user_id)).first()
    except (TypeError, ValueError):
        target = None
    if target is None:
        raise NotFound("That person was not found.")
    if target.id == actor.id:
        raise ValidationError("You cannot do this to yourself.")
    if is_host_tier(session, target):
        raise PermissionDenied("The teacher and the class staff cannot be moderated here.")
    return target


# ---------------------------------------------------------------------------
# chat mute
# ---------------------------------------------------------------------------
def apply_chat_mute(session, target, minutes, *, actor=None, kind="chat_mute", reason="", detail=""):
    """Mute `target`'s chat for `minutes`. Returns the end time. Every row of the person in this session carries it
    (a rejoin makes a new row - live_abuse.chat_mute_until reads across all rows, so the mute holds anyway)."""
    from . import live_push
    from .models import SessionParticipant
    from .realtime import broadcast_to_session

    minutes = max(1, min(int(minutes or MUTE_MINUTES_DEFAULT), MUTE_MINUTES_MAX))
    until = timezone.now() + timedelta(minutes=minutes)
    SessionParticipant.objects.filter(session=session, user=target).update(chat_muted_until=until)
    from . import live_abuse

    live_abuse.remember_mute(session, target, until)       # L-D #4: the next chat message already sees it
    log_event(session, kind, actor=actor, target=target, reason=reason, detail=detail or f"{minutes} min")
    broadcast_to_session(session.id, "chat.muted", {"user_id": target.id, "muted_until": until.isoformat()})
    live_push.notify_user_of_action(session, target.id, "chat_mute", minutes=minutes)
    return until


def clear_chat_mute(session, target, *, actor):
    from .models import SessionParticipant
    from .realtime import broadcast_to_session

    SessionParticipant.objects.filter(session=session, user=target).update(chat_muted_until=None)
    from . import live_abuse

    live_abuse.forget_mute(session, target)                # L-D #4: drop the cached "muted" answer
    log_event(session, "chat_unmute", actor=actor, target=target)
    broadcast_to_session(session.id, "chat.unmuted", {"user_id": target.id})


# ---------------------------------------------------------------------------
# warn
# ---------------------------------------------------------------------------
def warn(session, target, *, actor, note=""):
    from . import live_push
    from .realtime import broadcast_to_user

    log_event(session, "warn", actor=actor, target=target, detail=note)
    broadcast_to_user(target.id, "live.warning", {"session_id": session.id})
    live_push.notify_user_of_action(session, target.id, "warn")


# ---------------------------------------------------------------------------
# kick (the body of the old ClassSessionViewSet.kick)
# ---------------------------------------------------------------------------
def kick_participant(session, user_id):
    """Remove `user_id` from the LiveKit room (+ their linked parent observer), stamp kicked_at so /join/ and /token/
    refuse them for THIS session, free the seat for the waitlist and tell the room. LiveKitError of the student's own
    removal propagates (same as before); the parent's is ignored (most kicks have no parent connected)."""
    from .livekit_utils import LiveKitError, remove_participant
    from .realtime import broadcast_to_session
    from .views.sessions import _try_promote_from_waitlist

    remove_participant(str(session.room_id), identity=str(user_id))
    try:
        remove_participant(str(session.room_id), identity=f"parent-{user_id}")
    except LiveKitError:
        pass
    now = timezone.now()
    session.participants.filter(user_id=user_id, left_at__isnull=True).update(left_at=now, kicked_at=now)
    _try_promote_from_waitlist(session)
    broadcast_to_session(session.id, "participant.kicked", {"user_id": int(user_id)})


# ---------------------------------------------------------------------------
# classroom ban (the body of the old ClassroomViewSet.ban)
# ---------------------------------------------------------------------------
def apply_classroom_ban(classroom, student, actor, reason=""):
    """-> (ban, created). Creates the ban, reverses active paid passes, rejects a pending join request, then (outside
    the transaction) removes the student from the live room they are in right now. Idempotent."""
    from .livekit_utils import LiveKitError, remove_participant
    from .models import ClassJoinRequest, ClassroomBan, PassPurchase, SessionParticipant
    from .realtime import broadcast_to_session

    with transaction.atomic():
        ban, created = ClassroomBan.objects.get_or_create(
            classroom=classroom, student=student, defaults={"banned_by": actor, "reason": reason or ""},
        )
        if not created:
            return ban, False
        for purchase in list(PassPurchase.objects.select_for_update().filter(
            class_pass__classroom=classroom, student=student, status=PassPurchase.Status.SUCCESS,
            is_active=True, expires_at__gt=timezone.now(),
        )):
            purchase.reverse()
        ClassJoinRequest.objects.filter(
            classroom=classroom, student=student, status=ClassJoinRequest.Status.PENDING,
        ).update(status=ClassJoinRequest.Status.REJECTED)

    live = (
        SessionParticipant.objects.filter(session__classroom=classroom, user=student, left_at__isnull=True)
        .select_related("session").first()
    )
    if live is not None:
        try:
            remove_participant(str(live.session.room_id), identity=str(student.id))
        except LiveKitError:
            logger.warning("LiveKit remove_participant failed while banning student %s from classroom %s "
                           "(room already gone / LiveKit unreachable).", student.id, classroom.id)
        now = timezone.now()
        SessionParticipant.objects.filter(pk=live.pk).update(left_at=now, kicked_at=now)
        broadcast_to_session(live.session_id, "participant.kicked", {"user_id": student.id, "reason": "banned"})
    return ban, True


# ---------------------------------------------------------------------------
# moderate() - the dispatcher
# ---------------------------------------------------------------------------
def moderate(session, actor, action, user_id, *, minutes=None, note="", report_id=None):
    """Run ONE moderator action. Returns a small dict for the response. Raises DRF exceptions for refusals."""
    from .models import LiveModerationEvent

    if action not in ACTIONS:
        raise ValidationError({"action": f"Must be one of {list(ACTIONS)}."})
    ensure_moderator(session, actor)
    target = _target(session, actor, user_id)
    note = str(note or "").strip()[:255]
    out = {"action": action, "user_id": target.id}

    if action == "warn":
        warn(session, target, actor=actor, note=note)
    elif action == "chat_mute":
        try:
            minutes = int(minutes) if minutes not in (None, "") else MUTE_MINUTES_DEFAULT
        except (TypeError, ValueError):
            raise ValidationError({"minutes": "Must be a whole number of minutes."})
        if minutes < 1:
            raise ValidationError({"minutes": "Must be at least 1."})
        until = apply_chat_mute(session, target, minutes, actor=actor, reason="moderator", detail=note)
        out["muted_until"] = until.isoformat()
    elif action == "chat_unmute":
        clear_chat_mute(session, target, actor=actor)
    elif action == "kick":
        kick_participant(session, target.id)
        log_event(session, "kick", actor=actor, target=target, detail=note)
    elif action == "ban":
        _ban, created = apply_classroom_ban(session.classroom, target, actor, note)
        log_event(session, "ban", actor=actor, target=target, detail=note)
        from . import live_push

        if created:
            live_push.notify_user_of_action(session, target.id, "ban")

    if report_id:
        report = LiveModerationEvent.objects.filter(pk=report_id, session=session, kind="report").first()
        if report is not None and report.resolved_at is None:
            resolve_report(report, actor, "actioned", detail=action)
    return out


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------
def file_report(session, reporter, *, target_user=None, chat_message=None, reason="other", note=""):
    """-> (event, created). A participant reports a person, one chat line (target = its sender) or the class itself
    (no target). Re-reporting the same open thing is free and returns the existing row; a new report spends report
    budget (child-safety reports are never limited)."""
    from . import live_abuse, live_push
    from .models import LiveModerationEvent

    reason = str(reason or "other").strip().lower()
    if reason not in REPORT_REASONS:
        raise ValidationError({"reason": f"Must be one of {sorted(REPORT_REASONS)}."})
    note = str(note or "").strip()[:255]

    msg_id = None
    if chat_message is not None:
        if chat_message.session_id != session.id:
            raise ValidationError({"chat_message": "That message is not from this class."})
        msg_id = chat_message.id
        target_user = chat_message.sender
    if target_user is not None and target_user.id == reporter.id:
        raise ValidationError("You can't report yourself.")

    existing = LiveModerationEvent.objects.filter(
        session=session, kind="report", actor=reporter, target_user=target_user,
        chat_message_id=msg_id, resolved_at__isnull=True,
    ).first()
    if existing is not None:
        return existing, False

    if reason != "minor_safety":
        live_abuse.consume_report_budget(session, reporter)

    event = log_event(session, "report", actor=reporter, target=target_user, reason=reason, detail=note,
                      chat_message_id=msg_id)
    if event is None:
        raise ValidationError("Could not save the report. Please try again.")

    if chat_message is not None and not chat_message.is_flagged:
        chat_message.is_flagged = True
        chat_message.flagged_reason = f"reported:{reason}"[:100]
        chat_message.save(update_fields=["is_flagged", "flagged_reason"])

    _mirror_to_platform(event, reporter, session, target_user, chat_message, reason, note)

    # Moderators are told only when they can act on it: the accused is a student, not the teacher or staff.
    if target_user is not None and not is_host_tier(session, target_user):
        live_push.notify_moderators_of_report(session, event, exclude=[reporter.id, target_user.id])
    return event, True


def _mirror_to_platform(event, reporter, session, target_user, chat_message, reason, note):
    from .models import LiveModerationEvent

    try:
        from user_profile.services import file_report as platform_file_report

        if chat_message is not None:
            kind, target_id = "class_chat", chat_message.id
        elif target_user is not None:
            kind, target_id = "user", target_user.id
        else:
            kind, target_id = "live_session", session.id
        report, _created = platform_file_report(reporter, kind, target_id, REPORT_REASONS[reason], note)
        LiveModerationEvent.objects.filter(pk=event.pk).update(content_report_id=report.pk)
    except Exception:       # LookupError / ValueError / ReportRateLimited / anything - the class report stands
        logger.warning("live report %s: mirror into ContentReport skipped", event.pk, exc_info=True)


def _tell_live_reporter(event, resolution):
    """P-F15: the person who filed a live-class report is told how it ended, and the ContentReport behind it (when there
    is one) is closed too. Best-effort: a failure here never undoes the moderator's decision."""
    try:
        from user_profile.models import ContentReport
        from user_profile.report_automation import notify_outcome

        if event.content_report_id:
            ContentReport.objects.filter(pk=event.content_report_id, status__in=("open", "reviewed")).update(
                status=resolution)
        if event.actor_id:                      # for kind=report the actor is the reporter
            notify_outcome([event.actor_id], resolution, "live_session", event.session_id)
    except Exception:
        logger.exception("live report outcome notification failed (event=%s)", getattr(event, "pk", None))


def resolve_report(event, actor, resolution, detail=""):
    if resolution not in ("actioned", "dismissed"):
        raise ValidationError({"resolution": "Must be 'actioned' or 'dismissed'."})
    if event.kind != "report":
        raise ValidationError("Only a report can be resolved.")
    if event.resolved_at is not None:
        return event
    event.resolved_at = timezone.now()
    event.resolved_by = actor
    event.resolution = resolution
    event.save(update_fields=["resolved_at", "resolved_by", "resolution"])
    _tell_live_reporter(event, resolution)
    if resolution == "dismissed":
        log_event(event.session, "dismiss", actor=actor, target=event.target_user, reason=event.reason,
                  detail=detail, content_report_id=event.content_report_id)
    return event


# ---------------------------------------------------------------------------
# queue / log
# ---------------------------------------------------------------------------
def moderation_queue(session, viewer, *, resolved=False):
    """Reports of this class for the moderators. Hidden from everyone but platform staff: reports about the host tier
    (the classroom's own teacher/staff must not read reports about themselves) and reports about the class itself."""
    from . import live_push
    from .models import LiveModerationEvent

    qs = LiveModerationEvent.objects.filter(session=session, kind="report").select_related("target_user")
    qs = qs.filter(resolved_at__isnull=not resolved)
    if not viewer.is_staff:
        qs = qs.filter(target_user__isnull=False).exclude(target_user_id__in=live_push.moderator_ids(session))
        qs = qs.exclude(target_user_id=viewer.id)
    return qs.order_by("-created_at", "-id")


def queue_item(event):
    """Plain dict for the app. No reporter field, ever."""
    from . import live_abuse
    from .live_safety import is_minor
    from .models import ChatMessage, LiveModerationEvent

    target = event.target_user
    muted_until = live_abuse.chat_mute_until(event.session, target) if target is not None else None
    message = None
    if event.chat_message_id:
        m = ChatMessage.objects.filter(pk=event.chat_message_id).only("id", "message", "is_deleted").first()
        if m is not None:
            message = {"id": m.id, "text": "" if m.is_deleted else m.message, "deleted": m.is_deleted}
    same_target = 0
    if target is not None:
        same_target = LiveModerationEvent.objects.filter(
            session_id=event.session_id, kind="report", target_user=target).count()
    return {
        "id": event.id,
        "created_at": event.created_at.isoformat(),
        "reason": event.reason,
        "detail": event.detail,
        "target": None if target is None else {
            "id": target.id, "name": (target.get_full_name() or target.username),
            "is_minor": bool(is_minor(target)),
            "muted_until": muted_until.isoformat() if muted_until else None,
        },
        "message": message,
        "reports_on_target": same_target,
        "resolved": event.resolved_at is not None,
        "resolution": event.resolution,
    }


def audit_item(event):
    """Audit-log row for the host tier: actions show their moderator, reports never show the reporter."""
    is_report = event.kind == "report"
    actor = None if (is_report or event.actor is None) else (event.actor.get_full_name() or event.actor.username)
    target = event.target_user
    return {
        "id": event.id,
        "kind": event.kind,
        "created_at": event.created_at.isoformat(),
        "actor": actor,
        "system": event.actor_id is None,
        "target_id": getattr(target, "id", None),
        "target": None if target is None else (target.get_full_name() or target.username),
        "reason": event.reason,
        "detail": event.detail,
    }
