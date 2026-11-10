# campus/settings_views.py
"""
CAMPUS SETTINGS API  —  everything under  /campus/settings/<campus_id>/...
(rules: `campus.settings_rules` · database side: `campus.settings_service` · grading maths: `campus.grading`)

One place for the school's basic setup: school profile, grading, working days, holidays, timetable slots.
READ = any member of the campus (students / parents need the grading scale, working days, holidays).
WRITE = `settings.manage` (campus admin / principal).

OVERVIEW
    GET   overview/                      cards for the Settings hub + a 5-step checklist

SCHOOL PROFILE
    GET   profile/                       name, type, board, affiliation, address, contact
    PUT   profile/                       any of: name, tagline, board, affiliation_no, udise_code, principal_name,
                                         established_year, address_line1/2, city, state, pincode, phone, email, website
                                         (only the keys you send change; "" clears a text field)
    POST  logo/                          multipart, field `logo`: PNG / JPG / WEBP up to 2 MB (type read from the
                                         file's bytes). DELETE logo/ removes it. The profile shows `logo_url`.

GRADING   (CBSE letter grades / percentage only / GPA)
    GET   grading/                       {system, grade_scale, pass_percent, presets: [...]}
    PUT   grading/                       {system?, preset?: cbse|gpa10|gpa4, grade_scale?: [{min_percent, grade, points?}],
                                          pass_percent?}.  percent keeps the stored bands; gpa needs points on every band.

WORKING DAYS
    GET   working-days/                  {weekly_off_days, half_day_weekdays, working_weekdays}     (ISO: Mon=1 .. Sun=7)
    PUT   working-days/                  {weekly_off_days?, half_day_weekdays?}
    GET   calendar/?month=YYYY-MM        every day of the month as working / half_day / weekly_off / holiday + summary

HOLIDAYS  (a single add / edit / delete stays on  /campus/holidays/ ; these are the bulk helpers)
    GET   holidays/?year=                list for one year (default: this year)
    POST  holidays/range/                {date_from, date_to, name, skip_weekly_off?: true, dry_run?}
    POST  holidays/bulk/                 {items: [{date, name}], dry_run?}      up to 100
    POST  holidays/copy-year/            {from_year, to_year, dry_run?}         same dates in another year
    POST  holidays/national/             {year, dry_run?}                       Republic Day, Independence Day, Gandhi Jayanti
                                         A date the campus already has is skipped, never overwritten.

TIMETABLE SLOTS  (a single slot edit stays on  /campus/time-slots/ )
    GET   time-slots/                    the week: per day the slots + whether a timetable entry uses each
    POST  time-slots/generate/           {start_time: "09:00", period_minutes, periods, breaks?: [{after, minutes}],
                                          days?: [1..7] | "working", mode?: add_missing|replace_unused, dry_run?}
    POST  time-slots/copy-day/           {from_day, to_days?, mode?, dry_run?}
    POST  time-slots/clear/              {days?}   removes only slots no timetable entry uses

Errors carry a stable machine `code` (`invalid_profile`, `invalid_grade_scale`, `invalid_working_days`, ...) in the
project's error envelope; for `invalid_profile` the `detail` names the first bad field ("phone: Enter ...").
"""
import uuid

from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import settings_rules as rules
from . import settings_service as svc
from .models import Campus, CampusHoliday
from .permissions import get_campus_role, user_can
from .throttles import CampusSettingsWriteThrottle


class SettingsAPIError(APIException):
    """4xx with a stable machine `code` + a short human `detail` (a profile error names the field: "phone: ...")."""

    status_code = 400

    def __init__(self, err: svc.SettingsError):
        super().__init__(detail=err.detail)
        self.machine_code = err.code
        self.status_code = err.status


