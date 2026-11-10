# core/tasks.py
"""
Celery tasks for cross-app side effects. Enqueue them with
`core.async_utils.dispatch_after_commit(task, ...)` — never call `.delay()`
straight from a view/signal (that skips the after-commit guarantee).

Every task takes only JSON-safe primitives (ids, strings) and re-fetches what
it needs, so a task always acts on current DB state and never on a stale
pickled instance.
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)


def _backoff(task) -> int:
    """30s, 60s, 120s ... for successive retries."""
    return 30 * (2 ** task.request.retries)


def push_for_notification_row(row) -> bool:
    """T2.9 — push for ONE saved bell row, honouring the recipient's
    NotificationPreference (push toggle, muted types, quiet hours / DND —
    all inside `send_push_for_notification`). Never raises. Returns True when
    a push was attempted."""
    if row is None:
        return False
    try:
        from message.push_utils import send_push_for_notification

        send_push_for_notification(row)
        return True
    except Exception:
        logger.exception("push_for_notification_row failed (notification=%s)", getattr(row, "id", None))
        return False


def create_notification_rows(recipient_ids, notif_type, title, message="", data=None, push=False) -> list:
    """Plain (non-Celery) worker for `create_notifications`: creates one
    `Notification` row per recipient, each in its own savepoint, and returns
    the recipient ids that FAILED. Never raises.

    T2.9: `push=True` also sends the (preference / quiet-hours aware) push
    for every row that was created. Default False, so every existing caller
    keeps the old bell-row-only behaviour."""
    from .models import Notification

    failed = []
    from .services import in_app_blocked_ids  # N1

    recipient_ids = list(recipient_ids)
    in_app_off = in_app_blocked_ids(recipient_ids, notif_type)  # one query for the whole batch
    from .notification_center import bell_hidden_user_ids  # N9b

    bell_hidden = bell_hidden_user_ids(recipient_ids, notif_type, data)  # one query for the whole batch
    for recipient_id in recipient_ids:
        if recipient_id in in_app_off:
            continue  # N1 - In-app chip off for this type (not a failure)
        if recipient_id in bell_hidden:
            continue  # N9b - muted with "hide from bell too" (not a failure)
        try:
            with transaction.atomic():
                row = Notification.objects.create(
                    recipient_id=recipient_id,
                    notif_type=notif_type,
                    title=title,
                    message=message,
                    data=data or {},
                )
        except Exception:
            logger.exception("create_notification_rows failed for recipient %s (type=%s).", recipient_id, notif_type)
            failed.append(recipient_id)
            continue
        if push:
            push_for_notification_row(row)
    return failed


@shared_task(bind=True, name="core.create_notifications", max_retries=3, acks_late=True)
def create_notifications(self, recipient_ids, notif_type, title, message="", data=None, push=False):
    """Celery wrapper around `create_notification_rows`. Only the rows that
    failed are retried, so one bad row can't block the rest and a retry never
    duplicates a row that already succeeded (nor re-pushes one that was
    already pushed)."""
    failed = create_notification_rows(recipient_ids, notif_type, title, message, data, push)

    if failed:
        raise self.retry(
            args=[failed, notif_type, title, message, data, push],
            exc=RuntimeError(f"{len(failed)} notification(s) failed to create"),
            countdown=_backoff(self),
        )
    return len(recipient_ids)


@shared_task(bind=True, name="core.send_purchase_receipt", max_retries=3, acks_late=True)
def send_purchase_receipt(self, kind, purchase_id):
    """R9 / N6b - receipt notification for a coin top-up ("coins") or a paid pass ("pass"). Enqueue with
    `core.notification_receipts.queue_receipt()` (after-commit). Idempotent (one row per ref), so a retry or a redelivery
    never doubles the receipt; see core/notification_receipts.py."""
    from . import notification_receipts as receipts

    if not receipts.deliver(kind, purchase_id):
        raise self.retry(exc=RuntimeError(f"receipt {kind}:{purchase_id} not delivered"), countdown=_backoff(self))
    return True


CHAT_SYNC_ACTIONS = ("join_accept", "removal", "promote", "demote", "metadata", "archive", "create")

# `removal` reasons that must NOT remove a student who still holds another live
# pass (pass expired/refunded/left but a renewed or second pass is active).
# "kick" (ban) always removes.
_REMOVAL_REASONS_NEED_NO_ACCESS = ("refund", "lapse", "leave")


@shared_task(bind=True, name="core.chat_sync", max_retries=3, acks_late=True)
def chat_sync(self, action, classroom_id, user_id=None, reason=""):
    """Keep a tuitionclass classroom's linked chat group in step with the
    classroom (join accepted / student removed / co-teacher added / metadata
    changed / classroom closed). Thin wrapper over `core.classroom_chat_bridge`
    — all the actual group logic stays there, unchanged."""
    from tuitionclass.models import Classroom

    from . import classroom_chat_bridge as chat_bridge

    if action not in CHAT_SYNC_ACTIONS:
        logger.error("core.chat_sync: unknown action %r — dropping.", action)
        return

    classroom = Classroom.objects.filter(pk=classroom_id).first()
    if classroom is None:
        logger.warning("core.chat_sync(%s): classroom %s no longer exists — nothing to do.", action, classroom_id)
        return

    user = None
    if action in ("join_accept", "removal", "promote", "demote"):
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is None:
            logger.warning("core.chat_sync(%s): user %s no longer exists — nothing to do.", action, user_id)
            return

    try:
        if action == "join_accept":
            chat_bridge.sync_membership_on_join_accept(classroom, user)
        elif action == "removal":
            if reason in _REMOVAL_REASONS_NEED_NO_ACCESS and chat_bridge.student_has_active_access(classroom, user.id):
                return
            chat_bridge.sync_membership_on_removal(classroom, user, reason=reason)
        elif action == "promote":
            chat_bridge.promote_to_moderator(classroom, user)
        elif action == "demote":
            chat_bridge.demote_from_moderator(classroom, user)
        elif action == "create":
            # T3: auto-create the group for a brand-new classroom. Real errors
            # propagate -> Celery retry (3x, backoff). The classroom itself is
            # already committed, so a failure here never un-creates it.
            chat_bridge.ensure_classroom_group(classroom)
        elif action == "metadata":
            chat_bridge.sync_group_metadata(classroom)
        elif action == "archive":
            chat_bridge.archive_group_on_classroom_close(classroom)
    except Exception as exc:
        logger.exception("core.chat_sync(%s) failed for classroom %s; will retry.", action, classroom_id)
        raise self.retry(exc=exc, countdown=_backoff(self))


@shared_task(name="core.reconcile_classroom_groups")
def reconcile_classroom_groups(classroom_id=None, dry_run=False, expired_within_minutes=None, only_enabled_missing=False):
    """T3 — compare each classroom's ACTUAL participants with its chat group's
    members; add the missing, remove the extras, fix roles (see
    `classroom_chat_bridge.reconcile_classroom_group`).

    classroom_id            -> just that classroom (on-demand).
    expired_within_minutes  -> only classrooms that had a pass expire in the
                               last N minutes (cheap frequent sweep, so an
                               expired student leaves the group within minutes).
    otherwise               -> every active, group-enabled classroom (daily).
    Returns a JSON-safe summary; one bad classroom never stops the sweep.
    """
    from datetime import timedelta

    from django.utils import timezone

    from tuitionclass.models import Classroom, PassPurchase

    from . import classroom_chat_bridge as chat_bridge

    qs = Classroom.objects.filter(is_active=True, is_deleted=False, chat_group_enabled=True)
    if classroom_id is not None:
        qs = qs.filter(pk=classroom_id)
    elif expired_within_minutes:
        now = timezone.now()
        ids = PassPurchase.objects.filter(
            expires_at__gte=now - timedelta(minutes=int(expired_within_minutes)), expires_at__lt=now,
        ).values_list("class_pass__classroom_id", flat=True).distinct()
        qs = qs.filter(pk__in=list(ids))
    if only_enabled_missing:
        qs = qs.filter(linked_conversation_id__isnull=True)

    summary = {"classrooms": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0, "reports": []}
    for classroom in qs.select_related("teacher").iterator(chunk_size=200):
        try:
            report = chat_bridge.reconcile_classroom_group(classroom, dry_run=dry_run)
        except Exception:
            logger.exception("reconcile_classroom_groups: classroom %s failed.", classroom.pk)
            summary["errors"] += 1
            continue
        summary["classrooms"] += 1
        summary["created"] += int(bool(report["created"] or (dry_run and report["group_missing"])))
        summary["added"] += len(report["added"])
        summary["removed"] += len(report["removed"])
        summary["role_fixed"] += len(report["role_fixed"])
        if (report["added"] or report["removed"] or report["role_fixed"] or report["group_missing"]) \
                and len(summary["reports"]) < 50:  # keep the Celery result small
            summary["reports"].append(report)
    return summary


@shared_task(bind=True, name="core.section_chat_sync", max_retries=3)
def section_chat_sync(self, section_id):
    """[T4 §D] Roster changed (enroll / transfer / withdraw / assignment) ->
    bring ONE section's chat group in line with its roster. Idempotent."""
    from campus.models import Section

    from . import classroom_chat_bridge as chat_bridge

    section = Section.objects.select_related("school_class").filter(pk=section_id).first()
    if section is None:
        return {"skipped": "missing"}
    try:
        return chat_bridge.reconcile_section_group(section)
    except Exception as exc:  # noqa: BLE001
        logger.exception("section_chat_sync: section %s failed.", section_id)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


