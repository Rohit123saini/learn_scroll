# campus/safety_rules.py
"""
SAFETY — pure rules (no Django, no DB, no clock reads) so every decision here is unit-testable with
plain `python -m unittest campus.tests_safety_rules`.

Four features share this file:

  1. AUDIT LOG filter + search     -> `parse_audit_bound`, `audit_category`, `safe_cell`
  2. BACKUP / RESTORE              -> `BACKUP_GROUPS`, `validate_restore_options`, `RESTORE_CONFIRM`
  3. DATA DELETE REQUEST           -> `DeletionStatus`, `can_transition`, `due_at`, `is_overdue`
  4. CONSENT LOG                   -> `CONSENT_PURPOSES`, `validate_consent`, `needs_reconsent`

The DB side lives in `audit_query.py`, `backup.py`, `data_deletion.py`, `consent.py`; HTTP in `safety_views.py`.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# 1. Audit log
# ---------------------------------------------------------------------------
AUDIT_EXPORT_MAX_ROWS = 10_000
AUDIT_SEARCH_MAX_LEN = 100
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


def is_uuid(raw) -> bool:
    return bool(raw) and bool(_UUID_RE.match(str(raw)))


def audit_category(action: str) -> str:
    """`section.create` -> `section`; an action with no dot (`fine_waive`) is its own category."""
    return (action or "").split(".", 1)[0]


def clean_search(raw) -> str:
    """Trim + collapse whitespace + cap the length. Empty string = no search."""
    text = " ".join(str(raw or "").split())
    return text[:AUDIT_SEARCH_MAX_LEN]


def parse_audit_bound(raw, *, end: bool = False):
    """
    `since` / `until` query value -> naive `datetime` (the view makes it timezone-aware).

    * `2026-10-20`            -> 00:00 of that day for `since`; **end of that day** for `until`
                                 (the old code compared `created_at <= '2026-10-20'` = midnight, which silently
                                 dropped everything that happened on the chosen last day).
    * `2026-10-20T14:30:00`   -> used as is.
    Raises ValueError for anything else.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if "T" not in text and " " not in text:
        day = date.fromisoformat(text)  # ValueError on junk
        base = datetime(day.year, day.month, day.day)
        return base + timedelta(days=1) - timedelta(microseconds=1) if end else base
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def check_range(since, until) -> None:
    if since is not None and until is not None and since > until:
        raise ValueError("`since` must not be after `until`.")


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """CSV formula-injection guard: a cell that would be read as a formula gets a leading apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


# ---------------------------------------------------------------------------
# 2. Backup / restore
# ---------------------------------------------------------------------------
BACKUP_FORMAT = "learnscroll-campus-backup"
BACKUP_VERSION = 1
RESTORE_CONFIRM = "RESTORE"  # typed by the admin in the app

GROUP_SETUP = "setup"
GROUP_RECORDS = "records"
BACKUP_GROUPS = (GROUP_SETUP, GROUP_RECORDS)

MODE_MISSING_ONLY = "missing_only"  # default: only re-create rows that are gone; never touches a row that exists
MODE_OVERWRITE = "overwrite"  # also put back the old field values of rows that still exist
RESTORE_MODES = (MODE_MISSING_ONLY, MODE_OVERWRITE)

KIND_MANUAL = "manual"
KIND_SCHEDULED = "scheduled"
KIND_PRE_RESTORE = "pre_restore"
BACKUP_KINDS = (KIND_MANUAL, KIND_SCHEDULED, KIND_PRE_RESTORE)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_PER_CAMPUS = 20
DEFAULT_MAX_ROWS = 300_000


def validate_restore_options(groups, mode, available_groups, confirm, dry_run):
    """
    -> (groups: list[str], mode: str). Raises ValueError(code) with a short machine code.
    A real (non dry-run) restore needs the typed confirmation word.
    """
    if mode not in RESTORE_MODES:
        raise ValueError("invalid_mode")
    wanted = list(groups) if groups else [GROUP_SETUP]
    if any(g not in BACKUP_GROUPS for g in wanted):
        raise ValueError("invalid_group")
    wanted = [g for g in BACKUP_GROUPS if g in wanted]  # canonical order, no duplicates
    missing = [g for g in wanted if g not in set(available_groups or ())]
    if missing:
        raise ValueError("group_not_in_backup")
    if not dry_run and str(confirm or "").strip().upper() != RESTORE_CONFIRM:
        raise ValueError("confirm_required")
    return wanted, mode


# ---------------------------------------------------------------------------
# 3. Data delete request
# ---------------------------------------------------------------------------
class DeletionStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    ALL = (PENDING, APPROVED, REJECTED, COMPLETED, CANCELLED)
    OPEN = (PENDING, APPROVED)


KIND_MY_DATA = "my_data"
KIND_CHILD_DATA = "child_data"
DELETION_KINDS = (KIND_MY_DATA, KIND_CHILD_DATA)

_TRANSITIONS = {
    DeletionStatus.PENDING: {DeletionStatus.APPROVED, DeletionStatus.REJECTED, DeletionStatus.CANCELLED},
    DeletionStatus.APPROVED: {DeletionStatus.COMPLETED},
}

DEFAULT_DELETION_SLA_DAYS = 30
REASON_MIN = 3
REASON_MAX = 500
NOTE_MIN = 3
NOTE_MAX = 500


def can_transition(old: str, new: str) -> bool:
    return new in _TRANSITIONS.get(old, set())


def due_at(created, days: int = DEFAULT_DELETION_SLA_DAYS):
    return created + timedelta(days=int(days))


def is_overdue(status: str, due, now) -> bool:
    return status in DeletionStatus.OPEN and due is not None and now > due


def clean_text(raw, *, minimum: int, maximum: int, field: str) -> str:
    text = " ".join(str(raw or "").split())
    if len(text) < minimum:
        raise ValueError(f"{field}_too_short")
    return text[:maximum]


# What an executed request erases / keeps. Shown to the admin BEFORE executing (preview) and stored on the
# request afterwards, so "what happened to my data" has a written answer.
RETAINED_RECORDS = (
    ("attendance", "Attendance register entries (school record)"),
    ("results", "Marks and report-card results (academic record)"),
    ("fees", "Fee invoices and payments (financial / tax record)"),
    ("assignment_submissions", "Submitted assignments and their grades (academic record)"),
    ("audit_log", "Campus audit log entries (accountability trail)"),
    ("consent_log", "Consent log entries (proof of consent)"),
)


# ---------------------------------------------------------------------------
# 4. Consent log
# ---------------------------------------------------------------------------
CONSENT_GRANTED = "granted"
CONSENT_WITHDRAWN = "withdrawn"
CONSENT_STATUSES = (CONSENT_GRANTED, CONSENT_WITHDRAWN)

SOURCE_APP = "app"
SOURCE_ADMIN = "admin_entry"  # paper form / verbal consent typed in by campus staff
CONSENT_SOURCES = (SOURCE_APP, SOURCE_ADMIN)

# scope: "self"  -> about the person giving it
#        "child" -> a linked parent gives it for ONE child (student id required)
#        "either"-> the child if `student` is sent, otherwise the person themselves
CONSENT_PURPOSES = {
    "terms_privacy": {"scope": "self", "required": True},
    "child_data": {"scope": "child", "required": True},
    "photo_media": {"scope": "either", "required": False},
    # PRIVACY (campus/privacy_rules.py): a linked parent decides AI features for a MINOR child (the minor cannot
    # grant `ai_features` for themselves). Optional, so it never counts as "required missing".
    "child_ai_features": {"scope": "child", "required": False},
    "communications": {"scope": "self", "required": False},
    "ai_features": {"scope": "self", "required": False},
}
CONSENT_PURPOSE_KEYS = tuple(CONSENT_PURPOSES)
DEFAULT_POLICY_VERSION = "2026-10"
POLICY_VERSION_MAX = 20
NOTE_MAX_CONSENT = 300


def validate_consent(purpose: str, granted, has_student: bool, version: str | None, default_version: str):
    """-> (status, version). Raises ValueError(code)."""
    spec = CONSENT_PURPOSES.get(purpose)
    if spec is None:
        raise ValueError("invalid_purpose")
    if not isinstance(granted, bool):
        raise ValueError("granted_must_be_boolean")
    scope = spec["scope"]
    if scope == "child" and not has_student:
        raise ValueError("student_required")
    if scope == "self" and has_student:
        raise ValueError("student_not_allowed")
    ver = str(version or default_version).strip()[:POLICY_VERSION_MAX] or default_version
    return (CONSENT_GRANTED if granted else CONSENT_WITHDRAWN), ver


def needs_reconsent(status, given_version, current_version) -> bool:
    """A grant given under an older policy text has to be asked again. A withdrawal never is."""
    return status == CONSENT_GRANTED and (given_version or "") != (current_version or "")


def latest_per_key(rows):
    """
    rows: iterable of dicts with subject/student/purpose/created_at (+ anything else), in ANY order.
    -> {(subject, student, purpose): newest row}. Ties on created_at keep the later item in the iterable.
    """
    best = {}
    for row in rows:
        key = (row["subject"], row.get("student"), row["purpose"])
        cur = best.get(key)
        if cur is None or row["created_at"] >= cur["created_at"]:
            best[key] = row
    return best
