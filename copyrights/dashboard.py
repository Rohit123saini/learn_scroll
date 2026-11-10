"""
copyrights/dashboard.py - the MODERATOR DASHBOARD (queue, SLA clock, search, one-click decisions).

Lives INSIDE the hardened admin site (copyrights/admin.py -> CopyrightClaimAdmin.get_urls), so it gets the same
login, TOTP 2FA, lockout and staff-password policy as /admin/ - there is no second door to protect:

    /admin/copyrights/copyrightclaim/dashboard/            the page
    /admin/copyrights/copyrightclaim/dashboard/api/...     JSON used by the page (session + CSRF, staff only)

What it adds over the plain Django admin list
  * QUEUE      one list sorted by "who is closest to missing the deadline", with filters (status, SLA state,
               priority, level, target type, claimant tier, source, mine / unassigned / escalated) and paging.
  * SLA        every open claim gets a deadline and a state, computed from the SAME settings the Celery
               escalation uses (claimant.sla_hours), so the screen and the automation never disagree:
                 review          created_at + 48 h (12 h for fast-track publishers)      ok / soon / breached
                 claimant_reply  info_requested_at + COPYRIGHT_NEEDS_INFO_DAYS           waiting (claim auto-closes)
                 counter_wait    counter-notice restore_after                            waiting (content auto-restores)
               "soon" = less than COPYRIGHT_DASH_SOON_HOURS (6) left.
  * SEARCH     every word must match somewhere: claimant name / e-mail / organisation, content owner username,
               target id, caption, work description, claim id (the 8-character short id works).
  * DETAIL     one panel: claim, claimant + their history, live content preview, owner standing, counter-notice,
               evidence, related claims, full audit timeline.
  * ACTIONS    take / ask for info / reject / reject as bad faith / uphold (+- strike) / restore / legal hold /
               staff note - single or bulk. They call copyrights.services (and retention) - the same functions the
               admin and the API use - so L1 / L2 / L3 rules, notifications and the audit log are identical.
  * STATS      open, overdue, due soon, unassigned, mine, needs L2, ...; last-30-day decision time and
               "decided within SLA" %; workload per reviewer; court orders and appeals waiting.

Levels: L1 only sees claims with required_level = 1 (exactly like the admin list); L2 / L3 see everything. Buttons are
hidden when the level is too low, and the service layer refuses the call anyway.

Settings (optional): COPYRIGHT_DASH_SOON_HOURS (6), COPYRIGHT_DASH_PAGE_SIZE (25, max 100).
"""

import json
import re
import statistics
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.paginator import InvalidPage, Paginator
from django.db.models import (
    Case, CharField, Count, DateTimeField, ExpressionWrapper, F, IntegerField, Q, Value, When,
)
from django.db.models.functions import Cast
from django.http import HttpResponseForbidden, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import claimant as claimant_rules, permissions as perms, retention, services, targets
from .models import (
    CopyrightAppeal, CopyrightClaim as C, CopyrightStanding, LegalOrder, TargetHold,
)

REVIEW = (C.Status.SUBMITTED, C.Status.UNDER_REVIEW)
OPEN = C.OPEN_STATUSES
TIERS = ("new", "standard", "trusted", "publisher")
BULK_ACTIONS = ("take", "info", "reject", "reject_bad_faith", "uphold", "uphold_no_strike")   # never erase / hold in bulk


def _cfg(name, default):
    return getattr(settings, name, default)


def _soon_hours():
    return int(_cfg("COPYRIGHT_DASH_SOON_HOURS", 6))


