# campus/settings_rules.py
"""
CAMPUS SETTINGS — pure rules (no Django, no DB, no clock reads) so every decision is unit-testable with plain
`python -m unittest campus.tests_settings_rules`.

Five areas share this file:

  1. SCHOOL PROFILE    -> `clean_profile`
  2. GRADING           -> `clean_grading_request`   (the bands / points maths lives in `campus/grading.py`)
  3. WORKING DAYS      -> `clean_weekdays`, `validate_working_days`, `day_kind`, `month_calendar`, `count_working`
  4. HOLIDAYS          -> `expand_range`, `clean_holiday_items`, `national_fixed_holidays`, `shift_to_year`
  5. TIMETABLE SLOTS   -> `generate_slots`, `plan_slot_creation`

The DB side lives in `settings_service.py`; HTTP in `settings_views.py`.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta

# ---------------------------------------------------------------------------
# 1. School profile
# ---------------------------------------------------------------------------
BOARDS = ("cbse", "icse", "state", "ib", "cambridge", "university", "other")
MAX_NAME = 200
_PROFILE_TEXT = {  # field -> max length
    "tagline": 120, "affiliation_no": 30, "principal_name": 100,
    "address_line1": 150, "address_line2": 150, "city": 80, "state": 80,
}
PROFILE_FIELDS = (
    "tagline", "board", "affiliation_no", "udise_code", "principal_name", "established_year",
    "address_line1", "address_line2", "city", "state", "pincode", "phone", "email", "website",
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PIN_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z \-]{2,9}$")


def _squash(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def clean_profile(data, *, this_year: int):
    """Validate a (partial) profile payload. Returns ``(clean, errors)``.

    ``clean`` has only the keys that were sent (so a PATCH-style save leaves the rest alone); ``name`` (the campus
    name) is accepted too. ``errors`` is ``{field: message}``; empty means OK. Blank text clears a field."""
    if not isinstance(data, dict):
        return {}, {"detail": "Send an object."}
    clean, errors = {}, {}

    if "name" in data:
        name = _squash(data.get("name"))
        if not name:
            errors["name"] = "Give the school a name."
        elif len(name) > MAX_NAME:
            errors["name"] = f"At most {MAX_NAME} characters."
        else:
            clean["name"] = name

    for field, limit in _PROFILE_TEXT.items():
        if field in data:
            text = _squash(data.get(field))
            if len(text) > limit:
                errors[field] = f"At most {limit} characters."
            else:
                clean[field] = text

    if "board" in data:
        board = _squash(data.get("board")).lower()
        if board and board not in BOARDS:
            errors["board"] = "Pick one of: " + ", ".join(BOARDS) + "."
        else:
            clean["board"] = board

    if "udise_code" in data:
        udise = _squash(data.get("udise_code"))
        if udise and not (udise.isdigit() and len(udise) == 11):
            errors["udise_code"] = "UDISE code is 11 digits."
        else:
            clean["udise_code"] = udise

    if "established_year" in data:
        raw = data.get("established_year")
        if raw in (None, ""):
            clean["established_year"] = None
        else:
            try:
                if isinstance(raw, bool):
                    raise ValueError
                year = int(str(raw).strip())
            except ValueError:
                year = None
            if year is None or not (1800 <= year <= this_year):
                errors["established_year"] = f"Use a year between 1800 and {this_year}."
            else:
                clean["established_year"] = year

    if "pincode" in data:
        pin = _squash(data.get("pincode"))
        if pin and not _PIN_RE.match(pin):
            errors["pincode"] = "Enter a valid PIN / postal code."
        else:
            clean["pincode"] = pin

    if "phone" in data:
        phone = _squash(data.get("phone"))
        digits = re.sub(r"\D", "", phone)
        if phone and (not re.fullmatch(r"\+?[0-9 \-()]+", phone) or not 7 <= len(digits) <= 15):
            errors["phone"] = "Enter a phone number with 7 to 15 digits."
        else:
            clean["phone"] = phone

    if "email" in data:
        email = _squash(data.get("email")).lower()
        if email and (len(email) > 254 or not _EMAIL_RE.match(email)):
            errors["email"] = "Enter a valid email address."
        else:
            clean["email"] = email

    if "website" in data:
        site = _squash(data.get("website"))
        if site and not re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", site):
            site = "https://" + site
        if site and (len(site) > 200 or not re.match(r"^https?://[^\s/]+\.[^\s/]+", site, re.I)):
            errors["website"] = "Enter a valid website address."
        else:
            clean["website"] = site

    return clean, errors


# ---------------------------------------------------------------------------
# 2. Grading — request shape only
# ---------------------------------------------------------------------------
def clean_grading_request(data, *, systems, presets):
    """Pick WHAT the admin asked for. Returns ``(plan, error)``.

    plan = {"system": str, "preset": str|None, "bands": list|None, "pass_percent": raw|None}
    * ``preset`` loads that preset's bands + pass mark + system (the admin can send bands on top: they win).
    * ``percent`` needs no bands (the stored bands stay put, so switching back loses nothing).
    * ``gpa`` needs points on every band — checked by `grading.validate_grade_scale(require_points=True)` later.
    """
    if not isinstance(data, dict):
        return None, "Send an object."
    preset = _squash(data.get("preset")).lower() or None
    if preset and preset not in presets:
        return None, "Unknown preset. Use: " + ", ".join(presets) + "."
    system = _squash(data.get("system")).lower() or (presets[preset]["system"] if preset else "")
    if not system:
        return None, "Choose a grading system."
    if system not in systems:
        return None, "Grading system must be one of: " + ", ".join(systems) + "."
    if preset and presets[preset]["system"] != system:
        return None, "That preset belongs to a different grading system."
    bands = data.get("grade_scale")
    if bands is not None and not isinstance(bands, list):
        return None, "grade_scale must be a list."
    return {"system": system, "preset": preset, "bands": bands, "pass_percent": data.get("pass_percent")}, None


# ---------------------------------------------------------------------------
# 3. Working days
# ---------------------------------------------------------------------------
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def clean_weekdays(value, field="weekdays"):
    """ISO weekdays (Mon=1..Sun=7) -> sorted unique list. Raises ValueError with a readable message."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or any(
        isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 7 for d in value
    ):
        raise ValueError(f"{field}: use a list of weekdays 1 (Mon) to 7 (Sun), e.g. [7].")
    return sorted(set(value))


