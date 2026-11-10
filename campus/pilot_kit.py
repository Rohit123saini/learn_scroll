# campus/pilot_kit.py
"""
T2.8 (Loop 6) — "campus pilot kit": a live onboarding checklist for a school
that is being piloted on LearnScroll.

  GET /campus/<campus_id>/pilot-kit/        admin / principal-HOD only

Returns the ordered setup steps with `done` / `count` / `target`, an overall
percentage and the first unfinished step, so the app can render a progress
card on the setup screen and the pilot manager can see at a glance what a
school is still missing. The human-readable walkthrough that goes with it
lives in `campus/CAMPUS_PILOT_KIT.md`.

Read-only and cheap: ~8 COUNT queries, no writes.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AcademicSession,
    Campus,
    CampusInviteCode,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
)
from .permissions import is_campus_admin_or_principal


def _target_students() -> int:
    return int(getattr(settings, "CAMPUS_PILOT_TARGET_STUDENTS", 10))


def build_pilot_kit(campus: Campus) -> dict:
    """Plain function (no request) so it is unit-testable and reusable."""
    cid = campus.pk
    sections = Section.objects.filter(school_class__campus_id=cid).count()
    subjects = Subject.objects.filter(campus_id=cid).count()
    staff = StaffProfile.objects.filter(campus_id=cid, is_active=True).count()
    students = (
        StudentEnrollment.objects.filter(
            section__school_class__campus_id=cid, status=StudentEnrollment.Status.ACTIVE
        ).values("student_id").distinct().count()
    )
    codes = CampusInviteCode.objects.filter(campus_id=cid)
    codes_total = codes.count()
    joined_via_code = sum(codes.values_list("uses_count", flat=True))
    has_current_session = AcademicSession.objects.filter(campus_id=cid, is_current=True).exists()
    target = _target_students()

    steps = [
        {
            "key": "verified", "title": "Campus verified",
            "hint": "A LearnScroll admin approves the campus before students and staff can be invited.",
            "done": campus.verification_status == Campus.VerificationStatus.APPROVED,
            "count": 1 if campus.verification_status == Campus.VerificationStatus.APPROVED else 0, "target": 1,
        },
        {
            "key": "session", "title": "Academic session set as current",
            "hint": "Add this year's session (e.g. 2026-27) and mark it current.",
            "done": has_current_session, "count": 1 if has_current_session else 0, "target": 1,
        },
        {
            "key": "sections", "title": "Classes & sections created",
            "hint": "Create each class and its sections (10-A, 10-B...). Students join a section.",
            "done": sections > 0, "count": sections, "target": 1,
        },
        {
            "key": "subjects", "title": "Subjects added",
            "hint": "Add the subjects you teach so timetables, attendance and results work.",
            "done": subjects > 0, "count": subjects, "target": 1,
        },
        {
            "key": "staff", "title": "Teachers invited",
            "hint": "Invite at least one teacher besides yourself.",
            "done": staff >= 2, "count": staff, "target": 2,
        },
        {
            "key": "invite_code", "title": "Invite code shared",
            "hint": "Open a section -> Invite classmates, then paste the code in the class WhatsApp group.",
            "done": codes_total > 0, "count": codes_total, "target": 1,
        },
        {
            "key": "students", "title": f"{target} students on board",
            "hint": "The pilot is working once a real class is using it daily.",
            "done": students >= target, "count": students, "target": target,
        },
    ]
    done = sum(1 for s in steps if s["done"])
    next_step = next((s["key"] for s in steps if not s["done"]), None)
    return {
        "campus_id": str(cid),
        "campus_name": campus.name,
        "steps": steps,
        "completed": done,
        "total": len(steps),
        "percent": int(round(100 * done / len(steps))),
        "next_step": next_step,
        "students_joined_via_code": joined_via_code,
        "class_reminders_enabled": bool(getattr(campus, "class_reminders_enabled", True)),
    }


class CampusPilotKitView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, campus_id):
        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        if not is_campus_admin_or_principal(request.user, campus.id):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        return Response(build_pilot_kit(campus))
