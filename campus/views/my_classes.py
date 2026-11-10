# campus/views/my_classes.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..models import (
    CampusDoubt,
    CampusLiveSession,
    ClassMode,
    Notice,
    StudentEnrollment,
    SubjectTeacherassigments,
    SubstituteAssignment,
    TimetableEntry,
)
from django.db.models import Count
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from ..visibility import get_visibility
from django.utils import timezone


# ============================================================
# T4 §E/§G — "my classes" (student/parent subject-class cards)
# ============================================================
class MyClassesView(APIView):
    """
    `GET /campus/my/classes/[?campus=<id>][&student=<id>]`

    One card per SUBJECT-CLASS — an approved `(section, subject, teacher)`
    — of the caller's own / linked child's CURRENT section(s). Each card:
    subject, teacher, `mode` (that of the next session; `offline` when
    there is none), `next_session` (earliest upcoming live session or
    weekly slot, with the §G rule applied: an OFFLINE one is returned as
    `{"mode":"offline","time_hidden":true,"label":"Offline class"}` and
    NO time/day), `next_online_session` (earliest ONLINE one, with its
    time) and small counts (`notices`, `doubts_open`). Assignments /
    tests / doubts lists use the existing endpoints filtered by the
    card's `section` + `subject`.

    Staff-only callers get an empty list (this is the student's view).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import timedelta

        from ..class_schedule import OFFLINE_LABEL, next_occurrence

        vis = get_visibility(request.user, request)
        section_ids = set(vis.visible_section_ids)
        campus_id = request.query_params.get("campus")
        if campus_id:
            section_ids = {s for s in section_ids if str(vis.section_campus.get(s)) == str(campus_id)}
        student_id = request.query_params.get("student")
        if student_id:
            section_ids &= set(
                StudentEnrollment.objects.filter(
                    student_id=student_id, status=StudentEnrollment.Status.ACTIVE, section_id__in=section_ids
                ).values_list("section_id", flat=True)
            ) if str(student_id) in {str(s) for s in vis.visible_student_ids} else set()
        if not section_ids:
            return Response([])

        now = timezone.now()
        assignments = list(
            SubjectTeacherassigments.objects.filter(
                section_id__in=section_ids, status=SubjectTeacherassigments.Status.APPROVED
            ).select_related("subject", "staff__user", "section__school_class")
            .order_by("section_id", "subject__name")
        )

        # candidate next sessions per (section, subject): (start, kind, mode, extra)
        cands = {}
        for live in CampusLiveSession.objects.filter(
            section_id__in=section_ids,
            status__in=[CampusLiveSession.Status.SCHEDULED, CampusLiveSession.Status.LIVE],
            scheduled_at__gte=now - timedelta(hours=3),
        ).order_by("scheduled_at"):
            cands.setdefault((live.section_id, live.subject_id), []).append(
                (live.scheduled_at, "live_session", live.mode, {"live_session_id": str(live.id), "status": live.status})
            )
        for entry in TimetableEntry.objects.filter(section_id__in=section_ids).select_related("time_slot", "session"):
            start = next_occurrence(entry, now)
            if start:
                cands.setdefault((entry.section_id, entry.subject_id), []).append(
                    (start, "timetable", entry.mode, {"timetable_entry_id": str(entry.id)})
                )

        # Substitute teacher: active covers for the coming week, keyed by
        # (timetable entry, date), so a card can say "Substitute: X" for the
        # next timetable period. Names only — never the absence reason.
        from datetime import timedelta as _td

        from .. import substitutes as _sub_svc

        today = timezone.localdate()
        subs_by_key = {}
        for s in SubstituteAssignment.objects.filter(
            entry__section_id__in=section_ids,
            status=SubstituteAssignment.Status.ACTIVE,
            date__gte=today,
            date__lte=today + _td(days=8),
        ).select_related("substitute__user"):
            subs_by_key[(str(s.entry_id), s.date)] = s

        open_counts = {}
        for sec, sub in CampusDoubt.objects.filter(
            section_id__in=section_ids, is_active=True, author_id__in=vis.visible_student_ids
        ).exclude(status=CampusDoubt.Status.RESOLVED).values_list("section_id", "subject_id"):
            open_counts[(sec, sub)] = open_counts.get((sec, sub), 0) + 1
        notice_counts = dict(
            Notice.objects.filter(section_id__in=section_ids).values_list("section_id").annotate(n=Count("id"))
        )

        def render(c, hide_offline=True):
            start, kind, mode, extra = c
            hidden = mode == ClassMode.OFFLINE
            out = {"kind": kind, "mode": mode, "time_hidden": hidden,
                   "label": OFFLINE_LABEL if hidden else None,
                   "starts_at": None if hidden else start.isoformat()}
            if not hidden:
                out.update(extra)
            return out

        cards = []
        for a in assignments:
            options = sorted(cands.get((a.section_id, a.subject_id), []), key=lambda c: c[0])
            nxt = options[0] if options else None
            nxt_online = next((c for c in options if c[2] == ClassMode.ONLINE), None)
            t_user = a.staff.user
            substitute = None
            if nxt and nxt[1] == "timetable":
                key = (nxt[3]["timetable_entry_id"], timezone.localtime(nxt[0]).date())
                sub_row = subs_by_key.get(key)
                if sub_row is not None:
                    substitute = {
                        "staff_id": str(sub_row.substitute_id),
                        "name": _sub_svc.display_name(sub_row.substitute.user),
                        "date": sub_row.date.isoformat(),
                    }
            cards.append({
                "id": str(a.id),
                "campus": str(vis.section_campus.get(a.section_id)),
                "section": str(a.section_id),
                "section_name": a.section.name,
                "class_name": a.section.school_class.name,
                "subject": str(a.subject_id),
                "subject_name": a.subject.name,
                "teacher": {
                    "staff_id": str(a.staff_id), "user_id": t_user.id,
                    "name": (f"{t_user.first_name} {t_user.last_name}".strip() or t_user.username),
                },
                "substitute": substitute,
                "mode": nxt[2] if nxt else ClassMode.OFFLINE,
                "next_session": render(nxt) if nxt else None,
                "next_online_session": render(nxt_online) if nxt_online else None,
                "counts": {
                    "notices": notice_counts.get(a.section_id, 0),
                    "doubts_open": open_counts.get((a.section_id, a.subject_id), 0),
                },
            })
        return Response(cards)
