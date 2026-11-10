"""
post/staff_moderation_views.py - the "Review / Remove" shortcut on a feed card, for staff.

    GET  /post/<post_id>/staff-review/   what staff needs on the card: report counts, status, history
    POST /post/<post_id>/staff-action/   {"action": "remove" | "dismiss", "note": "..."}

NOTHING new is built for the decision itself: both actions go through
`user_profile.report_automation.resolve_reports`, the same function the admin "hide / dismiss" actions
use. So the post is hidden with `report_targets.hide()` (moderation_status -> rejected), every open
report on it is closed, the reporters and the owner are notified, and the audit row (ModerationAction)
is written with the staff member - exactly as if it had been done in the admin.

H23 - the FLAGGED QUEUE (same file, same guards):

    GET  /post/staff/moderation-queue/            everything waiting for a moderator, most urgent first
         ?source=all|held|automod|reports|audio|duplicate  &type=all|post|story  &severity=high|medium|low
         &minor=1 (only content of minors)  &page=1  &page_size=20 (max 50)
    GET  /post/staff/moderation-queue/summary/    counts for the badge / tabs
    POST /post/staff/moderation-queue/action/     {"target_type": "post"|"story", "target_id", "action":
                                                   "approve"|"remove"|"dismiss", "note"}

What lands in the queue: posts HELD at publish (moderation_status=flagged: abusive / NSFW text, recognised music,
report threshold), every open AutoModerationFlag (word list, AI, publish-gate flags: audio-copyright suspicion,
duplicate content, spam-looking text) and every open report on a post / story. Order: severity, held first, content
of a minor, number of reports, newest. "approve" releases a held post (followers are told then, not before);
"dismiss" closes reports + flags as a false alarm (and releases a held post); "remove" hides it. Reporters are
never shown (counts and reasons only). Child-safety reports are NOT listed here - they have their own queue.

Guards (the shortcut must never be a way around another workflow):
  * staff only (`is_staff`); everybody else gets 403.
  * child-safety reports have their own queue / case trail (POCSO) - while one is open the shortcut
    refuses both actions and points to that queue.
  * copyright holds / removals have their own dashboard (copyrights app) - refused.
  * reporters are never shown to the reviewer here (only reason, status, date, free text).
"""
import logging
import uuid
from types import SimpleNamespace

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import minor_privacy
from .models import Post, Story

logger = logging.getLogger(__name__)

NOTE_MAX = 300
LATEST_REPORTS = 5
HISTORY_ROWS = 5

# ---- the flagged queue
QUEUE_TYPES = ("post", "story")
QUEUE_SOURCES = ("held", "automod", "reports", "audio", "duplicate")
QUEUE_SEVERITIES = ("high", "medium", "low")
_SEV_RANK = {"high": 0, "medium": 1, "low": 2}
OPEN_STATES = ("open", "reviewed")
QUEUE_MAX_CANDIDATES = 1500   # newest N flagged targets per discovery query (held / flags / reports)
QUEUE_PAGE_DEFAULT = 20
QUEUE_PAGE_MAX = 50
QUEUE_FLAGS_SHOWN = 3
IN_CHUNK = 500                # `__in` lists are cut into pieces of this size (SQLite variable limit)
REPORTS_ESCALATE = 5          # this many open reports on one target = high severity, whatever the reasons
REPORT_SEVERITY = {
    "nudity": "high", "violence": "high", "self_harm": "high", "hate": "high", "harassment": "high",
    "scam": "medium", "impersonation": "medium", "spam": "low", "other": "low",
}
_AUDIO_REASONS = ("audio_copyright", "audio_copyright_match")
_DUPLICATE_REASONS = ("duplicate_content", "duplicate_media")


class StaffModerationThrottle(UserRateThrottle):
    scope = "staff_moderation"
    rate = "60/min"


def _live_post_or_404(post_id):
    post = Post.objects.filter(pk=post_id, is_deleted=False).select_related("user").first()
    if post is None:
        return None
    return post


def _open_reports(post):
    from user_profile.models import ContentReport

    return ContentReport.objects.filter(
        target_type="post", target_id=str(post.pk), status__in=("open", "reviewed"),
    )


