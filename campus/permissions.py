# campus/permissions.py
"""
Role checks built on `StaffProfile.role` (design doc §11 — role-based
access summary). Kept as plain helper functions (not just DRF
`BasePermission` classes) so the same check can be reused inside a
serializer's `validate()` as well as a view's `get_permissions()`.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Campus, CampusParentLink, ClassTeacherAssignment, StaffProfile, SubjectTeacherAssignment


def get_staff_profile(user, campus_id):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return StaffProfile.objects.filter(campus_id=campus_id, user=user, is_active=True).first()


def is_campus_admin(user, campus_id):
    """
    G-5 (resolved, keep merged): Admin and Principal/HOD are granted the
    same authority everywhere in this module — see
    `is_campus_admin_or_principal` below, which both this and every
    campus-scoped permission check ultimately route through. There is
    no `is_campus_principal`/split helper by design.

    IMPORTANT — this is a *campus-scoped* role, not a platform one.
    `StaffProfile.Role.ADMIN` is a per-`campus_id` DB row a campus's own
    creator can self-assign (see `CampusViewSet`'s docstring: creating a
    campus must always leave the creator able to manage it). It has NO
    relationship to Django's `user.is_staff`/`user.is_superuser` and
    must never be used as a stand-in for them. Being ADMIN on campus A
    grants zero authority on campus B, and grants zero platform-level
    authority anywhere — that boundary is `is_platform_admin` below,
    which deliberately does not consult `StaffProfile` at all. Do not
    "simplify" `is_platform_admin`/`IsPlatformAdmin` to accept a campus
    ADMIN role instead of real `is_staff`/`is_superuser` — that would
    let a campus's self-appointed admin approve/reject their own
    campus's verification, defeating the whole point of `is_platform_admin`.
    """
    profile = get_staff_profile(user, campus_id)
    return bool(profile and profile.role == StaffProfile.Role.ADMIN)


def is_campus_admin_or_principal(user, campus_id):
    """See `is_campus_admin`'s docstring — Admin and Principal/HOD are
    intentionally treated as one bucket (G-5), scoped to this one
    `campus_id` only, and carry no platform-level meaning."""
    profile = get_staff_profile(user, campus_id)
    return bool(profile and profile.role in (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD))


def is_any_active_staff(user, campus_id):
    return get_staff_profile(user, campus_id) is not None


def is_class_teacher_of_section(user, section_id):
    return ClassTeacherAssignment.objects.filter(
        section_id=section_id, staff__user=user, staff__is_active=True
    ).exists()


def is_linked_parent_of_student(user, student_id, campus_id=None):
    """
    True if `user` is a verified parent of `student_id` (design doc
    §2/§11 — `CampusParentLink`, only ever written after `message`'s
    ParentAccessCode/token verification succeeds via
    `bridge.resolve_parent_from_token`). `campus_id` is optional — pass
    it when the check is already scoped to one campus (report cards,
    fee payment) to also confirm the link was made at that campus;
    omit it for a campus-agnostic "is this person a parent of this
    student anywhere" check (e.g. the attendance summary endpoint,
    which isn't itself campus-scoped).
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    qs = CampusParentLink.objects.filter(parent=user, student_id=student_id)
    if campus_id is not None:
        qs = qs.filter(campus_id=campus_id)
    return qs.exists()


def can_manage_section_subject(user, campus_id, section_id, subject_id=None):
    """
    Can `user` manage content (attendance, assignments, syllabus,
    results, live sessions) for this section, optionally scoped to one
    subject? True if any of:
      - `user` is the section's class-teacher, or a campus admin/
        principal-HOD — always allowed, subject or no subject.
      - `subject_id` is given AND `user` holds an APPROVED
        `SubjectTeacherAssignment` for that exact section+subject
        (design doc §2/§5/§11 — approved subject-teachers manage only
        their own approved section+subject, not the whole section).
    When `subject_id` is None (a daily, not period-wise, mark — see
    `Attendance`'s model docstring), only the class-teacher/admin path
    applies; a subject-teacher with no subject specified is NOT
    automatically allowed.
    """
    if is_class_teacher_of_section(user, section_id):
        return True
    if is_campus_admin_or_principal(user, campus_id):
        return True
    if subject_id:
        return SubjectTeacherAssignment.objects.filter(
            section_id=section_id,
            subject_id=subject_id,
            staff__user=user,
            staff__is_active=True,
            status=SubjectTeacherAssignment.Status.APPROVED,
        ).exists()
    return False


