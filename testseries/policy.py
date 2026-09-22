# testseries/policy.py
"""
Pure business rules for the `testseries` app — deliberately **no Django
imports at module level**, so every rule in here can be unit-tested with
plain `python -m unittest` (see `tests_pure.py`) and can never be broken by
an ORM/import-cycle problem. Models, serializers and views call into this
module; they do not re-implement any of it.

What lives here
---------------
1. Pricing policy   — WHO may charge for a test series (config-driven).
2. Delivery windows — scheduled / live tests: may this student start now?
3. Attempt timing   — deadline, grace, late-submission handling.
4. Grading helpers  — negative marking, "did the student answer at all?".
5. Result release   — instant / after the window ends / manual.
6. Certificates     — pass/fail maths and the public verification code.

Every knob is read from `settings.TESTSERIES_*` (see `LearnScroll/
settings.py`, "TESTSERIES — ADVANCED CONFIG") and falls back to the
defaults below, so an untouched settings file still behaves sensibly.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# 1. PRICING POLICY
# ---------------------------------------------------------------------------
# mode:
#   "required"  -> the series MUST be paid (is_paid=True, price >= min_coins)
#   "optional"  -> creator chooses free or paid (if paid, price in range)
#   "forbidden" -> always free, whatever the client sends
#
# Product rule this encodes (owner's decision):
#   * individual  — a user who creates a test series on their own: PAID
#   * campus      — created from a campus: always FREE for students
#   * liveclass   — created from a live class: FREE by default, teacher MAY
#                   make it paid
DEFAULT_PRICING_POLICY: dict[str, dict[str, Any]] = {
    "individual": {"mode": "required", "min_coins": 1, "max_coins": 100_000},
    "campus": {"mode": "forbidden"},
    "liveclass": {"mode": "optional", "min_coins": 1, "max_coins": 100_000},
}


class PolicyError(ValueError):
    """A business-rule violation. `field` names the API field to attach the
    message to, so serializers can turn it into a field-level 400."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def _settings():
    """Lazy: this module must stay importable without Django configured."""
    try:
        from django.conf import settings  # noqa: WPS433

        return settings
    except Exception:  # pragma: no cover — pure-python test environment
        return None


def _cfg(name: str, default):
    s = _settings()
    try:
        return getattr(s, name, default) if s is not None else default
    except Exception:  # settings not configured (pure unit tests)
        return default


def get_pricing_policy() -> dict[str, dict[str, Any]]:
    """`DEFAULT_PRICING_POLICY` overlaid with `settings.TESTSERIES_PRICING_POLICY`
    (per-source dicts are merged, so a settings override can change just
    `min_coins` without restating `mode`)."""
    merged = {k: dict(v) for k, v in DEFAULT_PRICING_POLICY.items()}
    override = _cfg("TESTSERIES_PRICING_POLICY", None) or {}
    for source, rule in override.items():
        merged.setdefault(source, {}).update(rule)
    return merged