def _blocked_reason(post, open_reports):
    """None when staff may act from the card, else a short human reason (shown in the app)."""
    from user_profile.report_targets import COPYRIGHT_STATES

    if post.moderation_status in COPYRIGHT_STATES:
        return "This post is in a copyright case. Use the copyright dashboard."
    if open_reports.filter(reason="child_safety").exists():
        return "This post has a child-safety report. Use the Child Safety queue."
    return None


def _open_flags(target_type, target_id):
    from user_profile.models import AutoModerationFlag

    return AutoModerationFlag.objects.filter(target_type=target_type, target_id=str(target_id), status="open")


def _state(post):
    """(can_remove, can_dismiss, blocked_reason) for the card."""
    reports = _open_reports(post)
    blocked = _blocked_reason(post, reports)
    can_remove = blocked is None and post.moderation_status != "rejected"
    can_dismiss = blocked is None and (reports.exists() or _open_flags("post", post.pk).exists())
    return can_remove, can_dismiss, blocked


def _can_approve(post, blocked):
    """A held post with no open reports (reports are decided with 'dismiss' / 'remove')."""
    return blocked is None and post.moderation_status == "flagged" and not _open_reports(post).exists()


def _close_flags(target_type, target_id, new_status):
    _open_flags(target_type, target_id).update(status=new_status)


def _release(post, actor, note=""):
    """Held post -> visible. Followers / tagged people are told NOW (they were not when it was held)."""
    from django.db import transaction

    from user_profile import moderation_log as mlog
    from user_profile.models import ModerationAction

    changed = Post.objects.filter(pk=post.pk, moderation_status="flagged").update(moderation_status="approved")
    _close_flags("post", post.pk, "dismissed")
    if not changed:
        return False
    mlog.log_action(
        ModerationAction.Action.RESTORE, target_type="post", target_id=post.pk, actor=actor, owner=post.user,
        note=("Released from the publish hold (staff)" + (f": {note}" if note else ""))[:500], owner_visible=True,
    )
    mlog.notify(post.user, "Your post is live", "A moderator checked it and it is now visible to others.",
                data={"target_type": "post", "target_id": str(post.pk), "can_appeal": False})
    if not post.is_scheduled:
        def _announce():
            try:
                from .tagging import notify_post_tags
                from .tasks import notify_followers_new_post

                notify_followers_new_post.delay(post.pk)
                notify_post_tags(Post.objects.get(pk=post.pk))
                if post.post_type == "doubt":
                    from .tasks import notify_teachers_new_doubt

                    notify_teachers_new_doubt.delay(str(post.pk))
            except Exception:
                import logging

                logging.getLogger(__name__).exception("announce after release failed for %s", post.pk)

        transaction.on_commit(_announce)
    return True


class StaffPostReviewAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [StaffModerationThrottle]

    @extend_schema(summary="Staff: review a post from the feed card", responses={200: OpenApiTypes.OBJECT},
                   tags=["Staff moderation"])
    def get(self, request, post_id):
        from user_profile.models import ContentReport, ModerationAction

        post = _live_post_or_404(post_id)
        if post is None:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)

        all_reports = ContentReport.objects.filter(target_type="post", target_id=str(post.pk))
        open_reports = _open_reports(post)
        by_reason = {}
        for row in open_reports.values("reason"):
            by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + 1
        latest = [
            {
                "reason": r.reason,
                "status": r.status,
                "details": (r.details or "")[:200],
                "created_at": r.created_at.isoformat(),
            }
            for r in open_reports.exclude(reason="child_safety").order_by("-created_at")[:LATEST_REPORTS]
        ]
        history = [
            {
                "action": a.action,
                "actor": a.actor_label,
                "note": (a.note or "")[:200],
                "created_at": a.created_at.isoformat(),
            }
            for a in ModerationAction.objects.filter(target_type="post", target_id=str(post.pk))[:HISTORY_ROWS]
        ]
        can_remove, can_dismiss, blocked = _state(post)
        gate_flags = [
            {"reason": f.reason, "severity": f.severity, "source": f.source, "snippet": f.snippet,
             "created_at": f.created_at.isoformat()}
            for f in _open_flags("post", post.pk).order_by("-created_at")[:LATEST_REPORTS]
        ]
        return Response({
            "can_approve": _can_approve(post, blocked),
            "flags": gate_flags,
            "post_id": str(post.pk),
            "post_type": post.post_type,
            "title": post.title or "",
            "caption": (post.content or "")[:300],
            "created_at": post.created_at.isoformat(),
            "moderation_status": post.moderation_status,
            "author": {"id": post.user_id, "username": post.user.username},
            "reports": {
                "open": open_reports.count(),
                "total": all_reports.count(),
                "by_reason": by_reason,
                "latest": latest,
            },
            "history": history,
            "can_remove": can_remove,
            "can_dismiss": can_dismiss,
            "blocked_reason": blocked,
        })


