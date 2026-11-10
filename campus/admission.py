# campus/admission.py
"""
ADMISSION — enquiry -> application -> interview -> offer -> enrolled
(rules; the API is in `campus.admission_views`).

Lifecycle
---------
    enquiry ──> applied ──> interview ──> offered ──enroll()──> enrolled
       │           │            │   ▲         │
       │           ├──> waitlisted ──┘         ├──> waitlisted
       └───────────┴────────────┴─────────────┴──> rejected / withdrawn
    rejected / withdrawn ──reopen──> applied      (enrolled is final)

Rules
-----
* Who: campus admin / principal only (checked in the view). Students and parents
  never see admission rows.
* Session: defaults to the campus's CURRENT session; a class, when given, must
  belong to that campus AND that session.
* Application number: ``ADM/<session>/<0001>``, gap-free per (campus, session),
  row-locked (`AdmissionCounter`) so two admins can't get the same number.
* Duplicates: same applicant name + guardian phone in the same session while the
  other application is still live (not rejected / withdrawn) is refused
  (`duplicate_application`) unless the caller passes ``force``.
* Moves: only the arrows above. `rejected` needs a short note. `interview` may
  carry an ``interview_on`` time. `offered` needs a class and a free seat
  (`no_seats` — waitlist the child instead): seats = sum of section capacities of
  that class minus ACTIVE students minus other OFFERED applications. A class with
  any section without a capacity limit is treated as unlimited.
* Enrol: only from `offered`. Needs a real student account (`student` = user id
  or username) and a section of the offered class. Goes through
  `roster.enroll_student` (capacity, roll number, audit, chat-group sync), links
  the student + enrollment, and creates the invoices of every ACTIVE
  ``FeeStructure(category=admission)`` of that class / campus-wide for the session.
* Reopen: rejected / withdrawn -> applied (clears the decision).
* Remarks are staff-only notes; the push never carries them.

Every failure raises `AdmissionError(code, message, status)` — the view turns it
into a 4xx with a stable machine `code`.
"""
import csv
import io
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import finance, roster
from .models import (
    AcademicSession,
    AdmissionApplication,
    AdmissionCounter,
    FeeStructure,
    SchoolClass,
    Section,
    StudentEnrollment,
)

User = get_user_model()
S = AdmissionApplication.Status

NAME_MAX = 150
TEXT_MAX = 300
REMARKS_MAX = 500
NOTE_MAX = 200
REJECT_NOTE_MIN_LEN = 3
MAX_IMPORT_ROWS = 300
MAX_IMPORT_BYTES = 300_000

LIVE_STATUSES = (S.ENQUIRY, S.APPLIED, S.INTERVIEW, S.WAITLISTED, S.OFFERED, S.ENROLLED)
OPEN_STATUSES = (S.ENQUIRY, S.APPLIED, S.INTERVIEW, S.WAITLISTED, S.OFFERED)

TRANSITIONS = {
    S.ENQUIRY: {S.APPLIED, S.REJECTED, S.WITHDRAWN},
    S.APPLIED: {S.INTERVIEW, S.WAITLISTED, S.OFFERED, S.REJECTED, S.WITHDRAWN},
    S.INTERVIEW: {S.WAITLISTED, S.OFFERED, S.REJECTED, S.WITHDRAWN},
    S.WAITLISTED: {S.INTERVIEW, S.OFFERED, S.REJECTED, S.WITHDRAWN},
    S.OFFERED: {S.WAITLISTED, S.REJECTED, S.WITHDRAWN},
    S.ENROLLED: set(),
    S.REJECTED: {S.APPLIED},
    S.WITHDRAWN: {S.APPLIED},
}


class AdmissionError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ------------------------------------------------------------------ helpers

def _clean(text, limit, field):
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        raise AdmissionError(f"{field}_too_long", f"Keep {field.replace('_', ' ')} within {limit} characters.")
    return text


