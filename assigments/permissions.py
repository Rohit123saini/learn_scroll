# assignment/permissions.py
"""
§7 permissions, translated into DRF permission classes. Note that the
*actual* enforcement for "campus/liveclass assignments can't be created
through the public endpoint" is `AssignmentViewSet.perform_create()`
hard-wiring `source=personal` regardless of payload (see views.py) — the
permission class below is defence-in-depth so that if a future refactor
ever removes that hard-coding, this still blocks the request rather than
silently reopening a creation path §7 explicitly says should not exist.
"""
from rest_framework import permissions

from .models import AssignmentSource


class IsPersonalSourceOnly(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if view.action != "create":
            return True
        requested_source = request.data.get("source", AssignmentSource.PERSONAL)
        return requested_source == AssignmentSource.PERSONAL


class IsSubmissionStudent(permissions.BasePermission):
    """§7 — 'student sirf apni submission create/patch kar sakta hai'."""

    def has_object_permission(self, request, view, obj) -> bool:
        return obj.student_id == request.user.id


class IsAssignmentStaffOrOwner(permissions.BasePermission):
    """§7 grading actions — 'requester Assignment.posted_by hai YA
    is_staff'. `obj` here is an `AssignmentSubmission`; the check is
    against its parent `assignment`, not the submission itself, since
    it's the assignment's poster (teacher/staff) who has grading rights,
    not the submission's own student."""

    def has_object_permission(self, request, view, obj) -> bool:
        assignment = obj.assignment
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or assignment.posted_by_id == request.user.id)
        )