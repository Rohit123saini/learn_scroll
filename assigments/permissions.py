# assigments/permissions.py
"""
§7 permissions, translated into DRF permission classes. Note that the
*actual* enforcement for "campus/liveclass assigmentss can't be created
through the public endpoint" is `assigmentsViewSet.perform_create()`
hard-wiring `source=personal` regardless of payload (see views.py) — the
permission class below is defence-in-depth so that if a future refactor
ever removes that hard-coding, this still blocks the request rather than
silently reopening a creation path §7 explicitly says should not exist.
"""
from rest_framework import permissions

from .models import assigmentsSource


class IsPersonalSourceOnly(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if view.action != "create":
            return True
        requested_source = request.data.get("source", assigmentsSource.PERSONAL)
        return requested_source == assigmentsSource.PERSONAL


class IsSubmissionStudent(permissions.BasePermission):
    """§7 — 'student sirf apni submission create/patch kar sakta hai'."""

    def has_object_permission(self, request, view, obj) -> bool:
        return obj.student_id == request.user.id


class IsassigmentsStaffOrOwner(permissions.BasePermission):
    """§7 grading actions — 'requester assigments.posted_by hai YA
    is_staff'. `obj` here is an `assigmentsSubmission`; the check is
    against its parent `assigments`, not the submission itself, since
    it's the assigments's poster (teacher/staff) who has grading rights,
    not the submission's own student."""

    def has_object_permission(self, request, view, obj) -> bool:
        assigments = obj.assigments
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or assigments.posted_by_id == request.user.id)
        )