# --------------------------------------------------------------------------
# queryset with the SLA deadline computed in the database (so we can sort / filter on it)
# --------------------------------------------------------------------------
def _annotated():
    dt = DateTimeField()
    std = timedelta(hours=claimant_rules.sla_hours("standard"))
    fast = timedelta(hours=claimant_rules.sla_hours("publisher"))
    wait = timedelta(days=int(_cfg("COPYRIGHT_NEEDS_INFO_DAYS", 14)))
    due = Case(
        When(status__in=REVIEW, claimant_tier="publisher",
             then=ExpressionWrapper(F("created_at") + fast, output_field=dt)),
        When(status__in=REVIEW, then=ExpressionWrapper(F("created_at") + std, output_field=dt)),
        When(status=C.Status.NEEDS_INFO, info_requested_at__isnull=False,
             then=ExpressionWrapper(F("info_requested_at") + wait, output_field=dt)),
        When(status=C.Status.COUNTERED, then=F("counter_notice__restore_after")),
        default=Value(None, output_field=dt), output_field=dt,
    )
    prio = Case(
        When(priority=C.Priority.URGENT, then=Value(3)), When(priority=C.Priority.HIGH, then=Value(2)),
        default=Value(1), output_field=IntegerField(),
    )
    return C.objects.select_related("reviewer", "content_owner").annotate(
        sla_due_at=due, prio_rank=prio, id_str=Cast("id", CharField()))


def _scope(level):
    qs = _annotated()
    return qs.filter(required_level=1) if level < perms.L2 else qs      # L1 only sees the simple claims


def sla_info(claim, now):
    due = getattr(claim, "sla_due_at", None)
    if due is None:
        return None
    if claim.status in REVIEW:
        kind = "review"
    elif claim.status == C.Status.NEEDS_INFO:
        kind = "claimant_reply"
    else:
        kind = "counter_wait"
    left = int((due - now).total_seconds())
    if kind == "review":
        state = "breached" if left < 0 else "soon" if left <= _soon_hours() * 3600 else "ok"
    else:
        state = "waiting"
    return {"kind": kind, "due_at": due.isoformat(), "seconds_left": left, "state": state,
            "escalated": claim.escalated_at is not None}


# --------------------------------------------------------------------------
# filters / search / sort
# --------------------------------------------------------------------------
def _search(qs, text):
    for term in (text or "").split()[:5]:
        cond = (Q(claimant_name__icontains=term) | Q(claimant_email__icontains=term)
                | Q(organisation__icontains=term) | Q(target_id__icontains=term)
                | Q(content_owner__username__icontains=term) | Q(content_snapshot__icontains=term)
                | Q(work_description__icontains=term) | Q(id_str__icontains=term))
        if re.fullmatch(r"[0-9a-fA-F-]{6,36}", term):
            cond |= Q(id_str__icontains=term.replace("-", ""))      # sqlite stores UUIDs without dashes
        qs = qs.filter(cond)
    return qs


def _filter(qs, g, user, now):
    status = g.get("status") or "open"
    if status == "open":
        qs = qs.filter(status__in=OPEN)
    elif status == "review":
        qs = qs.filter(status__in=REVIEW)
    elif status == "closed":
        qs = qs.exclude(status__in=OPEN)
    elif status in C.Status.values:
        qs = qs.filter(status=status)
    sla = g.get("sla")
    if sla == "breached":
        qs = qs.filter(status__in=REVIEW, sla_due_at__lt=now)
    elif sla == "soon":
        qs = qs.filter(status__in=REVIEW, sla_due_at__gte=now, sla_due_at__lt=now + timedelta(hours=_soon_hours()))
    if g.get("priority") in C.Priority.values:
        qs = qs.filter(priority=g["priority"])
    if (g.get("level") or "").isdigit():
        qs = qs.filter(required_level=int(g["level"]))
    if g.get("type") in C.TargetType.values:
        qs = qs.filter(target_type=g["type"])
    if g.get("tier") in TIERS:
        qs = qs.filter(claimant_tier=g["tier"])
    if g.get("source") in C.Source.values:
        qs = qs.filter(source=g["source"])
    if g.get("mine") == "1":
        qs = qs.filter(reviewer=user)
    if g.get("unassigned") == "1":
        qs = qs.filter(reviewer__isnull=True, status__in=REVIEW)
    if g.get("escalated") == "1":
        qs = qs.exclude(escalated_at=None)
    return _search(qs, g.get("q"))


