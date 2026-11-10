"""
user_profile/report_automation.py - what happens AFTER a ContentReport is filed.

  on_report_created(report)   called by services.file_report (never raises)
      1. keeps Post.reported_count exact (distinct, reliable reporters)
      2. child_safety reports go to user_profile/child_safety.py (instant hold, urgent case)
      3. AUTO-HOLD: enough distinct, trusted reporters -> post goes to 'flagged'
         (invisible in every feed), comment gets is_hidden=True, and a HIGH/MEDIUM
         AutoModerationFlag lands in the moderator queue. Everything else (story,
         account, DM, class chat, live class, profile photo) is only flagged.
      4. the OWNER is told (notice + appeal button) and the hold is written to the
         audit trail (ModerationAction).
      5. BRIGADING (audit item 19) - see analyse_reports().

  resolve_reports(reports, action, actor)   used by the Django-admin actions
      'hide' (content removed, all sibling reports actioned) or 'dismiss'
      (false alarm; auto-held content comes back). Both are audited.

THRESHOLDS (settings.py, all env-tunable; tune them with `manage.py report_stats`):
  REPORT_HOLD_THRESHOLD_SEVERE   3   distinct severe-reason reporters
  REPORT_HOLD_THRESHOLD_ANY      8   distinct reporters, any reason
  REPORT_UNRELIABLE_DISMISSED   10   reporter whose reports were all dismissed -> ignored
BRIGADING knobs:
  REPORT_NEW_ACCOUNT_DAYS        3   reporter accounts younger than this ...
  REPORT_NEW_ACCOUNT_WEIGHT    0.5   ... count only this much
  REPORT_PERSISTENT_DISMISSED    2   reporter who already had this many reports against
                                     the SAME person dismissed counts 0 against them again
  REPORT_BURST_MIN              10   this many reports ...
  REPORT_BURST_MINUTES          10   ... inside this window = a burst
  REPORT_NEW_SHARE             0.7   share of new accounts among reporters that looks coordinated
  REPORT_BRIGADE_MODE      "review"  "review" = suspected brigade is NOT auto-held, it goes to
                                     a human (high-severity flag); "off" = ignore these signals
Copyright reports never auto-hold: they go through the formal notice in the copyrights app.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from . import moderation_log as mlog
from . import report_targets as rt

logger = logging.getLogger(__name__)

SEVERE_REASONS = ("nudity", "violence", "self_harm", "hate", "harassment", "child_safety")
FLAG_HASH = "user-reports"
BRIGADE_HASH = "user-reports-brigade"


def _cfg(name, default):
    return getattr(settings, name, default)


# --------------------------------------------------------------------------
# who counts
# --------------------------------------------------------------------------
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


def _persistent_complainants(reporter_ids, owner_id, target_type, target_id):
    """Reporters who already had >= N reports against THIS PERSON dismissed (other
    content): the same people coming back again and again to get one account hidden."""
    from .models import ContentReport

    if not reporter_ids or not owner_id:
        return set()
    since = timezone.now() - timedelta(days=90)
    rows = (
        ContentReport.objects.filter(reporter_id__in=reporter_ids, reported_user_id=owner_id, status="dismissed",
                                     created_at__gte=since)
        .exclude(target_type=target_type, target_id=str(target_id))
        .values("reporter_id").annotate(n=Count("id"))
    )
    limit = _cfg("REPORT_PERSISTENT_DISMISSED", 2)
    return {r["reporter_id"] for r in rows if r["n"] >= limit}


def reporter_is_reliable(reporter) -> bool:
    return getattr(reporter, "id", reporter) not in _unreliable_reporters([getattr(reporter, "id", reporter)])


def analyse_reports(target_type, target_id):
    """Everything the hold decision (and `report_stats`) needs, with the reasons.

    -> {"count": int, "severe": int,            weighted, rounded DOWN
        "raw_count": int,                       distinct reporters, no discounts
        "brigading": bool, "signals": [str]}

    Weights: 1.0 normal | REPORT_NEW_ACCOUNT_WEIGHT for a brand-new account |
             0 for an 'unreliable' reporter or a persistent complainant.
    Signals (only when REPORT_BRIGADE_MODE == "review"):
      burst              >= REPORT_BURST_MIN reports inside REPORT_BURST_MINUTES
      mostly_new         >= REPORT_NEW_SHARE of >=4 reporters are brand-new accounts
    A suspected brigade is never auto-held (child safety excepted) - a person decides.
    Honest limit: this catches lazy / mass-created-account brigades, not a patient group
    of established accounts. Review `report_stats` output regularly.
    """
    from .models import ContentReport

    rows = list(
        ContentReport.objects.filter(target_type=target_type, target_id=str(target_id))
        .exclude(status="dismissed").exclude(reason="copyright")
        .values_list("reporter_id", "reason", "created_at", "reporter__date_joined", "reported_user_id")
    )
    now = timezone.now()
    ids = {r[0] for r in rows}
    owner_id = next((r[4] for r in rows if r[4]), None)
    bad = _unreliable_reporters(ids) | _persistent_complainants(ids, owner_id, target_type, target_id)
    new_cut = now - timedelta(days=_cfg("REPORT_NEW_ACCOUNT_DAYS", 3))
    new_w = float(_cfg("REPORT_NEW_ACCOUNT_WEIGHT", 0.5))

    weight, is_new = {}, set()
    for rid, _reason, _at, joined, _o in rows:
        if rid in bad:
            weight[rid] = 0.0
        elif joined and joined >= new_cut:
            weight[rid] = new_w
            is_new.add(rid)
        else:
            weight[rid] = 1.0
    all_w = sum(weight.values())
    severe_ids = {rid for rid, reason, *_ in rows if reason in SEVERE_REASONS}
    severe_w = sum(w for rid, w in weight.items() if rid in severe_ids)

    signals = []
    if _cfg("REPORT_BRIGADE_MODE", "review") == "review" and rows:
        times = sorted(r[2] for r in rows)
        need, win = int(_cfg("REPORT_BURST_MIN", 10)), timedelta(minutes=_cfg("REPORT_BURST_MINUTES", 10))
        if len(times) >= need and any(times[i + need - 1] - times[i] <= win for i in range(len(times) - need + 1)):
            signals.append("burst")
        if len(ids) >= 4 and len(is_new) / len(ids) >= float(_cfg("REPORT_NEW_SHARE", 0.7)):
            signals.append("mostly_new")

    return {
        "count": int(all_w + 1e-9), "severe": int(severe_w + 1e-9), "raw_count": len(ids),
        "brigading": bool(signals), "signals": signals,
    }


def count_reports(target_type, target_id):
    """(all_count, severe_count) of DISTINCT trusted reporters, dismissed reports ignored."""
    a = analyse_reports(target_type, target_id)
    return a["count"], a["severe"]


# --------------------------------------------------------------------------
def _flag(report, count, severe, *, brigade=False, note=""):
    from .models import AutoModerationFlag

    return AutoModerationFlag.objects.get_or_create(
        target_type=report.target_type, target_id=str(report.target_id),
        text_hash=BRIGADE_HASH if brigade else FLAG_HASH,
        defaults={
            "user": report.reported_user,
            "reason": "report_brigading" if brigade else "user_reports",
            "source": AutoModerationFlag.Source.REPORTS,
            # a suspected brigade is high: somebody must look at it soon, in BOTH directions
            "severity": "high" if (severe or brigade) else "medium",
            "snippet": (note or f"{count} reporters ({severe} severe)")[:200],
        },
    )


def on_report_created(report):
    """Never raises. Returns what it did, e.g. {"count": 3, "held": True}."""
    from .models import ContentReport, ModerationAction

    out = {"count": 0, "held": False, "brigading": False}
    try:
        if report.reason == "copyright":
            return out
        if report.reason == "child_safety":
            from . import child_safety

            res = child_safety.handle_report(report)
            out["held"] = res["held"]
            out["child_safety"] = True
            return out

        a = analyse_reports(report.target_type, report.target_id)
        count, severe = a["count"], a["severe"]
        out["count"] = count
        if report.target_type == "post":
            from post.models import Post

            Post.objects.filter(pk=report.target_id).update(reported_count=count)

        hold = severe >= _cfg("REPORT_HOLD_THRESHOLD_SEVERE", 3) or count >= _cfg("REPORT_HOLD_THRESHOLD_ANY", 8)
        if not hold:
            return out

        if a["brigading"]:
            # Threshold reached, but it looks coordinated -> a person decides, nothing is hidden.
            out["brigading"] = True
            ContentReport.objects.filter(
                target_type=report.target_type, target_id=str(report.target_id)).update(brigading_suspected=True)
            _, created = _flag(report, count, severe, brigade=True,
                               note=f"possible brigading ({', '.join(a['signals'])}): {a['raw_count']} reporters")
            if created:
                mlog.log_action(
                    ModerationAction.Action.BRIGADING_REVIEW, target_type=report.target_type,
                    target_id=report.target_id, owner=report.reported_user, reason=report.reason, count=count,
                    note=",".join(a["signals"]),
                )
            return out

        # What can be held automatically: only posts and comments. Others: flag for a person.
        if report.target_type in rt.AUTO_HOLD_TARGETS:
            changed, _ = rt.hide(report.target_type, report.target_id, mode="hold")
            out["held"] = changed
        _flag(report, count, severe)
        from .report_sla import tighten  # P-F15: a flagged / held target is reviewed sooner

        tighten(report.target_type, report.target_id)

        if out["held"]:
            row = mlog.log_action(
                ModerationAction.Action.AUTO_HOLD, target_type=report.target_type, target_id=report.target_id,
                owner=report.reported_user, reason=report.reason, count=count,
                note=f"{count} reporters, {severe} severe", owner_visible=True, can_appeal=True,
            )
            mlog.tell_owner(
                report.reported_user, row, f"Your {mlog.what(report.target_type)} is hidden for now",
                f"Several people reported it, so we hid it while we review it. You didn't lose it. "
                f"If you think this is a mistake, you can appeal in Settings > Notices.",
            )
    except Exception:
        logger.exception("report automation failed (report=%s)", getattr(report, "pk", None))
    return out


# --------------------------------------------------------------------------
# moderator actions on reports (Django admin)
# --------------------------------------------------------------------------
# H15: what the reporter is told once their report is decided (type `report_outcome`, data carries
# {outcome, target_type, target_id} so the app can route / word it).
OUTCOME_COPY = {
    "actioned": ("Thanks for your report", "We reviewed it and took action."),
    "dismissed": ("We reviewed your report", "We didn't find a rule violation this time."),
    "reversed": ("Update on your report", "After an appeal, the earlier decision on your report was reversed."),
}


def _notify(user, title, message, *, outcome="", target_type="", target_id=""):
    try:
        from core.services import create_notification

        create_notification(
            user, "report_outcome", title, message,
            data={"kind": "report_outcome", "outcome": outcome, "target_type": target_type, "target_id": str(target_id)},
        )
    except Exception:
        logger.exception("report outcome notification failed")


def notify_reporters(reports, outcome):
    """Tell each reporter ONCE per target how their report ended (outcome: actioned | dismissed | reversed).

    `reports` = ContentReport rows (or a queryset) captured BEFORE their status was changed. Only the rows that
    were still undecided (open / reviewed) get a message, except outcome 'reversed' (an appeal undid a decision, so
    the earlier 'actioned' reporters are told). Used by resolve_reports, child_safety, appeals and the admin edit, so
    a report can never produce two messages for the same decision. Never raises."""
    try:
        title, message = OUTCOME_COPY[outcome]
        seen = set()
        for r in reports:
            if outcome != "reversed" and r.status not in ("open", "reviewed"):
                continue
            if outcome == "reversed" and r.status != "actioned":
                continue
            key = (r.reporter_id, r.target_type, str(r.target_id))
            if not r.reporter_id or key in seen:
                continue
            seen.add(key)
            _notify(r.reporter, title, message, outcome=outcome, target_type=r.target_type, target_id=r.target_id)
    except Exception:
        logger.exception("notify_reporters failed")


def notify_outcome(reporters, outcome, target_type, target_id):
    """P-F15: the same "how did my report end" message for the report systems that are NOT ContentReport rows
    (study-room reports, classroom chat reports, live-class reports, sound reports). `reporters` = users (or user ids);
    each is told once. outcome: actioned | dismissed. Never raises."""
    from types import SimpleNamespace

    try:
        rows, seen = [], set()
        for r in reporters:
            uid = getattr(r, "pk", r)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            user = r if hasattr(r, "pk") else None
            if user is None:
                from django.contrib.auth import get_user_model

                user = get_user_model().objects.filter(pk=uid).first()
            if user is not None:
                rows.append(SimpleNamespace(status="open", reporter_id=uid, reporter=user,
                                            target_type=target_type, target_id=str(target_id)))
        notify_reporters(rows, outcome)
        return len(rows)
    except Exception:
        logger.exception("notify_outcome failed")
        return 0


def _hide_target(target_type, target_id):
    """Staff 'hide'. Returns the extra info hide() gives (photo quarantine path)."""
    changed, extra = rt.hide(target_type, target_id, mode="staff")
    return extra


def _unhide_target(target_type, target_id):
    return rt.restore(target_type, target_id, held_only=True)


def resolve_reports(reports, action, actor=None):
    """action: 'hide' | 'dismiss'. Resolves every OPEN/REVIEWED report on the same targets too.
    Every call is written to the audit trail (ModerationAction) with the staff member."""
    from .models import AutoModerationFlag, ContentReport, ModerationAction

    A = ModerationAction.Action
    targets = {(r.target_type, r.target_id) for r in reports}
    done = 0
    for target_type, target_id in targets:
        siblings = ContentReport.objects.filter(
            target_type=target_type, target_id=target_id, status__in=("open", "reviewed"))
        sibling_list = list(siblings.select_related("reporter", "reported_user"))
        owner = sibling_list[0].reported_user if sibling_list else rt.owner_of(target_type, target_id)
        reasons = sorted({r.reason for r in sibling_list})
        flags = AutoModerationFlag.objects.filter(
            target_type=target_type, target_id=target_id, text_hash__in=(FLAG_HASH, BRIGADE_HASH))
        if action == "hide":
            quarantine = _hide_target(target_type, target_id)
            siblings.update(status=ContentReport.Status.ACTIONED)
            flags.update(status=AutoModerationFlag.Status.ACTIONED)
            notify_reporters(sibling_list, "actioned")  # sibling_list holds the pre-update (open / reviewed) rows
            row = mlog.log_action(
                A.PHOTO_REMOVED if target_type == "profile_photo" else A.HIDE,
                target_type=target_type, target_id=target_id, actor=actor, owner=owner,
                reason=",".join(reasons)[:20], count=len(sibling_list),
                note=(f"quarantine:{quarantine}" if quarantine else f"{len(sibling_list)} report(s) actioned"),
                owner_visible=target_type != "user", can_appeal=target_type != "user",  # P-LEFT: stories too (review request, see appeals.decide_appeal)
            )
            if owner is not None and target_type != "user":
                mlog.tell_owner(owner, row, f"We removed your {mlog.what(target_type)}",
                                "It broke our community rules."
                                + ((" A story can't be put back once it is removed, but if you think we got this wrong you can ask for a "
                                    "review in Settings > Notices and we will clear the decision from your record.")
                                   if target_type == "story" and row is not None and row.can_appeal else
                                   " If you think we got this wrong, you can appeal in Settings > Notices."
                                   if row is not None and row.can_appeal else ""))
        else:
            siblings.update(status=ContentReport.Status.DISMISSED)
            open_flags = flags.filter(status="open")
            restored = False
            if open_flags.exists():
                restored = _unhide_target(target_type, target_id)  # only auto-held content is 'flagged'/hidden by reports
                open_flags.update(status=AutoModerationFlag.Status.DISMISSED)
            notify_reporters(sibling_list, "dismissed")
            mlog.log_action(
                A.DISMISS, target_type=target_type, target_id=target_id, actor=actor, owner=owner,
                reason=",".join(reasons)[:20], count=len(sibling_list),
                note="reports dismissed" + ("; auto-held content restored" if restored else ""),
            )
            if restored and owner is not None:
                mlog.notify(owner, f"Your {mlog.what(target_type)} is visible again",
                            "We reviewed the reports and found no rule violation.",
                            data={"target_type": target_type, "target_id": target_id, "can_appeal": False})
        done += len(sibling_list)
    return done
