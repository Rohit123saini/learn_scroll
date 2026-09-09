# liveclass/signals.py
"""
Side-effects that must fire regardless of *which* code path changed the
state — the `end` API action, Django admin, a management command, or a
future Celery task that auto-completes stale sessions. Centralizing these
here means "session ended" or "a seat opened up" always does the right
thing, instead of every call site having to remember to do it itself.

Wired here:
    1. ClassSession pre_save   -> stash the pre-save status (post_save
       doesn't receive the old row, so this is the standard Django pattern
       for diffing a transition).
    2. ClassSession post_save  -> on a *fresh* transition into
       COMPLETED/CANCELLED: tear down the LiveKit room, close any still-open
       polls, force-checkout any still-"in room" participants, clear any
       leftover waitlist entries (a seat opening up after the session is
       over is moot), and stamp actual_end if it isn't set yet.
    3. ClassSession post_delete -> best-effort LiveKit room teardown if a
       session row is deleted outright (e.g. from the Django admin).
    4. SessionParticipant pre_save / post_save -> on left_at transitioning
       None -> a timestamp (a student/host actually left the room):
         - promote the next student off the SessionWaitlist (FCFS)
         - best-effort +1 to classes_attended on the PassPurchase that
           granted access, for capped ("N-class pack") passes only
    5. ClassJoinRequest post_save -> fresh transition into ACCEPTED: sync
       the student into the classroom's linked chat group (task 31).
    6. SessionWaitlist FCFS promotion (inside #4 above) -> same chat-group
       sync as #5 (task 32).
    7. ClassroomStaff post_save (created) -> promote the new co-teacher/
       moderator to GroupMember.MODERATOR in the linked chat group
       (task 33).
    8. ClassroomBan post_save (created) -> remove the banned student from
       the linked chat group (task 34).
    9. PassPurchase post_save -> fresh transition into REFUNDED -> remove
       the refunded student from the linked chat group (task 35).
    10. Classroom post_save -> is_active True->False or is_deleted->True:
        archive the linked chat group (task 37). title/cover_image/
        description change (and NOT closed): sync group metadata
        (task 36).
    All of #5-#10 go through core/classroom_chat_bridge.py, which is a
    complete no-op if the classroom in question has no linked chat group
    (chat_group_enabled=False) — see that file's module docstring.

PRODUCTION-HARDENING NOTES (read this before touching the code below):
    - Every step below runs as its OWN try/except. Earlier versions of this
      module let an exception from any one step (a bad LiveKit response, a
      stale FK, a query that raises) bubble straight out of the signal
      handler. Since these signals fire *synchronously inside*
      ClassSession.save()/SessionParticipant.save(), an uncaught exception
      here doesn't just skip a cleanup step — it raises out of the .save()
      call itself, which means whatever view/task called .save() (session
      end, /join/, /leave/, auto-complete, admin edit...) blows up
      mid-request with a 500, even though the actual status/left_at change
      the caller cared about was already valid. That is the "process beech
      me ruk jaata hai, phir wapas jaake fix karna padta hai" failure mode.
      Isolating each step means one bad step is logged and skipped, and
      every other step (and the request itself) still completes normally.
    - Everything that is not needed to make THIS .save() call itself
      correct (LiveKit calls, notification dispatch, cross-row cleanup) is
      deferred to transaction.on_commit(). Two reasons:
        1. If the enclosing request is wrapped in transaction.atomic()
           (explicit, or via ATOMIC_REQUESTS), code here runs *before* the
           transaction commits. A Celery task queued at that point (e.g.
           notify_waitlist_promotion.delay(...)) can start executing on a
           worker before the row it's about to query has actually landed
           in the DB — the worker gets a stale/missing row. on_commit()
           guarantees the task is only queued once the change is durable.
        2. If the outer transaction rolls back for an unrelated reason
           (something later in the same view raises), work already done
           in a signal handler that talked to LiveKit or Celery cannot be
           undone — on_commit() ensures it never happens for a change that
           didn't actually stick.
    - Waitlist promotion is compare-and-swap, not get-then-save. Two
      participants leaving at nearly the same moment used to be able to
      both read the same `notified=False` waitlist row before either had
      saved, and both promote the same student. The promotion below uses a
      single conditional .filter(notified=False).update(notified=True) —
      exactly one concurrent caller can ever "win" that update — instead of
      loading the row, mutating it in Python, and saving it back.
    - LiveKit failures are logged and swallowed, never raised. A LiveKit-
      side hiccup should not prevent our own DB from correctly recording
      that a session ended — the DB is the source of truth; LiveKit room
      state is a downstream effect we best-effort keep in sync. If you need
      hard guarantees here (e.g. alerting when teardown fails), hook into
      the log line below or wrap end_room in a retry task instead of
      failing the request.
    - SessionParticipant has no FK back to the PassPurchase that granted
      access, so attendance-credit matching re-derives "the currently valid
      capped purchase for this classroom" the same way Classroom.has_access()
      does. If a student has more than one active capped purchase for the
      same classroom this credits the most recently purchased one — fine
      for the common case. For exact tracking, add a `pass_purchase` FK to
      SessionParticipant at join time and use that instead.
    - Notification dispatch for the FCFS waitlist "seat opened up" event
      creates BOTH the in-app (bell icon) row and the push, from this one
      call site:
        - create_notification(...) (models.py) writes the bell row —
          same helper every other notification in this app uses.
        - tasks.notify_waitlist_promotion.delay(...) queues the actual
          push send as a Celery task — NOT called inline — so a slow or
          failing push provider can never delay the on_commit callback
          itself. This mirrors the exact pattern views._refund_purchase
          uses for notify_purchase_refunded.delay(...).
      NOTE (fix): this used to import a `notify_waitlist_seat_open` from
      liveclass.notifications — that function was never defined anywhere
      in the codebase, so every promotion silently raised ImportError,
      was swallowed by the try/except below, and logged-and-forgotten.
      No student was ever notified of a freed-up seat. The real push task
      lives in tasks.py as `notify_waitlist_promotion`, and the bell row
      never existed at all for this path (unlike the separate
      host-triggered /waitlist/{id}/promote/ endpoint in views.py, which
      already calls create_notification()). Both are now wired here.
"""

