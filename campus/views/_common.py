# campus/views/_common.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

import logging
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from ..permissions import IsCampusAdminOrPrincipal
from django.db.models import Q
from ..visibility import get_visibility
from .. import visibility as vis_mod


logger = logging.getLogger("campus.views")  # same logger name as the old campus/views.py


def _truthy(value):
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


class RosterAPIError(APIException):
    """400 with a stable machine `code` (e.g. section_full) + human `detail`."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        # the project's exception handler (tuitionclass.exceptions) puts this in the envelope's "code"
        self.machine_code = err.code


def get_my_campus_ids(user):
    """
    Every campus a user has *some* legitimate reason to see rows from:
    as active staff, as an enrolled student, or as a linked parent
    (design doc §11 — Parent gets read-only access to a linked
    child's campus data). Centralized here so every viewset's
    campus-visibility check — including `CampusViewSet` itself — stays
    in sync as new membership routes (e.g. parent links) get added.
    """
    return vis_mod.get_my_campus_ids(user)


class CampusMemberScopedMixin:
    """
    Shared "which campus does this row belong to" + "am I a member of
    that campus" plumbing for every viewset below. `campus_field_path`
    is a Django `__`-lookup from the model to its `Campus` FK (e.g.
    `''` for `Campus` itself, `'campus'` for a direct FK, `'school_
    class__campus'` for something scoped through `SchoolClass`).
    """
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_campus_id_for_permission_check(self, request):
        # POST/PATCH bodies carry the FK by whatever field the
        # serializer exposes as top-level `campus` (already true for
        # every model here except the ones scoped through `section`/
        # `school_class`, which override this method below), GET/
        # detail routes fall back to the object's own campus.
        campus_id = request.data.get("campus") or request.query_params.get("campus")
        if campus_id:
            return campus_id
        obj_id = self.kwargs.get("pk")
        if obj_id:
            obj = self.get_queryset().filter(pk=obj_id).first()
            if obj:
                return self._campus_id_from_instance(obj)
        return None

    def _campus_id_from_instance(self, obj):
        target = obj
        for part in self.campus_field_path.split("__"):
            target = getattr(target, part)
        return target.id

    # T4 §E — how rows are narrowed for a NON-staff member (student /
    # parent) of the campus. Active staff always keep campus-wide access.
    #   None      -> no narrowing (campus-wide data, e.g. exam terms)
    #   "section" -> `member_section_path` must be in the visible sections
    #   "student" -> `member_student_path` must be self / a linked child
    #   "none"    -> hidden from students/parents entirely
    # Override `member_scope_q(vis)` for anything custom.
    member_scope = None
    member_section_path = None
    member_student_path = None

    def member_scope_q(self, vis):
        if self.member_scope is None:
            return None
        if self.member_scope == "section":
            return Q(**{f"{self.member_section_path}__in": vis.visible_section_ids})
        if self.member_scope == "student":
            return Q(**{f"{self.member_student_path}__in": vis.visible_student_ids})
        return Q(pk__in=[])

    def filter_queryset_to_my_campuses(self, qs, request):
        vis = get_visibility(request.user, request)
        cp = self.campus_field_path
        campus_id = request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(**{f"{cp}__id": campus_id})
        qs = qs.filter(**{f"{cp}__id__in": vis.member_campus_ids})
        if vis.nonstaff_campus_ids:
            member_q = self.member_scope_q(vis)
            if member_q is not None:
                qs = qs.filter(
                    Q(**{f"{cp}__id__in": vis.staff_campus_ids})
                    | (Q(**{f"{cp}__id__in": vis.nonstaff_campus_ids}) & member_q)
                )
        return qs