def _sort(qs, g):
    sort = g.get("sort") or ("sla" if (g.get("status") or "open") in ("open", "review") else "newest")
    if sort == "oldest":
        return qs.order_by("created_at")
    if sort == "priority":
        return qs.order_by("-prio_rank", F("sla_due_at").asc(nulls_last=True), "created_at")
    if sort == "newest":
        return qs.order_by("-created_at")
    return qs.order_by(F("sla_due_at").asc(nulls_last=True), "-prio_rank", "created_at")   # most urgent first


# --------------------------------------------------------------------------
# JSON shapes
# --------------------------------------------------------------------------
def _row(c, now):
    return {
        "id": str(c.id), "short_id": str(c.id)[:8], "status": c.status, "status_label": c.get_status_display(),
        "priority": c.priority, "level": c.required_level, "tier": c.claimant_tier, "source": c.source,
        "target_type": c.target_type, "target_label": c.get_target_type_display(), "target_id": c.target_id,
        "snapshot": c.content_snapshot[:120], "claimant_name": c.claimant_name, "organisation": c.organisation,
        "owner": getattr(c.content_owner, "username", "") or "", "reviewer": getattr(c.reviewer, "username", "") or "",
        "created_at": c.created_at.isoformat(), "bad_faith": c.bad_faith, "held": c.retention_hold,
        "sla": sla_info(c, now),
    }


def _safe_url(f):
    try:
        return f.url
    except Exception:
        return ""


def _actions_for(c, user, level):
    """The buttons this person may press on this claim (mirrors the checks in services.py)."""
    st, out = c.status, []

    def add(key, label, *, note=False, danger=False, confirm=""):
        out.append({"key": key, "label": label, "note": note, "danger": danger, "confirm": confirm})

    if st in REVIEW and (st == C.Status.SUBMITTED or c.reviewer_id != user.pk):
        add("take", "Take for review")
    if st in REVIEW:
        add("info", "Ask claimant for info", note=True)
    if st in OPEN and level >= (perms.L2 if c.required_level >= 2 else perms.L1):
        add("reject", "Reject", note=True, danger=True, confirm="Reject this claim? The content comes back.")
    if st in OPEN and level >= perms.L2:
        add("reject_bad_faith", "Reject - bad faith", note=True, danger=True,
            confirm="Mark this notice as FALSE / bad faith? It counts against the claimant.")
    if st in (C.Status.SUBMITTED, C.Status.UNDER_REVIEW, C.Status.COUNTERED) and level >= perms.L2:
        add("uphold", "Uphold + strike", note=False, danger=True,
            confirm="Uphold: the content is REMOVED and the owner gets a strike.")
        add("uphold_no_strike", "Uphold, no strike", danger=True, confirm="Uphold: the content is REMOVED (no strike).")
    if st not in (C.Status.REJECTED, C.Status.WITHDRAWN, C.Status.RESTORED) \
            and level >= (perms.L3 if st == C.Status.UPHELD else perms.L2):
        add("restore", "Restore content", note=True, danger=True,
            confirm="Restore the content" + (" and REVERSE the upheld decision?" if st == C.Status.UPHELD else "?"))
    if level >= perms.L3 and not c.pii_erased_at:
        add("release" if c.retention_hold else "hold",
            "Release legal hold" if c.retention_hold else "Legal hold (keep claimant data)", note=True)
        if st not in OPEN and not c.retention_hold:
            add("erase", "Erase claimant data now", note=True, danger=True,
                confirm="Erase the claimant's personal details and proof files NOW? This cannot be undone.")
    add("note", "Add staff note", note=True)
    return out


def _evidence(rows):
    return [{"id": e.pk, "kind": e.kind, "name": e.original_name or e.kind, "kb": e.size_bytes // 1024,
             "url": _safe_url(e.file), "at": e.created_at.isoformat()} for e in rows]