import logging

from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .livekit_utils import LiveKitError, end_room
from .models import (
    ClassJoinRequest,
    Classroom,
    ClassroomBan,
    ClassroomStaff,
    ClassSession,
    LivePoll,
    PassPurchase,
    SessionParticipant,
    SessionWaitlist,
)
# 🔥 MOVED (tasks 42/43) — Notification/NotificationPreference + the
# create_notification() helper now live in `core`, not `liveclass`.
from core.models import Notification
from core.services import create_notification

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = (ClassSession.Status.COMPLETED, ClassSession.Status.CANCELLED)


# ---------------------------------------------------------------------------
# ClassSession -> transition into a terminal status (COMPLETED / CANCELLED)
# ---------------------------------------------------------------------------
@receiver(pre_save, sender=ClassSession)
def stash_previous_session_status(sender, instance, **kwargs):
    previous = None
    if instance.pk:
        try:
            previous = (
                ClassSession.objects.filter(pk=instance.pk)
                .values_list("status", flat=True)
                .first()
            )
        except Exception:
            # Never let a lookup failure here block the save itself — worst
            # case we treat this as "no previous status known" and the
            # post_save handler below simply won't detect a transition.
            logger.exception("Could not read previous status for ClassSession %s.", instance.pk)
    instance._previous_status = previous


