# campus/data_deletion.py
"""
DATA DELETE REQUEST — "delete my / my child's data from this campus".

FLOW      pending --approve--> approved --execute--> completed
                  \\--reject--> rejected   (note required)
                  \\--cancel--> cancelled  (by the requester, only while pending)
  * Who may ask: any member of the campus (staff / student / linked parent). A parent asks for ONE linked child
    (`kind=child_data`); everyone else asks for their own (`kind=my_data`).
  * One OPEN (pending / approved) request per person per campus (DB constraint + a friendly check).
  * Who decides: campus admin / principal (`safety.manage`). Nobody decides a request that is about / filed by
    themselves unless they are the campus's only admin / principal.
  * `due_at` = filed date + `CAMPUS_DELETION_SLA_DAYS` (default 30): the promised answer date. A beat task warns
    the admins about overdue open requests (`campus.tasks.warn_overdue_deletion_requests`).

WHAT EXECUTING ERASES  (inside this campus only; personal content, not the school's records)
  student private notes · doubts + replies written by the person (text replaced, attachment file removed) ·
  parent<->teacher chat messages written by the person / the parent (text replaced) · digital ID card ·
  live-class presence rows · PTM booking topic / cancel reason of the parent · staff leave reason + note.
  `wants_account_deletion` (own data only) additionally runs `login.account_deletion.delete_account`, which
  anonymises the whole LearnScroll account (every campus) — refused while a coin withdrawal is in flight.

WHAT IT KEEPS (`safety_rules.RETAINED_RECORDS`; stated to the person before and after)
  attendance, results, fee invoices/payments, assignment submissions, the campus audit log, the consent log.
  The audit log gets one line per decision (`data_request.*`) — without the reason text.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import bridge
from . import safety_rules as rules
from .audit import log_action
from .bridge import NotifTypes
from .models import (
    CampusDataDeletionRequest,
    CampusDoubt,
    CampusDoubtReply,
    CampusLiveParticipant,
    CampusParentLink,
    DigitalIDCard,
    ParentChatMessage,
    ParentChatThread,
    PTMBooking,
    StaffLeave,
    StaffProfile,
    StudentNote,
)
from .permissions import get_campus_role
from .substitutes import display_name

logger = logging.getLogger(__name__)

Status = CampusDataDeletionRequest.Status
OPEN = rules.DeletionStatus.OPEN  # ("pending", "approved")
REMOVED_TEXT = "[removed on request]"
_APPROVER_ROLES = (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD)


class DeletionError(Exception):
    """`code` is a short machine code the app maps to a human message."""

    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def sla_days() -> int:
    return int(getattr(settings, "CAMPUS_DELETION_SLA_DAYS", rules.DEFAULT_DELETION_SLA_DAYS))


# ---------------------------------------------------------------------------
# who decides
# ---------------------------------------------------------------------------
def approvers(campus_id, exclude_user_id=None):
    rows = StaffProfile.objects.filter(campus_id=campus_id, is_active=True, role__in=_APPROVER_ROLES).select_related("user")
    out, seen = [], set()
    for row in rows:
        if row.user_id in seen or row.user_id == exclude_user_id:
            continue
        seen.add(row.user_id)
        out.append(row.user)
    return out


def _involved(req, user) -> bool:
    return user.pk in (req.requester_id, req.subject_id)


def _check_can_decide(req, reviewer):
    if _involved(req, reviewer) and approvers(req.campus_id, exclude_user_id=reviewer.pk):
        raise DeletionError("cannot_decide_own_request", 403)


# ---------------------------------------------------------------------------
# file a request
# ---------------------------------------------------------------------------
def request_deletion(campus, requester, *, kind, reason, student=None, wants_account_deletion=False):
    if kind not in rules.DELETION_KINDS:
        raise DeletionError("invalid_kind")
    try:
        reason = rules.clean_text(reason, minimum=rules.REASON_MIN, maximum=rules.REASON_MAX, field="reason")
    except ValueError as exc:
        raise DeletionError(str(exc))
    if get_campus_role(requester, campus.id) is None:
        raise DeletionError("not_a_member", 403)
    if kind == rules.KIND_CHILD_DATA:
        if student is None:
            raise DeletionError("student_required")
        if not CampusParentLink.objects.filter(campus=campus, parent=requester, student=student).exists():
            raise DeletionError("not_a_linked_parent", 403)
        if wants_account_deletion:
            raise DeletionError("account_deletion_own_data_only")
        subject = student
    else:
        if student is not None and student.pk != requester.pk:
            raise DeletionError("student_not_allowed")
        subject = requester
    if CampusDataDeletionRequest.objects.filter(campus=campus, subject=subject, status__in=OPEN).exists():
        raise DeletionError("request_already_open", 409)
    now = timezone.now()
    try:
        with transaction.atomic():
            req = CampusDataDeletionRequest.objects.create(
                campus=campus, requester=requester, subject=subject, kind=kind, reason=reason,
                wants_account_deletion=bool(wants_account_deletion), status=Status.PENDING,
                due_at=rules.due_at(now, sla_days()),
            )
    except IntegrityError:  # two taps at once: the DB constraint caught what the check above could not
        raise DeletionError("request_already_open", 409)
    log_action(
        campus.id, requester, "data_request.create", target=req,
        summary=f"Data deletion requested ({kind}) for {display_name(subject)}", kind=kind,
        wants_account_deletion=bool(wants_account_deletion),
    )
    _notify_approvers(req)
    return req


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------
def _transition(req, new_status):
    if not rules.can_transition(req.status, new_status):
        raise DeletionError("invalid_transition", 409, f"{req.status} -> {new_status}")


def approve(req, reviewer, note=""):
    with transaction.atomic():
        req = CampusDataDeletionRequest.objects.select_for_update().get(pk=req.pk)
        _transition(req, Status.APPROVED)
        _check_can_decide(req, reviewer)
        req.status = Status.APPROVED
        req.reviewed_by = reviewer
        req.reviewed_at = timezone.now()
        req.review_note = _optional_note(note)
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    log_action(req.campus_id, reviewer, "data_request.approve", target=req,
               summary=f"Data deletion approved for {display_name(req.subject)}")
    _notify_requester(req, "approved")
    return req


def reject(req, reviewer, note):
    try:
        note = rules.clean_text(note, minimum=rules.NOTE_MIN, maximum=rules.NOTE_MAX, field="note")
    except ValueError as exc:
        raise DeletionError(str(exc))
    with transaction.atomic():
        req = CampusDataDeletionRequest.objects.select_for_update().get(pk=req.pk)
        _transition(req, Status.REJECTED)
        _check_can_decide(req, reviewer)
        req.status = Status.REJECTED
        req.reviewed_by = reviewer
        req.reviewed_at = timezone.now()
        req.review_note = note
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    log_action(req.campus_id, reviewer, "data_request.reject", target=req,
               summary=f"Data deletion rejected for {display_name(req.subject)}")
    _notify_requester(req, "rejected")
    return req


def cancel(req, user):
    if req.requester_id != user.pk:
        raise DeletionError("only_requester_can_cancel", 403)
    with transaction.atomic():
        req = CampusDataDeletionRequest.objects.select_for_update().get(pk=req.pk)
        _transition(req, Status.CANCELLED)
        req.status = Status.CANCELLED
        req.save(update_fields=["status", "updated_at"])
    log_action(req.campus_id, user, "data_request.cancel", target=req, summary="Data deletion request withdrawn")
    return req


def _optional_note(note):
    text = " ".join(str(note or "").split())
    return text[: rules.NOTE_MAX]


# ---------------------------------------------------------------------------
# what would be / is erased
# ---------------------------------------------------------------------------
def _people(req):
    """The people whose own words are erased: the subject, and (for a child's data) the asking parent."""
    return [pk for pk in {req.subject_id, req.requester_id} if pk]


def erase_plan(req) -> dict:
    """label -> queryset. The same querysets are counted (preview) and acted on (execute)."""
    campus_id = req.campus_id
    people = _people(req)
    return {
        "student_notes": StudentNote.objects.filter(campus_id=campus_id, author_id__in=people),
        "doubts": CampusDoubt.objects.filter(campus_id=campus_id, author_id__in=people).exclude(text=REMOVED_TEXT),
        "doubt_replies": CampusDoubtReply.objects.filter(doubt__campus_id=campus_id, author_id__in=people)
        .exclude(text=REMOVED_TEXT),
        "parent_chat_messages": ParentChatMessage.objects.filter(thread__campus_id=campus_id, sender_id__in=people)
        .exclude(body=REMOVED_TEXT),
        "digital_id_cards": DigitalIDCard.objects.filter(campus_id=campus_id, user_id__in=people),
        "live_presence": CampusLiveParticipant.objects.filter(session__campus_id=campus_id, user_id__in=people),
        "ptm_bookings": PTMBooking.objects.filter(campus_id=campus_id, parent_id__in=people).filter(
            ~Q(topic="") | ~Q(cancel_reason="")
        ),
        "staff_leave_texts": StaffLeave.objects.filter(campus_id=campus_id, staff__user_id__in=people).filter(
            ~Q(reason="") | ~Q(decision_note="")
        ),
    }


def preview(req) -> dict:
    return {
        "will_erase": {label: qs.count() for label, qs in erase_plan(req).items()},
        "will_keep": [{"key": k, "label": text} for k, text in rules.RETAINED_RECORDS],
        "account_deletion": bool(req.wants_account_deletion),
    }


def execute(req, executor) -> CampusDataDeletionRequest:
    """Erase. Only an APPROVED request, only once. Raises `DeletionError`."""
    from login import account_deletion  # local: login is a different app

    files = []
    with transaction.atomic():
        req = CampusDataDeletionRequest.objects.select_for_update(of=("self",)).select_related("subject", "requester").get(pk=req.pk)
        _transition(req, Status.COMPLETED)
        _check_can_decide(req, executor)
        if req.wants_account_deletion and req.subject is not None:
            blocked = account_deletion.blocking_reasons(req.subject)
            if blocked:
                raise DeletionError("account_deletion_blocked", 409, blocked[0])
        plan = erase_plan(req)
        erased = {}

        erased["student_notes"] = plan["student_notes"].delete()[0]
        erased["doubts"] = _blank_rows(plan["doubts"], "text", files, active_flag=True)
        erased["doubt_replies"] = _blank_rows(plan["doubt_replies"], "text", files, active_flag=True)
        erased["parent_chat_messages"] = plan["parent_chat_messages"].update(body=REMOVED_TEXT)
        ParentChatThread.objects.filter(
            Q(campus_id=req.campus_id) & (Q(parent_id__in=_people(req)) | Q(student_id=req.subject_id))
        ).update(last_message_preview="")
        erased["digital_id_cards"] = plan["digital_id_cards"].delete()[0]
        erased["live_presence"] = plan["live_presence"].delete()[0]
        erased["ptm_bookings"] = plan["ptm_bookings"].update(topic="", cancel_reason="")
        erased["staff_leave_texts"] = plan["staff_leave_texts"].update(reason="", decision_note="")

        account_deleted = False
        if req.wants_account_deletion and req.subject is not None:
            account_deletion.delete_account(req.subject)
            account_deleted = True  # `result` only says whether it was already gone; either way it is deleted now

        req.status = Status.COMPLETED
        req.completed_at = timezone.now()
        req.result = {
            "erased": erased,
            "retained": [{"key": k, "label": text} for k, text in rules.RETAINED_RECORDS],
            "account_deleted": account_deleted,
            "executed_by": executor.pk,
        }
        req.save(update_fields=["status", "completed_at", "result", "updated_at"])
        # files go only after the database part committed (a rolled-back run must not lose them)
        transaction.on_commit(lambda: _delete_files(files))
    log_action(req.campus_id, executor, "data_request.execute", target=req,
               summary=f"Personal data erased for {display_name(req.subject)}", erased=erased,
               account_deleted=account_deleted)
    _notify_requester(req, "completed")
    return req


def _blank_rows(qs, text_field, files, *, active_flag):
    count = 0
    for row in qs:
        if getattr(row, "attachment", None):
            files.append(row.attachment)
        updates = {text_field: REMOVED_TEXT, "attachment": None}
        if active_flag:
            updates["is_active"] = False
        type(row)._base_manager.filter(pk=row.pk).update(**updates)
        count += 1
    return count


def _delete_files(files):
    for f in files:
        try:
            f.delete(save=False)
        except Exception:  # pragma: no cover - a missing file must never fail the request
            logger.exception("could not delete a file while erasing data")


# ---------------------------------------------------------------------------
# queries for the views
# ---------------------------------------------------------------------------
def queryset_for(campus, user, params, *, is_admin):
    qs = CampusDataDeletionRequest.objects.filter(campus=campus).select_related("requester", "subject", "reviewed_by")
    if not is_admin:
        qs = qs.filter(Q(requester=user) | Q(subject=user))
    if params.get("status"):
        qs = qs.filter(status__in=[s for s in str(params["status"]).split(",") if s in Status.values])
    if params.get("overdue") in ("1", "true", "yes"):
        qs = qs.filter(status__in=OPEN, due_at__lt=timezone.now())
    text = rules.clean_search(params.get("q"))
    if text and is_admin:
        qs = qs.filter(
            Q(subject__username__icontains=text) | Q(subject__first_name__icontains=text)
            | Q(subject__last_name__icontains=text) | Q(requester__username__icontains=text)
            | Q(requester__first_name__icontains=text) | Q(requester__last_name__icontains=text)
        )
    return qs


def open_counts(campus) -> dict:
    now = timezone.now()
    open_qs = CampusDataDeletionRequest.objects.filter(campus=campus, status__in=OPEN)
    return {
        "pending": open_qs.filter(status=Status.PENDING).count(),
        "approved_not_executed": open_qs.filter(status=Status.APPROVED).count(),
        "overdue": open_qs.filter(due_at__lt=now).count(),
    }


def row_dict(req: CampusDataDeletionRequest, *, for_admin: bool, now=None) -> dict:
    now = now or timezone.now()

    def person(u):
        return {"id": u.pk, "username": u.username, "name": display_name(u)} if u else None

    data = {
        "id": str(req.id),
        "campus": str(req.campus_id),
        "kind": req.kind,
        "status": req.status,
        "reason": req.reason,
        "wants_account_deletion": req.wants_account_deletion,
        "due_at": req.due_at,
        "is_overdue": rules.is_overdue(req.status, req.due_at, now),
        "created_at": req.created_at,
        "reviewed_at": req.reviewed_at,
        "review_note": req.review_note,
        "completed_at": req.completed_at,
        "requester": person(req.requester),
        "subject": person(req.subject),
        "reviewed_by": person(req.reviewed_by),
        "result": req.result if req.status == Status.COMPLETED else {},
        "will_keep": [{"key": k, "label": text} for k, text in rules.RETAINED_RECORDS],
    }
    return data


def overdue_open_requests(now=None):
    now = now or timezone.now()
    return CampusDataDeletionRequest.objects.filter(status__in=OPEN, due_at__lt=now)


# ---------------------------------------------------------------------------
# notifications (bell row + push; no reason / note text on the lock screen)
# ---------------------------------------------------------------------------
def _payload(req, event):
    return {
        "type": NotifTypes.CAMPUS_SAFETY,
        "event": event,
        "request_id": str(req.pk),
        "campus_id": str(req.campus_id),
        "status": req.status,
    }


def _notify_approvers(req):
    users = approvers(req.campus_id, exclude_user_id=req.requester_id)
    bridge.notify(
        users=users, notif_type=NotifTypes.CAMPUS_SAFETY,
        title="Data deletion request",
        body=f"{display_name(req.requester)} asked to delete personal data. Answer by {req.due_at:%d %b %Y}.",
        data=_payload(req, "requested"), push=True,
    )


def _notify_requester(req, event):
    if req.requester is None:
        return
    text = {
        "approved": ("Your data request was approved", "The campus will now erase the data. You will get a message when it is done."),
        "rejected": ("Your data request was not approved", "Open the request to read the reason."),
        "completed": ("Your data was erased", "Open the request to see what was erased and what the school must keep."),
    }[event]
    bridge.notify(users=[req.requester], notif_type=NotifTypes.CAMPUS_SAFETY, title=text[0], body=text[1],
                  data=_payload(req, event), push=True)


def notify_overdue(req):
    bridge.notify(
        users=approvers(req.campus_id), notif_type=NotifTypes.CAMPUS_SAFETY,
        title="Data deletion request is overdue",
        body=f"A request from {display_name(req.requester)} passed its due date ({req.due_at:%d %b %Y}).",
        data=_payload(req, "overdue"), push=True,
    )