def validate_working_days(off, half):
    """Returns ``(off, half)`` cleaned, or raises ValueError. Rules: no weekday is both off and half-day, and at
    least one full working day must remain."""
    off = clean_weekdays(off, "weekly_off_days")
    half = clean_weekdays(half, "half_day_weekdays")
    both = sorted(set(off) & set(half))
    if both:
        raise ValueError(f"{WEEKDAY_NAMES[both[0] - 1]} can't be both a weekly off and a half day.")
    if len(set(off) | set(half)) >= 7:
        raise ValueError("At least one day of the week must be a full working day.")
    return off, half


def working_weekdays(off):
    """Weekdays that are not a weekly off (half days included)."""
    off = set(off or [])
    return [d for d in range(1, 8) if d not in off]


KIND_WORKING, KIND_HALF, KIND_OFF, KIND_HOLIDAY = "working", "half_day", "weekly_off", "holiday"
_KIND_VALUE = {KIND_WORKING: 1.0, KIND_HALF: 0.5, KIND_OFF: 0.0, KIND_HOLIDAY: 0.0}


def day_kind(d: date, off, half, holidays) -> str:
    """What `d` is for the campus. A holiday wins over everything; a weekly off wins over a half day.
    ``holidays`` is a mapping/set of dates."""
    if d in holidays:
        return KIND_HOLIDAY
    wd = d.isoweekday()
    if wd in (off or ()):
        return KIND_OFF
    if wd in (half or ()):
        return KIND_HALF
    return KIND_WORKING


def day_value(kind: str) -> float:
    return _KIND_VALUE[kind]


def count_working(start: date, end: date, off, half, holidays) -> float:
    """Working-day value between two dates inclusive (full day 1, half day 0.5). 0 when end < start."""
    total, d = 0.0, start
    while d <= end:
        total += day_value(day_kind(d, off, half, holidays))
        d += timedelta(days=1)
    return total


def month_calendar(year: int, month: int, off, half, holidays: dict):
    """One entry per day of the month + a summary. ``holidays`` maps date -> holiday name."""
    last = calendar.monthrange(year, month)[1]
    days, counts = [], {KIND_WORKING: 0, KIND_HALF: 0, KIND_OFF: 0, KIND_HOLIDAY: 0}
    for n in range(1, last + 1):
        d = date(year, month, n)
        kind = day_kind(d, off, half, holidays)
        counts[kind] += 1
        entry = {"date": d.isoformat(), "weekday": d.isoweekday(), "kind": kind}
        if kind == KIND_HOLIDAY:
            entry["holiday"] = holidays[d]
        days.append(entry)
    return {
        "year": year, "month": month, "days": days,
        "summary": {
            "working_days": counts[KIND_WORKING], "half_days": counts[KIND_HALF],
            "weekly_offs": counts[KIND_OFF], "holidays": counts[KIND_HOLIDAY],
            "working_day_value": counts[KIND_WORKING] + counts[KIND_HALF] * 0.5,
        },
    }