def _preview(c):
    spec = targets.spec_for(c.target_type)
    if spec is None:
        return {"exists": False, "items": [], "owner": "", "hidden": ""}
    try:
        live = spec.load_any(c.target_id)
    except Exception:
        live = None
    if live is None:
        return {"exists": False, "items": [], "owner": "", "hidden": ""}
    hold = TargetHold.objects.filter(target_type=c.target_type, target_id=c.target_id).first()
    try:
        items = [{"kind": k, "value": str(v)} for k, v in spec.preview(live, hold) if v]
    except Exception:
        items = []
    owner = spec.owner(live)
    return {"exists": True, "items": items, "owner": getattr(owner, "username", "") or "",
            "hidden": hold.level if hold else "", "quarantined": bool(hold and hold.media_quarantined)}


def _detail(c, user, level, now):
    d = _row(c, now)
    cn = getattr(c, "counter_notice", None)      # reverse one-to-one: missing row -> AttributeError -> None
    td = getattr(c, "takedown", None)
    ev = list(c.evidence.all())
    owner = c.content_owner
    standing = CopyrightStanding.objects.filter(user=owner).first() if owner else None
    hist = None
    if c.claimant_id:
        mine = C.objects.filter(claimant_id=c.claimant_id)
        by = dict(mine.values_list("status").annotate(n=Count("pk")))
        hist = {"total": sum(by.values()), "upheld": by.get("upheld", 0), "rejected": by.get("rejected", 0),
                "withdrawn": by.get("withdrawn", 0),
                "bad_faith_365d": mine.filter(bad_faith=True, created_at__gte=now - timedelta(days=365)).count()}
    d.update({
        "claimant_email": c.claimant_email, "is_rights_owner": c.is_rights_owner, "signature": c.signature,
        "good_faith": c.good_faith_statement, "accuracy": c.accuracy_statement,
        "work_description": c.work_description, "original_work_url": c.original_work_url,
        "infringement_details": c.infringement_details, "needs_info_message": c.needs_info_message,
        "decision_note": c.decision_note, "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "escalated_at": c.escalated_at.isoformat() if c.escalated_at else None,
        "pii_erased": c.pii_erased_at is not None, "claimant_history": hist,
        "takedown": {"state": td.state, "auto": td.auto_applied} if td else None,
        "counter_notice": ({"status": cn.status, "explanation": cn.explanation, "filed_at": cn.filed_at.isoformat(),
                            "restore_after": cn.restore_after.isoformat(), "court_note": cn.court_action_note}
                           if cn else None),
        "proof": _evidence([e for e in ev if e.kind != "content_snapshot"]),
        "snapshot_files": _evidence([e for e in ev if e.kind == "content_snapshot"]),
        "preview": _preview(c),
        "owner_info": ({"username": owner.username, "level": standing.level if standing else "good",
                        "active_strikes": services.active_strike_count(owner),
                        "other_claims": C.objects.filter(content_owner=owner).exclude(pk=c.pk).count()}
                       if owner else None),
        "related": [{"id": str(r.id), "short_id": str(r.id)[:8], "status": r.status}
                    for r in C.objects.filter(target_type=c.target_type, target_id=c.target_id).exclude(pk=c.pk)[:10]],
        "duplicate_of": str(c.duplicate_of_id) if c.duplicate_of_id else None,
        "timeline": [{"at": a.created_at.isoformat(), "actor": a.actor_label, "action": a.action, "note": a.note}
                     for a in c.audit.order_by("-created_at")[:100]],
        "actions": _actions_for(c, user, level),
    })
    return d


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------
def _stats(qs, user, level, now):
    soon = now + timedelta(hours=_soon_hours())
    a = qs.aggregate(
        open=Count("pk", filter=Q(status__in=OPEN)),
        awaiting=Count("pk", filter=Q(status__in=REVIEW)),
        breached=Count("pk", filter=Q(status__in=REVIEW, sla_due_at__lt=now)),
        soon=Count("pk", filter=Q(status__in=REVIEW, sla_due_at__gte=now, sla_due_at__lt=soon)),
        unassigned=Count("pk", filter=Q(status__in=REVIEW, reviewer__isnull=True)),
        mine=Count("pk", filter=Q(status__in=OPEN, reviewer=user)),
        escalated=Count("pk", filter=Q(status__in=OPEN, escalated_at__isnull=False)),
        needs_info=Count("pk", filter=Q(status=C.Status.NEEDS_INFO)),
        countered=Count("pk", filter=Q(status=C.Status.COUNTERED)),
        needs_l2=Count("pk", filter=Q(status__in=OPEN, required_level__gte=2)),
    )
    # last 30 days: how fast are we, and how often inside the deadline
    rows = list(qs.filter(decided_at__gte=now - timedelta(days=30),
                          status__in=(C.Status.UPHELD, C.Status.REJECTED, C.Status.RESTORED))
                .values_list("created_at", "decided_at", "claimant_tier")[:2000])
    hours = [(d - c0).total_seconds() / 3600 for c0, d, _t in rows]
    within = sum(1 for (c0, d, t), h in zip(rows, hours)
                 if h <= claimant_rules.sla_hours("publisher" if t == "publisher" else "standard"))
    a["perf"] = {"decided_30d": len(rows),
                 "median_hours": round(statistics.median(hours), 1) if hours else None,
                 "within_sla_pct": round(100 * within / len(rows)) if rows else None}
    load = (qs.filter(status__in=OPEN).values("reviewer__username").annotate(n=Count("pk")).order_by("-n")[:8])
    a["workload"] = [{"who": r["reviewer__username"] or "unassigned", "n": r["n"]} for r in load]
    if level >= perms.L2:
        orders = list(LegalOrder.objects.filter(status__in=("received", "in_review")).order_by("due_at")[:5])
        a["orders"] = [{"id": o.pk, "reference": o.reference, "authority": o.authority, "status": o.status,
                        "due_at": o.due_at.isoformat(), "seconds_left": int((o.due_at - now).total_seconds()),
                        "url": _admin_url("admin:copyrights_legalorder_change", o.pk)} for o in orders]
        a["appeals_open"] = CopyrightAppeal.objects.filter(status=CopyrightAppeal.Status.OPEN).count()
        a["appeals_url"] = _admin_url("admin:copyrights_copyrightappeal_changelist") + "?status__exact=open"
    return a