@receiver(post_save, sender=ClassSession)
def cleanup_on_session_end(sender, instance, created, **kwargs):
    if created:
        return

    previous = getattr(instance, "_previous_status", None)
    if previous in _TERMINAL_STATUSES or instance.status not in _TERMINAL_STATUSES:
        return  # not a fresh transition into a terminal state — nothing to do here

    session_id = instance.pk
    new_status = instance.status
    room_id = str(instance.room_id)
    logger.info("ClassSession %s -> %s: scheduling end-of-session cleanup.", session_id, new_status)

    # Every step below is independent and best-effort: one failing step is
    # logged and must never stop the others from running. Deferred to
    # on_commit so none of this fires against a transaction that might
    # still roll back, and so the LiveKit call / Celery queueing never
    # happens before the COMPLETED/CANCELLED status is actually durable.
    def _run_cleanup():
        try:
            end_room(room_id)
        except LiveKitError:
            logger.warning(
                "LiveKit room teardown failed for session %s (already gone / LiveKit unreachable).",
                session_id,
            )
        except Exception:
            logger.exception("Unexpected error tearing down LiveKit room for session %s.", session_id)

        try:
            LivePoll.objects.filter(session_id=session_id, is_active=True).update(
                is_active=False, closed_at=timezone.now()
            )
        except Exception:
            logger.exception("Failed closing open polls for session %s.", session_id)

        try:
            SessionParticipant.objects.filter(session_id=session_id, left_at__isnull=True).update(
                left_at=timezone.now()
            )
        except Exception:
            logger.exception("Failed force-checking-out participants for session %s.", session_id)

        try:
            # Session is over — a waitlist seat opening up now is moot.
            # Drop the entries so they don't linger and confuse a "your
            # waitlist" screen.
            SessionWaitlist.objects.filter(session_id=session_id).delete()
        except Exception:
            logger.exception("Failed clearing waitlist for session %s.", session_id)

        try:
            ClassSession.objects.filter(pk=session_id, actual_end__isnull=True).update(
                actual_end=timezone.now()
            )
        except Exception:
            logger.exception("Failed stamping actual_end for session %s.", session_id)

        # NOTE (fix — Pass 14's engagement report never actually got
        # queued): ClassSession.engagement_report's own docstring, and
        # ClassSessionViewSet.engagement_report's fallback-computation
        # branch, both already described this task firing here — it
        # just never did. COMPLETED only (not CANCELLED — a cancelled
        # session has no attendance/chat/poll activity worth a report).
        if new_status == ClassSession.Status.COMPLETED:
            try:
                from .tasks import build_engagement_report

                build_engagement_report.delay(session_id)
            except Exception:
                logger.exception("Failed to queue engagement report build for session %s.", session_id)

        logger.info("ClassSession %s -> %s: end-of-session cleanup finished.", session_id, new_status)

    transaction.on_commit(_run_cleanup)


@receiver(post_delete, sender=ClassSession)
def cleanup_on_session_delete(sender, instance, **kwargs):
    room_id = str(instance.room_id)
    session_id = instance.pk

    def _teardown():
        try:
            end_room(room_id)
        except LiveKitError:
            logger.warning(
                "LiveKit room teardown failed for deleted session %s (already gone / LiveKit unreachable).",
                session_id,
            )
        except Exception:
            logger.exception("Unexpected error tearing down LiveKit room for deleted session %s.", session_id)

    transaction.on_commit(_teardown)


# ---------------------------------------------------------------------------
# SessionParticipant -> left_at set (someone actually left the room)
# ---------------------------------------------------------------------------
@receiver(pre_save, sender=SessionParticipant)
def stash_previous_left_at(sender, instance, **kwargs):
    previous = None
    if instance.pk:
        try:
            previous = (
                SessionParticipant.objects.filter(pk=instance.pk)
                .values_list("left_at", flat=True)
                .first()
            )
        except Exception:
            logger.exception("Could not read previous left_at for SessionParticipant %s.", instance.pk)
    instance._previous_left_at = previous