@shared_task(name="core.reconcile_section_groups")
def reconcile_section_groups(section_id=None, dry_run=False):
    """[T4 §D] Daily sweep (+ management command): every group-enabled section
    of an active campus. One bad section never stops the sweep."""
    from campus.models import Section

    from . import classroom_chat_bridge as chat_bridge

    qs = Section.objects.filter(chat_group_enabled=True, school_class__campus__is_active=True)
    if section_id is not None:
        qs = qs.filter(pk=section_id)
    summary = {"sections": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0, "reports": []}
    for section in qs.select_related("school_class").iterator(chunk_size=200):
        try:
            report = chat_bridge.reconcile_section_group(section, dry_run=dry_run)
        except Exception:
            logger.exception("reconcile_section_groups: section %s failed.", section.pk)
            summary["errors"] += 1
            continue
        summary["sections"] += 1
        summary["created"] += int(bool(report["created"] or (dry_run and report["group_missing"])))
        summary["added"] += len(report["added"])
        summary["removed"] += len(report["removed"])
        summary["role_fixed"] += len(report["role_fixed"])
        if (report["added"] or report["removed"] or report["role_fixed"] or report["group_missing"]) \
                and len(summary["reports"]) < 50:
            summary["reports"].append(report)
    return summary


@shared_task(bind=True, name="core.grant_referral_rewards", max_retries=3, acks_late=True)
def grant_referral_rewards_task(self, referral_id):
    """T2.7 (Loop 7) — cost-free referral rewards (streak freeze, badges,
    founding member) for one redeemed `tuitionclass.Referral`. Thin wrapper
    over `user_profile.services.grant_referral_rewards`, which claims the row
    atomically, so a retried / duplicated task never pays twice."""
    from user_profile.services import grant_referral_rewards

    try:
        return grant_referral_rewards(referral_id)
    except Exception as exc:
        logger.exception("core.grant_referral_rewards failed for referral %s; will retry.", referral_id)
        raise self.retry(exc=exc, countdown=_backoff(self))