def _run_post_action(actor, post, action, note, via="the feed card"):
    """-> (http_status, body). ONE implementation for the per-post shortcut and the queue action."""
    from user_profile import moderation_log as mlog
    from user_profile.models import ModerationAction
    from user_profile.report_automation import resolve_reports

    can_remove, can_dismiss, blocked = _state(post)
    if blocked:
        return 403, {"detail": blocked, "code": "not_allowed_here"}

    target = SimpleNamespace(target_type="post", target_id=str(post.pk))  # resolve_reports only reads these two
    if action == "approve":
        if post.moderation_status != "flagged":
            return 409, {"detail": "This post is not on hold.", "code": "not_held"}
        if not _can_approve(post, blocked):
            return 409, {"detail": "This post has open reports. Dismiss or remove them instead.",
                         "code": "has_reports"}
        _release(post, actor, note)
        post.refresh_from_db(fields=["moderation_status"])
    elif action == "remove":
        if not can_remove:
            return 409, {"detail": "This post is already removed.", "code": "already_removed"}
        resolve_reports([target], "hide", actor)
        post.refresh_from_db(fields=["moderation_status"])
        if post.moderation_status != "rejected":
            return 409, {"detail": "The post could not be removed. Try the admin.", "code": "not_hidden"}
        _close_flags("post", post.pk, "actioned")
        mlog.log_action(
            ModerationAction.Action.STATUS_CHANGE, target_type="post", target_id=post.pk, actor=actor,
            owner=post.user, note=(f"Removed from {via} (staff shortcut)" + (f": {note}" if note else "")),
        )
    else:
        if not can_dismiss:
            return 400, {"detail": "There are no open reports to dismiss.", "code": "no_reports"}
        open_reports = list(_open_reports(post))
        if open_reports:
            resolve_reports(open_reports, "dismiss", actor)
        _close_flags("post", post.pk, "dismissed")
        post.refresh_from_db(fields=["moderation_status"])
        if post.moderation_status == "flagged":
            _release(post, actor, note)  # still held by the publish gate -> false alarm, release it
        if note:
            mlog.log_action(
                ModerationAction.Action.STATUS_CHANGE, target_type="post", target_id=post.pk,
                actor=actor, owner=post.user, note=f"Dismissed from {via}: {note}",
            )
        post.refresh_from_db(fields=["moderation_status"])

    return 200, {"ok": True, "action": action, "moderation_status": post.moderation_status}


class StaffPostActionAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [StaffModerationThrottle]

    @extend_schema(summary="Staff: remove a post / dismiss its reports from the feed card",
                   request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT}, tags=["Staff moderation"])
    def post(self, request, post_id):
        action = str(request.data.get("action") or "").strip().lower()
        if action not in ("remove", "dismiss", "approve"):
            return Response({"detail": "action must be 'remove', 'dismiss' or 'approve'."},
                            status=status.HTTP_400_BAD_REQUEST)
        note = str(request.data.get("note") or "").strip()[:NOTE_MAX]

        post = _live_post_or_404(post_id)
        if post is None:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)

        code, body = _run_post_action(request.user, post, action, note)
        return Response(body, status=code)


# ==========================================================================
# H23 - the flagged queue
# ==========================================================================
def _chunks(values, size=IN_CHUNK):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _flag_source(flag):
    """Which queue tab an AutoModerationFlag belongs to."""
    if flag.reason in _AUDIO_REASONS:
        return "audio"
    if flag.reason in _DUPLICATE_REASONS:
        return "duplicate"
    if flag.source == "reports":
        return "reports"
    return "automod"