@receiver(post_save, sender=SessionParticipant)
def on_participant_left(sender, instance, created, **kwargs):
    if created or instance.left_at is None:
        return
    if getattr(instance, "_previous_left_at", None) is not None:
        return  # already processed this leave — avoid double-promotion on unrelated re-saves

    participant_id = instance.pk
    session_id = instance.session_id
    role = instance.role
    user_id = instance.user_id

    def _promote_next_waitlist_entry():
        """Compare-and-swap promotion: exactly one concurrent caller can
        ever flip a given row from notified=False -> True, so two
        participants leaving at nearly the same instant can never promote
        the same waitlist entry twice."""
        try:
            next_in_line = (
                SessionWaitlist.objects.select_related("student", "session__classroom")
                .filter(session_id=session_id, notified=False)
                .order_by("joined_at")
                .first()
            )
            if not next_in_line:
                return

            updated = SessionWaitlist.objects.filter(pk=next_in_line.pk, notified=False).update(
                notified=True
            )
            if not updated:
                # Someone else's leave-signal won the race for this row —
                # fine, nothing further to do here.
                return
        except Exception:
            logger.exception(
                "Failed selecting/promoting next waitlist entry for session %s.", session_id
            )
            return

        # Single call site for this notification — see module docstring.
        # Deferred to on_commit for the same reason as the ClassSession
        # cleanup above: never queue the bell row / push before the
        # notified=True flip is actually durable.
        def _dispatch():
            try:
                create_notification(
                    recipient=next_in_line.student,
                    notif_type=Notification.NotifType.WAITLIST_PROMOTED,
                    title="A seat opened up!",
                    message=(
                        f"A seat is now open in {next_in_line.session.classroom.title} "
                        "— join now."
                    ),
                    classroom=next_in_line.session.classroom,
                    session=next_in_line.session,
                )
            except Exception:
                logger.exception(
                    "Failed creating in-app waitlist-seat-open notification for entry %s.",
                    next_in_line.pk,
                )

            # 🔥 NAYA (task 32) — same classroom-chat-group bridge call as
            # the ClassJoinRequest -> ACCEPTED path below. A promoted
            # student already has real classroom access (that's what
            # earned them the waitlist spot); this just makes sure
            # they're in the linked chat group too, in case the group was
            # created after they first got access. No-op if this
            # classroom has no linked group — see classroom_chat_bridge.py.
            try:
                from core.classroom_chat_bridge import sync_membership_on_join_accept

                sync_membership_on_join_accept(
                    next_in_line.session.classroom, next_in_line.student,
                )
            except Exception:
                logger.exception(
                    "Failed syncing promoted waitlist student %s into chat group for entry %s.",
                    next_in_line.student_id, next_in_line.pk,
                )

            try:
                # FIX (audit): this used to pass next_in_line.pk alone —
                # the same broken pre-fix call shape views.py's own
                # comments (SessionWaitlistViewSet.promote()) describe as
                # a "CRITICAL production bug": notify_waitlist_promotion's
                # real signature is (student_id, session_id), not a
                # single waitlist-entry pk. views.py's two call sites
                # were already updated to the new shape; this one was
                # missed, so every push here raised TypeError inside the
                # Celery worker (silent — this is a fire-and-forget
                # .delay(), so nothing here ever saw the failure) and no
                # student was ever actually pushed for THIS promotion
                # path (the leave-triggered one) even though the in-app
                # bell row above worked fine.
                from .tasks import notify_waitlist_promotion

                notify_waitlist_promotion.delay(next_in_line.student_id, session_id)
            except Exception:
                logger.exception(
                    "Failed queuing waitlist-seat-open push for entry %s.",
                    next_in_line.pk,
                )

        transaction.on_commit(_dispatch)

    def _credit_attendance():
        """Best-effort attendance credit for capped ("N-class pack")
        passes only."""
        if role != SessionParticipant.Role.STUDENT:
            return
        try:
            purchase = (
                PassPurchase.objects.filter(
                    student_id=user_id,
                    class_pass__classroom=instance.session.classroom,
                    status=PassPurchase.Status.SUCCESS,
                    is_active=True,
                    class_pass__max_classes__isnull=False,
                )
                .order_by("-purchased_at")
                .first()
            )
            if purchase:
                PassPurchase.objects.filter(pk=purchase.pk).update(
                    classes_attended=F("classes_attended") + 1
                )
        except Exception:
            logger.exception(
                "Failed crediting attendance for participant %s (session %s).",
                participant_id,
                session_id,
            )

    # A seat opened up — promote the next student off the waitlist (FCFS).
    _promote_next_waitlist_entry()
    _credit_attendance()