# ---------------------------------------------------------------------------
# T2.9 — streak-at-risk push SCHEDULING with quiet hours.
#
# The old `user_profile.send_streak_risk_reminders` fired ONE multicast at a
# fixed hour straight through `send_push_to_users`, which skips every user
# preference: a user who muted the type, turned push off, or sleeps between
# 22:00 and 07:00 was buzzed anyway. This task replaces that behaviour:
#
#   * It runs every STREAK_REMINDER_RUN_EVERY_MINUTES (beat, see settings) but
#     does nothing before STREAK_REMINDER_HOUR (server-local) — so the "2 hours
#     before the streak day rolls over" window is [REMINDER_HOUR, midnight).
#     (The streak day is the SERVER-local date, `Streak.record_activity`'s own
#     `timezone.localdate()`; only the user's QUIET HOURS use their own
#     timezone, via NotificationPreference.timezone.)
#   * The bell row is written once per user per day (found again on the next
#     run, so the task is idempotent) with `data.push_state`:
#         pending -> sent      push delivered
#         pending -> skipped   push disabled / type muted (never will be sent)
#         pending              user is in quiet hours / DND RIGHT NOW: the next
#                              run retries, so the push goes out as soon as
#                              the quiet window ends — if that is still before
#                              the streak day ends. Otherwise the bell row is
#                              all they get (the streak reminder is useless
#                              once the day has rolled over).
#   * Someone who checked in since the previous run drops out of the query, so
#     they are never pushed after the fact.
# ---------------------------------------------------------------------------
def _push_state_for(pref, notif_type, now=None, *, ignore_reminder_cap=False) -> str:
    """"send" | "defer" | "skip" for a push of `notif_type` to a user whose
    preference row is `pref` (None = brand-new user, defaults: push on)."""
    if pref is None:
        return "send"
    if not pref.channel_allowed(notif_type, "push"):  # N1: master toggle + muted + per-type override
        return "skip"
    reason = pref.push_block_reason(notif_type, now)  # N5: focus / low roll-up (drop) vs DND / quiet hours (wait)
    if reason in ("focus", "low"):
        return "skip"
    if reason:
        return "defer"
    from . import notification_reminders  # N6: daily cap / same-type collapse (read-only here; the push itself takes the slot)

    if not ignore_reminder_cap and notification_reminders.blocked_reason(pref.user_id, notif_type, pref, now):
        return "skip"
    return "send"


