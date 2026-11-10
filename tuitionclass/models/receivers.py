# tuitionclass/models/receivers.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import logging
from django.db import transaction
from .schedules import ClassSession
from .classroom import Classroom
from .classroom_admin import ClassroomReport
from .social import ClassroomReview
from .notices_queries import Notice
from .passes import PassPurchase
from .cache_versions import (
    bump_classroom_list_cache_version,
    bump_notice_list_cache_version,
)


# ---------------------------------------------------------------------------
# SIGNALS — keep Classroom.rating_avg / rating_count / enrolled_count in sync
# ---------------------------------------------------------------------------
from django.db.models.signals import post_delete, post_save, pre_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=Classroom)
@receiver(post_delete, sender=Classroom)
def _bump_classroom_list_cache(sender, instance, **kwargs):
    """Single choke point for Explore/search list cache invalidation — see
    the CLASSROOM LIST CACHE VERSIONING note near the top of this file.
    Fires on every Classroom save (including the internal
    refresh_rating/refresh_enrolled_count/sync_flag_status saves below) and
    on every delete, so the cached list can never drift from what's
    actually in the DB by more than the time it takes this signal to run."""
    bump_classroom_list_cache_version()


@receiver(post_save, sender=Notice)
@receiver(post_delete, sender=Notice)
def _bump_notice_list_cache(sender, instance, **kwargs):
    """Invalidates the cached notice-board page for this notice's
    classroom only — see the PER-CLASSROOM NOTICE LIST CACHE VERSIONING
    note near the top of this file. Also fires on the expires_at auto-hide
    boundary being crossed only if something re-saves the row at that
    point (nothing currently does) — same known limitation as
    Classroom.enrolled_count going stale on pure time-based expiry; a
    reasonable follow-up is a short TTL on top of this (see
    NoticeViewSet.LIST_CACHE_TTL_SECONDS) rather than a new sweep task,
    since a notice going stale by a few minutes is low-stakes compared to
    money/access state.
    """
    bump_notice_list_cache_version(instance.classroom_id)


@receiver(post_save, sender=ClassroomReview)
@receiver(post_delete, sender=ClassroomReview)
def _sync_classroom_rating(sender, instance, **kwargs):
    instance.classroom.refresh_rating()


@receiver(post_save, sender=PassPurchase)
def _sync_classroom_enrolled_count(sender, instance, **kwargs):
    instance.class_pass.classroom.refresh_enrolled_count()


@receiver(pre_save, sender=ClassSession)
def _stash_previous_session_status(sender, instance, **kwargs):
    """Capture the pre-save status so the post_save handler below can tell
    an actual transition INTO completed apart from an unrelated field edit
    (title/recording_url/etc.) on a session that was already completed —
    same reasoning as the was_cancelled comparison in
    ClassSessionViewSet.perform_update (views.py)."""
    if instance.pk:
        instance._previous_status = (
            ClassSession.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )
    else:
        instance._previous_status = None


@receiver(post_save, sender=ClassSession)
def _charge_passes_for_completed_session(sender, instance, created, **kwargs):
    """The other half of the per-day escrow design (see the NOTE (fix —
    "pay only for classes actually held") on PassPurchase above):
    PassPurchase.charge_for_session() existed but nothing ever called it,
    so no pass was ever actually charged day-by-day — this is that missing
    call site. Fires on every path that can mark a session COMPLETED
    (ClassSessionViewSet.end, Django admin, a future auto-complete task),
    same "doesn't matter which path" guarantee the docstrings already
    promised. Guarded to only run on an actual transition into COMPLETED
    (via the pre_save stash above), not on every later edit to an
    already-completed session. charge_for_session() itself is idempotent
    (unique purchase+date constraint), so even a double-fire here is safe.
    """
    if instance.status != ClassSession.Status.COMPLETED:
        return
    if getattr(instance, "_previous_status", None) == ClassSession.Status.COMPLETED:
        return

    purchases = PassPurchase.objects.filter(
        class_pass__classroom_id=instance.classroom_id,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    )
    for purchase in purchases:
        try:
            with transaction.atomic():
                locked = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
                locked.charge_for_session(instance)
        except Exception:
            # NOTE: one purchase's charge failing (e.g. a lock timeout)
            # must never block the rest of the classroom's purchases from
            # being charged for this same session, and must never roll
            # back the session-completion save itself. sync_missed_charges()
            # is the safety net that catches whatever this loop misses.
            logging.getLogger("tuitionclass.models").exception(
                "Failed to charge purchase %s for completed session %s", purchase.id, instance.id
            )


@receiver(post_save, sender=ClassroomReport)
def _auto_flag_classroom(sender, instance, created, **kwargs):
    """Once a classroom accumulates AUTO_FLAG_THRESHOLD pending reports,
    flag it so it drops out of the public Explore listing (see
    ClassroomViewSet.get_queryset) until platform staff review it via
    ClassroomReportViewSet.review. Doesn't touch is_active, so the
    teacher/existing enrolled students keep normal access while it's
    under review — this only hides it from new students discovering it."""
    if not created or instance.classroom.is_flagged:
        return
    pending_count = instance.classroom.reports.filter(status=ClassroomReport.Status.PENDING).count()
    if pending_count >= ClassroomReport.AUTO_FLAG_THRESHOLD:
        instance.classroom.is_flagged = True
        instance.classroom.save(update_fields=["is_flagged"])

        # NOTE (fix): the teacher previously had no way to find out their
        # classroom had dropped out of Explore — they'd only discover it
        # if enrollment mysteriously stalled and they went looking. Queued
        # (not sent inline) so a notification-provider hiccup can never
        # roll back the flagging transaction itself. Lazy import: models.py
        # must stay importable even if tasks.py/Celery isn't fully wired
        # yet (e.g. during migrations).
        try:
            from ..tasks import notify_classroom_flagged

            notify_classroom_flagged.delay(instance.classroom_id)
        except Exception:
            logging.getLogger("tuitionclass.models").exception(
                "Failed to queue flagged-classroom notification for classroom %s",
                instance.classroom_id,
            )