# ---------------------------------------------------------------------------
# (task 31) ClassJoinRequest -> ACCEPTED: sync the accepted student into
# the classroom's linked chat group, if one exists.
# ---------------------------------------------------------------------------
@receiver(pre_save, sender=ClassJoinRequest)
def stash_previous_join_request_status(sender, instance, **kwargs):
    previous = None
    if instance.pk:
        try:
            previous = (
                ClassJoinRequest.objects.filter(pk=instance.pk)
                .values_list("status", flat=True)
                .first()
            )
        except Exception:
            logger.exception("Could not read previous status for ClassJoinRequest %s.", instance.pk)
    instance._previous_status = previous


@receiver(post_save, sender=ClassJoinRequest)
def sync_chat_group_on_join_accept(sender, instance, created, **kwargs):
    if created:
        return  # a fresh row is never already ACCEPTED on creation in this app's flow
    previous = getattr(instance, "_previous_status", None)
    if previous == ClassJoinRequest.Status.ACCEPTED or instance.status != ClassJoinRequest.Status.ACCEPTED:
        return  # not a fresh transition into ACCEPTED

    def _sync():
        try:
            from core.classroom_chat_bridge import sync_membership_on_join_accept

            sync_membership_on_join_accept(instance.classroom, instance.student)
        except Exception:
            logger.exception(
                "Failed syncing chat group membership for accepted join-request %s.", instance.pk,
            )

    transaction.on_commit(_sync)


# ---------------------------------------------------------------------------
# (task 33) A co-teacher/moderator is added to a classroom -> promote them
# to GroupMember.MODERATOR in the linked chat group (adds them first if
# they aren't already a member).
# ---------------------------------------------------------------------------
@receiver(post_save, sender=ClassroomStaff)
def sync_chat_group_on_staff_add(sender, instance, created, **kwargs):
    if not created:
        return  # role changes on an existing staff row don't need re-promotion

    def _sync():
        try:
            from core.classroom_chat_bridge import promote_to_moderator

            promote_to_moderator(instance.classroom, instance.user)
        except Exception:
            logger.exception(
                "Failed promoting staff user %s to moderator for classroom %s.",
                instance.user_id, instance.classroom_id,
            )

    transaction.on_commit(_sync)


# ---------------------------------------------------------------------------
# (task 36) Classroom title/cover_image/description update -> keep the
# linked chat group's name/photo/description in step.
# (task 37) Classroom close (is_active True->False) or soft-delete
# (is_deleted False->True, if this app's BaseModel has that field like
# message's does) -> archive the linked chat group.
# ---------------------------------------------------------------------------
_CLASSROOM_METADATA_FIELDS = ("title", "cover_image", "description")


@receiver(pre_save, sender=Classroom)
def stash_previous_classroom_snapshot(sender, instance, **kwargs):
    previous = None
    if instance.pk:
        try:
            previous = (
                Classroom.objects.filter(pk=instance.pk)
                .values("is_active", *_CLASSROOM_METADATA_FIELDS)
                .first()
            )
        except Exception:
            logger.exception("Could not read previous snapshot for Classroom %s.", instance.pk)
    instance._previous_snapshot = previous