def parse_month(raw, today: date):
    """'2026-10' -> (2026, 10); empty -> the month of ``today``. Raises ValueError."""
    text = _squash(raw)
    if not text:
        return today.year, today.month
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if not m or not 1 <= int(m.group(2)) <= 12 or not 1900 <= int(m.group(1)) <= 2200:
        raise ValueError("Use month=YYYY-MM.")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------
# 4. Holidays
# ---------------------------------------------------------------------------
MAX_RANGE_DAYS = 60
MAX_BULK_ITEMS = 100
MAX_HOLIDAY_NAME = 100

# Only holidays whose date never moves. Festival dates change every year — the admin adds those.
NATIONAL_FIXED = (
    (1, 26, "Republic Day"),
    (8, 15, "Independence Day"),
    (10, 2, "Gandhi Jayanti"),
)


def parse_date(raw, field="date") -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return datetime.strptime(_squash(raw), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field}: use YYYY-MM-DD.")


def clean_holiday_name(raw) -> str:
    name = _squash(raw)
    if not name:
        raise ValueError("Give the holiday a name.")
    if len(name) > MAX_HOLIDAY_NAME:
        raise ValueError(f"The holiday name can be at most {MAX_HOLIDAY_NAME} characters.")
    return name


def expand_range(date_from, date_to, name, *, skip_weekdays=()):
    """Every date from..to inclusive as ``[(date, name)]``. Raises ValueError for a bad / too long range.
    ``skip_weekdays`` (the weekly off) can be dropped so a Diwali break doesn't list the Sundays inside it."""
    start, end = parse_date(date_from, "date_from"), parse_date(date_to, "date_to")
    name = clean_holiday_name(name)
    if end < start:
        raise ValueError("date_to can't be before date_from.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"A holiday range can be at most {MAX_RANGE_DAYS} days.")
    out, d = [], start
    while d <= end:
        if d.isoweekday() not in skip_weekdays:
            out.append((d, name))
        d += timedelta(days=1)
    return out


def clean_holiday_items(items):
    """Bulk list ``[{date, name}]`` -> ``(rows, errors)``. Bad rows are reported by 1-based index and skipped;
    a repeated date keeps its first name. Raises ValueError for a wrong container / too many rows."""
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list.")
    if len(items) > MAX_BULK_ITEMS:
        raise ValueError(f"At most {MAX_BULK_ITEMS} holidays per request.")
    rows, errors, seen = [], [], set()
    for idx, item in enumerate(items, start=1):
        try:
            if not isinstance(item, dict):
                raise ValueError("Each item must be an object.")
            d = parse_date(item.get("date"))
            name = clean_holiday_name(item.get("name"))
        except ValueError as exc:
            errors.append({"index": idx, "message": str(exc)})
            continue
        if d in seen:
            errors.append({"index": idx, "message": f"{d.isoformat()} is listed twice."})
            continue
        seen.add(d)
        rows.append((d, name))
    return rows, errors


def national_fixed_holidays(year: int):
    return [(date(year, m, d), name) for m, d, name in NATIONAL_FIXED]


def shift_to_year(d: date, year: int) -> date:
    """Same month/day in ``year`` (29 Feb -> 28 Feb in a non-leap year)."""
    try:
        return d.replace(year=year)
    except ValueError:
        return date(year, d.month, 28)


def clean_year(raw, field="year") -> int:
    try:
        if isinstance(raw, bool):
            raise ValueError
        year = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field}: use a 4-digit year.")
    if not 1900 <= year <= 2200:
        raise ValueError(f"{field}: use a 4-digit year.")
    return year


# ---------------------------------------------------------------------------
# 5. Timetable slots
# ---------------------------------------------------------------------------
MAX_PERIODS = 14
MIN_PERIOD_MINUTES, MAX_PERIOD_MINUTES = 10, 180
MAX_BREAK_MINUTES = 120


def parse_hhmm(raw, field="start_time") -> time:
    text = _squash(raw)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(3) or 0) > 59:
        raise ValueError(f"{field}: use HH:MM (24 hour).")
    return time(int(m.group(1)), int(m.group(2)))


def _int_in(raw, lo, hi, field) -> int:
    try:
        if isinstance(raw, bool):
            raise ValueError
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number.")
    if not lo <= n <= hi:
        raise ValueError(f"{field} must be between {lo} and {hi}.")
    return n


