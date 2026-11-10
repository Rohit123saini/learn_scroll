"""
Marks -> grade (+ grade point). Pure Python, no Django imports (standalone testable).

Grade store NAHI hota: marks se turant nikalta hai, taaki campus scale badle
to purane grade stale na rahein.

Scale = list of ``{"min_percent": number, "grade": str, "points"?: number}`` — highest band
pehle ya kisi bhi order me; hum khud sort karte hain. ``percent >= min_percent``
wali sabse oonchi band jeet-ti hai. Sabse neeche ek ``min_percent = 0`` band
honi chahiye (validate_grade_scale enforce karta hai). ``points`` (0-10) sirf GPA ke liye hai.

GRADING SYSTEM (per campus, `Campus.grading_system`):
    cbse     letter grades from the bands (A1 ... E)                      — default
    percent  percentage only: no letter grade anywhere (`grade_for_percent` -> None)
    gpa      letter grade + grade point per band; GPA = average of subject points

Call sites do NOT read `campus.grade_scale` directly any more — they call `campus_scale(campus)`, which
hands back the stored bands, or `PERCENT_ONLY` when the campus shows percentages only.
"""
from decimal import Decimal, InvalidOperation

SYSTEM_CBSE = "cbse"
SYSTEM_PERCENT = "percent"
SYSTEM_GPA = "gpa"
SYSTEMS = (SYSTEM_CBSE, SYSTEM_PERCENT, SYSTEM_GPA)

# CBSE-style default.
DEFAULT_GRADE_SCALE = [
    {"min_percent": 91, "grade": "A1"},
    {"min_percent": 81, "grade": "A2"},
    {"min_percent": 71, "grade": "B1"},
    {"min_percent": 61, "grade": "B2"},
    {"min_percent": 51, "grade": "C1"},
    {"min_percent": 41, "grade": "C2"},
    {"min_percent": 33, "grade": "D"},
    {"min_percent": 0, "grade": "E"},
]
# CBSE 9-point grade points. Used when a CBSE band has no `points` of its own.
CBSE_POINTS = {"A1": 10, "A2": 9, "B1": 8, "B2": 7, "C1": 6, "C2": 5, "D": 4, "E": 0}
DEFAULT_PASS_PERCENT = Decimal("33")
MAX_BANDS = 20
MAX_POINTS = 10


class _PercentOnly(tuple):
    """Marker handed to the grade functions when a campus shows percentages only."""

    def __repr__(self):  # pragma: no cover
        return "PERCENT_ONLY"


PERCENT_ONLY = _PercentOnly()


def _band(min_percent, grade, points=None):
    b = {"min_percent": min_percent, "grade": grade}
    if points is not None:
        b["points"] = points
    return b


# Starting points the admin can pick and then edit. (Universities differ — these are common defaults,
# not an official table.)
PRESETS = {
    "cbse": {
        "system": SYSTEM_CBSE, "pass_percent": 33,
        "bands": [_band(91, "A1", 10), _band(81, "A2", 9), _band(71, "B1", 8), _band(61, "B2", 7),
                  _band(51, "C1", 6), _band(41, "C2", 5), _band(33, "D", 4), _band(0, "E", 0)],
    },
    "gpa10": {
        "system": SYSTEM_GPA, "pass_percent": 40,
        "bands": [_band(90, "O", 10), _band(80, "A+", 9), _band(70, "A", 8), _band(60, "B+", 7),
                  _band(50, "B", 6), _band(40, "C", 5), _band(0, "F", 0)],
    },
    "gpa4": {
        "system": SYSTEM_GPA, "pass_percent": 60,
        "bands": [_band(90, "A", 4), _band(80, "B", 3), _band(70, "C", 2), _band(60, "D", 1), _band(0, "F", 0)],
    },
}


def _num(value):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d.is_finite() else None


def _plain(d: Decimal):
    return float(d) if d % 1 else int(d)


