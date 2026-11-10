# campus/settings_service.py
"""
CAMPUS SETTINGS — the database side (rules: `campus.settings_rules`, grading maths: `campus.grading`).

Plain functions, no request / response objects. Everything that changes data runs in one transaction and is
audit-logged (`settings.*`). Errors are `SettingsError(code, detail, status)`; the view turns them into the
project's error envelope.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count

from . import grading
from . import settings_rules as rules
from .audit import log_action
from .models import Campus, CampusHoliday, CampusProfile, TimeSlot, TimetableEntry
from .soft_delete import SoftDeleteError


class SettingsError(Exception):
    def __init__(self, code: str, detail: str = "", status: int = 400, errors=None):
        super().__init__(code)
        self.code, self.detail, self.status, self.errors = code, detail or code.replace("_", " "), status, errors


def _fail_value(exc: ValueError, code="invalid"):
    return SettingsError(code, str(exc))


# ---------------------------------------------------------------------------
# 1. profile
# ---------------------------------------------------------------------------
def profile_dict(campus: Campus) -> dict:
    p = CampusProfile.objects.filter(campus=campus).first()
    out = {"name": campus.name, "type": campus.type}
    for f in rules.PROFILE_FIELDS:
        out[f] = getattr(p, f, None) if p else None
        if out[f] is None and f != "established_year":
            out[f] = ""
    out["boards"] = list(rules.BOARDS)
    out["logo_url"] = _logo_url(p)
    return out


def _logo_url(profile) -> str:
    """Relative media URL of the logo, \"\" when there is none (a storage that cannot make a URL is treated as none)."""
    if not profile or not profile.logo:
        return ""
    try:
        return profile.logo.url
    except Exception:  # noqa: BLE001 - a broken storage must never break the profile
        return ""


@transaction.atomic
def save_profile(campus: Campus, user, data, *, this_year: int) -> dict:
    clean, errors = rules.clean_profile(data, this_year=this_year)
    if errors:
        field, message = next(iter(errors.items()))
        raise SettingsError("invalid_profile", message if field == "detail" else f"{field}: {message}", errors=errors)
    if not clean:
        raise SettingsError("nothing_to_save", "Send at least one field.")
    if "name" in clean:
        campus.name = clean.pop("name")
        campus.save(update_fields=["name"])
    if clean:
        profile, _ = CampusProfile.objects.get_or_create(campus=campus)
        for field, value in clean.items():
            setattr(profile, field, value)
        profile.save()
    log_action(campus.id, user, "settings.profile.update", target=campus, summary="School profile updated",
               fields=sorted(list(clean) + (["name"] if "name" in data else [])))
    return profile_dict(campus)


# ---------------------------------------------------------------------------
# 2. grading
# ---------------------------------------------------------------------------
def grading_dict(campus: Campus) -> dict:
    return {
        "system": campus.grading_system,
        "grade_scale": grading.get_scale(campus.grade_scale),
        "pass_percent": float(campus.pass_percent),
        "is_default": not campus.grade_scale,
        "systems": list(grading.SYSTEMS),
        "presets": [
            {"key": key, "system": p["system"], "pass_percent": p["pass_percent"], "bands": p["bands"]}
            for key, p in grading.PRESETS.items()
        ],
    }


def _pass_percent(raw, fallback):
    if raw in (None, ""):
        return fallback
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise SettingsError("invalid_pass_percent", "pass_percent is not a number.")
    if not value.is_finite() or not Decimal("0") <= value <= Decimal("100"):
        raise SettingsError("invalid_pass_percent", "pass_percent must be between 0 and 100.")
    return value.quantize(Decimal("0.01"))


@transaction.atomic
def save_grading(campus: Campus, user, data) -> dict:
    plan, err = rules.clean_grading_request(data, systems=grading.SYSTEMS, presets=grading.PRESETS)
    if err:
        raise SettingsError("invalid_grading", err)
    system, preset = plan["system"], grading.PRESETS.get(plan["preset"]) if plan["preset"] else None

    bands_in = plan["bands"] if plan["bands"] is not None else (preset["bands"] if preset else None)
    pass_in = plan["pass_percent"]
    if pass_in in (None, "") and preset:
        pass_in = preset["pass_percent"]

    if system == grading.SYSTEM_PERCENT and bands_in is None:
        new_bands = campus.grade_scale          # untouched: switching back to letters loses nothing
    else:
        if bands_in is None:
            # same system as before and no bands sent: keep what is stored (or the default)
            bands_in = grading.get_scale(campus.grade_scale)
            if system == grading.SYSTEM_GPA:
                bands_in = [{**b, "points": b.get("points", grading.CBSE_POINTS.get(b["grade"]))} for b in bands_in]
        new_bands, berr = grading.validate_grade_scale(bands_in, require_points=system == grading.SYSTEM_GPA)
        if berr:
            raise SettingsError("invalid_grade_scale", berr)

    campus.grading_system = system
    campus.grade_scale = new_bands
    campus.pass_percent = _pass_percent(pass_in, campus.pass_percent)
    campus.save(update_fields=["grading_system", "grade_scale", "pass_percent"])
    log_action(campus.id, user, "settings.grading.update", target=campus,
               summary=f"Grading set to {system}", system=system, preset=plan["preset"] or "",
               bands=len(new_bands or []), pass_percent=float(campus.pass_percent))
    return grading_dict(campus)


