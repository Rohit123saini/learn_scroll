"""
user_profile/role_settings.py - S12: the data rules behind the role-aware settings.

  * Teacher live-class defaults  (TeacherLiveDefaults)   - what a NEW classroom starts with.
  * Parent -> child daily limit  (ParentChildLimit)      - a parent of a linked CHILD (under 18) sets the child's daily limit.

Who sees which section is decided by `core/capabilities.py` (GET /core/me/capabilities/). That is a UI hint only: every rule
below is enforced again here, on the server, on every call.

Child-limit rules (all server side):
  * the parent must have a `campus.CampusParentLink` to that child - anything else answers 404 (never reveals whether the
    child exists);
  * the child must be a KNOWN minor (date of birth on file, under 18). Adults and unknown ages answer 403 - a parent cannot
    put limits on an adult's account;
  * limit = 5 .. 1440 minutes, or null / 0 to remove this parent's limit;
  * while a limit is set the child can only choose an equal or LOWER limit (`ActivityView.patch` asks `parent_limit_floor`).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from common import minor_policy  # P-F16

from .models import DailyUsage, ParentChildLimit, TeacherLiveDefaults, UserPreference

logger = logging.getLogger(__name__)

TEACHER_BOOL_FIELDS = (
    "chat_enabled",
    "recording_enabled",
    "recording_auto",
    "whiteboard_enabled",
    "screen_share_enabled",
    "parents_enabled",
)
MODEL_ATTENDANCE_MIN_PERCENT = 50  # Classroom.attendance_min_percent default
MODEL_ATTENDANCE_EDIT_DAYS = 7  # Classroom.attendance_edit_days default
MIN_CHILD_LIMIT = 5
MAX_CHILD_LIMIT = 1440


# ---------------------------------------------------------------------------
# teacher defaults
# ---------------------------------------------------------------------------
def get_teacher_defaults(user) -> TeacherLiveDefaults:
    obj, _ = TeacherLiveDefaults.objects.get_or_create(user=user)
    return obj


def serialize_teacher_defaults(obj: TeacherLiveDefaults) -> dict:
    data = {f: bool(getattr(obj, f)) for f in TEACHER_BOOL_FIELDS}
    data["attendance_min_percent"] = int(obj.attendance_min_percent)
    data["attendance_edit_days"] = int(obj.attendance_edit_days)
    return data


def _as_bool(value):
    if isinstance(value, bool):
        return value
    raise ValueError("Must be true or false.")


def _as_int(value, lo, hi):
    if isinstance(value, bool):
        raise ValueError("Must be a whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("Must be a whole number.")
    if number < lo or number > hi:
        raise ValueError(f"Must be between {lo} and {hi}.")
    return number


def update_teacher_defaults(user, payload) -> tuple:
    """PATCH semantics. Returns (obj, errors). Nothing is saved when any field is invalid."""
    if not isinstance(payload, dict):
        return get_teacher_defaults(user), {"non_field_errors": ["Send a JSON object."]}
    errors = {}
    clean = {}
    for name in TEACHER_BOOL_FIELDS:
        if name in payload:
            try:
                clean[name] = _as_bool(payload[name])
            except ValueError as exc:
                errors[name] = [str(exc)]
    for name, lo, hi in (("attendance_min_percent", 1, 100), ("attendance_edit_days", 0, 60)):
        if name in payload:
            try:
                clean[name] = _as_int(payload[name], lo, hi)
            except ValueError as exc:
                errors[name] = [str(exc)]
    if errors:
        return get_teacher_defaults(user), errors

    obj = get_teacher_defaults(user)
    for name, value in clean.items():
        setattr(obj, name, value)
    # same rule the classroom itself enforces: auto-recording needs recording
    if not obj.recording_enabled:
        obj.recording_auto = False
    obj.save()
    return obj, {}


def apply_attendance_defaults(sender, instance, created, **kwargs):
    """post_save(Classroom): a NEW classroom whose attendance knobs are still the model defaults takes the teacher's own
    defaults. Never raises (a failed default must not fail creating a classroom), never touches an existing classroom."""
    if not created:
        return
    try:
        if (
            instance.attendance_min_percent != MODEL_ATTENDANCE_MIN_PERCENT
            or instance.attendance_edit_days != MODEL_ATTENDANCE_EDIT_DAYS
        ):
            return
        defaults = TeacherLiveDefaults.objects.filter(user_id=instance.teacher_id).first()
        if defaults is None:
            return
        if (
            defaults.attendance_min_percent == MODEL_ATTENDANCE_MIN_PERCENT
            and defaults.attendance_edit_days == MODEL_ATTENDANCE_EDIT_DAYS
        ):
            return
        type(instance).objects.filter(pk=instance.pk).update(
            attendance_min_percent=defaults.attendance_min_percent,
            attendance_edit_days=defaults.attendance_edit_days,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("S12 apply_attendance_defaults failed")


def connect():
    from django.db.models.signals import post_save

    from tuitionclass.models import Classroom

    post_save.connect(apply_attendance_defaults, sender=Classroom, dispatch_uid="s12_apply_attendance_defaults")


# ---------------------------------------------------------------------------
# parent -> child daily limit
# ---------------------------------------------------------------------------
class ChildLimitError(Exception):
    def __init__(self, code, message, http_status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def parent_limit_floor(child):
    """Smallest daily limit (minutes) any parent set on this child, or None."""
    rows = list(ParentChildLimit.objects.filter(child=child).values_list("daily_limit_minutes", flat=True))
    return min(rows) if rows else None


def _display_name(user) -> str:
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def _today_seconds(child_id) -> int:
    row = DailyUsage.objects.filter(user_id=child_id, date=timezone.localdate()).values_list("seconds", flat=True).first()
    return int(row or 0)


def _child_row(link, parent_limits: dict, prefs: dict, request=None) -> dict:
    child = link.student
    photo = ""
    if getattr(child, "profile_photo", None):
        try:
            photo = child.profile_photo.url
            if request is not None:
                photo = request.build_absolute_uri(photo)
        except Exception:
            photo = ""
    is_minor = bool(child.is_minor)
    return {
        "id": child.pk,
        "username": child.username,
        "name": _display_name(child),
        "photo": photo,
        "campus_id": str(link.campus_id),
        "campus_name": getattr(link.campus, "name", "") or "",
        "is_minor": is_minor,
        "age_known": child.date_of_birth is not None,
        # can this parent change anything? (server checks again on PATCH)
        "can_set_limit": is_minor,
        "my_limit_minutes": parent_limits.get(child.pk),
        "effective_limit_minutes": prefs.get(child.pk),
        "today_minutes": _today_seconds(child.pk) // 60,
        # P-F16: what the minor-privacy rules do for this child (read only; the server enforces them, a parent cannot loosen them)
        "privacy_rules": minor_policy.policy_for(child)["rules"],
    }


def child_privacy(parent, child_id) -> dict:
    """P-F16 - read-only view for a LINKED parent: the child's effective privacy (minor floors applied) + the minor rules."""
    from . import privacy as privacy_rules

    link = _linked_child(parent, child_id)
    child = link.student
    stored = privacy_rules.get_settings(child)
    effective = privacy_rules.apply_minor_floor(stored) if minor_policy.is_minor(child) else dict(stored)
    keys = privacy_rules.AUDIENCE_FIELDS + privacy_rules.BOOLEAN_FIELDS
    return {
        "child_id": child.pk,
        "is_private_account": bool(child.is_private),
        "settings": {k: effective.get(k) for k in keys},
        "locked_fields": list(privacy_rules.MINOR_LOCKED_FIELDS) if minor_policy.is_minor(child) else [],
        **minor_policy.policy_for(child),
    }


