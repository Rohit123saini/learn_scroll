# campus/privacy_views.py
"""
PARENT / CHILD PRIVACY API  —  everything under  /campus/privacy/<campus_id>/...   (rules: `campus.privacy_rules`)

ADMIN / PRINCIPAL
    GET  policy/                    safety.view    {enforce_guardian_consent, unknown_dob_is_minor, adult_age, updated_at}
    POST policy/                    safety.manage  {enforce_guardian_consent?: bool, unknown_dob_is_minor?: bool}
    GET  summary/                   safety.view    counts: minors, dob_missing, no_linked_parent, consent_missing / withdrawn / stale, covered
    GET  children/                  safety.view    students to look at (minors + missing date of birth);
                                                   ?issue=consent_missing,... | any  ?age_status=  ?section=  ?school_class=  ?q=
                                                   ?include_adults=1  ?page= ?page_size=
    GET  children/<student_id>/     safety.view    one child with EACH linked parent's answer (the access is audit-logged)
    POST remind/                    safety.manage  push to parents whose child still needs a (new) consent (once per 24 h)

PARENT
    GET  my-children/               every child linked to me: combined decision per purpose + MY OWN answer.
                                    The other parent's name / answer is never returned.

Answers themselves are given with the existing  POST /campus/safety/<campus_id>/consent/me/  (purposes `child_data`,
`photo_media`, `child_ai_features`, with `student`). A minor's own attempt to grant `photo_media` / `ai_features`
is refused with code `guardian_consent_required`.

Errors carry a stable machine `code` in the project's error envelope.
"""
from rest_framework.response import Response

from . import child_privacy as svc
from .audit import log_action
from .safety_views import SafetyAPIError, _campus, _member, _need, _SafetyView


def _student_id(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SafetyAPIError(svc.PrivacyError("invalid_student", 400))


class PrivacyPolicyView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return Response(svc.get_policy(campus))

    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        try:
            return Response(svc.save_policy(campus, request.user, request.data))
        except svc.PrivacyError as exc:
            raise SafetyAPIError(exc)


class PrivacySummaryView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return Response(svc.compliance_summary(campus))


class PrivacyChildrenView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        try:
            return Response(svc.compliance_rows(campus, request.query_params))
        except svc.PrivacyError as exc:
            raise SafetyAPIError(exc)


class PrivacyChildDetailView(_SafetyView):
    def get(self, request, campus_id, student_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        try:
            return Response(svc.child_detail(campus, _student_id(student_id), request.user))
        except svc.PrivacyError as exc:
            raise SafetyAPIError(exc)


class PrivacyRemindView(_SafetyView):
    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        try:
            return Response(svc.remind_parents(campus, request.user))
        except svc.PrivacyError as exc:
            raise SafetyAPIError(exc)


class PrivacyMyChildrenView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _member(request.user, campus)
        return Response(svc.my_children(request.user, campus))
