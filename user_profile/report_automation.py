"""
user_profile/report_automation.py - what happens AFTER a ContentReport is filed.

Before this module reports only landed in a table: `Post.reported_count` was
never updated (so feed_quality's "heavily reported" rule never fired), nothing
was hidden however many people reported, and moderators had no way to act from
the report itself.

  on_report_created(report)   called by services.file_report (never raises)
      1. keeps Post.reported_count exact (distinct, reliable reporters)
      2. AUTO-HOLD: enough distinct reporters -> post goes to 'flagged'
         (invisible in every feed), comment gets is_hidden=True, and a HIGH/MEDIUM
         AutoModerationFlag lands in the moderator queue. Stories / accounts are
         only flagged (stories expire by themselves; accounts need a human).
      3. unreliable reporters (many dismissed reports, none ever actioned) are
         still stored but no longer count towards a threshold - stops report-bombing.

  resolve_reports(reports, action, actor)   used by the Django-admin actions
      'hide' (content removed, all sibling reports actioned) or 'dismiss'
      (false alarm; auto-held content comes back).

Thresholds: settings.REPORT_HOLD_THRESHOLD_SEVERE (3, nudity/violence/self-harm/hate/
harassment), REPORT_HOLD_THRESHOLD_ANY (8, any reason), REPORT_UNRELIABLE_DISMISSED (10).
Copyright reports never auto-hold: they go through the formal notice in the copyrights app.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

SEVERE_REASONS = ("nudity", "violence", "self_harm", "hate", "harassment")
FLAG_HASH = "user-reports"


def _cfg(name, default):
    return getattr(settings, name, default)


def _unreliable_reporters(reporter_ids):
    from .models import ContentReport

    if not reporter_ids:
        return set()
    since = timezone.now() - timedelta(days=90)
    stats = (
        ContentReport.objects.filter(reporter_id__in=reporter_ids, created_at__gte=since)
        .values("reporter_id")
        .annotate(dismissed=Count("id", filter=Q(status="dismissed")), actioned=Count("id", filter=Q(status="actioned")))
    )
    limit = _cfg("REPORT_UNRELIABLE_DISMISSED", 10)
    return {r["reporter_id"] for r in stats if r["dismissed"] >= limit and r["actioned"] == 0}


def reporter_is_reliable(reporter) -> bool:
    return getattr(reporter, "id", reporter) not in _unreliable_reporters([getattr(reporter, "id", reporter)])


def count_reports(target_type, target_id):
    """(all_count, severe_count) of DISTINCT reliable reporters, dismissed reports ignored."""
    from .models import ContentReport

    rows = list(
        ContentReport.objects.filter(target_type=target_type, target_id=str(target_id))
        .exclude(status="dismissed").exclude(reason="copyright").values_list("reporter_id", "reason")
    )
    bad = _unreliable_reporters({r for r, _ in rows})
    all_ids = {r for r, _ in rows if r not in bad}
    severe_ids = {r for r, reason in rows if r not in bad and reason in SEVERE_REASONS}
    return len(all_ids), len(severe_ids)


def _flag(report, count, severe):
    from .models import AutoModerationFlag

    AutoModerationFlag.objects.get_or_create(
        target_type=report.target_type, target_id=str(report.target_id), text_hash=FLAG_HASH,
        defaults={
            "user": report.reported_user, "reason": "user_reports", "source": AutoModerationFlag.Source.REPORTS,
            "severity": "high" if severe else "medium", "snippet": f"{count} reporters ({severe} severe)"[:200],
        },
    )


def on_report_created(report):
    """Never raises. Returns what it did, e.g. {"count": 3, "held": True}."""
    out = {"count": 0, "held": False}
    try:
        if report.reason == "copyright":
            return out
        count, severe = count_reports(report.target_type, report.target_id)
        out["count"] = count
        if report.target_type == "post":
            from post.models import Post

            Post.objects.filter(pk=report.target_id).update(reported_count=count)

        hold = severe >= _cfg("REPORT_HOLD_THRESHOLD_SEVERE", 3) or count >= _cfg("REPORT_HOLD_THRESHOLD_ANY", 8)
        if not hold:
            return out

        if report.target_type == "post":
            from post.models import Post

            out["held"] = Post.objects.filter(pk=report.target_id, moderation_status="approved").update(
                moderation_status="flagged") > 0
            _flag(report, count, severe)
        elif report.target_type == "comment":
            from post.models import PostComment

            out["held"] = PostComment.objects.filter(pk=report.target_id, is_hidden=False).update(is_hidden=True) > 0
            _flag(report, count, severe)
        else:  # story / user: moderators decide
            _flag(report, count, severe)
    except Exception:
        logger.exception("report automation failed (report=%s)", getattr(report, "pk", None))
    return out


# --------------------------------------------------------------------------
# moderator actions on reports (Django admin)
# --------------------------------------------------------------------------
def _notify(user, title, message):
    try:
        from core.services import create_notification

        create_notification(user, "generic", title, message, data={"kind": "report_outcome"})
    except Exception:
        logger.exception("report outcome notification failed")


def _hide_target(target_type, target_id):
    if target_type == "post":
        from post.models import Post

        # never overwrite a copyright state (that has its own undo)
        Post.objects.filter(pk=target_id).exclude(
            moderation_status__in=("copyright_hold", "copyright_removed")).update(moderation_status="rejected")
    elif target_type == "comment":
        from post.models import PostComment

        PostComment.objects.filter(pk=target_id).update(is_hidden=True)
    elif target_type == "story":
        from post.models import Story

        Story.objects.filter(pk=target_id).update(is_deleted=True, deleted_at=timezone.now())


def _unhide_target(target_type, target_id):
    if target_type == "post":
        from post.models import Post

        Post.objects.filter(pk=target_id, moderation_status="flagged").update(moderation_status="approved")
    elif target_type == "comment":
        from post.models import PostComment

        PostComment.objects.filter(pk=target_id).update(is_hidden=False)


def resolve_reports(reports, action, actor=None):
    """action: 'hide' | 'dismiss'. Resolves every OPEN/REVIEWED report on the same targets too."""
    from .models import AutoModerationFlag, ContentReport

    targets = {(r.target_type, r.target_id) for r in reports}
    done = 0
    for target_type, target_id in targets:
        siblings = ContentReport.objects.filter(
            target_type=target_type, target_id=target_id, status__in=("open", "reviewed"))
        sibling_list = list(siblings.select_related("reporter"))
        if action == "hide":
            _hide_target(target_type, target_id)
            siblings.update(status=ContentReport.Status.ACTIONED)
            AutoModerationFlag.objects.filter(
                target_type=target_type, target_id=target_id, text_hash=FLAG_HASH,
            ).update(status=AutoModerationFlag.Status.ACTIONED)
            for r in sibling_list:
                _notify(r.reporter, "Thanks for your report", "We reviewed it and took action.")
            if sibling_list and sibling_list[0].reported_user_id and target_type != "user":
                _notify(sibling_list[0].reported_user, "Content removed",
                        "We removed your content because it broke our community rules.")
        else:
            siblings.update(status=ContentReport.Status.DISMISSED)
            flags = AutoModerationFlag.objects.filter(
                target_type=target_type, target_id=target_id, text_hash=FLAG_HASH, status="open")
            if flags.exists():
                _unhide_target(target_type, target_id)  # only auto-held content is 'flagged'/hidden by reports
                flags.update(status=AutoModerationFlag.Status.DISMISSED)
            for r in sibling_list:
                _notify(r.reporter, "We reviewed your report", "We didn't find a rule violation this time.")
        done += len(sibling_list)
    return done
