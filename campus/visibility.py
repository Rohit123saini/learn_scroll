# campus/visibility.py
"""
T4 §E — strict, BACKEND-enforced visibility for students and parents.

Before this module every campus viewset scoped rows by "is the caller a
member of the campus" only (`views.get_my_campus_ids`): a student could
therefore list every section, every staff row, every other student's
enrollment/attendance/results/fee invoice and every notice in the campus.
Hiding those in the Flutter UI is not enough — the API has to refuse.

Rules (design doc §E, decision D2: one role per user per campus):

  * ACTIVE STAFF of a campus (any `StaffProfile.role`) keep the exact
    campus-wide visibility they always had. Staff precedence wins if the
    same user is somehow also enrolled/linked at that campus.
  * A STUDENT sees only the section(s) of their own ACTIVE enrollment in
    the campus's CURRENT academic session (if a campus has no current
    session flagged, their ACTIVE enrollments are used as a fallback so
    nobody is locked out by an unconfigured campus). Their own records
    (fee invoices, results, attendance, ID card, enrollment history) stay
    visible across sessions; nobody else's do.
  * A PARENT sees exactly what the linked child sees (read-only — every
    write endpoint still has its own staff permission check).

Detail routes for hidden rows 404 (they are simply absent from
`get_queryset()`), list routes come back filtered/empty.

This module only reads `campus` models — no other app is imported.
"""
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    AcademicSession,
    CampusParentLink,
    Section,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
)

_CACHE_ATTR = "_campus_visibility"


@dataclass
class Visibility:
    user_id: object = None
    staff_campus_ids: frozenset = frozenset()
    member_campus_ids: frozenset = frozenset()
    # campuses where the user is student/parent but NOT staff
    nonstaff_campus_ids: frozenset = frozenset()
    # sections a student/parent may see (non-staff campuses only)
    visible_section_ids: frozenset = frozenset()
    visible_class_ids: frozenset = frozenset()
    visible_department_ids: frozenset = frozenset()
    # the user's OWN active-current sections (excludes children's) — used
    # where a parent must NOT inherit the child's write-ish access
    own_section_ids: frozenset = frozenset()
    # self + linked children: whose personal records the user may read
    visible_student_ids: frozenset = frozenset()
    # {section_id: campus_id} for visible_section_ids
    section_campus: dict = field(default_factory=dict)
    _subject_ids: Optional[frozenset] = None

    def is_staff_in(self, campus_id) -> bool:
        return campus_id in self.staff_campus_ids

    def subject_ids(self) -> frozenset:
        """Subjects that have an APPROVED teacher in a visible section
        (= the subjects the student actually studies). Lazy + cached."""
        if self._subject_ids is None:
            if not self.visible_section_ids:
                self._subject_ids = frozenset()
            else:
                self._subject_ids = frozenset(
                    SubjectTeacherassigments.objects.filter(
                        section_id__in=self.visible_section_ids,
                        status=SubjectTeacherassigments.Status.APPROVED,
                    ).values_list("subject_id", flat=True)
                )
        return self._subject_ids


def _anonymous():
    return Visibility()


def compute_visibility(user) -> Visibility:
    if not user or not getattr(user, "is_authenticated", False):
        return _anonymous()

    staff_campus_ids = set(
        StaffProfile.objects.filter(user=user, is_active=True).values_list("campus_id", flat=True)
    )
    enrolled_campus_ids = set(
        StudentEnrollment.objects.filter(student=user).values_list("section__school_class__campus_id", flat=True)
    )
    links = list(CampusParentLink.objects.filter(parent=user).values_list("campus_id", "student_id"))
    parent_campus_ids = {campus_id for campus_id, _ in links}

    member = staff_campus_ids | enrolled_campus_ids | parent_campus_ids
    nonstaff = member - staff_campus_ids

    section_ids, class_ids, dept_ids, own_section_ids = set(), set(), set(), set()
    section_campus = {}
    student_ids = set()

    if nonstaff:
        student_ids.add(user.pk)
        child_pairs = {(campus_id, student_id) for campus_id, student_id in links if campus_id in nonstaff}
        student_ids |= {student_id for _, student_id in child_pairs}

        campuses_with_current = set(
            AcademicSession.objects.filter(campus_id__in=nonstaff, is_current=True)
            .values_list("campus_id", flat=True)
        )
        rows = StudentEnrollment.objects.filter(
            student_id__in=student_ids,
            status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus_id__in=nonstaff,
        ).values_list(
            "student_id", "section_id", "section__school_class_id",
            "section__school_class__department_id", "section__school_class__campus_id",
            "session__is_current",
        )
        for student_id, section_id, class_id, dept_id, campus_id, is_current in rows:
            if campus_id in campuses_with_current and not is_current:
                continue  # old-session enrollment — not "current"
            is_own = student_id == user.pk
            if not is_own and (campus_id, student_id) not in child_pairs:
                continue  # a child of someone else who merely shares the campus
            section_ids.add(section_id)
            class_ids.add(class_id)
            if dept_id:
                dept_ids.add(dept_id)
            section_campus[section_id] = campus_id
            if is_own:
                own_section_ids.add(section_id)

    return Visibility(
        user_id=user.pk,
        staff_campus_ids=frozenset(staff_campus_ids),
        member_campus_ids=frozenset(member),
        nonstaff_campus_ids=frozenset(nonstaff),
        visible_section_ids=frozenset(section_ids),
        visible_class_ids=frozenset(class_ids),
        visible_department_ids=frozenset(dept_ids),
        own_section_ids=frozenset(own_section_ids),
        visible_student_ids=frozenset(student_ids),
        section_campus=section_campus,
    )


def get_visibility(user, request=None) -> Visibility:
    """Per-request memoised `compute_visibility`. Cached on the REQUEST
    (never on the user object: tests re-use one `User` instance across
    many requests, and a cache there would go stale between them)."""
    if request is None:
        return compute_visibility(user)
    cached = getattr(request, _CACHE_ATTR, None)
    if cached is not None and cached.user_id == getattr(user, "pk", None):
        return cached
    vis = compute_visibility(user)
    try:
        setattr(request, _CACHE_ATTR, vis)
    except Exception:  # pragma: no cover - exotic request wrappers
        pass
    return vis


def get_my_campus_ids(user, request=None):
    """Every campus the user has SOME legitimate reason to see rows from
    (active staff, enrolled student, linked parent). Kept as the single
    definition; `views.get_my_campus_ids` delegates here."""
    return set(get_visibility(user, request).member_campus_ids)


def accessible_section_ids_and_campus_map(user, request=None):
    """`(section_ids, {section_id: campus_id})` the user may read
    section-scoped content (assignments, test series, ...) for: EVERY
    section of a campus they staff, plus only their own/child's current
    section(s) elsewhere. Replaces the old campus-wide
    `_my_section_ids_and_campus_map` which showed students every
    section's assignments."""
    vis = get_visibility(user, request)
    section_map = dict(vis.section_campus)
    if vis.staff_campus_ids:
        for section_id, campus_id in Section.objects.filter(
            school_class__campus_id__in=vis.staff_campus_ids
        ).values_list("id", "school_class__campus_id"):
            section_map[section_id] = campus_id
    return set(section_map), section_map
