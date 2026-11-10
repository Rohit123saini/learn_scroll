# campus/parent_comms.py
"""
PARENT <-> TEACHER — the shared rules both PTM scheduling (`campus.ptm`) and the
rules-based chat (`campus.parent_chat`) are built on.

Who counts as what
------------------
  * PARENT  = a REAL, loginable `User` with a `CampusParentLink` to the student
              at that campus. The non-loginable "shadow" users minted for
              loginless Parent Mode codes (`parent_shadow_<id>`) can never
              book or chat — they have no account to book/chat from; they stay
              read-only/push-only exactly like before.
  * TEACHER = an ACTIVE `StaffProfile` of the campus who actually teaches the
              student: class teacher of the student's section, or an APPROVED
              subject teacher / moderator of it. A principal/admin is NOT
              automatically "the child's teacher" (they oversee; see
              `parent_chat` for oversight).
  * STUDENT = an ACTIVE enrolment (in the campus's current academic session when
              the campus has one flagged, same rule as `campus.visibility`).

Everything here is pure ORM + small helpers — no DRF, no Celery, no push — so the
API, the notifications and the tests all judge eligibility with the SAME code.
"""
from __future__ import annotations

import logging
from datetime import time as dt_time

from django.utils import timezone

from .models import (
    AcademicSession,
    CampusParentLink,
    ClassTeacherassigments,
    ParentTeacherSettings,
    Section,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
)

logger = logging.getLogger(__name__)

SHADOW_PARENT_PREFIX = "parent_shadow_"  # see bridge._get_or_create_shadow_parent_user

# Hard ceilings the per-campus settings can never exceed (a typo of 9999 must not
# turn a rule off by accident).
MAX_CHAT_CHARS_CEILING = 2000
MAX_UNANSWERED_CEILING = 20


class CommsError(Exception):
    """A business-rule failure with a stable machine `code` (the API turns it
    into a 400 whose `code` the app switches on / translates)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def display_name(user) -> str:
    if user is None:
        return ""
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def is_shadow_parent(user) -> bool:
    return (getattr(user, "username", "") or "").startswith(SHADOW_PARENT_PREFIX)


def get_settings(campus_id) -> ParentTeacherSettings:
    """The campus's settings row, created with the defaults on first use."""
    obj = ParentTeacherSettings.objects.filter(campus_id=campus_id).first()
    if obj is None:
        obj, _ = ParentTeacherSettings.objects.get_or_create(campus_id=campus_id)
    return obj


def local_now():
    return timezone.localtime(timezone.now())


def in_chat_window(settings: ParentTeacherSettings, now=None) -> bool:
    """Is `now` (campus local time) inside the chat window? A window that wraps
    midnight (start > end, e.g. 20:00-06:00) is supported; start == end means
    "always open"."""
    now = now or local_now()
    t: dt_time = now.time()
    start, end = settings.chat_start_time, settings.chat_end_time
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end


# --------------------------------------------------------------------------
# parents
# --------------------------------------------------------------------------

def is_real_linked_parent(user, student_id, campus_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False) or is_shadow_parent(user):
        return False
    return CampusParentLink.objects.filter(campus_id=campus_id, student_id=student_id, parent=user).exists()


def real_parents_of_student(student_id, campus_id):
    """Linked parents that can actually log in (shadow Parent Mode users out)."""
    out, seen = [], set()
    for link in CampusParentLink.objects.filter(campus_id=campus_id, student_id=student_id).select_related("parent"):
        p = link.parent
        if p.pk in seen or is_shadow_parent(p):
            continue
        seen.add(p.pk)
        out.append(p)
    return out


def children_of_parent(parent, campus_id):
    """`[(student_user, [section_id, ...])]` for the parent's linked children
    that are actively enrolled at the campus."""
    out = []
    for link in CampusParentLink.objects.filter(campus_id=campus_id, parent=parent).select_related("student"):
        sections = student_section_ids(link.student_id, campus_id)
        if sections:
            out.append((link.student, sorted(sections, key=str)))
    return out


# --------------------------------------------------------------------------
# students -> sections -> teachers
# --------------------------------------------------------------------------

def campus_has_current_session(campus_id) -> bool:
    return AcademicSession.objects.filter(campus_id=campus_id, is_current=True).exists()


def student_section_ids(student_id, campus_id) -> set:
    """Sections the student is ACTIVELY enrolled in at the campus (current
    session only when the campus flags one)."""
    rows = StudentEnrollment.objects.filter(
        student_id=student_id,
        status=StudentEnrollment.Status.ACTIVE,
        section__school_class__campus_id=campus_id,
    ).values_list("section_id", "session__is_current")
    rows = list(rows)
    if not rows:
        return set()
    has_current = campus_has_current_session(campus_id)
    return {section_id for section_id, is_current in rows if is_current or not has_current}


def campus_section_ids(campus_id) -> set:
    qs = Section.objects.filter(school_class__campus_id=campus_id)
    if campus_has_current_session(campus_id):
        qs = qs.filter(school_class__session__is_current=True)
    return set(qs.values_list("pk", flat=True))


def teaching_staff_for_sections(section_ids) -> dict:
    """`{staff_id: {"staff": StaffProfile, "class_teacher": bool, "subjects": [name, ...],
    "section_ids": set}}` for every ACTIVE staff member who teaches any of the
    sections (class teacher or APPROVED subject teacher / moderator)."""
    section_ids = list(section_ids)
    out: dict = {}
    if not section_ids:
        return out

    def slot(staff):
        return out.setdefault(
            staff.pk, {"staff": staff, "class_teacher": False, "subjects": [], "section_ids": set()}
        )

    for ct in ClassTeacherassigments.objects.filter(
        section_id__in=section_ids, staff__is_active=True
    ).select_related("staff__user"):
        row = slot(ct.staff)
        row["class_teacher"] = True
        row["section_ids"].add(ct.section_id)
    for st in SubjectTeacherassigments.objects.filter(
        section_id__in=section_ids,
        status=SubjectTeacherassigments.Status.APPROVED,
        staff__is_active=True,
    ).select_related("staff__user", "subject"):
        row = slot(st.staff)
        if st.subject.name not in row["subjects"]:
            row["subjects"].append(st.subject.name)
        row["section_ids"].add(st.section_id)
    return out


def teachers_of_student(student_id, campus_id) -> list:
    """Teaching staff of a student (class teacher first, then by name)."""
    sections = student_section_ids(student_id, campus_id)
    rows = list(teaching_staff_for_sections(sections).values())
    rows.sort(key=lambda r: (not r["class_teacher"], display_name(r["staff"].user).lower()))
    return rows


def teaches_student(staff, student_id, campus_id) -> bool:
    """Does this (active) StaffProfile teach the student's current section(s)?"""
    if staff is None or not staff.is_active or staff.campus_id != campus_id:
        return False
    sections = student_section_ids(student_id, campus_id)
    if not sections:
        return False
    return staff.pk in teaching_staff_for_sections(sections)


def taught_section_ids(user, campus_id) -> set:
    """Sections `user` teaches at the campus (class teacher / approved subject
    teacher). Used to filter a teacher's own lists in ONE query each."""
    staff = StaffProfile.objects.filter(user=user, campus_id=campus_id, is_active=True).first()
    if staff is None:
        return set()
    ids = set(
        ClassTeacherassigments.objects.filter(staff=staff).values_list("section_id", flat=True)
    )
    ids |= set(
        SubjectTeacherassigments.objects.filter(
            staff=staff, status=SubjectTeacherassigments.Status.APPROVED
        ).values_list("section_id", flat=True)
    )
    return ids

