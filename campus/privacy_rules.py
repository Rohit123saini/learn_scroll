# campus/privacy_rules.py
"""
PARENT / CHILD PRIVACY + MINORS' CONSENT — pure rules (no Django, no DB, no clock reads), unit-testable with
plain `python -m unittest campus.tests_privacy_rules`. DB side: `child_privacy.py`; HTTP: `privacy_views.py`.

The ideas (all deliberately simple and visible to the school admin):

  * A student is a MINOR when a date of birth is on file and the age is below ADULT_AGE (18, same as login/age.py).
    No date of birth = "dob_missing": the campus policy decides whether that is treated as a minor
    (default yes — the privacy-safe side — and the admin is asked to fill the date in).
  * Consent about a minor is given by a LINKED PARENT (`child_data`, `photo_media`, `child_ai_features`), never by
    the child. A minor may still WITHDRAW (stop something) on their own; they may not GRANT `photo_media` /
    `ai_features` for themselves.
  * Several parents can be linked to one child. The decision for the child is combined like this: if ANY linked
    parent has withdrawn, it is withdrawn (stopping is always honoured); else granted when at least one parent
    granted under the CURRENT policy text; a grant under an older text is "stale"; nobody answered = "missing".
  * Core school records (attendance, results, fees) do NOT depend on these consents; they gate optional
    processing only (see `feature_allowed`).
"""
from __future__ import annotations

from datetime import date

ADULT_AGE = 18

AGE_MINOR = "minor"
AGE_ADULT = "adult"
AGE_DOB_MISSING = "dob_missing"
AGE_STATUSES = (AGE_MINOR, AGE_ADULT, AGE_DOB_MISSING)

# ---- purposes (keys of safety_rules.CONSENT_PURPOSES) ----
PURPOSE_CHILD_DATA = "child_data"
PURPOSE_PHOTO = "photo_media"
PURPOSE_AI = "ai_features"
PURPOSE_CHILD_AI = "child_ai_features"

# guardian-given purposes tracked per child (order = order shown in the app)
GUARDIAN_PURPOSES = (PURPOSE_CHILD_DATA, PURPOSE_PHOTO, PURPOSE_CHILD_AI)
# the one a minor MUST have for the campus to be "covered"
REQUIRED_GUARDIAN_PURPOSE = PURPOSE_CHILD_DATA

# feature purpose (what the code asks about) -> the consent that decides it for a MINOR
FEATURE_TO_GUARDIAN = {PURPOSE_PHOTO: PURPOSE_PHOTO, PURPOSE_AI: PURPOSE_CHILD_AI}
# a minor cannot GRANT these for themselves (withdrawing is always allowed)
MINOR_SELF_GRANT_BLOCKED = (PURPOSE_PHOTO, PURPOSE_AI)

# ---- combined decision across linked parents ----
DEC_GRANTED = "granted"
DEC_WITHDRAWN = "withdrawn"
DEC_STALE = "stale"      # granted, but under an older policy text -> ask again
DEC_MISSING = "missing"  # nobody linked has answered
DECISIONS = (DEC_GRANTED, DEC_WITHDRAWN, DEC_STALE, DEC_MISSING)

# ---- compliance issues shown to the admin ----
ISSUE_DOB_MISSING = "dob_missing"
ISSUE_NO_PARENT = "no_linked_parent"
ISSUE_CONSENT_MISSING = "consent_missing"
ISSUE_CONSENT_WITHDRAWN = "consent_withdrawn"
ISSUE_CONSENT_STALE = "consent_stale"
ISSUES = (ISSUE_DOB_MISSING, ISSUE_NO_PARENT, ISSUE_CONSENT_MISSING, ISSUE_CONSENT_WITHDRAWN, ISSUE_CONSENT_STALE)

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100
REMINDER_COOLDOWN_HOURS = 24


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def age_status(dob: date | None, today: date, adult_age: int = ADULT_AGE) -> str:
    if dob is None:
        return AGE_DOB_MISSING
    return AGE_MINOR if age_on(dob, today) < adult_age else AGE_ADULT


def treated_as_minor(status: str, unknown_is_minor: bool) -> bool:
    """Minor for every privacy decision? (A missing date of birth follows the campus policy.)"""
    return status == AGE_MINOR or (status == AGE_DOB_MISSING and bool(unknown_is_minor))


def resolve_guardian(decisions, current_version: str) -> str:
    """
    decisions: one item per LINKED parent — `{"status": "granted"|"withdrawn", "version": str}` or None when that
    parent never answered. -> one of DECISIONS (see the module docstring for the combining rule).
    """
    rows = [d for d in decisions if d]
    if any(d.get("status") == "withdrawn" for d in rows):
        return DEC_WITHDRAWN
    granted = [d for d in rows if d.get("status") == "granted"]
    if any((d.get("version") or "") == (current_version or "") for d in granted):
        return DEC_GRANTED
    if granted:
        return DEC_STALE
    return DEC_MISSING


def child_issues(status: str, unknown_is_minor: bool, parent_count: int, required_decision: str) -> list:
    """What is wrong with this student's privacy paperwork. [] for an adult, or a fully covered minor."""
    if status == AGE_ADULT:
        return []
    issues = []
    if status == AGE_DOB_MISSING:
        issues.append(ISSUE_DOB_MISSING)
    if not treated_as_minor(status, unknown_is_minor):
        return issues
    if parent_count == 0:
        issues.append(ISSUE_NO_PARENT)
        return issues  # nobody can give consent yet; the other codes would only repeat that
    if required_decision == DEC_WITHDRAWN:
        issues.append(ISSUE_CONSENT_WITHDRAWN)
    elif required_decision == DEC_STALE:
        issues.append(ISSUE_CONSENT_STALE)
    elif required_decision == DEC_MISSING:
        issues.append(ISSUE_CONSENT_MISSING)
    return issues


def minor_may_not_grant(subject_status: str, purpose: str, granted: bool) -> bool:
    """True when a MINOR is trying to grant, for themselves, something only a parent may grant."""
    return subject_status == AGE_MINOR and granted is True and purpose in MINOR_SELF_GRANT_BLOCKED


def feature_allowed(enforce: bool, minor: bool, guardian_decision: str, own_status) -> bool:
    """
    May an OPTIONAL feature (photo / media sharing, AI) use this student's data?
    enforce False -> the campus has not switched the rule on: always True (nothing changes for existing campuses).
    minor         -> only when the linked parents' combined decision is "granted".
    adult         -> only when the person's own latest answer is "granted".
    """
    if not enforce:
        return True
    if minor:
        return guardian_decision == DEC_GRANTED
    return own_status == "granted"


def clean_page(params) -> tuple[int, int]:
    """`page` / `page_size` query values -> (page >= 1, 1 <= page_size <= PAGE_SIZE_MAX). Junk falls back to defaults."""
    def _int(raw, default):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    page = max(1, _int(params.get("page"), 1))
    size = _int(params.get("page_size"), PAGE_SIZE_DEFAULT)
    return page, min(max(1, size), PAGE_SIZE_MAX)


def clean_issue_filter(raw) -> list:
    """`issue=consent_missing,no_linked_parent` or `issue=any` -> list of valid codes ([] = no filter, ["any"] = any issue).
    Raises ValueError for an unknown code."""
    text = str(raw or "").strip()
    if not text:
        return []
    if text == "any":
        return ["any"]
    codes = [c for c in (p.strip() for p in text.split(",")) if c]
    bad = [c for c in codes if c not in ISSUES]
    if bad:
        raise ValueError("invalid_issue")
    return codes