def _admin_url(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
def _note_action(claim, user, note):
    note = (note or "").strip()
    if not note:
        raise services.ClaimError("incomplete", "Write the note.")
    services._log(claim, "staff_note", actor=user, note=note)
    return claim


def run_action(claim, user, action, note="", **_):
    if action == "take":
        return services.take_for_review(claim, user)
    if action == "info":
        return services.request_info(claim, user, note)
    if action == "reject":
        return services.reject_claim(claim, user, note)
    if action == "reject_bad_faith":
        return services.reject_claim(claim, user, note, bad_faith=True)
    if action == "uphold":
        return services.uphold_claim(claim, user, note)
    if action == "uphold_no_strike":
        return services.uphold_claim(claim, user, note, issue_strike=False)
    if action == "restore":
        return services.restore_claim(claim, user, note)
    if action == "erase":
        retention.request_erasure(claim, user, note)
        return claim
    if action in ("hold", "release"):
        return retention.set_retention_hold(claim, user, action == "hold", note)
    if action == "note":
        return _note_action(claim, user, note)
    raise services.ClaimError("bad_action", "Unknown action.")


# --------------------------------------------------------------------------
# views (wrapped by admin_site.admin_view in urls(): login + 2FA + CSRF)
# --------------------------------------------------------------------------
def _fail(code, message, status=400):
    return JsonResponse({"status": False, "code": code, "message": message}, status=status)


def _staff_view(fn):
    def wrapper(request, *args, **kwargs):
        level = perms.level_for(request.user)
        if level < perms.L1:
            return _fail("forbidden", "You need copyright staff access.", 403)
        request.mod_level = level
        return fn(request, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


@require_GET
def page(request):
    level = perms.level_for(request.user)
    if level < perms.L1:
        return HttpResponseForbidden("You need copyright staff access.")
    cfg = {
        "base": reverse("admin:copyrights_dashboard"), "level": level, "user": request.user.get_username(),
        "csrf": get_token(request), "soon_hours": _soon_hours(),
        "statuses": [list(x) for x in C.Status.choices], "priorities": [list(x) for x in C.Priority.choices],
        "types": [list(x) for x in C.TargetType.choices], "sources": [list(x) for x in C.Source.choices],
        "tiers": list(TIERS), "claim_admin": _admin_url("admin:copyrights_copyrightclaim_changelist"),
        "admin_home": _admin_url("admin:index"),
    }
    return render(request, "copyrights/dashboard.html", {"cfg": cfg})


@require_GET
@_staff_view
def api_queue(request):
    now, g = timezone.now(), request.GET
    base = _scope(request.mod_level)
    qs = _sort(_filter(base, g, request.user, now), g)
    try:
        size = int(g.get("size") or _cfg("COPYRIGHT_DASH_PAGE_SIZE", 25))
    except (TypeError, ValueError):
        size = 25
    size = min(max(size, 5), 100)
    pager = Paginator(qs, size)
    try:
        pg = pager.page(g.get("page") or 1)
    except InvalidPage:
        pg = pager.page(1)
    data = {"results": [_row(c, now) for c in pg.object_list], "count": pager.count, "page": pg.number,
            "pages": pager.num_pages, "size": size, "now": now.isoformat()}
    if g.get("stats", "1") == "1":
        data["stats"] = _stats(base, request.user, request.mod_level, now)
    return JsonResponse({"status": True, **data})


def _get_claim(request, claim_id):
    return _scope(request.mod_level).filter(pk=claim_id).first()


@require_GET
@_staff_view
def api_detail(request, claim_id):
    c = _get_claim(request, claim_id)
    if c is None:
        return _fail("not_found", "Claim not found (or it needs a higher level).", 404)
    return JsonResponse({"status": True, "data": _detail(c, request.user, request.mod_level, timezone.now())})


@require_POST
@_staff_view
def api_action(request, claim_id):
    c = _get_claim(request, claim_id)
    if c is None:
        return _fail("not_found", "Claim not found (or it needs a higher level).", 404)
    body = _body(request)
    try:
        run_action(c, request.user, body.get("action", ""), note=body.get("note", ""))
    except services.ClaimError as exc:
        return _fail(exc.code, exc.message)
    except PermissionDenied as exc:
        return _fail("forbidden", str(exc), 403)
    c = _get_claim(request, claim_id)
    return JsonResponse({"status": True, "data": _detail(c, request.user, request.mod_level, timezone.now())})


@require_POST
@_staff_view
def api_bulk(request):
    body = _body(request)
    action, note = body.get("action", ""), body.get("note", "")
    ids = []
    for i in (body.get("ids") or [])[:50]:
        try:
            ids.append(str(uuid.UUID(str(i))))        # a malformed id must not turn the whole call into a 500
        except ValueError:
            continue
    if action not in BULK_ACTIONS:
        return _fail("bad_action", "This action can't be run on many claims at once.")
    done, errors = 0, []
    for c in _scope(request.mod_level).filter(pk__in=ids):
        try:
            run_action(c, request.user, action, note=note)
            done += 1
        except services.ClaimError as exc:
            errors.append(f"{str(c.id)[:8]}: {exc.message}")
        except PermissionDenied as exc:
            errors.append(f"{str(c.id)[:8]}: {exc}")
    return JsonResponse({"status": True, "done": done, "errors": errors[:8], "skipped": len(ids) - done - len(errors)})


def urls(model_admin):
    """URL patterns for CopyrightClaimAdmin.get_urls() - wrapped so the admin login / 2FA / CSRF apply."""
    w = model_admin.admin_site.admin_view
    return [
        path("dashboard/", w(page), name="copyrights_dashboard"),
        path("dashboard/api/queue/", w(api_queue), name="copyrights_dashboard_queue"),
        path("dashboard/api/bulk/", w(api_bulk), name="copyrights_dashboard_bulk"),
        path("dashboard/api/claim/<uuid:claim_id>/", w(api_detail), name="copyrights_dashboard_claim"),
        path("dashboard/api/claim/<uuid:claim_id>/action/", w(api_action), name="copyrights_dashboard_action"),
    ]
