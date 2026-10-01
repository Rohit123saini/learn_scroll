"""
tuitionclass/attendance_views.py

Endpoints (all under /tuitionclass/):

  ATTENDANCE
    GET   sessions/<id>/attendance/             manager: full roster · student: own row
    PATCH sessions/<id>/attendance/             manager: bulk hand edit (inside the edit window)
    GET   classrooms/<id>/attendance/summary/   manager: every student · student: own row

  OPTIONAL PARENT  (manager = teacher / co-teacher / moderator)
    GET   classrooms/<id>/parents/                          status list + counts
    POST  classrooms/<id>/students/<uid>/parent/invite/     generate invite link (phone optional)
    POST  classrooms/<id>/students/<uid>/parent/skip/       "Skip" — no more prompts

A parent is optional: none of these endpoints is ever consulted by join /
pass purchase / session / attendance / payment.
"""
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.parent_invite_links import (
    build_parent_invite_link,
    generate_or_reuse_parent_code,
    parent_invite_share_text,
)

from .attendance import (
    apply_manual_edits,
    attendance_edit_deadline,
    attendance_edit_open,
    attendance_summary,
    enrolled_student_ids,
    parent_status_map,
    set_parent_state,
)
from .models import ClassSession, Classroom, ClassroomParentState, SessionAttendance
from .views import _can_manage_classroom

User = get_user_model()

_VALID_STATUSES = {c for c, _ in SessionAttendance.Status.choices}


def _person(u):
    return {"id": u.id, "username": u.username, "name": u.get_full_name() or u.username}


def _row(a):
    return {
        "student": a.student_id,
        "status": a.status,
        "source": a.source,
        "joined_at": a.joined_at,
        "left_at": a.left_at,
        "minutes_present": a.minutes_present,
        "marked_by": a.marked_by_id,
        "marked_at": a.marked_at,
        "note": a.note,
    }


# =============================================================================
# ATTENDANCE
# =============================================================================
class SessionAttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def _load(self, request, session_id):
        session = get_object_or_404(ClassSession.objects.select_related("classroom"), pk=session_id)
        return session, session.classroom

    def get(self, request, session_id):
        session, classroom = self._load(request, session_id)
        is_manager = _can_manage_classroom(classroom, request.user)

        rows = SessionAttendance.objects.filter(session=session).select_related("student")
        if not is_manager:
            if not classroom.is_enrolled(request.user):
                return Response({"detail": "Not permitted"}, status=http.HTTP_403_FORBIDDEN)
            rows = rows.filter(student=request.user)

        payload = {
            "session": session.pk,
            "session_status": session.status,
            "min_percent": classroom.attendance_min_percent,
            "edit_open": attendance_edit_open(session),
            "edit_until": attendance_edit_deadline(session),
            "records": [{**_row(a), "student_info": _person(a.student)} for a in rows],
        }
        if is_manager:
            marked = {a.student_id for a in rows}
            missing = enrolled_student_ids(classroom, session) - marked
            payload["unmarked"] = [_person(u) for u in User.objects.filter(pk__in=missing)]
        return Response(payload)

    def patch(self, request, session_id):
        session, classroom = self._load(request, session_id)
        if not _can_manage_classroom(classroom, request.user):
            return Response({"detail": "Not permitted"}, status=http.HTTP_403_FORBIDDEN)
        if session.status != ClassSession.Status.COMPLETED:
            return Response(
                {"detail": "Attendance can be edited once the session is completed."},
                status=http.HTTP_400_BAD_REQUEST,
            )
        if not attendance_edit_open(session):
            return Response(
                {
                    "detail": f"The {classroom.attendance_edit_days}-day edit window for this session has closed.",
                    "edit_until": attendance_edit_deadline(session),
                },
                status=http.HTTP_403_FORBIDDEN,
            )

        records = request.data.get("records")
        if not isinstance(records, list) or not records:
            return Response({"detail": "'records' must be a non-empty list."}, status=http.HTTP_400_BAD_REQUEST)

        clean, errors = [], []
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                errors.append({"index": i, "detail": "must be an object"})
                continue
            status, sid = rec.get("status"), rec.get("student")
            if status not in _VALID_STATUSES:
                errors.append({"index": i, "detail": f"status must be one of {sorted(_VALID_STATUSES)}"})
                continue
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                errors.append({"index": i, "detail": "student must be a user id"})
                continue
            clean.append({"student": sid, "status": status, "note": rec.get("note", "")})
        if errors:
            return Response({"detail": "Invalid records.", "errors": errors}, status=http.HTTP_400_BAD_REQUEST)

        allowed = enrolled_student_ids(classroom) | set(
            SessionAttendance.objects.filter(session=session).values_list("student_id", flat=True)
        )
        stray = sorted({r["student"] for r in clean} - allowed)
        if stray:
            return Response(
                {"detail": "Some students are not enrolled in this classroom.", "students": stray},
                status=http.HTTP_400_BAD_REQUEST,
            )

        written = apply_manual_edits(session, clean, request.user)
        return Response({"updated": len(written), "records": [_row(a) for a in written]})


class ClassroomAttendanceSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if _can_manage_classroom(classroom, request.user):
            ids = None
        elif classroom.is_enrolled(request.user):
            ids = [request.user.id]
        else:
            return Response({"detail": "Not permitted"}, status=http.HTTP_403_FORBIDDEN)

        rows, sessions_total = attendance_summary(classroom, ids)
        people = {u.id: u for u in User.objects.filter(pk__in=[r["student"] for r in rows])}
        for r in rows:
            r["student_info"] = _person(people[r["student"]]) if r["student"] in people else None
        avg = round(sum(r["attendance_percent"] for r in rows) / len(rows), 2) if rows else 0
        return Response({
            "classroom": classroom.pk,
            "sessions_completed": sessions_total,
            "average_percent": avg,
            "students": rows,
        })


# =============================================================================
# OPTIONAL PARENT
# =============================================================================
def _manage_or_error(request, classroom_id):
    classroom = Classroom.objects.filter(pk=classroom_id).first()
    if classroom is None:
        return None, Response({"detail": "Classroom not found"}, status=http.HTTP_404_NOT_FOUND)
    if not _can_manage_classroom(classroom, request.user):
        return None, Response({"detail": "Not permitted"}, status=http.HTTP_403_FORBIDDEN)
    return classroom, None


def _parents_off():
    return Response(
        {"detail": "Parents are switched off for this classroom.", "code": "parents_disabled"},
        status=http.HTTP_400_BAD_REQUEST,
    )


class ClassroomParentsListView(APIView):
    """GET classrooms/<id>/parents/ — one row per enrolled student with
    parent_status = none | skipped | invited | linked."""
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom, err = _manage_or_error(request, classroom_id)
        if err:
            return err
        ids = enrolled_student_ids(classroom)
        statuses = parent_status_map(classroom, ids)
        people = User.objects.filter(pk__in=ids).order_by("first_name", "username")
        counts = {"none": 0, "skipped": 0, "invited": 0, "linked": 0}
        students = []
        for u in people:
            st = statuses[u.id]
            counts[st] += 1
            students.append({**_person(u), "parent_status": st})
        return Response({
            "classroom": classroom.pk,
            "parents_enabled": classroom.parents_enabled,
            "counts": counts,
            "pending_count": counts["none"],
            "students": students,
        })


class ClassroomStudentParentInviteView(APIView):
    """POST classrooms/<id>/students/<uid>/parent/invite/  body: {"phone"?, "label"?, "ttl_days"?}

    Returns the invite link + share text once (reveal-once, same as the
    existing teacher-generated code flow). With a phone number it also
    returns ready-made sms:/WhatsApp URLs — the message itself is sent by
    the teacher's own phone, nothing is sent from the server."""
    permission_classes = [IsAuthenticated]

    def post(self, request, classroom_id, user_id):
        classroom, err = _manage_or_error(request, classroom_id)
        if err:
            return err
        if not classroom.parents_enabled:
            return _parents_off()
        student = User.objects.filter(pk=user_id).first()
        if student is None or user_id not in enrolled_student_ids(classroom):
            return Response(
                {"detail": "This user is not an enrolled student of this classroom."},
                status=http.HTTP_404_NOT_FOUND,
            )

        label = (request.data.get("label") or "").strip()[:50] or "Parent"
        ttl_days = request.data.get("ttl_days")
        code_obj, _created = generate_or_reuse_parent_code(student, label, ttl_days)
        link = build_parent_invite_link(code=code_obj.code, classroom_id=classroom.id)
        share_text = parent_invite_share_text(
            student_name=student.get_full_name() or student.username, link=link,
        )

        current = parent_status_map(classroom, [student.id])[student.id]
        if current != "linked":
            set_parent_state(classroom, student.id, ClassroomParentState.Status.INVITED, request.user)
            current = "invited"

        out = {
            "student_id": student.id,
            "parent_status": current,
            "code": code_obj.code,
            "link": link,
            "share_text": share_text,
            "expires_at": code_obj.expires_at,
        }
        phone = "".join(ch for ch in str(request.data.get("phone") or "") if ch.isdigit() or ch == "+")
        if phone:
            from urllib.parse import quote

            out["sms_url"] = f"sms:{phone}?body={quote(share_text)}"
            out["whatsapp_url"] = f"https://wa.me/{phone.lstrip('+')}?text={quote(share_text)}"
        return Response(out, status=http.HTTP_200_OK)


class ClassroomStudentParentSkipView(APIView):
    """POST classrooms/<id>/students/<uid>/parent/skip/ — the teacher chose
    not to add a parent for this student. Reversible: invite later works."""
    permission_classes = [IsAuthenticated]

    def post(self, request, classroom_id, user_id):
        classroom, err = _manage_or_error(request, classroom_id)
        if err:
            return err
        if not classroom.parents_enabled:
            return _parents_off()
        if user_id not in enrolled_student_ids(classroom):
            return Response(
                {"detail": "This user is not an enrolled student of this classroom."},
                status=http.HTTP_404_NOT_FOUND,
            )
        current = parent_status_map(classroom, [user_id])[user_id]
        if current == "linked":
            return Response({"student_id": user_id, "parent_status": "linked"})
        set_parent_state(classroom, user_id, ClassroomParentState.Status.SKIPPED, request.user)
        return Response({"student_id": user_id, "parent_status": "skipped"})