def _discover_candidates():
    """-> {"post": {ids}, "story": {ids}}: newest flagged targets from the three places content can come from.
    Only ids here - counts / flags / reports are read exactly, per chunk of ids, afterwards."""
    from django.db.models import Max

    from user_profile.models import AutoModerationFlag, ContentReport

    cand = {"post": set(), "story": set()}
    held = (Post.objects.filter(moderation_status="flagged", is_deleted=False)
            .order_by("-created_at").values_list("pk", flat=True)[:QUEUE_MAX_CANDIDATES])
    cand["post"].update(str(pk) for pk in held)
    flagged = (AutoModerationFlag.objects.filter(status="open", target_type__in=QUEUE_TYPES)
               .values("target_type", "target_id").annotate(last=Max("created_at")).order_by("-last")
               [:QUEUE_MAX_CANDIDATES])
    for row in flagged:
        cand[row["target_type"]].add(row["target_id"])
    reported = (ContentReport.objects.filter(target_type__in=QUEUE_TYPES, status__in=OPEN_STATES)
                .values("target_type", "target_id").annotate(last=Max("created_at")).order_by("-last")
                [:QUEUE_MAX_CANDIDATES])
    for row in reported:
        cand[row["target_type"]].add(row["target_id"])
    return cand


def _load_live_objects(cand):
    """-> {(type, id): Post | Story}. Gone, already removed, copyright-handled content is dropped here."""
    from user_profile.report_targets import COPYRIGHT_STATES

    objs = {}
    for chunk in _chunks(sorted(cand["post"])):
        rows = (Post.objects.filter(pk__in=chunk, is_deleted=False)
                .exclude(moderation_status__in=tuple(COPYRIGHT_STATES) + ("rejected",)).select_related("user"))
        for p in rows:
            objs[("post", str(p.pk))] = p
    for chunk in _chunks(sorted(cand["story"])):
        rows = Story.objects.filter(pk__in=chunk, is_deleted=False, takedown_hidden=False).select_related("user")
        for st in rows:
            objs[("story", str(st.pk))] = st
    return objs


def _build_queue():
    """Every item waiting for a moderator, most urgent first. Plain dicts (see `_item_payload`)."""
    from django.db.models import Count

    from user_profile.models import AutoModerationFlag, ContentReport

    objs = _load_live_objects(_discover_candidates())
    entries = {}
    for (tt, tid), obj in objs.items():
        held = tt == "post" and obj.moderation_status == "flagged"
        entries[(tt, tid)] = {
            "obj": obj, "type": tt, "id": tid, "held": held,
            "sources": {"held"} if held else set(),
            "flags": [], "flag_count": 0, "flag_rank": 3,
            "by_reason": {}, "open_reports": 0, "total_open_reports": 0, "child_safety": False,
        }

    for tt in QUEUE_TYPES:
        ids = [k[1] for k in entries if k[0] == tt]
        for chunk in _chunks(ids):
            flags = AutoModerationFlag.objects.filter(
                status="open", target_type=tt, target_id__in=chunk).order_by("-created_at")
            for f in flags:
                e = entries[(tt, f.target_id)]
                e["sources"].add(_flag_source(f))
                e["flag_count"] += 1
                e["flag_rank"] = min(e["flag_rank"], _SEV_RANK.get(f.severity, 1))
                if len(e["flags"]) < QUEUE_FLAGS_SHOWN:
                    e["flags"].append({"reason": f.reason, "severity": f.severity, "source": f.source,
                                       "snippet": f.snippet, "created_at": f.created_at.isoformat()})
            reports = (ContentReport.objects.filter(target_type=tt, target_id__in=chunk, status__in=OPEN_STATES)
                       .values("target_id", "reason").annotate(n=Count("id")))
            for row in reports:
                e = entries[(tt, row["target_id"])]
                e["total_open_reports"] += row["n"]
                if row["reason"] == "child_safety":
                    e["child_safety"] = True          # own queue / POCSO trail - never listed here
                elif row["reason"] != "copyright":    # copyright reports belong to the copyright dashboard
                    e["open_reports"] += row["n"]
                    e["sources"].add("reports")
                    e["by_reason"][row["reason"]] = e["by_reason"].get(row["reason"], 0) + row["n"]

    items = [e for e in entries.values() if e["sources"] and not e["child_safety"]]

    minors = minor_privacy.minors_among({e["obj"].user_id for e in items})
    for e in items:
        rank = e["flag_rank"]
        for reason in e["by_reason"]:
            rank = min(rank, _SEV_RANK[REPORT_SEVERITY.get(reason, "low")])
        if e["open_reports"] >= REPORTS_ESCALATE:
            rank = 0
        if e["held"]:
            rank = min(rank, 1)            # a held post is never 'low'
        e["sev_rank"] = min(rank, 2)
        e["severity"] = QUEUE_SEVERITIES[e["sev_rank"]]
        e["is_minor"] = e["obj"].user_id in minors
        e["created_at"] = e["obj"].created_at
    # severity, held first, content of a minor, number of reports, newest
    items.sort(key=lambda e: (e["sev_rank"], 0 if e["held"] else 1, 0 if e["is_minor"] else 1,
                              -e["open_reports"], -e["created_at"].timestamp()))
    return items