def generate_slots(start_time, period_minutes, periods, breaks=None, label_prefix="Period"):
    """The day's periods as ``[{"index", "label", "start", "end"}]`` (time objects).

    ``breaks`` = ``[{"after": 3, "minutes": 20}]`` leaves a gap of 20 minutes after period 3 (a gap, not a slot —
    nobody teaches in it). Everything has to finish before midnight. Raises ValueError."""
    start = start_time if isinstance(start_time, time) else parse_hhmm(start_time)
    length = _int_in(period_minutes, MIN_PERIOD_MINUTES, MAX_PERIOD_MINUTES, "period_minutes")
    count = _int_in(periods, 1, MAX_PERIODS, "periods")
    gaps = {}
    for b in breaks or []:
        if not isinstance(b, dict):
            raise ValueError("Each break must be an object like {\"after\": 3, \"minutes\": 20}.")
        after = _int_in(b.get("after"), 1, count - 1 if count > 1 else 1, "break after")
        if after >= count:
            raise ValueError("A break has to come between two periods.")
        if after in gaps:
            raise ValueError(f"Two breaks after period {after}.")
        gaps[after] = _int_in(b.get("minutes"), 1, MAX_BREAK_MINUTES, "break minutes")
    cursor = datetime.combine(date(2000, 1, 1), start)
    out = []
    for i in range(1, count + 1):
        end = cursor + timedelta(minutes=length)
        if end.date() != cursor.date():
            raise ValueError("The periods run past midnight — start earlier or shorten them.")
        out.append({"index": i, "label": f"{label_prefix} {i}", "start": cursor.time(), "end": end.time()})
        cursor = end + timedelta(minutes=gaps.get(i, 0))
        if i < count and cursor.date() != end.date():
            raise ValueError("The periods run past midnight — start earlier or shorten them.")
    return out


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


MODE_ADD_MISSING, MODE_REPLACE_UNUSED = "add_missing", "replace_unused"
SLOT_MODES = (MODE_ADD_MISSING, MODE_REPLACE_UNUSED)


def plan_slot_creation(days, wanted, existing, *, mode=MODE_ADD_MISSING):
    """Decide, per weekday, which slots to create without touching timetable rows by accident.

    days     ISO weekdays to fill
    wanted   ``[{"label","start","end"}]``  (one day's template)
    existing ``[{"id","day","start","end","in_use": bool}]``   slots the campus already has
    mode     add_missing    keep every existing slot; clashing ones block the day
             replace_unused delete existing slots no timetable entry uses, then create (a used slot that clashes
                            still blocks the day)

    Returns ``{"create": [{"day","label","start","end"}], "delete": [id, ...], "already_there": n,
    "conflicts": [{"day","label","message"}]}``. A day with a conflict gets NO new slots (all or nothing)."""
    if mode not in SLOT_MODES:
        raise ValueError("mode must be one of: " + ", ".join(SLOT_MODES) + ".")
    plan = {"create": [], "delete": [], "already_there": 0, "conflicts": []}
    for day in days:
        mine = [e for e in existing if e["day"] == day]
        to_delete = [e for e in mine if mode == MODE_REPLACE_UNUSED and not e["in_use"]]
        keep = [e for e in mine if e not in to_delete]
        day_create, day_conflicts, day_there = [], [], 0
        for slot in wanted:
            same = [e for e in keep if e["start"] == slot["start"] and e["end"] == slot["end"]]
            if same:
                day_there += 1
                continue
            clash = [e for e in keep if overlaps(slot["start"], slot["end"], e["start"], e["end"])]
            if clash:
                c = clash[0]
                day_conflicts.append({
                    "day": day, "label": slot["label"],
                    "message": f"{slot['label']} overlaps an existing slot "
                               f"({c['start'].strftime('%H:%M')}-{c['end'].strftime('%H:%M')})"
                               + (" that is used in the timetable." if c["in_use"] else "."),
                })
                continue
            day_create.append({"day": day, "label": slot["label"], "start": slot["start"], "end": slot["end"]})
        plan["already_there"] += day_there
        if day_conflicts:
            plan["conflicts"].extend(day_conflicts)
            continue
        plan["create"].extend(day_create)
        plan["delete"].extend(e["id"] for e in to_delete if day_create)
    return plan


def slot_summary(slots):
    """``[{"day", ...}]`` -> ``{day: count}`` for the Settings overview."""
    out = {}
    for s in slots:
        out[s["day"]] = out.get(s["day"], 0) + 1
    return out


# ---------------------------------------------------------------------------
# 6. School logo
# ---------------------------------------------------------------------------
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TYPES_TEXT = "PNG, JPG or WEBP"


def sniff_image(head: bytes):
    """`png` / `jpg` / `webp` from the first bytes of a file, else None (the file NAME is never trusted)."""
    head = bytes(head or b"")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def clean_logo_upload(size, head):
    """Returns ``(extension, error)``; ``error`` None means the upload can be stored."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return None, "Choose an image file."
    if size <= 0:
        return None, "The file is empty."
    if size > MAX_LOGO_BYTES:
        return None, f"The logo is too large — at most {MAX_LOGO_BYTES // (1024 * 1024)} MB."
    ext = sniff_image(head)
    if ext is None:
        return None, f"The logo must be a {LOGO_TYPES_TEXT} image."
    return ext, None