def list_children(parent, request=None) -> list:
    from campus.models import CampusParentLink

    links = list(CampusParentLink.objects.filter(parent=parent).select_related("student", "campus").order_by("student__username", "campus__name"))
    child_ids = {l.student_id for l in links}
    parent_limits = dict(ParentChildLimit.objects.filter(parent=parent, child_id__in=child_ids).values_list("child_id", "daily_limit_minutes"))
    prefs = dict(UserPreference.objects.filter(user_id__in=child_ids).values_list("user_id", "daily_limit_minutes"))
    seen, rows = set(), []
    for link in links:  # one row per child even when linked through two campuses
        if link.student_id in seen:
            continue
        seen.add(link.student_id)
        rows.append(_child_row(link, parent_limits, prefs, request))
    return rows


def _linked_child(parent, child_id):
    from campus.models import CampusParentLink

    link = CampusParentLink.objects.filter(parent=parent, student_id=child_id).select_related("student", "campus").first()
    if link is None:
        raise ChildLimitError("not_linked", "This child is not linked to your account.", 404)
    return link


def set_child_limit(parent, child_id, minutes, request=None) -> dict:
    """minutes: int 5..1440 sets / changes this parent's limit; None or 0 removes it. Returns the child's row."""
    link = _linked_child(parent, child_id)
    child = link.student
    if not child.is_minor:
        raise ChildLimitError(
            "child_not_minor",
            "Limits can only be set on an account that is known to be under 18.",
            403,
        )
    if minutes in (None, 0):
        minutes = None
    else:
        if isinstance(minutes, bool):
            raise ChildLimitError("invalid_limit", "Choose a number of minutes.", 400)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ChildLimitError("invalid_limit", "Choose a number of minutes.", 400)
        if minutes < MIN_CHILD_LIMIT or minutes > MAX_CHILD_LIMIT:
            raise ChildLimitError(
                "invalid_limit",
                f"Choose between {MIN_CHILD_LIMIT} and {MAX_CHILD_LIMIT} minutes, or remove the limit.",
                400,
            )

    with transaction.atomic():
        if minutes is None:
            ParentChildLimit.objects.filter(parent=parent, child=child).delete()
        else:
            ParentChildLimit.objects.update_or_create(parent=parent, child=child, defaults={"daily_limit_minutes": minutes})
        floor = parent_limit_floor(child)
        pref = UserPreference.for_user(child)
        # the child's own value is only ever pulled DOWN to the floor here; removing a limit never changes what the child chose
        if floor is not None and (pref.daily_limit_minutes is None or pref.daily_limit_minutes > floor):
            pref.daily_limit_minutes = floor
            pref.save(update_fields=["daily_limit_minutes", "updated_at"])
        # a changed limit re-arms today's one-per-day nudge (same as the child's own PATCH)
        DailyUsage.objects.filter(user=child, date=timezone.localdate()).update(limit_notified=False)

    parent_limits = dict(ParentChildLimit.objects.filter(parent=parent, child=child).values_list("child_id", "daily_limit_minutes"))
    prefs = dict(UserPreference.objects.filter(user=child).values_list("user_id", "daily_limit_minutes"))
    return _child_row(link, parent_limits, prefs, request)
