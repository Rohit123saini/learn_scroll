# campus/participants.py
"""
[T4 §A] Unified "participants" view: Admin / Principal-HOD / Moderator /
Class Teacher / Subject Teacher / Non-teaching staff / Student / Parent in ONE
paginated, role-scoped list + a category/class-wise counts summary.

Scope (who may see what) — `resolve_scope()`:
  - Admin                     -> whole campus
  - Principal/HOD             -> their `StaffProfile.department` (NULL = whole campus, G-5)
  - Class teacher             -> only the sections they are class teacher of
  - everyone else             -> PermissionDenied (403)

The three sources (StaffProfile / StudentEnrollment / CampusParentLink) are
different tables, so "all categories" is a *concatenated* page: counts are
taken per source and each source is sliced by LIMIT/OFFSET only for the part
of the requested window that falls inside it (no loading everything, safe for
10k-student campuses).
"""
from dataclasses import dataclass, field

from django.db.models import Count, Exists, OuterRef, Q
from rest_framework.exceptions import PermissionDenied
from rest_framework.utils.urls import replace_query_param

from common.pagination import get_max_page_size

from .models import (
    AcademicSession,
    CampusParentLink,
    ClassTeacherassigments,
    Section,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
)
from .permissions import ROLE_PARENT, ROLE_STUDENT, get_campus_role

STAFF_CATEGORIES = (
    StaffProfile.Role.ADMIN.value,
    StaffProfile.Role.PRINCIPAL_HOD.value,
    StaffProfile.Role.MODERATOR.value,
    StaffProfile.Role.CLASS_TEACHER.value,
    StaffProfile.Role.SUBJECT_TEACHER.value,
    StaffProfile.Role.NON_TEACHING.value,
)
CATEGORIES = STAFF_CATEGORIES + (ROLE_STUDENT, ROLE_PARENT)


@dataclass
class Scope:
    kind: str  # "campus" | "department" | "section"
    department_id: object = None
    section_ids: list = field(default_factory=list)


def resolve_scope(user, campus_id):
    role = get_campus_role(user, campus_id)
    if role == StaffProfile.Role.ADMIN:
        return Scope("campus")
    if role == StaffProfile.Role.PRINCIPAL_HOD:
        profile = StaffProfile.objects.filter(campus_id=campus_id, user=user, is_active=True).first()
        if profile and profile.department_id:
            return Scope("department", department_id=profile.department_id)
        return Scope("campus")
    if role == StaffProfile.Role.CLASS_TEACHER:
        ids = list(
            ClassTeacherassigments.objects.filter(
                staff__user=user, staff__is_active=True, staff__campus_id=campus_id
            ).values_list("section_id", flat=True)
        )
        return Scope("section", section_ids=ids)
    raise PermissionDenied("You are not allowed to view campus participants.")


def current_session(campus_id):
    return AcademicSession.objects.filter(campus_id=campus_id, is_current=True).first()


def _section_filter(prefix, scope, params):
    """Q on `<prefix>section` fields for scope + department/class/section filters."""
    p = f"{prefix}section__" if prefix is not None else "section__"
    q = Q()
    if scope.kind == "department":
        q &= Q(**{f"{p}school_class__department_id": scope.department_id})
    elif scope.kind == "section":
        q &= Q(**{f"{p}id__in": scope.section_ids})
    if params.get("department"):
        q &= Q(**{f"{p}school_class__department_id": params["department"]})
    if params.get("class"):
        q &= Q(**{f"{p}school_class_id": params["class"]})
    if params.get("section"):
        q &= Q(**{f"{p}id": params["section"]})
    return q


def _name_q(prefix, text):
    q = Q()
    for word in text.split():
        q &= (
            Q(**{f"{prefix}username__icontains": word})
            | Q(**{f"{prefix}first_name__icontains": word})
            | Q(**{f"{prefix}last_name__icontains": word})
        )
    return q