# ---------------------------------------------------------------------------
# 3. working days + calendar
# ---------------------------------------------------------------------------
def working_dict(campus: Campus) -> dict:
    off = sorted(campus.weekly_off_days or [])
    half = sorted(campus.half_day_weekdays or [])
    return {
        "weekly_off_days": off,
        "half_day_weekdays": half,
        "working_weekdays": rules.working_weekdays(off),
        "weekday_names": list(rules.WEEKDAY_NAMES),
    }


@transaction.atomic
def save_working_days(campus: Campus, user, data) -> dict:
    if not isinstance(data, dict):
        raise SettingsError("invalid_working_days", "Send an object.")
    off_in = data["weekly_off_days"] if "weekly_off_days" in data else campus.weekly_off_days
    half_in = data["half_day_weekdays"] if "half_day_weekdays" in data else campus.half_day_weekdays
    try:
        off, half = rules.validate_working_days(off_in, half_in)
    except ValueError as exc:
        raise SettingsError("invalid_working_days", str(exc))
    campus.weekly_off_days, campus.half_day_weekdays = off, half
    campus.save(update_fields=["weekly_off_days", "half_day_weekdays"])
    log_action(campus.id, user, "settings.working_days.update", target=campus,
               summary="Working days updated", weekly_off_days=off, half_day_weekdays=half)
    return working_dict(campus)


def holiday_map(campus_id, start: date, end: date) -> dict:
    return {h.date: h.name for h in CampusHoliday.objects.filter(campus_id=campus_id, date__gte=start, date__lte=end)}


def month_calendar(campus: Campus, year: int, month: int) -> dict:
    import calendar as _cal

    start, end = date(year, month, 1), date(year, month, _cal.monthrange(year, month)[1])
    return rules.month_calendar(
        year, month, campus.weekly_off_days or [], campus.half_day_weekdays or [], holiday_map(campus.id, start, end)
    )


# ---------------------------------------------------------------------------
# 4. holidays (bulk helpers — single add / edit / delete stay on /campus/holidays/)
# ---------------------------------------------------------------------------
def apply_holidays(campus: Campus, user, rows, *, source: str, dry_run: bool = False, errors=None) -> dict:
    """Insert ``[(date, name)]``; a date the campus already has is skipped (never overwritten)."""
    errors = errors or []
    dates = [d for d, _ in rows]
    have = set(CampusHoliday.objects.filter(campus=campus, date__in=dates).values_list("date", flat=True)) if dates else set()
    fresh = [(d, n) for d, n in rows if d not in have]
    skipped = [{"date": d.isoformat(), "name": n} for d, n in rows if d in have]
    if not dry_run and fresh:
        with transaction.atomic():
            CampusHoliday.objects.bulk_create([CampusHoliday(campus=campus, date=d, name=n) for d, n in fresh])
            log_action(campus.id, user, f"settings.holidays.{source}", target=campus,
                       summary=f"Added {len(fresh)} holiday(s) ({source})", added=len(fresh), skipped=len(skipped))
    return {
        "dry_run": dry_run,
        "created": [{"date": d.isoformat(), "name": n} for d, n in fresh],
        "created_count": len(fresh),
        "skipped_existing": skipped,
        "errors": errors,
    }


def copy_year_rows(campus: Campus, from_year: int, to_year: int):
    if from_year == to_year:
        raise SettingsError("same_year", "Pick two different years.")
    src = CampusHoliday.objects.filter(campus=campus, date__year=from_year).order_by("date")
    return [(rules.shift_to_year(h.date, to_year), h.name) for h in src]


# ---------------------------------------------------------------------------
# 5. timetable slots
# ---------------------------------------------------------------------------
def _slot_rows(campus: Campus) -> list:
    used = set(TimetableEntry.objects.filter(time_slot__campus=campus).values_list("time_slot_id", flat=True).distinct())
    return [
        {"id": s.id, "day": s.day_of_week, "start": s.start_time, "end": s.end_time, "label": s.label,
         "in_use": s.id in used}
        for s in TimeSlot.objects.filter(campus=campus).order_by("day_of_week", "start_time")
    ]


