"""
message/views/study_room_review.py

S4 leftover — study room reports ka STAFF review (pehle sirf Django admin me tha).

    GET  /message/staff/study-room-reports/            ?status=open|reviewed|dismissed|all (default open)
                                                       &reason=harassment|inappropriate|spam|unsafe_minor|other
                                                       &page=1 &page_size=20 (max 50)
    GET  /message/staff/study-room-reports/summary/    {"open": n, "unsafe_minor_open": n, "by_reason": {...}}
    POST /message/staff/study-room-reports/<id>/action/
         {"action": "dismiss" | "review" | "remove", "note": "..."}

Actions (sab same (conversation, reported_user) ke saare OPEN reports par lagte hain, audit `reviewed_by/at/note` ke saath):
  * dismiss — false alarm => status `dismissed`.
  * review  — dekh liya (koi room action nahi) => `reviewed`.
  * remove  — user ko chalte session se hata do (room-row `removed`, LiveKit se kick, WS `removed`) + `reviewed`.
              Host / co-host ko nahi hataya jata (409 `cannot_remove_manager`) — unke liye `review` + admin.

Guards: sirf staff (`is_staff` / superuser; baaki 403). Reporter ki identity payload me kabhi nahi aati (sirf count + reason +
free-text), jaise post moderation queue me. Child-safety (`unsafe_minor`) reports sabse upar dikhte hain.
"""

import logging

from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .. import livekit_utils as lk
from .. import study_room_events as sr_events
from .. import study_room_roles as sr_roles
from ..models import StudyRoomParticipant, StudyRoomReport
from ..study_room_session import get_active_session
from ..user_display import get_display_name

logger = logging.getLogger(__name__)

NOTE_MAX = 300
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 50
ACTIONS = ('dismiss', 'review', 'remove')


class StudyRoomReviewThrottle(UserRateThrottle):
    rate = '120/min'
    scope = 'study_room_review'


def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _forbidden():
    return Response({"detail": "Staff only.", "code": "forbidden"}, status=403)


def _int(value, default, lo, hi):
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _serialize(report, open_counts):
    reported = report.reported_user
    return {
        "id": str(report.id),
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "status": report.status,
        "reason": report.reason,
        "details": report.details,
        "conversation_id": str(report.conversation_id),
        "session_room": report.session.room_name if report.session_id else None,
        "reported_user": {"id": str(reported.id), "name": get_display_name(reported)},
        # Reporter kabhi nahi — sirf is user par kitne alag open reports hain.
        "open_reports_on_user": open_counts.get((report.conversation_id, report.reported_user_id), 1),
        "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
        "review_note": report.review_note,
    }


class StudyRoomReportQueueView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomReviewThrottle]

    def get(self, request):
        if not _is_staff(request.user):
            return _forbidden()
        status_q = str(request.query_params.get('status') or 'open').strip().lower()
        reason_q = str(request.query_params.get('reason') or '').strip().lower()
        page = _int(request.query_params.get('page'), 1, 1, 100000)
        page_size = _int(request.query_params.get('page_size'), PAGE_SIZE_DEFAULT, 1, PAGE_SIZE_MAX)

        qs = StudyRoomReport.objects.select_related('reported_user', 'session')
        if status_q != 'all':
            if status_q not in StudyRoomReport.Status.values:
                return Response({"detail": "status galat hai.", "code": "invalid_status"}, status=400)
            qs = qs.filter(status=status_q)
        if reason_q:
            if reason_q not in StudyRoomReport.Reason.values:
                return Response({"detail": "reason galat hai.", "code": "invalid_reason"}, status=400)
            qs = qs.filter(reason=reason_q)

        total = qs.count()
        # Child-safety sabse upar, phir naya pehle.
        rows = list(
            qs.annotate(
                _prio=Case(
                    When(reason=StudyRoomReport.Reason.UNSAFE_MINOR, then=Value(0)),
                    default=Value(1), output_field=IntegerField(),
                ),
            ).order_by('_prio', '-created_at')[(page - 1) * page_size: page * page_size]
        )
        open_counts = {
            (row['conversation_id'], row['reported_user_id']): row['n']
            for row in StudyRoomReport.objects.filter(
                status=StudyRoomReport.Status.OPEN,
                reported_user_id__in={r.reported_user_id for r in rows},
            ).values('conversation_id', 'reported_user_id').annotate(n=Count('reporter_id', distinct=True))
        }
        return Response({
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": [_serialize(r, open_counts) for r in rows],
        })