def students_qs(campus_id, scope, params, session=None):
    session = session or current_session(campus_id)
    qs = StudentEnrollment.objects.filter(
        section__school_class__campus_id=campus_id, status=StudentEnrollment.Status.ACTIVE
    )
    qs = qs.filter(session=session) if session else qs.none()
    qs = qs.filter(_section_filter(None, scope, params))
    text = (params.get("q") or "").strip()
    if text:
        qs = qs.filter(
            _name_q("student__", text) | Q(roll_number__iexact=text) | Q(enrollment_no__iexact=text)
        )
    return qs.select_related("student", "section__school_class__department").order_by(
        "section__school_class__name", "section__name", "roll_number", "student__username"
    )


def parents_qs(campus_id, scope, params, session=None):
    session = session or current_session(campus_id)
    if not session:
        return CampusParentLink.objects.none()
    child = StudentEnrollment.objects.filter(
        student=OuterRef("student_id"), status=StudentEnrollment.Status.ACTIVE, session=session,
    ).filter(_section_filter(None, scope, params))
    qs = CampusParentLink.objects.filter(campus_id=campus_id).filter(Exists(child))
    text = (params.get("q") or "").strip()
    if text:
        qs = qs.filter(_name_q("parent__", text) | _name_q("student__", text))
    return qs.select_related("parent", "student").order_by("parent__username", "student__username")


def staff_qs(campus_id, scope, params, category=None):
    qs = StaffProfile.objects.filter(campus_id=campus_id, is_active=True)
    if category in STAFF_CATEGORIES:
        qs = qs.filter(role=category)
    sec_q = Q()  # staff attached to matching sections (class teacher / approved subject teacher)
    sections_filtered = scope.kind != "campus" or any(params.get(k) for k in ("department", "class", "section"))
    if sections_filtered:
        sec_ids = Section.objects.filter(school_class__campus_id=campus_id).filter(
            _plain_section_q(scope, params)
        ).values("id")
        sec_q = Q(class_teacher_of__section_id__in=sec_ids) | Q(
            subject_assigmentss__section_id__in=sec_ids,
            subject_assigmentss__status=SubjectTeacherassigments.Status.APPROVED,
        )
        if scope.kind == "department" or params.get("department"):
            dept = scope.department_id if scope.kind == "department" else params.get("department")
            sec_q |= Q(department_id=dept)
        qs = qs.filter(sec_q).distinct()
    text = (params.get("q") or "").strip()
    if text:
        qs = qs.filter(_name_q("user__", text))
    return qs.select_related("user", "department").order_by("role", "user__username")


def _plain_section_q(scope, params):
    q = Q()
    if scope.kind == "department":
        q &= Q(school_class__department_id=scope.department_id)
    elif scope.kind == "section":
        q &= Q(id__in=scope.section_ids)
    if params.get("department"):
        q &= Q(school_class__department_id=params["department"])
    if params.get("class"):
        q &= Q(school_class_id=params["class"])
    if params.get("section"):
        q &= Q(id=params["section"])
    return q


# --------------------------------------------------------------------------
# row builders (plain dicts — cheap, no serializer overhead per row)
# --------------------------------------------------------------------------
def _user(u):
    return {"id": u.id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name}


def _section_bits(section):
    sc = section.school_class
    return (
        {"id": str(sc.id), "name": sc.name},
        {"id": str(section.id), "name": section.name},
        {"id": str(sc.department_id), "name": sc.department.name} if sc.department_id else None,
    )


def staff_row(sp):
    return {
        "category": sp.role, "id": str(sp.id), "user": _user(sp.user), "role": sp.role,
        "department": {"id": str(sp.department_id), "name": sp.department.name} if sp.department_id else None,
        "school_class": None, "section": None, "roll_number": "",
    }


def student_row(en):
    cls, sec, dept = _section_bits(en.section)
    return {
        "category": ROLE_STUDENT, "id": str(en.id), "user": _user(en.student), "role": ROLE_STUDENT,
        "department": dept, "school_class": cls, "section": sec, "roll_number": en.roll_number,
    }