def normalize_phone(raw) -> str:
    """Digits only (keeps a leading +); so '98765-43210' and '9876543210' match."""
    raw = str(raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if not 7 <= len(digits) <= 15:
        raise AdmissionError("bad_phone", "Phone number must have 7 to 15 digits.")
    return ("+" if raw.startswith("+") else "") + digits


def current_session(campus) -> AcademicSession:
    session = AcademicSession.objects.filter(campus=campus, is_current=True).first()
    if session is None:
        raise AdmissionError("no_current_session", "Set a current academic session first.")
    return session


def resolve_session(campus, session_id=None) -> AcademicSession:
    if not session_id:
        return current_session(campus)
    try:
        session = AcademicSession.objects.filter(pk=session_id, campus=campus).first()
    except (ValueError, TypeError, DjangoValidationError):  # not a uuid
        session = None
    if session is None:
        raise AdmissionError("session_not_found", "Session not found in this campus.", 404)
    return session


def resolve_class(campus, session, class_id):
    if not class_id:
        return None
    try:
        school_class = SchoolClass.objects.filter(pk=class_id, campus=campus, session=session).first()
    except (ValueError, TypeError, DjangoValidationError):
        school_class = None
    if school_class is None:
        raise AdmissionError("class_not_found", "Class not found in this campus and session.", 404)
    return school_class


def display_name(user) -> str:
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def next_application_no(campus, session) -> str:
    with transaction.atomic():
        counter, _ = AdmissionCounter.objects.select_for_update().get_or_create(campus=campus, session=session)
        counter.last_no += 1
        counter.save(update_fields=["last_no"])
        return f"ADM/{session.name}/{counter.last_no:04d}"


def _find_duplicate(campus, session, name, phone, exclude_pk=None):
    if not phone:
        return None
    qs = AdmissionApplication.objects.filter(
        campus=campus, session=session, guardian_phone=phone, applicant_name__iexact=name,
        status__in=LIVE_STATUSES,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


# ------------------------------------------------------------------ seats

def seats_info(school_class, session=None, exclude_app_id=None) -> dict:
    """{capacity, enrolled, offered, seats_left, unlimited} for one class."""
    session = session or school_class.session
    sections = list(Section.objects.filter(school_class=school_class).values_list("capacity", flat=True))
    enrolled = StudentEnrollment.objects.filter(
        section__school_class=school_class, session=session, status=StudentEnrollment.Status.ACTIVE
    ).count()
    offers = AdmissionApplication.objects.filter(school_class=school_class, session=session, status=S.OFFERED)
    if exclude_app_id:
        offers = offers.exclude(pk=exclude_app_id)
    offered = offers.count()
    unlimited = (not sections) or any(c is None for c in sections)
    capacity = None if unlimited else sum(sections)
    return {
        "capacity": capacity,
        "enrolled": enrolled,
        "offered": offered,
        "unlimited": unlimited,
        "seats_left": None if unlimited else max(capacity - enrolled - offered, 0),
    }


# ------------------------------------------------------------------ create / edit

def _apply_fields(app, data, *, campus, session):
    """Validate + set the editable text fields that are present in `data`."""
    if "applicant_name" in data:
        name = _clean(data["applicant_name"], NAME_MAX, "applicant_name")
        if len(name) < 2:
            raise AdmissionError("name_required", "Enter the child's name.")
        app.applicant_name = name
    if "guardian_name" in data:
        app.guardian_name = _clean(data["guardian_name"], NAME_MAX, "guardian_name")
    if "guardian_phone" in data:
        app.guardian_phone = normalize_phone(data["guardian_phone"])
    if "guardian_email" in data:
        app.guardian_email = _clean(data["guardian_email"], 254, "guardian_email").lower()
    if "previous_school" in data:
        app.previous_school = _clean(data["previous_school"], NAME_MAX, "previous_school")
    if "address" in data:
        app.address = _clean(data["address"], TEXT_MAX, "address")
    if "remarks" in data:
        app.remarks = _clean(data["remarks"], REMARKS_MAX, "remarks")
    if "date_of_birth" in data:
        dob = data["date_of_birth"]
        if dob and dob >= timezone.localdate():
            raise AdmissionError("bad_dob", "Date of birth must be in the past.")
        app.date_of_birth = dob or None
    if "follow_up_on" in data:
        app.follow_up_on = data["follow_up_on"] or None
    if "source" in data and data["source"]:
        if data["source"] not in AdmissionApplication.Source.values:
            raise AdmissionError("bad_source", f"Source must be one of: {', '.join(AdmissionApplication.Source.values)}.")
        app.source = data["source"]
    if "school_class" in data:
        app.school_class = resolve_class(campus, session, data["school_class"])
        if app.section_id and (app.school_class is None or app.section.school_class_id != app.school_class.pk):
            app.section = None  # allotted section no longer matches the class
    if "section" in data:
        app.section = _resolve_section(app, data["section"])


def _resolve_section(app, section_id):
    if not section_id:
        return None
    if app.school_class is None:
        raise AdmissionError("class_required", "Pick the class before the section.")
    try:
        section = Section.objects.filter(pk=section_id, school_class=app.school_class).first()
    except (ValueError, TypeError, DjangoValidationError):
        section = None
    if section is None:
        raise AdmissionError("section_not_found", "Section not found in that class.", 404)
    return section


@transaction.atomic
def create_application(*, campus, actor, session_id=None, force=False, **data) -> AdmissionApplication:
    session = resolve_session(campus, session_id)
    app = AdmissionApplication(campus=campus, session=session, created_by=actor)
    data.setdefault("applicant_name", "")
    _apply_fields(app, data, campus=campus, session=session)
    if not app.source:
        app.source = AdmissionApplication.Source.WALK_IN
    dup = _find_duplicate(campus, session, app.applicant_name, app.guardian_phone)
    if dup and not force:
        raise AdmissionError(
            "duplicate_application",
            f"{app.applicant_name} is already on file ({dup.application_no or dup.get_status_display()}).",
            409,
        )
    app.application_no = next_application_no(campus, session)
    app.save()
    return app


@transaction.atomic
def edit_application(*, app_id, force=False, **data) -> AdmissionApplication:
    app = AdmissionApplication.objects.select_for_update().select_related("campus", "session", "school_class").get(pk=app_id)
    if app.status == S.ENROLLED:
        data = {k: v for k, v in data.items() if k in ("remarks", "follow_up_on")}  # history stays as it was
    _apply_fields(app, data, campus=app.campus, session=app.session)
    dup = _find_duplicate(app.campus, app.session, app.applicant_name, app.guardian_phone, exclude_pk=app.pk)
    if dup and not force and app.status in LIVE_STATUSES:
        raise AdmissionError("duplicate_application", f"{app.applicant_name} is already on file.", 409)
    app.save()
    return app


# ------------------------------------------------------------------ moves

@transaction.atomic
def move(*, app_id, to, actor, note="", interview_on=None, section_id=None) -> AdmissionApplication:
    try:
        to = AdmissionApplication.Status(to)
    except ValueError:
        raise AdmissionError("bad_status", f"Status must be one of: {', '.join(AdmissionApplication.Status.values)}.")
    app = AdmissionApplication.objects.select_for_update().select_related("campus", "session", "school_class").get(pk=app_id)
    if app.status == to:
        return app  # idempotent
    if to == S.ENROLLED:
        raise AdmissionError("use_enroll", "Use the enrol step: it links the student account and creates the fee invoices.")
    if to not in TRANSITIONS.get(AdmissionApplication.Status(app.status), set()):
        raise AdmissionError(
            "bad_transition", f"An application that is {app.get_status_display().lower()} can't move to {to.label.lower()}.", 409
        )
    note = _clean(note, NOTE_MAX, "note")
    if to == S.REJECTED and len(note) < REJECT_NOTE_MIN_LEN:
        raise AdmissionError("note_required", "Give a short reason for rejecting.")
    if to == S.INTERVIEW and interview_on is not None:
        app.interview_on = interview_on
    if to == S.OFFERED:
        if app.school_class is None:
            raise AdmissionError("class_required", "Pick the class before offering a seat.")
        if section_id:
            app.section = _resolve_section(app, section_id)
        info = seats_info(app.school_class, app.session, exclude_app_id=app.pk)
        if not info["unlimited"] and info["seats_left"] <= 0:
            raise AdmissionError("no_seats", "No seat is free in this class — waitlist the child instead.", 409)
    if to == S.APPLIED and app.status in (S.REJECTED, S.WITHDRAWN):  # reopen
        app.decision_note = ""
        app.decided_by = None
        app.decided_at = None
    if to in (S.OFFERED, S.REJECTED, S.WITHDRAWN, S.WAITLISTED):
        app.decision_note = note
        app.decided_by = actor
        app.decided_at = timezone.now()
    app.status = to
    app.save()
    return app


# ------------------------------------------------------------------ enrol

def _resolve_student(raw):
    raw = str(raw or "").strip()
    if not raw:
        raise AdmissionError("student_required", "Give the student's account (user id or username).")
    q = Q(username__iexact=raw)
    if raw.isdigit():
        q |= Q(pk=int(raw))
    user = User.objects.filter(q).first()
    if user is None:
        raise AdmissionError(
            "student_not_found",
            "No account with that id / username yet — ask the family to sign up (or share the class invite code), then enrol.",
            404,
        )
    return user


@transaction.atomic
def enroll(*, app_id, actor, student, section_id=None, roll_number="", enrollment_no="", override_capacity=False):
    """offered -> enrolled. Returns ``(application, enrollment, invoices_created)``."""
    app = AdmissionApplication.objects.select_for_update().select_related("campus", "session", "school_class").get(pk=app_id)
    if app.status == S.ENROLLED:
        raise AdmissionError("already_enrolled", "This child is already enrolled.", 409)
    if app.status != S.OFFERED:
        raise AdmissionError("not_offered", "Offer a seat first, then enrol.", 409)
    if app.school_class is None:
        raise AdmissionError("class_required", "Pick the class first.")
    user = _resolve_student(student)
    if StudentEnrollment.objects.filter(
        student=user, session=app.session, status=StudentEnrollment.Status.ACTIVE,
        section__school_class__campus_id=app.campus_id,
    ).exists():
        raise AdmissionError("already_student", "This account is already an active student of this campus this session.", 409)
    section = _resolve_section(app, section_id) if section_id else app.section
    if section is None:
        raise AdmissionError("section_required", "Choose the section to enrol into.")
    try:
        enrollment, _ = roster.enroll_student(
            section=section, student=user, session=app.session, roll_number=roll_number,
            enrollment_no=_clean(enrollment_no, 30, "enrollment_no"), actor=actor,
            override_capacity=override_capacity,
        )
    except roster.RosterError as exc:
        raise AdmissionError(exc.code, exc.message, 409)

    invoices = 0
    structures = FeeStructure.objects.filter(
        campus=app.campus, session=app.session, is_active=True, category=FeeStructure.Category.ADMISSION
    ).filter(Q(school_class=app.school_class) | Q(school_class__isnull=True))
    for fs in structures:
        _, created = finance.create_invoice_for(enrollment, fs)
        invoices += 1 if created else 0

    app.student = user
    app.enrollment = enrollment
    app.section = section
    app.status = S.ENROLLED
    app.decided_by = actor
    app.decided_at = timezone.now()
    app.save()
    return app, enrollment, invoices


# ------------------------------------------------------------------ summary / board

def summary(campus, session, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    qs = AdmissionApplication.objects.filter(campus=campus, session=session)
    by_status = {s: 0 for s in AdmissionApplication.Status.values}
    for row in qs.values("status").annotate(n=Count("id")):
        by_status[row["status"]] = row["n"]
    total = sum(by_status.values())
    enrolled = by_status[S.ENROLLED]
    closed = enrolled + by_status[S.REJECTED] + by_status[S.WITHDRAWN]
    follow_qs = qs.filter(status__in=OPEN_STATUSES, follow_up_on__isnull=False)
    by_class = [
        {"school_class": str(r["school_class"]) if r["school_class"] else None,
         "name": r["school_class__name"] or "Not decided", "total": r["n"]}
        for r in qs.values("school_class", "school_class__name").annotate(n=Count("id")).order_by("-n")
    ]
    by_source = {r["source"]: r["n"] for r in qs.values("source").annotate(n=Count("id"))}
    return {
        "session": str(session.pk),
        "session_name": session.name,
        "total": total,
        "by_status": by_status,
        "open": sum(by_status[s] for s in OPEN_STATUSES),
        "conversion_percent": round(enrolled * 100 / total, 1) if total else 0.0,
        "decided": closed,
        "follow_up_due": follow_qs.filter(follow_up_on__lte=today).count(),
        "follow_up_today": follow_qs.filter(follow_up_on=today).count(),
        "by_class": by_class,
        "by_source": by_source,
    }


# ------------------------------------------------------------------ CSV import / export

CSV_HEADERS = {
    "name": "applicant_name", "child": "applicant_name", "child_name": "applicant_name",
    "student": "applicant_name", "student_name": "applicant_name", "applicant_name": "applicant_name",
    "parent": "guardian_name", "parent_name": "guardian_name", "guardian": "guardian_name",
    "guardian_name": "guardian_name", "father_name": "guardian_name",
    "phone": "guardian_phone", "mobile": "guardian_phone", "guardian_phone": "guardian_phone",
    "parent_phone": "guardian_phone", "contact": "guardian_phone",
    "email": "guardian_email", "guardian_email": "guardian_email",
    "class": "class", "class_name": "class", "grade": "class", "applying_for": "class",
    "dob": "date_of_birth", "date_of_birth": "date_of_birth", "birth_date": "date_of_birth",
    "previous_school": "previous_school", "school": "previous_school",
    "address": "address", "source": "source", "remarks": "remarks", "notes": "remarks",
    "follow_up": "follow_up_on", "follow_up_on": "follow_up_on",
}
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DATE_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _DATE_RE.match(raw)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = _DATE_DMY.match(raw)
        if not m:
            raise ValueError("date")
        d, mo, y = map(int, m.groups())
    return date(y, mo, d)


def parse_import_csv(text: str) -> tuple[list[dict], list[dict]]:
    """Pure parse -> (rows, errors). Each error is {line, error}; one bad row never hides the others."""
    if len(text.encode("utf-8", "ignore")) > MAX_IMPORT_BYTES:
        raise AdmissionError("file_too_large", "CSV is too large (max 300 KB).")
    text = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise AdmissionError("empty_file", "The CSV is empty.")
    cols = [CSV_HEADERS.get(re.sub(r"[\s\-]+", "_", h.strip().lower())) for h in header]
    if "applicant_name" not in cols:
        raise AdmissionError("name_column_missing", "The CSV needs a child name column (name / child_name).")
    rows, errors = [], []
    for line_no, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        if len(rows) + len(errors) >= MAX_IMPORT_ROWS:
            raise AdmissionError("too_many_rows", f"At most {MAX_IMPORT_ROWS} rows per file.")
        rec = {}
        for col, val in zip(cols, raw):
            if col:
                rec[col] = val.strip()
        try:
            for key in ("date_of_birth", "follow_up_on"):
                if key in rec:
                    rec[key] = _parse_date(rec[key])
            if not rec.get("applicant_name"):
                raise AdmissionError("name_required", "Child name is empty.")
            rec["guardian_phone"] = normalize_phone(rec.get("guardian_phone", ""))
        except AdmissionError as exc:
            errors.append({"line": line_no, "error": exc.message})
            continue
        except ValueError:
            errors.append({"line": line_no, "error": "Dates must be YYYY-MM-DD or DD/MM/YYYY."})
            continue
        rec["_line"] = line_no
        rows.append(rec)
    return rows, errors


def import_applications(*, campus, actor, text, session_id=None, commit=False) -> dict:
    """Preview (`commit=False`) or save every good row as an ENQUIRY. Classes are matched by
    name inside the session; a name that matches no class leaves the class blank and is reported."""
    session = resolve_session(campus, session_id)
    rows, errors = parse_import_csv(text)
    classes = {c.name.strip().lower(): c for c in SchoolClass.objects.filter(campus=campus, session=session)}
    ready, skipped = [], list(errors)
    seen = set()
    for rec in rows:
        name_key = (rec["applicant_name"].lower(), rec.get("guardian_phone", ""))
        if rec.get("guardian_phone") and name_key in seen:
            skipped.append({"line": rec["_line"], "error": "Repeated in this file."})
            continue
        seen.add(name_key)
        class_name = rec.pop("class", "")
        school_class = classes.get(class_name.strip().lower()) if class_name else None
        rec["_class_missing"] = bool(class_name and not school_class)
        rec["school_class"] = str(school_class.pk) if school_class else None
        if _find_duplicate(campus, session, rec["applicant_name"], rec.get("guardian_phone", "")):
            skipped.append({"line": rec["_line"], "error": "Already on file."})
            continue
        ready.append(rec)
    created = 0
    if commit:
        with transaction.atomic():
            for rec in ready:
                data = {k: v for k, v in rec.items() if not k.startswith("_")}
                data["source"] = AdmissionApplication.Source.IMPORT
                create_application(campus=campus, actor=actor, session_id=session.pk, force=True, **data)
                created += 1
    return {
        "committed": bool(commit),
        "session": str(session.pk),
        "ready": len(ready),
        "created": created,
        "skipped": skipped,
        "class_not_matched": [r["_line"] for r in ready if r["_class_missing"]],
        "preview": [
            {"line": r["_line"], "applicant_name": r["applicant_name"], "guardian_phone": r.get("guardian_phone", ""),
             "school_class": r["school_class"]}
            for r in ready[:20]
        ],
    }


EXPORT_COLUMNS = [
    "application_no", "applicant_name", "date_of_birth", "class", "guardian_name", "guardian_phone",
    "guardian_email", "previous_school", "source", "status", "follow_up_on", "created_on",
]


def _csv_safe(value):
    """Stop spreadsheet formula injection (=, +, -, @ at the start of a cell)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"\+?\d[\d ]*", text) else text


def export_csv(queryset) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for a in queryset.select_related("school_class"):
        writer.writerow([_csv_safe(v) for v in (
            a.application_no, a.applicant_name, a.date_of_birth or "", a.school_class.name if a.school_class else "",
            a.guardian_name, a.guardian_phone, a.guardian_email, a.previous_school, a.source, a.status,
            a.follow_up_on or "", timezone.localtime(a.created_at).date().isoformat(),
        )])
    return buf.getvalue()