def can_post_notice(user, campus_id, department_id=None, school_class_id=None, section_id=None):
    """
    G-1 fix. `Notice`'s scope is whichever one of
    campus/department/school_class/section is set (see `Notice`'s
    model docstring) — this decides who may post at that scope,
    resolving the design doc's previously-deferred "class-teacher sirf
    apni section ko post kare" rule:

      - Campus admin / principal-HOD: allowed at ANY scope
        (campus-wide, department-wide, class-wide, or a single
        section) — same "manages the whole campus" authority
        `is_campus_admin_or_principal` already grants everywhere else.
      - Anyone else (class-teacher, subject-teacher, non-teaching
        staff): allowed ONLY when the notice is scoped to a single
        `section`, and only when `user` is that section's
        class-teacher. A subject-teacher or non-teaching staff member
        doesn't own any notice-scope of their own yet (subject-scoped
        notices aren't a thing `Notice` supports — it has no `subject`
        field), so they can't post at all until that's product-defined
        — same "don't guess it" posture as the other open design-doc
        questions flagged in models.py.
      - A campus-wide/department-wide/class-wide notice attempted by
        anyone other than an admin/principal-HOD is always denied,
        regardless of `section_id` — those broader scopes are
        deliberately admin/principal-only for now.

    Callers pass whichever of department_id/school_class_id/section_id
    the notice actually sets (see `Notice`'s nullable-FK-per-scope
    design) — at most one is expected to be non-None in practice, but
    this function denies conservatively if more than one broader scope
    is set alongside a section.
    """
    if is_campus_admin_or_principal(user, campus_id):
        return True
    if department_id or school_class_id:
        return False
    if section_id:
        return is_class_teacher_of_section(user, section_id)
    return False


def is_platform_admin(user):
    """
    G-2 fix. Platform-level staff (Django's own `is_staff`/
    `is_superuser`, NOT a `campus`-scoped `StaffProfile` role) who can
    approve/reject a newly-created `Campus`'s
    `verification_status` (design doc §13.2, previously an open
    question — see `Campus.VerificationStatus`'s docstring in
    models.py). Deliberately independent of any campus: a campus that
    hasn't been approved yet has no admin/principal `StaffProfile`
    whose authority we could check even if we wanted to, since the
    self-appointed creator's ADMIN profile is exactly the thing this
    gate exists to not blindly trust.

    See `is_campus_admin`'s docstring for the other half of this
    boundary: a campus `StaffProfile.Role.ADMIN` row must never be
    treated as equivalent to `is_staff`/`is_superuser` here.
    """
    return bool(user and getattr(user, 'is_authenticated', False) and (user.is_staff or user.is_superuser))


def is_campus_approved(campus_id):
    """
    G-2 fix. True only once a platform admin has approved the campus
    (`Campus.VerificationStatus.APPROVED`) — used to gate the
    membership-growth endpoints (inviting staff, enrolling students,
    verifying a parent link) that let a campus actually start operating
    as a real institute. Deliberately NOT used to gate the creator's
    own structural setup (sessions/classes/sections/subjects/rooms) —
    per `CampusViewSet`'s docstring, creating a campus must always
    leave the creator able to manage it; what's gated is OTHER people
    being pulled into an unverified campus.
    """
    return Campus.objects.filter(
        id=campus_id, verification_status=Campus.VerificationStatus.APPROVED
    ).exists()


class IsCampusAdminOrPrincipal(BasePermission):
    """
    For structural-setup endpoints (departments/classes/sections/
    subjects/rooms/staff) — design doc §11: only Admin/Principal-HOD
    manage the campus structure itself.

    Expects the view to expose the target campus id as
    `view.get_campus_id_for_permission_check()` (views below implement
    this), since the campus id can come from a URL kwarg, a query
    param, or the request body depending on the endpoint.
    """

    def has_permission(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        campus_id = view.get_campus_id_for_permission_check(request)
        if campus_id is None:
            return False
        return is_campus_admin_or_principal(request.user, campus_id)


class IsSectionSubjectStaffOrReadOnly(BasePermission):
    """
    For section+subject-scoped content endpoints (live sessions,
    attendance, assignments, syllabus, results — design doc §5/§6/§7):
    safe methods (list/retrieve) are left to the viewset's own
    campus-membership queryset scoping; unsafe methods require
    `can_manage_section_subject` to be true for the (campus, section,
    subject) the request targets.

    Expects the view to expose
    `view.get_section_subject_for_permission_check(request)` returning
    a `(campus_id, section_id, subject_id)` tuple — `subject_id` may be
    `None` (see `can_manage_section_subject`'s docstring for what that
    means), but `campus_id`/`section_id` being `None` always denies,
    the same "can't verify it, don't allow it" posture
    `IsCampusAdminOrPrincipal` already takes.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        campus_id, section_id, subject_id = view.get_section_subject_for_permission_check(request)
        if campus_id is None or section_id is None:
            return False
        return can_manage_section_subject(request.user, campus_id, section_id, subject_id)


class IsPlatformAdmin(BasePermission):
    """
    G-2 fix. For the `CampusViewSet.approve`/`.reject` actions only —
    platform-level staff, not a campus-scoped role (see
    `is_platform_admin`'s docstring for why this is deliberately
    independent of any campus's own `StaffProfile` rows).
    """

    def has_permission(self, request, view):
        return is_platform_admin(request.user)