def parent_row(link):
    return {
        "category": ROLE_PARENT, "id": str(link.id), "user": _user(link.parent), "role": ROLE_PARENT,
        "department": None, "school_class": None, "section": None, "roll_number": "",
        "child": _user(link.student),
    }


def paginate_concat(request, sources, page_size_default=20):
    """`sources` = [(queryset, row_builder), ...] in display order. Returns the
    standard DRF page envelope {count,next,previous,results}."""
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.query_params.get("page_size", page_size_default))
    except (TypeError, ValueError):
        size = page_size_default
    size = max(1, min(size, get_max_page_size()))
    start, end = (page - 1) * size, page * size

    counts = [qs.count() for qs, _ in sources]
    total = sum(counts)
    results, offset = [], 0
    for (qs, builder), n in zip(sources, counts):
        lo, hi = max(start - offset, 0), min(end - offset, n)
        if lo < hi:
            results.extend(builder(obj) for obj in qs[lo:hi])
        offset += n
    url = request.build_absolute_uri()
    return {
        "count": total,
        "next": replace_query_param(url, "page", page + 1) if end < total else None,
        "previous": replace_query_param(url, "page", page - 1) if page > 1 else None,
        "results": results,
    }


def list_participants(request, campus_id):
    scope = resolve_scope(request.user, campus_id)
    params = request.query_params
    category = (params.get("category") or "").strip()
    if category and category not in CATEGORIES:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"category": f"Must be one of {', '.join(CATEGORIES)}."})
    session = current_session(campus_id)
    sources = []
    if not category or category in STAFF_CATEGORIES:
        sources.append((staff_qs(campus_id, scope, params, category or None), staff_row))
    if not category or category == ROLE_STUDENT:
        sources.append((students_qs(campus_id, scope, params, session), student_row))
    if not category or category == ROLE_PARENT:
        sources.append((parents_qs(campus_id, scope, params, session), parent_row))
    return paginate_concat(request, sources)


def participants_summary(request, campus_id):
    """Category-wise + class-wise counts (same scope rules as the list)."""
    scope = resolve_scope(request.user, campus_id)
    params = request.query_params
    session = current_session(campus_id)

    staff_counts = {c: 0 for c in STAFF_CATEGORIES}
    base = staff_qs(campus_id, scope, params)
    for row in base.order_by().values("role").annotate(n=Count("id", distinct=True)):
        staff_counts[row["role"]] = row["n"]
    categories = dict(staff_counts)
    categories[ROLE_STUDENT] = students_qs(campus_id, scope, params, session).count()
    categories[ROLE_PARENT] = parents_qs(campus_id, scope, params, session).count()
    categories["total"] = sum(categories.values())

    per_section = {}
    if session:
        for row in (
            students_qs(campus_id, scope, params, session).order_by().values("section_id").annotate(n=Count("id"))
        ):
            per_section[row["section_id"]] = row["n"]
    classes = {}
    sections = (
        Section.objects.filter(school_class__campus_id=campus_id, school_class__session=session)
        .filter(_plain_section_q(scope, params))
        .select_related("school_class")
        .order_by("school_class__name", "name")
        if session
        else Section.objects.none()
    )
    for sec in sections:
        sc = sec.school_class
        entry = classes.setdefault(
            sc.id, {"school_class": str(sc.id), "name": sc.name,
                    "department": str(sc.department_id) if sc.department_id else None,
                    "students": 0, "sections": []},
        )
        n = per_section.get(sec.id, 0)
        entry["students"] += n
        entry["sections"].append(
            {"section": str(sec.id), "name": sec.name, "students": n, "capacity": sec.capacity,
             "is_full": sec.capacity is not None and n >= sec.capacity}
        )
    return {
        "campus": str(campus_id),
        "session": str(session.id) if session else None,
        "scope": scope.kind,
        "categories": categories,
        "classes": list(classes.values()),
    }