def _truthy(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _campus(campus_id) -> Campus:
    try:
        uuid.UUID(str(campus_id))
    except ValueError:
        raise Http404
    try:
        return Campus.objects.get(pk=campus_id)
    except Campus.DoesNotExist:
        raise Http404


class _SettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return [CampusSettingsWriteThrottle()]
        return super().get_throttles()

    def read_campus(self, request, campus_id) -> Campus:
        campus = _campus(campus_id)
        if get_campus_role(request.user, campus.id) is None:
            raise Http404  # not a member: don't reveal the campus exists
        return campus

    def manage_campus(self, request, campus_id) -> Campus:
        campus = self.read_campus(request, campus_id)
        if not user_can(request.user, campus.id, "settings.manage"):
            raise PermissionDenied("Only a campus admin or principal can change the settings.")
        return campus

    def run(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except svc.SettingsError as err:
            raise SettingsAPIError(err)


class SettingsOverviewView(_SettingsView):
    def get(self, request, campus_id):
        campus = self.read_campus(request, campus_id)
        data = svc.overview(campus, today=timezone.localdate())
        data["can_manage"] = user_can(request.user, campus.id, "settings.manage")
        return Response(data)


class SettingsProfileView(_SettingsView):
    def get(self, request, campus_id):
        return Response(svc.profile_dict(self.read_campus(request, campus_id)))

    def put(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        return Response(self.run(svc.save_profile, campus, request.user, request.data,
                                 this_year=timezone.localdate().year))

    patch = put


class SettingsLogoView(_SettingsView):
    """POST (multipart, field `logo`) sets / replaces the school logo; DELETE removes it. Both answer with the
    full profile (`logo_url` is a relative media URL, \"\" when there is none)."""

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        return Response(self.run(svc.save_logo, campus, request.user, request.FILES.get("logo")))

    def delete(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        return Response(self.run(svc.remove_logo, campus, request.user))


class SettingsGradingView(_SettingsView):
    def get(self, request, campus_id):
        return Response(svc.grading_dict(self.read_campus(request, campus_id)))

    def put(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        return Response(self.run(svc.save_grading, campus, request.user, request.data))

    patch = put


class SettingsWorkingDaysView(_SettingsView):
    def get(self, request, campus_id):
        return Response(svc.working_dict(self.read_campus(request, campus_id)))

    def put(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        return Response(self.run(svc.save_working_days, campus, request.user, request.data))

    patch = put


class SettingsCalendarView(_SettingsView):
    def get(self, request, campus_id):
        campus = self.read_campus(request, campus_id)
        try:
            year, month = rules.parse_month(request.query_params.get("month"), timezone.localdate())
        except ValueError as exc:
            raise SettingsAPIError(svc.SettingsError("invalid_month", str(exc)))
        return Response(svc.month_calendar(campus, year, month))


class SettingsHolidayListView(_SettingsView):
    def get(self, request, campus_id):
        campus = self.read_campus(request, campus_id)
        try:
            year = rules.clean_year(request.query_params.get("year") or timezone.localdate().year)
        except ValueError as exc:
            raise SettingsAPIError(svc.SettingsError("invalid_year", str(exc)))
        rows = CampusHoliday.objects.filter(campus=campus, date__year=year).order_by("date")
        return Response({
            "year": year,
            "results": [{"id": str(h.id), "date": h.date.isoformat(), "name": h.name,
                         "weekday": h.date.isoweekday()} for h in rows],
        })


class SettingsHolidayWriteView(_SettingsView):
    """POST holidays/<kind>/ — kind is `range`, `bulk`, `copy-year` or `national`."""

    kind = ""

    def post(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        body = request.data if isinstance(request.data, dict) else {}
        dry = _truthy(body.get("dry_run"))
        errors = []
        try:
            if self.kind == "range":
                skip = campus.weekly_off_days if _truthy(body.get("skip_weekly_off"), True) else ()
                rows = rules.expand_range(body.get("date_from"), body.get("date_to"), body.get("name"),
                                          skip_weekdays=skip or ())
                if not rows:
                    raise ValueError("Every day in that range is a weekly off — nothing to add.")
            elif self.kind == "bulk":
                rows, errors = rules.clean_holiday_items(body.get("items"))
            elif self.kind == "copy-year":
                rows = self.run(svc.copy_year_rows, campus, rules.clean_year(body.get("from_year"), "from_year"),
                                rules.clean_year(body.get("to_year"), "to_year"))
                if not rows:
                    raise ValueError("That year has no holidays to copy.")
            else:  # national
                rows = rules.national_fixed_holidays(rules.clean_year(body.get("year")))
        except ValueError as exc:
            raise SettingsAPIError(svc.SettingsError("invalid_holidays", str(exc)))
        result = svc.apply_holidays(campus, request.user, rows, source=self.kind.replace("-", "_"),
                                    dry_run=dry, errors=errors)
        return Response(result, status=200 if dry or not result["created_count"] else 201)


class SettingsSlotsView(_SettingsView):
    def get(self, request, campus_id):
        return Response(svc.slots_dict(self.read_campus(request, campus_id)))


class SettingsSlotsActionView(_SettingsView):
    """POST time-slots/<action>/ — action is `generate`, `copy-day` or `clear`."""

    action_name = ""

    def post(self, request, campus_id):
        campus = self.manage_campus(request, campus_id)
        body = request.data if isinstance(request.data, dict) else {}
        if self.action_name == "clear":
            return Response(self.run(svc.clear_slots, campus, request.user, body))
        fn = svc.generate_slots if self.action_name == "generate" else svc.copy_day_slots
        dry = _truthy(body.get("dry_run"))
        result = self.run(fn, campus, request.user, body, dry_run=dry)
        return Response(result, status=200 if dry or not result["create_count"] else 201)