def slots_dict(campus: Campus) -> dict:
    rows = _slot_rows(campus)
    days = []
    for d in range(1, 8):
        mine = [r for r in rows if r["day"] == d]
        days.append({
            "day": d, "name": rules.WEEKDAY_NAMES[d - 1], "is_off": d in (campus.weekly_off_days or []),
            "slots": [{"id": str(r["id"]), "label": r["label"], "start_time": r["start"].strftime("%H:%M"),
                       "end_time": r["end"].strftime("%H:%M"), "in_use": r["in_use"]} for r in mine],
        })
    return {"days": days, "total": len(rows)}


def _plan_dict(plan: dict) -> dict:
    return {
        "create": [{"day": c["day"], "label": c["label"], "start_time": c["start"].strftime("%H:%M"),
                    "end_time": c["end"].strftime("%H:%M")} for c in plan["create"]],
        "create_count": len(plan["create"]),
        "delete_count": len(plan["delete"]),
        "already_there": plan["already_there"],
        "conflicts": plan["conflicts"],
    }


def _days_param(campus: Campus, raw) -> list:
    if raw in (None, "", "working"):
        days = rules.working_weekdays(campus.weekly_off_days or [])
    else:
        try:
            days = rules.clean_weekdays(raw, "days")
        except ValueError as exc:
            raise _fail_value(exc)
    if not days:
        raise SettingsError("no_days", "Choose at least one day.")
    return days


def _trash_slots(campus: Campus, user, ids) -> int:
    """Move slots to the trash (restorable) instead of a bulk hard delete. Call inside a transaction."""
    try:
        return TimeSlot.objects.filter(campus=campus, id__in=ids).soft_delete_all(user=user)
    except SoftDeleteError as exc:
        raise SettingsError("delete_refused", exc.message, 409)


def _apply_plan(campus: Campus, user, plan: dict, *, action: str, summary: str):
    with transaction.atomic():
        if plan["delete"]:
            _trash_slots(campus, user, plan["delete"])
        TimeSlot.objects.bulk_create([
            TimeSlot(campus=campus, day_of_week=c["day"], start_time=c["start"], end_time=c["end"], label=c["label"])
            for c in plan["create"]
        ])
        log_action(campus.id, user, action, target=campus, summary=summary,
                   created=len(plan["create"]), deleted=len(plan["delete"]), soft_delete=True)


def generate_slots(campus: Campus, user, data, *, dry_run: bool) -> dict:
    if not isinstance(data, dict):
        raise SettingsError("invalid_slots", "Send an object.")
    try:
        wanted = rules.generate_slots(
            data.get("start_time"), data.get("period_minutes"), data.get("periods"), data.get("breaks"),
            label_prefix=rules._squash(data.get("label_prefix")) or "Period",
        )
    except ValueError as exc:
        raise _fail_value(exc, "invalid_slots")
    days = _days_param(campus, data.get("days"))
    mode = rules._squash(data.get("mode")) or rules.MODE_ADD_MISSING
    try:
        plan = rules.plan_slot_creation(days, wanted, _slot_rows(campus), mode=mode)
    except ValueError as exc:
        raise _fail_value(exc, "invalid_mode")
    if not dry_run and plan["create"]:
        _apply_plan(campus, user, plan, action="settings.time_slots.generate",
                    summary=f"Generated {len(plan['create'])} time slot(s)")
    return {"dry_run": dry_run, **_plan_dict(plan),
            "template": [{"label": w["label"], "start_time": w["start"].strftime("%H:%M"),
                          "end_time": w["end"].strftime("%H:%M")} for w in wanted]}


def copy_day_slots(campus: Campus, user, data, *, dry_run: bool) -> dict:
    if not isinstance(data, dict):
        raise SettingsError("invalid_slots", "Send an object.")
    try:
        (src,) = rules.clean_weekdays([data.get("from_day")], "from_day") or [None]
    except (ValueError, TypeError):
        raise SettingsError("invalid_slots", "from_day: use a weekday 1 (Mon) to 7 (Sun).")
    to_days = [d for d in _days_param(campus, data.get("to_days")) if d != src]
    if not to_days:
        raise SettingsError("no_days", "Choose at least one other day to copy to.")
    rows = _slot_rows(campus)
    wanted = [{"label": r["label"], "start": r["start"], "end": r["end"]} for r in rows if r["day"] == src]
    if not wanted:
        raise SettingsError("empty_day", "That day has no slots to copy.")
    mode = rules._squash(data.get("mode")) or rules.MODE_ADD_MISSING
    try:
        plan = rules.plan_slot_creation(to_days, wanted, rows, mode=mode)
    except ValueError as exc:
        raise _fail_value(exc, "invalid_mode")
    if not dry_run and plan["create"]:
        _apply_plan(campus, user, plan, action="settings.time_slots.copy_day",
                    summary=f"Copied slots of day {src} to {len(to_days)} day(s)")
    return {"dry_run": dry_run, **_plan_dict(plan)}