@receiver(post_save, sender=Classroom)
def sync_chat_group_on_classroom_change(sender, instance, created, **kwargs):
    if created:
        return
    previous = getattr(instance, "_previous_snapshot", None)
    if previous is None:
        return  # couldn't read the old row — nothing safe to diff against

    # --- task 37: close / soft-delete -> archive -----------------------
    was_active = previous.get("is_active", True)
    is_deleted_now = getattr(instance, "is_deleted", False)
    if was_active and not instance.is_active or is_deleted_now:
        def _archive():
            try:
                from core.classroom_chat_bridge import archive_group_on_classroom_close

                archive_group_on_classroom_close(instance)
            except Exception:
                logger.exception("Failed archiving chat group for classroom %s close.", instance.pk)

        transaction.on_commit(_archive)
        return  # closed/deleted classroom's metadata doesn't need syncing too

    # --- task 36: title/cover_image/description -> sync metadata -------
    changed = any(
        previous.get(field) != getattr(instance, field, None)
        for field in _CLASSROOM_METADATA_FIELDS
    )
    if not changed:
        return

    def _sync():
        try:
            from core.classroom_chat_bridge import sync_group_metadata

            sync_group_metadata(instance)
        except Exception:
            logger.exception("Failed syncing chat group metadata for classroom %s.", instance.pk)

    transaction.on_commit(_sync)


# ---------------------------------------------------------------------------
# (task 34) Classroom-wide ban -> remove the student from the linked chat
# group. (Session-level "kick" — SessionParticipant.kicked_at — is a
# temporary, single-session removal and deliberately does NOT touch chat
# group membership; only a full ClassroomBan does. See
# classroom_chat_bridge.sync_membership_on_removal's reason= param, used
# here and by the refund path below.)
# ---------------------------------------------------------------------------
@receiver(post_save, sender=ClassroomBan)
def sync_chat_group_on_ban(sender, instance, created, **kwargs):
    if not created:
        return

    def _sync():
        try:
            from core.classroom_chat_bridge import sync_membership_on_removal

            sync_membership_on_removal(instance.classroom, instance.student, reason="kick")
        except Exception:
            logger.exception(
                "Failed removing banned student %s from chat group for classroom %s.",
                instance.student_id, instance.classroom_id,
            )

    transaction.on_commit(_sync)


# ---------------------------------------------------------------------------
# (task 35) PassPurchase.reverse() (refund) -> remove the student from the
# linked chat group. Wired as a status-transition signal (same
# pre_save-stash / post_save-diff pattern as ClassSession/ClassJoinRequest
# above) rather than a direct call inside reverse() itself, so this fires
# no matter which code path flips the status to REFUNDED.
# ---------------------------------------------------------------------------
@receiver(pre_save, sender=PassPurchase)
def stash_previous_purchase_status(sender, instance, **kwargs):
    previous = None
    if instance.pk:
        try:
            previous = (
                PassPurchase.objects.filter(pk=instance.pk)
                .values_list("status", flat=True)
                .first()
            )
        except Exception:
            logger.exception("Could not read previous status for PassPurchase %s.", instance.pk)
    instance._previous_status = previous


@receiver(post_save, sender=PassPurchase)
def sync_chat_group_on_purchase_refund(sender, instance, created, **kwargs):
    if created:
        return
    previous = getattr(instance, "_previous_status", None)
    if previous == PassPurchase.Status.REFUNDED or instance.status != PassPurchase.Status.REFUNDED:
        return  # not a fresh transition into REFUNDED

    def _sync():
        try:
            from core.classroom_chat_bridge import sync_membership_on_removal

            classroom = instance.class_pass.classroom
            sync_membership_on_removal(classroom, instance.student, reason="refund")
        except Exception:
            logger.exception(
                "Failed removing refunded student %s from chat group for purchase %s.",
                instance.student_id, instance.pk,
            )

    transaction.on_commit(_sync)