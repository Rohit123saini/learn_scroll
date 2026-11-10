"""
core/capabilities.py - S12: "what may this person see in Settings" (role-aware settings).

    GET core/me/capabilities/

One answer the app uses to decide which role sections to draw in Settings, so the UI shows only what the person is allowed
to use. It is a UI HINT ONLY: every endpoint behind a section checks permission again (staff endpoints check is_staff,
campus endpoints check the StaffProfile, the child-limit endpoint checks the parent link and the child's age).

Roles (a person can hold several; "student" is always there as the base):
    student        everyone
    teacher        owns a tuition classroom, OR is an active campus class / subject teacher / moderator, OR picked "teacher" in
                   onboarding (onboarding grants nothing - it only personalises, see login.User.onboarding_role)
    parent         has at least one campus parent link (a real link is needed to manage a child, so the onboarding "parent" pick
                   alone does not add this role)
    campus_staff   an active StaffProfile of any kind at an approved campus (incl. non-teaching)
    campus_admin   an active StaffProfile with role admin / principal_hod at an approved campus
    moderator      platform staff (`is_staff` and active) - the moderation queue and study-room reports

Response:
    {
      "version": 1,
      "roles": ["student", "teacher", ...],      # stable order, see ROLE_ORDER
      "primary_role": "campus_admin",             # first of PRIMARY_ORDER the person holds (display only)
      "is_minor": false,
      "campus": {"admin_id": "<uuid>|null", "staff_id": ..., "teacher_id": ..., "student_id": ..., "parent_id": ...},
      "can": {"teacher_live_defaults": bool, "child_limits": bool, "moderation_queue": bool,
              "study_room_reports": bool, "campus_settings": bool},
      "sections": ["teacher_tools", "parent_controls", "staff_tools", "campus_admin"],   # what Settings should add
      "partial": false                            # true = a lookup failed and its role was left OUT (fail closed)
    }

Every lookup runs on its own, so one broken table never hides the others; a failed lookup only removes the role it decides.
"""
from __future__ import annotations

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .me_context import build_context

logger = logging.getLogger(__name__)

CAPABILITIES_VERSION = 1
ROLE_ORDER = ("student", "teacher", "parent", "campus_staff", "campus_admin", "moderator")
PRIMARY_ORDER = ("moderator", "campus_admin", "teacher", "campus_staff", "parent", "student")

_ADMIN_ROLES = frozenset({"admin", "principal_hod"})
_TEACHING_ROLES = frozenset({"class_teacher", "subject_teacher", "moderator"})
MAX_CAMPUSES = 5  # approved campuses looked at (same spirit as home_bootstrap.MAX_CAMPUSES_FOR_ROLE)


class CapabilitiesThrottle(UserRateThrottle):
    scope = "me_capabilities"
    rate = "60/min"


def _campus_staff(user) -> dict:
    """{campus_id(str): role} for the user's ACTIVE staff rows at APPROVED campuses (lowest pk first, capped)."""
    from campus.models import Campus, StaffProfile

    rows = (
        StaffProfile.objects.filter(
            user=user,
            is_active=True,
            campus__verification_status=Campus.VerificationStatus.APPROVED,
        )
        .order_by("campus_id")
        .values_list("campus_id", "role")[:MAX_CAMPUSES]
    )
    return {str(cid): role for cid, role in rows}


def _first_campus(staff: dict, wanted) -> str | None:
    for cid, role in staff.items():
        if role in wanted:
            return cid
    return None


def _student_campus(user) -> str | None:
    from campus.models import Campus, StudentEnrollment

    return (
        StudentEnrollment.objects.filter(
            student=user,
            status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus__verification_status=Campus.VerificationStatus.APPROVED,
        )
        .order_by("section__school_class__campus_id")
        .values_list("section__school_class__campus_id", flat=True)
        .first()
    )


def _parent_campus(user) -> str | None:
    from campus.models import CampusParentLink

    return CampusParentLink.objects.filter(parent=user).order_by("-created_at").values_list("campus_id", flat=True).first()


def _teaches_classroom(user) -> bool:
    from tuitionclass.models import Classroom

    return Classroom.objects.filter(teacher=user, is_deleted=False).exists()


def build_capabilities(user) -> dict:
    ctx = build_context(user)
    partial = False

    def safe(label, fn, default=None):
        nonlocal partial
        try:
            return fn()
        except Exception:
            partial = True
            logger.exception("capabilities lookup failed: %s", label)
            return default

    staff = safe("campus_staff", lambda: _campus_staff(user), {})
    student_campus = safe("campus_student", lambda: _student_campus(user))
    parent_campus = safe("campus_parent", lambda: _parent_campus(user))
    teaches = safe("teaches_classroom", lambda: _teaches_classroom(user), False)

    admin_id = _first_campus(staff, _ADMIN_ROLES)
    teacher_id = _first_campus(staff, _TEACHING_ROLES)
    hint = ctx.get("onboarding_role") or ""

    is_moderator = bool(ctx.get("can_moderate"))
    is_admin = admin_id is not None
    is_teacher = bool(teacher_id is not None or teaches or hint == "teacher")
    is_parent = parent_campus is not None
    is_campus_staff = bool(staff)

    held = {
        "student": True,
        "teacher": is_teacher,
        "parent": is_parent,
        "campus_staff": is_campus_staff,
        "campus_admin": is_admin,
        "moderator": is_moderator,
    }
    roles = [r for r in ROLE_ORDER if held[r]]
    primary = next((r for r in PRIMARY_ORDER if held[r]), "student")

    can = {
        "teacher_live_defaults": is_teacher,
        "child_limits": is_parent,
        "moderation_queue": is_moderator,
        "study_room_reports": is_moderator,
        "campus_settings": is_admin,
    }
    sections = []
    if can["teacher_live_defaults"]:
        sections.append("teacher_tools")
    if can["child_limits"]:
        sections.append("parent_controls")
    if can["moderation_queue"] or can["study_room_reports"]:
        sections.append("staff_tools")
    if can["campus_settings"]:
        sections.append("campus_admin")

    return {
        "version": CAPABILITIES_VERSION,
        "roles": roles,
        "primary_role": primary,
        "is_minor": bool(ctx.get("is_minor")),
        "campus": {
            "admin_id": admin_id,
            "staff_id": next(iter(staff), None),
            "teacher_id": teacher_id,
            "student_id": str(student_campus) if student_campus is not None else None,
            "parent_id": str(parent_campus) if parent_campus is not None else None,
        },
        "can": can,
        "sections": sections,
        "partial": partial,
    }


class MeCapabilitiesView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CapabilitiesThrottle]

    def get(self, request):
        return Response(build_capabilities(request.user))