# H18 - study digest ("Physics: 5 new notes - #topic: 3 new posts"). The logic lives in core/content_digest.py;
# this hourly task just calls it (idempotent per user per local day, quiet-hours aware).
@shared_task(name="core.send_content_digests")
def send_content_digests():
    from . import content_digest

    return content_digest.run()


# S8 - the student's own daily study reminder (Settings > Learning). Every 15 min; per user once per local day at the
# chosen time (their NotificationPreference.timezone), quiet-hours aware (core/study_reminders.py).
@shared_task(name="core.send_study_reminders")
def send_study_reminders():
    from . import study_reminders

    try:
        return study_reminders.run()
    except Exception:
        logger.exception("core.send_study_reminders failed")
        return {"error": True}


# P-F3_F4 - revision-deck reminder (core/habit_loop.py). Every 15 min; per user once per `revision_every_days`, inside
# a grace window after the user-local `revision_hour`; quiet-hours pushes are retried every run.
@shared_task(name="core.send_revision_reminders")
def send_revision_reminders():
    from . import habit_loop

    try:
        return habit_loop.run_revision_reminders()
    except Exception:
        logger.exception("core.send_revision_reminders failed")
        return {"error": True}


# P-F3_F4 - the weekly learning report e-mail for ONE user (queued per user by generate_weekly_recaps so a slow SMTP
# call never blocks the recap run). Gated by channel_policy (verified address, e-mail on, type not muted, daily cap).
@shared_task(name="core.send_weekly_report_email")
def send_weekly_report_email(user_id, recap_id):
    from . import habit_loop

    return habit_loop.send_weekly_report_email(user_id, recap_id)


# N8 - housekeeping for the e-mail / SMS / WhatsApp daily-cap log (core/channel_policy.py).
@shared_task(name="core.prune_channel_send_log")
def prune_channel_send_log(days=35):
    from . import channel_policy

    return {"deleted": channel_policy.prune_send_log(days)}


# N9 - 30-day notification history (+ expired class / chat / post mutes). Daily; bounded per run (core/notification_center.py).
@shared_task(name="core.prune_notification_history")
def prune_notification_history():
    from . import notification_center

    try:
        result = notification_center.prune_history()
    except Exception:
        logger.exception("core.prune_notification_history failed")
        return {"error": True}
    logger.info("core.prune_notification_history %s", result)
    return result


# N10 - push SLO watch: every 10 min looks at the last window (default 15 min) and logs / Sentry-alerts a breach
# (delivery success below target, or too many skips with no clear reason). core/notification_ops.py.
@shared_task(name="core.notification_slo_watch")
def notification_slo_watch():
    from . import notification_ops

    try:
        return notification_ops.check_slo_and_alert()
    except Exception:
        logger.exception("core.notification_slo_watch failed")
        return {"error": True}


# N5 - low-priority roll-up: hourly; ONE summary push per user per local day (core/notification_delivery.py).
@shared_task(name="core.send_notification_rollups")
def send_notification_rollups():
    from . import notification_delivery

    return notification_delivery.send_rollups()