def normalize_pricing(
    *,
    source: str,
    is_paid: bool,
    price_coins: int,
    strict: bool,
    policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[bool, int]:
    """Apply the pricing policy for `source`. Returns `(is_paid, price_coins)`.

    `strict=True`  (serializers / creation entry points / publish): a
                   violation raises `PolicyError` -> clean 400 for the client.
    `strict=False` (`TestSeries.save()` defence-in-depth): never raises;
                   only *coerces* the one thing that must never leak —
                   a `forbidden` source is forced free. Deliberately does
                   NOT force `required` sources to paid here, otherwise every
                   legacy free individual series in the database would start
                   failing on unrelated saves.
    """
    rules = (policy or get_pricing_policy()).get(source) or {"mode": "optional"}
    mode = rules.get("mode", "optional")
    min_coins = int(rules.get("min_coins", 1))
    max_coins = int(rules.get("max_coins", 10**9))
    price_coins = int(price_coins or 0)

    if mode == "forbidden":
        if strict and (is_paid or price_coins):
            raise PolicyError("is_paid", f"A {source} test series is always free.")
        return False, 0

    if not is_paid:
        if strict and mode == "required":
            raise PolicyError(
                "is_paid",
                f"A {source} test series must be paid (minimum {min_coins} coin(s)).",
            )
        if strict and price_coins:
            raise PolicyError("price_coins", "price_coins must be 0 when is_paid is False.")
        return False, 0

    # paid
    if strict:
        if price_coins < min_coins:
            raise PolicyError("price_coins", f"Price must be at least {min_coins} coin(s).")
        if price_coins > max_coins:
            raise PolicyError("price_coins", f"Price cannot exceed {max_coins} coins.")
    return True, price_coins


# ---------------------------------------------------------------------------
# 2. DELIVERY WINDOWS  (self_paced / scheduled / live)
# ---------------------------------------------------------------------------
SELF_PACED, SCHEDULED, LIVE = "self_paced", "scheduled", "live"

# window_state() results
OPEN = "open"
NOT_STARTED = "not_started"
LATE_CLOSED = "late_closed"
ENDED = "ended"


def window_state(
    *,
    mode: str,
    now: datetime,
    starts_at: datetime | None,
    ends_at: datetime | None,
    late_entry_minutes: int = 0,
) -> str:
    """May a student START an attempt right now?

    self_paced : always open.
    scheduled  : open anywhere inside [starts_at, ends_at).
    live       : everyone starts together — open from `starts_at` until
                 `starts_at + late_entry_minutes` (and never past `ends_at`).
    """
    if mode == SELF_PACED or (starts_at is None and ends_at is None):
        return OPEN
    if starts_at is not None and now < starts_at:
        return NOT_STARTED
    if ends_at is not None and now >= ends_at:
        return ENDED
    if mode == LIVE and starts_at is not None:
        if now > starts_at + timedelta(minutes=max(0, int(late_entry_minutes or 0))):
            return LATE_CLOSED
    return OPEN


WINDOW_MESSAGES = {
    NOT_STARTED: "This test has not started yet.",
    LATE_CLOSED: "Late entry for this live test is closed.",
    ENDED: "This test has ended.",
}


# ---------------------------------------------------------------------------
# 3. ATTEMPT TIMING
# ---------------------------------------------------------------------------
def compute_deadline(
    *,
    started_at: datetime,
    duration_minutes: int | None,
    window_end: datetime | None = None,
) -> datetime | None:
    """`started_at + duration`, never later than the series' `ends_at`.
    No duration and no window end = untimed (None)."""
    deadline = started_at + timedelta(minutes=duration_minutes) if duration_minutes else None
    if window_end is not None and (deadline is None or deadline > window_end):
        deadline = window_end
    return deadline


def grace_seconds() -> int:
    return int(_cfg("TESTSERIES_SUBMIT_GRACE_SECONDS", 30))


# What to do with a submit that arrives after `deadline + grace`:
#   "use_draft" (default) — ignore the late payload, grade the last answers
#                           the server already had (autosave). Nobody loses
#                           marks to a flaky network, nobody gains time.
#   "accept"              — grade whatever arrived, but flag `submitted_late`.
#   "reject"              — 400, the auto-submit task will close the attempt.
LATE_POLICIES = ("use_draft", "accept", "reject")


def late_policy() -> str:
    value = _cfg("TESTSERIES_LATE_SUBMIT", "use_draft")
    return value if value in LATE_POLICIES else "use_draft"


def is_past_deadline(*, now: datetime, deadline: datetime | None, grace: int | None = None) -> bool:
    if deadline is None:
        return False
    return now > deadline + timedelta(seconds=grace_seconds() if grace is None else grace)


# ---------------------------------------------------------------------------
# 4. GRADING HELPERS
# ---------------------------------------------------------------------------
def coerce_answer_data(raw: Any) -> dict:
    """A client can send anything under `answers[question_id]`. The shared
    grader does `answer_data.get(...)`, so a string/list would 500 the whole
    submit. Anything that is not a dict is treated as 'no answer'."""
    return raw if isinstance(raw, dict) else {}


def is_answered(question_type: str, answer_data: Mapping[str, Any]) -> bool:
    """Did the student actually attempt this question? (Needed for negative
    marking: leaving a question blank must never be penalised.)"""
    if not isinstance(answer_data, Mapping) or not answer_data:
        return False
    if question_type == "mcq":
        return bool(answer_data.get("option_id"))
    if question_type == "msq":
        return bool(answer_data.get("option_ids"))
    if question_type == "list":
        return bool(answer_data.get("sequence")) or bool(answer_data.get("pairs"))
    if question_type == "text":
        return bool(str(answer_data.get("text", "")).strip())
    return False


def negative_penalty(
    *, question_type: str, is_correct: bool | None, answer_data: Mapping[str, Any], negative_marks: int
) -> int:
    """Marks to deduct: only for an auto-graded question that was answered
    AND wrong. Blank = 0, correct = 0, text (manual) = 0."""
    if question_type == "text" or is_correct is not False:
        return 0
    if not is_answered(question_type, answer_data):
        return 0
    return max(0, int(negative_marks or 0))


def net_score(marks_total: int, penalty_total: int) -> int:
    """Net score is never below zero (fields are PositiveInteger; and a
    student can't finish 'owing' marks)."""
    return max(0, int(marks_total or 0) - int(penalty_total or 0))


# ---------------------------------------------------------------------------
# 5. RESULT RELEASE
# ---------------------------------------------------------------------------
INSTANT, AFTER_END, MANUAL = "instant", "after_end", "manual"


def results_visible(
    *,
    release_mode: str,
    now: datetime,
    ends_at: datetime | None,
    released_at: datetime | None,
) -> bool:
    """May a student see their score / solutions yet?"""
    if release_mode == MANUAL:
        return released_at is not None and released_at <= now
    if release_mode == AFTER_END:
        # No window configured => nothing to wait for.
        return ends_at is None or now >= ends_at
    return True


# ---------------------------------------------------------------------------
# 6. CERTIFICATES
# ---------------------------------------------------------------------------
def percentage(score: int | None, total_marks: int | None) -> float | None:
    if score is None or not total_marks:
        return None
    return round(100.0 * float(score) / float(total_marks), 2)


def has_passed(pct: float | None, pass_percentage: int | None) -> bool | None:
    """None = this series has no pass mark (no pass/fail concept)."""
    if pass_percentage is None or pct is None:
        return None
    return pct + 1e-9 >= float(pass_percentage)


# No 0/O/1/I/L — a certificate code is read aloud and typed by hand.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_certificate_code(groups: int = 3, group_len: int = 4) -> str:
    """e.g. `LS-7K2M-XQ9D-4TBR` — ~60 bits of entropy with the defaults, so
    codes can't be enumerated; the public verify endpoint is also throttled."""
    parts = ["".join(secrets.choice(_CODE_ALPHABET) for _ in range(group_len)) for _ in range(groups)]
    return "LS-" + "-".join(parts)


def generate_share_slug(nbytes: int = 7) -> str:
    """Short, URL-safe public slug for a test series / assignment share link
    (10 chars, ~56 bits). Callers still rely on a UNIQUE constraint + retry;
    this only has to be unguessable-enough and easy to paste."""
    return secrets.token_urlsafe(nbytes).replace("_", "x").replace("-", "y")