def clear_slots(campus: Campus, user, data) -> dict:
    """Remove the slots of the chosen days that no timetable entry uses. Used slots are always kept."""
    days = _days_param(campus, (data or {}).get("days") if isinstance(data, dict) else None)
    rows = [r for r in _slot_rows(campus) if r["day"] in days]
    free = [r["id"] for r in rows if not r["in_use"]]
    kept = len(rows) - len(free)
    if free:
        with transaction.atomic():
            _trash_slots(campus, user, free)
            log_action(campus.id, user, "settings.time_slots.clear", target=campus,
                       summary=f"Removed {len(free)} unused time slot(s)", removed=len(free), kept_in_use=kept, soft_delete=True)
    return {"removed": len(free), "kept_in_use": kept}


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
def overview(campus: Campus, *, today: date) -> dict:
    profile = profile_dict(campus)
    slot_counts = dict(
        TimeSlot.objects.filter(campus=campus).order_by().values_list("day_of_week").annotate(n=Count("id")).values_list("day_of_week", "n")
    )
    year_holidays = CampusHoliday.objects.filter(campus=campus, date__year=today.year)
    nxt = CampusHoliday.objects.filter(campus=campus, date__gte=today).order_by("date").first()
    profile_done = bool(profile["board"] and (profile["address_line1"] or profile["city"]) and profile["phone"])
    working = working_dict(campus)
    slots_done = bool(slot_counts) and all(d in slot_counts for d in working["working_weekdays"])
    return {
        "campus": {"id": str(campus.id), "name": campus.name, "type": campus.type},
        "profile": {k: profile[k] for k in ("board", "city", "state", "phone", "email", "affiliation_no")},
        "grading": {"system": campus.grading_system, "bands": len(grading.get_scale(campus.grade_scale)),
                    "pass_percent": float(campus.pass_percent), "is_default": not campus.grade_scale},
        "working_days": working,
        "holidays": {"year": today.year, "count": year_holidays.count(),
                     "next": {"date": nxt.date.isoformat(), "name": nxt.name} if nxt else None},
        "time_slots": {"total": sum(slot_counts.values()), "per_day": {str(k): v for k, v in sorted(slot_counts.items())}},
        "checklist": [
            {"key": "profile", "done": profile_done},
            {"key": "grading", "done": bool(campus.grade_scale) or campus.grading_system != grading.SYSTEM_CBSE},
            {"key": "working_days", "done": bool(campus.weekly_off_days)},
            {"key": "holidays", "done": year_holidays.exists()},
            {"key": "time_slots", "done": slots_done},
        ],
    }


# ---------------------------------------------------------------------------
# school logo (own endpoint: a file cannot travel in the JSON profile save)
# ---------------------------------------------------------------------------
def _delete_file_later(storage, name: str):
    """Delete the old file only after the database change is committed (a rollback must not lose the old logo)."""
    if not name:
        return

    def _go():
        try:
            storage.delete(name)
        except Exception:  # noqa: BLE001 - a leftover file is harmless, the row is already correct
            pass

    transaction.on_commit(_go)


@transaction.atomic
def save_logo(campus: Campus, user, upload) -> dict:
    if upload is None:
        raise SettingsError("invalid_logo", "Choose an image file (field `logo`).")
    head = upload.read(16)
    upload.seek(0)
    ext, err = rules.clean_logo_upload(getattr(upload, "size", 0), head)
    if err:
        raise SettingsError("invalid_logo", err)
    profile, _ = CampusProfile.objects.get_or_create(campus=campus)
    old_name = profile.logo.name if profile.logo else ""
    storage = profile.logo.storage
    profile.logo.save(f"{campus.id}.{ext}", upload, save=True)
    if old_name and old_name != profile.logo.name:
        _delete_file_later(storage, old_name)
    log_action(campus.id, user, "settings.logo.update", target=campus, summary="School logo changed", type=ext)
    return profile_dict(campus)


@transaction.atomic
def remove_logo(campus: Campus, user) -> dict:
    profile = CampusProfile.objects.filter(campus=campus).first()
    if profile and profile.logo:
        old_name, storage = profile.logo.name, profile.logo.storage
        profile.logo = None
        profile.save(update_fields=["logo", "updated_at"])
        _delete_file_later(storage, old_name)
        log_action(campus.id, user, "settings.logo.remove", target=campus, summary="School logo removed")
    return profile_dict(campus)