# N6 - "Snooze" on a reminder push/row: re-push the SAME row later (core/notification_reminders.py). Long snoozes are
# chained in <=50 min hops (the task re-schedules itself), a newer snooze / delete supersedes an older hop.
@shared_task(name="core.deliver_snoozed_reminder")
def deliver_snoozed_reminder(notification_id, token, until_iso):
    from . import notification_reminders

    try:
        return notification_reminders.deliver_snoozed(notification_id, token, until_iso)
    except Exception:
        logger.exception("core.deliver_snoozed_reminder failed (notification=%s)", notification_id)
        return "error"


@shared_task(name="core.send_streak_at_risk_reminders")
def send_streak_at_risk_reminders():
    from datetime import datetime, time, timedelta

    from django.conf import settings
    from django.utils import timezone

    from user_profile.models import Streak

    from .models import Notification, NotificationPreference
    from .services import create_notification

    notif_type = Notification.NotifType.STREAK_AT_RISK
    now = timezone.now()
    local_now = timezone.localtime(now)
    reminder_hour = int(getattr(settings, "STREAK_REMINDER_HOUR", 22))
    from . import habit_loop  # P-F3_F4: smart timing - each user's own hour instead of one fixed server hour

    first_hour = min(reminder_hour, habit_loop.cfg("earliest_hour")) if habit_loop.cfg("smart_timing") else reminder_hour
    if local_now.hour < first_hour:
        return {"skipped": "before_window", "created": 0, "sent": 0, "deferred": 0, "skipped_prefs": 0}

    today = local_now.date()
    day_start = timezone.make_aware(datetime.combine(today, time.min), timezone.get_current_timezone())
    yesterday = today - timedelta(days=1)

    at_risk = list(
        Streak.objects.filter(current_streak__gt=0, last_active_date=yesterday).values_list(
            "user_id", "current_streak", "freeze_tokens"
        )
    )
    summary = {"created": 0, "sent": 0, "deferred": 0, "skipped_prefs": 0}
    if not at_risk:
        return summary

    # P-F3_F4 smart timing: only users whose OWN nudge hour has arrived (history-less users = STREAK_REMINDER_HOUR).
    hour_map = habit_loop.nudge_hours_for([row[0] for row in at_risk], reminder_hour, today)
    at_risk = [row for row in at_risk if hour_map.get(row[0], reminder_hour) <= local_now.hour]
    if not at_risk:
        if local_now.hour < reminder_hour:
            return {"skipped": "before_window", "created": 0, "sent": 0, "deferred": 0, "skipped_prefs": 0}
        return summary

    user_ids = [row[0] for row in at_risk]
    todays_rows = {}
    for row in Notification.objects.filter(
        recipient_id__in=user_ids, notif_type=notif_type, created_at__gte=day_start
    ).order_by("created_at"):
        todays_rows.setdefault(row.recipient_id, row)
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=user_ids)}

    for user_id, streak_days, freeze_tokens in at_risk:
        try:
            row = todays_rows.get(user_id)
            if row is None:
                row = create_notification(
                    user_id,
                    notif_type,
                    "Don't lose your streak! \U0001F525",
                    f"You're on a {streak_days}-day streak — open LearnScroll before midnight to keep it alive.",
                    data={
                        "type": "streak_at_risk",
                        "streak_days": streak_days,
                        "freeze_tokens": freeze_tokens,
                        "push_state": "pending",
                    },
                )
                if row is None:
                    continue
                summary["created"] += 1
            if (row.data or {}).get("push_state") != "pending":
                continue  # already sent / skipped earlier today

            state = _push_state_for(prefs.get(user_id), notif_type, now)
            if state == "defer":
                summary["deferred"] += 1
                continue
            if state == "send":
                push_for_notification_row(row)
                habit_loop.take_slot(user_id, today, "streak")  # one habit nudge per day: no revision push after this
                summary["sent"] += 1
                new_state = "sent"
            else:
                summary["skipped_prefs"] += 1
                new_state = "skipped"
            row.data = {**(row.data or {}), "push_state": new_state}
            row.save(update_fields=["data"])
        except Exception:
            logger.exception("send_streak_at_risk_reminders failed for user %s.", user_id)
    return summary