class StudyRoomReportSummaryView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomReviewThrottle]

    def get(self, request):
        if not _is_staff(request.user):
            return _forbidden()
        open_qs = StudyRoomReport.objects.filter(status=StudyRoomReport.Status.OPEN)
        by_reason = {row['reason']: row['n'] for row in open_qs.values('reason').annotate(n=Count('id'))}
        return Response({
            "open": sum(by_reason.values()),
            "unsafe_minor_open": by_reason.get(StudyRoomReport.Reason.UNSAFE_MINOR, 0),
            "by_reason": by_reason,
        })


def _remove_from_active_session(report):
    """(removed: bool, error_code | None). Host / co-host nahi hatte; active session na ho to no-op (removed=False)."""
    conversation = report.conversation
    session = get_active_session(conversation.id)
    if session is None:
        return False, None
    row, _ = StudyRoomParticipant.objects.get_or_create(
        session=session, user_id=report.reported_user_id,
        defaults={'status': StudyRoomParticipant.Status.ADMITTED},
    )
    role = sr_roles.effective_role(conversation, session, report.reported_user_id, row)
    if sr_roles.is_manager(role):
        return False, 'cannot_remove_manager'
    if row.status != StudyRoomParticipant.Status.REMOVED:
        row.status = StudyRoomParticipant.Status.REMOVED
        row.hand_raised = False
        row.hand_raised_at = None
        row.save(update_fields=['status', 'hand_raised', 'hand_raised_at', 'updated_at'])
    try:
        lk.remove_participant(session.room_name, str(report.reported_user_id))
        sr_events.broadcast(
            conversation.id, sr_events.EVT_REMOVED,
            {"userId": str(report.reported_user_id), "staff": True}, target_user_ids=[report.reported_user_id],
        )
    except Exception:
        logger.exception("study room staff remove: kick/broadcast failed conv=%s", conversation.id)
    return True, None


class StudyRoomReportActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomReviewThrottle]

    def post(self, request, report_id):
        if not _is_staff(request.user):
            return _forbidden()
        data = request.data if hasattr(request.data, 'get') else {}
        action = str(data.get('action') or '').strip().lower()
        if action not in ACTIONS:
            return Response({"detail": "action galat hai.", "code": "invalid_action"}, status=400)
        note = str(data.get('note') or '').strip()[:NOTE_MAX]

        report = (
            StudyRoomReport.objects.select_related('conversation', 'reported_user', 'session')
            .filter(pk=report_id).first()
        )
        if report is None:
            return Response({"detail": "Report not found", "code": "not_found"}, status=404)

        removed = False
        if action == 'remove':
            removed, err = _remove_from_active_session(report)
            if err:
                return Response(
                    {"detail": "Host / co-host ko yahan se nahi hataya ja sakta.", "code": err}, status=409,
                )

        new_status = (
            StudyRoomReport.Status.DISMISSED if action == 'dismiss' else StudyRoomReport.Status.REVIEWED
        )
        now = timezone.now()
        # P-F15: reporters of every report this decision closes (read BEFORE the update) hear how it ended.
        _reporters = list(StudyRoomReport.objects.filter(
            Q(pk=report.pk) | Q(
                conversation_id=report.conversation_id, reported_user_id=report.reported_user_id,
                status=StudyRoomReport.Status.OPEN,
            ),
        ).values_list('reporter_id', flat=True))
        with transaction.atomic():
            # Is (conversation, user) ke saare OPEN reports ek saath (+ ye wala, chahe pehle se band ho).
            updated = StudyRoomReport.objects.filter(
                Q(pk=report.pk) | Q(
                    conversation_id=report.conversation_id, reported_user_id=report.reported_user_id,
                    status=StudyRoomReport.Status.OPEN,
                ),
            ).update(
                status=new_status, reviewed_by=request.user, reviewed_at=now, review_note=note, updated_at=now,
            )
        if report.reason == StudyRoomReport.Reason.UNSAFE_MINOR:
            logger.warning("study room child-safety report reviewed id=%s action=%s staff=%s",
                           report.id, action, request.user.id)
        from user_profile.report_automation import notify_outcome

        if action in ('dismiss', 'remove'):   # 'review' = looked at, no decision yet -> nothing to tell the reporter
            notify_outcome(_reporters, 'dismissed' if action == 'dismiss' else 'actioned',
                           'study_room', report.conversation_id)
        return Response({"id": str(report.id), "status": new_status, "updated": updated, "removed": removed})