def validate_grade_scale(scale, *, require_points=False):
    """Returns ``(clean_scale, error)``; ``error`` None means OK.
    ``clean_scale`` highest-first, ``min_percent`` as float-able numbers.
    ``points`` is optional per band; with ``require_points`` (GPA) every band needs it."""
    if not isinstance(scale, (list, tuple)) or not scale:
        return None, "Grade scale must be a non-empty list."
    if len(scale) > MAX_BANDS:
        return None, f"At most {MAX_BANDS} grade bands."
    clean, seen_min, seen_grade = [], set(), set()
    for band in scale:
        if not isinstance(band, dict):
            return None, "Each band must be an object."
        mp = _num(band.get("min_percent"))
        grade = str(band.get("grade") or "").strip()
        if mp is None or mp < 0 or mp > 100:
            return None, "min_percent must be a number between 0 and 100."
        if not grade or len(grade) > 10:
            return None, "Each band needs a grade label (max 10 characters)."
        if mp in seen_min:
            return None, "Two bands have the same min_percent."
        if grade.lower() in seen_grade:
            return None, "Two bands have the same grade label."
        raw_points = band.get("points")
        points = None
        if raw_points not in (None, ""):
            p = _num(raw_points)
            if p is None or p < 0 or p > MAX_POINTS:
                return None, f"points must be a number between 0 and {MAX_POINTS}."
            points = _plain(p.quantize(Decimal("0.01")))
        elif require_points:
            return None, f"Band {grade} needs grade points (GPA)."
        seen_min.add(mp)
        seen_grade.add(grade.lower())
        entry = {"min_percent": _plain(mp), "grade": grade}
        if points is not None:
            entry["points"] = points
        clean.append(entry)
    if 0 not in seen_min:
        return None, "The lowest band must start at 0."
    clean.sort(key=lambda b: b["min_percent"], reverse=True)
    if require_points:
        pts = [b["points"] for b in clean]
        if any(pts[i] < pts[i + 1] for i in range(len(pts) - 1)):
            return None, "A higher band can't have fewer grade points than a lower one."
    return clean, None


def get_scale(scale):
    """Stored scale agar valid hai to wahi, warna default (kabhi raise nahi)."""
    if scale is PERCENT_ONLY:
        return []
    clean, err = validate_grade_scale(scale) if scale else (None, "empty")
    return clean if not err else list(DEFAULT_GRADE_SCALE)


def campus_scale(campus):
    """What every grade call site passes as `scale`: the campus bands, or PERCENT_ONLY."""
    if getattr(campus, "grading_system", SYSTEM_CBSE) == SYSTEM_PERCENT:
        return PERCENT_ONLY
    return campus.grade_scale


def uses_points(campus) -> bool:
    return getattr(campus, "grading_system", SYSTEM_CBSE) == SYSTEM_GPA


def percent_of(obtained, max_marks):
    o, m = _num(obtained), _num(max_marks)
    if o is None or m is None or m <= 0:
        return None
    return o * 100 / m


def _band_for(percent, scale):
    p = _num(percent) if percent is not None else None
    if p is None or scale is PERCENT_ONLY:
        return None
    bands = get_scale(scale)
    for band in bands:  # highest first
        if p >= Decimal(str(band["min_percent"])):
            return band
    return bands[-1]


def grade_for_percent(percent, scale=None):
    """``None`` percent -> ``None`` grade. A percentage-only campus never has a grade."""
    band = _band_for(percent, scale)
    return band["grade"] if band else None


def grade_for_marks(obtained, max_marks, scale=None):
    return grade_for_percent(percent_of(obtained, max_marks), scale)


def grade_point_for_percent(percent, scale=None):
    """Grade point of the band `percent` falls in: the band's own `points`, else the CBSE table by label."""
    band = _band_for(percent, scale)
    if band is None:
        return None
    if band.get("points") is not None:
        return band["points"]
    return CBSE_POINTS.get(band["grade"])


def gpa_of(percents, scale=None):
    """Average grade point of the given subject percentages (None entries ignored). None when nothing counts."""
    points = [grade_point_for_percent(p, scale) for p in percents]
    points = [Decimal(str(x)) for x in points if x is not None]
    if not points:
        return None
    return float((sum(points) / len(points)).quantize(Decimal("0.01")))


def is_pass(percent, pass_percent=None):
    p = _num(percent) if percent is not None else None
    if p is None:
        return False
    pp = _num(pass_percent) if pass_percent is not None else None
    if pp is None:
        pp = DEFAULT_PASS_PERCENT
    return p >= pp
