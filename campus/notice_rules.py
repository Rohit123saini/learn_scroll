# campus/notice_rules.py
"""
COMMUNICATION — the pure rules behind notices (school-wide / class-wise / section-wise),
scheduled notices and the emergency broadcast.

No Django, no ORM, no push in here: the API (`views.NoticeViewSet`), the scheduler task
(`tasks.publish_due_notices`) and the tests all judge the same rules with the same code, exactly
like `finance_rules.py`. Run the tests with:

    python -m unittest campus.tests_notice_rules      (from backend/backend; no Django needed)
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ---- vocabulary (must match the TextChoices on `models.Notice`) -----------------------------------
KIND_NORMAL = "normal"
KIND_EMERGENCY = "emergency"
KINDS = (KIND_NORMAL, KIND_EMERGENCY)

AUDIENCE_EVERYONE = "everyone"   # students + their parents + staff in scope
AUDIENCE_STUDENTS = "students"
AUDIENCE_PARENTS = "parents"
AUDIENCE_STAFF = "staff"
AUDIENCES = (AUDIENCE_EVERYONE, AUDIENCE_STUDENTS, AUDIENCE_PARENTS, AUDIENCE_STAFF)

STATUS_PUBLISHED = "published"
STATUS_SCHEDULED = "scheduled"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_PUBLISHED, STATUS_SCHEDULED, STATUS_CANCELLED)

# ---- limits ---------------------------------------------------------------------------------------
MIN_SCHEDULE_LEAD = timedelta(minutes=1)    # "in the past / right now" is not a schedule: post it instead
MAX_SCHEDULE_LEAD = timedelta(days=90)      # a typo'd year must not park a notice for years
EMERGENCY_TITLE_MAX = 120                   # has to fit a push banner
EMERGENCY_BODY_MIN = 5
EMERGENCY_PIN_HOURS = 24                    # an emergency stays pinned at the top for a day
EMERGENCY_DUPLICATE_WINDOW = timedelta(minutes=2)
PUSH_BODY_MAX = 200
RECIPIENT_CHUNK = 500                       # users per `bridge.notify` call


class NoticeRuleError(Exception):
    """A business-rule failure with a stable machine `code` (the API turns it into a 400 whose
    `code` the app switches on)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- audience -------------------------------------------------------------------------------------
def audience_flags(audience: str) -> tuple[bool, bool, bool]:
    """-> (students, parents, staff). Unknown value -> treated as EVERYONE, so a bad/old row can
    never silently deliver to nobody."""
    if audience == AUDIENCE_STUDENTS:
        return True, False, False
    if audience == AUDIENCE_PARENTS:
        return False, True, False
    if audience == AUDIENCE_STAFF:
        return False, False, True
    return True, True, True


def hidden_from_non_staff(audience: str) -> bool:
    """A staff-only notice is not shown to students / parents in the notice list either."""
    return audience == AUDIENCE_STAFF


# ---- scheduling -----------------------------------------------------------------------------------
def validate_schedule(scheduled_for: datetime, now: datetime) -> None:
    """Raise NoticeRuleError unless `scheduled_for` is between 1 minute and 90 days from `now`.
    Both must be timezone-aware (or both naive) — the API passes aware datetimes."""
    if scheduled_for < now + MIN_SCHEDULE_LEAD:
        raise NoticeRuleError("schedule_in_past", "Choose a time at least a minute from now.")
    if scheduled_for > now + MAX_SCHEDULE_LEAD:
        raise NoticeRuleError("schedule_too_far", "A notice can be scheduled at most 90 days ahead.")


def is_due(status: str, scheduled_for: datetime | None, now: datetime) -> bool:
    """True when the scheduler should publish this notice now."""
    return status == STATUS_SCHEDULED and scheduled_for is not None and scheduled_for <= now


def can_cancel(status: str) -> bool:
    return status == STATUS_SCHEDULED


def can_edit(status: str) -> bool:
    """Only a notice that has not gone out yet may be edited / re-scheduled."""
    return status == STATUS_SCHEDULED


# ---- emergency ------------------------------------------------------------------------------------
def validate_emergency(title: str, body: str) -> tuple[str, str]:
    """-> (clean title, clean body). An emergency needs a clear short headline and some text."""
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        raise NoticeRuleError("emergency_title_required", "Write a short headline for the alert.")
    if len(title) > EMERGENCY_TITLE_MAX:
        raise NoticeRuleError("emergency_title_long", f"Keep the headline under {EMERGENCY_TITLE_MAX} characters.")
    if len(body) < EMERGENCY_BODY_MIN:
        raise NoticeRuleError("emergency_body_required", "Tell people what to do.")
    return title, body


def is_duplicate_emergency(last_sent_at: datetime | None, now: datetime) -> bool:
    """Same sender + same campus + same headline inside 2 minutes = a double tap, not a new alert."""
    return last_sent_at is not None and now - last_sent_at < EMERGENCY_DUPLICATE_WINDOW


def emergency_pin_until(now: datetime) -> datetime:
    return now + timedelta(hours=EMERGENCY_PIN_HOURS)


# ---- push text ------------------------------------------------------------------------------------
def push_title(kind: str, title: str) -> str:
    return f"🚨 {title}" if kind == KIND_EMERGENCY else title


def push_body(title: str, body: str) -> str:
    return ((body or "").strip() or title)[:PUSH_BODY_MAX]


# ---- batching -------------------------------------------------------------------------------------
def chunked(ids, size: int = RECIPIENT_CHUNK):
    """Yield lists of at most `size` ids (keeps each Celery payload small for a 10k-student school)."""
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]