def _item_payload(e):
    obj = e["obj"]
    is_post = e["type"] == "post"
    has_flags_or_reports = e["flag_count"] > 0 or e["total_open_reports"] > 0
    return {
        "target_type": e["type"],
        "target_id": e["id"],
        "kind": obj.post_type if is_post else obj.media_type,
        "title": (obj.title or "") if is_post else "",
        "caption": ((obj.content if is_post else obj.caption) or "")[:300],
        "created_at": obj.created_at.isoformat(),
        "author": {"id": obj.user_id, "username": obj.user.username},
        "is_minor": e["is_minor"],
        "held": e["held"],
        "moderation_status": obj.moderation_status if is_post else None,
        "severity": e["severity"],
        "sources": [s for s in QUEUE_SOURCES if s in e["sources"]],
        # reporters are never shown: counts and reasons only
        "reports": {"open": e["open_reports"], "by_reason": e["by_reason"]},
        "flags": e["flags"],
        # same rules as `_state` / `_can_approve` (child-safety and copyright items are not in the queue at all)
        "can_approve": is_post and e["held"] and e["total_open_reports"] == 0,
        "can_remove": True,
        "can_dismiss": has_flags_or_reports,
        "blocked_reason": None,
    }


def _queue_filters(request):
    """-> (filters dict, error Response | None)."""
    q = request.query_params
    source = str(q.get("source") or "all").strip().lower()
    ttype = str(q.get("type") or "all").strip().lower()
    severity = str(q.get("severity") or "").strip().lower()
    if source != "all" and source not in QUEUE_SOURCES:
        return None, Response({"detail": "source must be all, " + ", ".join(QUEUE_SOURCES) + "."}, status=400)
    if ttype != "all" and ttype not in QUEUE_TYPES:
        return None, Response({"detail": "type must be all, post or story."}, status=400)
    if severity and severity not in QUEUE_SEVERITIES:
        return None, Response({"detail": "severity must be high, medium or low."}, status=400)
    minor = str(q.get("minor") or "").strip().lower() in ("1", "true", "yes")
    try:
        page = max(1, int(q.get("page") or 1))
        page_size = min(QUEUE_PAGE_MAX, max(1, int(q.get("page_size") or QUEUE_PAGE_DEFAULT)))
    except (TypeError, ValueError):
        return None, Response({"detail": "page and page_size must be numbers."}, status=400)
    return {"source": source, "type": ttype, "severity": severity, "minor": minor,
            "page": page, "page_size": page_size}, None


class StaffModerationQueueAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [StaffModerationThrottle]

    @extend_schema(summary="Staff: the flagged-content queue (held posts, auto flags, reports)",
                   responses={200: OpenApiTypes.OBJECT}, tags=["Staff moderation"])
    def get(self, request):
        f, error = _queue_filters(request)
        if error is not None:
            return error
        items = _build_queue()
        if f["source"] != "all":
            items = [e for e in items if f["source"] in e["sources"]]
        if f["type"] != "all":
            items = [e for e in items if e["type"] == f["type"]]
        if f["severity"]:
            items = [e for e in items if e["severity"] == f["severity"]]
        if f["minor"]:
            items = [e for e in items if e["is_minor"]]
        count = len(items)
        size = f["page_size"]
        num_pages = max(1, -(-count // size))
        start = (f["page"] - 1) * size
        return Response({
            "count": count,
            "page": f["page"],
            "page_size": size,
            "num_pages": num_pages,
            "results": [_item_payload(e) for e in items[start:start + size]],
        })


class StaffModerationQueueSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [StaffModerationThrottle]

    @extend_schema(summary="Staff: counts for the moderation badge / tabs",
                   responses={200: OpenApiTypes.OBJECT}, tags=["Staff moderation"])
    def get(self, request):
        items = _build_queue()
        by_source = {s: 0 for s in QUEUE_SOURCES}
        by_type = {t: 0 for t in QUEUE_TYPES}
        by_severity = {s: 0 for s in QUEUE_SEVERITIES}
        for e in items:
            for s in e["sources"]:
                by_source[s] += 1            # an item with two reasons counts in both tabs
            by_type[e["type"]] += 1
            by_severity[e["severity"]] += 1
        return Response({
            "total": len(items),             # what the badge shows
            "by_source": by_source,
            "by_type": by_type,
            "by_severity": by_severity,
            "minors": sum(1 for e in items if e["is_minor"]),
            "held": by_source["held"],
        })


def _run_story_action(actor, story, action, note):
    """-> (http_status, body). A story has no hold state, so there is nothing to 'approve'."""
    from user_profile import moderation_log as mlog
    from user_profile.models import ModerationAction
    from user_profile.report_automation import resolve_reports

    sid = str(story.pk)
    reports = _story_open_reports(sid)
    if story.takedown_hidden:
        return 403, {"detail": "This story is in a copyright case. Use the copyright dashboard.",
                     "code": "not_allowed_here"}
    if reports.filter(reason="child_safety").exists():
        return 403, {"detail": "This story has a child-safety report. Use the Child Safety queue.",
                     "code": "not_allowed_here"}

    target = SimpleNamespace(target_type="story", target_id=sid)
    if action == "approve":
        return 409, {"detail": "Stories are not held for review, so there is nothing to approve. "
                               "Use dismiss to close the flags.", "code": "not_held"}
    if action == "remove":
        resolve_reports([target], "hide", actor)
        if not Story.objects.filter(pk=story.pk, is_deleted=True).exists():
            return 409, {"detail": "The story could not be removed. Try the admin.", "code": "not_hidden"}
        _close_flags("story", sid, "actioned")
        mlog.log_action(
            ModerationAction.Action.STATUS_CHANGE, target_type="story", target_id=sid, actor=actor,
            owner=story.user, note="Removed from the moderation queue (staff)" + (f": {note}" if note else ""),
        )
        return 200, {"ok": True, "action": "remove", "removed": True}

    open_reports = list(reports)
    if not open_reports and not _open_flags("story", sid).exists():
        return 400, {"detail": "There are no open reports or flags to dismiss.", "code": "no_reports"}
    if open_reports:
        resolve_reports(open_reports, "dismiss", actor)
    _close_flags("story", sid, "dismissed")
    if note:
        mlog.log_action(
            ModerationAction.Action.STATUS_CHANGE, target_type="story", target_id=sid, actor=actor,
            owner=story.user, note=f"Dismissed from the moderation queue: {note}",
        )
    return 200, {"ok": True, "action": "dismiss", "removed": False}


def _story_open_reports(story_id):
    from user_profile.models import ContentReport

    return ContentReport.objects.filter(target_type="story", target_id=str(story_id), status__in=OPEN_STATES)


class StaffModerationQueueActionAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [StaffModerationThrottle]

    @extend_schema(summary="Staff: approve / dismiss / remove one item of the moderation queue",
                   request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT}, tags=["Staff moderation"])
    def post(self, request):
        target_type = str(request.data.get("target_type") or "").strip().lower()
        if target_type not in QUEUE_TYPES:
            return Response({"detail": "target_type must be 'post' or 'story'."},
                            status=status.HTTP_400_BAD_REQUEST)
        action = str(request.data.get("action") or "").strip().lower()
        if action not in ("approve", "remove", "dismiss"):
            return Response({"detail": "action must be 'approve', 'remove' or 'dismiss'."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            target_id = uuid.UUID(str(request.data.get("target_id") or ""))
        except ValueError:
            return Response({"detail": "target_id is not valid."}, status=status.HTTP_400_BAD_REQUEST)
        note = str(request.data.get("note") or "").strip()[:NOTE_MAX]

        if target_type == "post":
            obj = _live_post_or_404(target_id)
            if obj is None:
                return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
            code, body = _run_post_action(request.user, obj, action, note, via="the moderation queue")
        else:
            obj = Story.objects.filter(pk=target_id, is_deleted=False).select_related("user").first()
            if obj is None:
                return Response({"detail": "Story not found."}, status=status.HTTP_404_NOT_FOUND)
            code, body = _run_story_action(request.user, obj, action, note)
        body = dict(body, target_type=target_type, target_id=str(target_id))
        return Response(body, status=code)
