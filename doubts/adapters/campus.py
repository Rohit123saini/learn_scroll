"""campus adapter — campus.CampusDoubt (+ CampusDoubtReply).

Visibility is the most intricate of the five (author / linked parent / that
subject's approved teachers / class-teacher / campus admin / opt-in public
to classmates / block lists). Rather than copy those rules, we run the
existing `CampusDoubtViewSet.get_queryset()` against a stub request, so
there is exactly ONE definition of "who sees a campus doubt"."""
from __future__ import annotations

from django.db.models import Q
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from .. import hub
from ..dispatch import call_view
from .base import BaseAdapter, user_payload


def _stub_request(user):
    req = Request(APIRequestFactory().get("/campus/doubts/"))
    req.user = user
    return req


class CampusAdapter(BaseAdapter):
    source = hub.SOURCE_CAMPUS
    label = "Campus"
    supported_actions = frozenset({hub.ACTION_ANSWER, hub.ACTION_RESOLVE, hub.ACTION_REOPEN})

    def build_ctx(self, user, request=None):
        from campus.models import ClassTeacherassigments, StaffProfile

        admin_campuses = set(
            StaffProfile.objects.filter(
                user=user, is_active=True,
                role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD],
            ).values_list("campus_id", flat=True)
        )
        class_teacher_sections = set(
            ClassTeacherassigments.objects.filter(staff__user=user, staff__is_active=True)
            .values_list("section_id", flat=True)
        )
        return {"user": user, "request": request,
                "admin_campuses": admin_campuses, "ct_sections": class_teacher_sections}

    def scoped_queryset(self, user, ctx):
        from campus.views import CampusDoubtViewSet

        viewset = CampusDoubtViewSet()
        viewset.request = _stub_request(user)   # no query params => no extra filters
        viewset.action = "list"
        viewset.kwargs = {}
        viewset.format_kwarg = None
        return viewset.get_queryset().select_related("section", "subject")

    def status_q(self):
        return {
            hub.STATUS_OPEN: Q(status="open"),
            hub.STATUS_ANSWERED: Q(status="answered"),
            hub.STATUS_RESOLVED: Q(status="resolved"),
        }

    def _staff_q(self, ctx):
        # `_teaches` is annotated by CampusDoubtViewSet.get_queryset().
        return Q(_teaches=True) | Q(campus_id__in=ctx["admin_campuses"]) | Q(section_id__in=ctx["ct_sections"])

    def to_answer_q(self, ctx):
        return Q(status="open") & self._staff_q(ctx) & ~Q(author_id=ctx["user"].pk)

    def parent_filter_field(self):
        return "section_id"

    def _is_staff(self, obj, ctx):
        return bool(getattr(obj, "_teaches", False) or obj.campus_id in ctx["admin_campuses"]
                    or obj.section_id in ctx["ct_sections"])

    def to_item(self, obj, ctx):
        user, request = ctx["user"], ctx["request"]
        is_mine = obj.author_id == user.pk
        is_staff = self._is_staff(obj, ctx)
        can_reply = is_mine or is_staff
        can_resolve = (is_mine or is_staff) and obj.status != "resolved"
        return hub.make_item(
            source=self.source, source_id=obj.pk, text=obj.text, status=obj.status,
            created_at=obj.created_at, updated_at=obj.updated_at,
            parent={"type": "subject_class", "id": str(obj.section_id),
                    "name": f"{obj.subject.name} · {obj.section}", "subject_id": str(obj.subject_id)},
            author=user_payload(obj.author, request), is_mine=is_mine,
            role=hub.ROLE_ASKED if is_mine else (
                hub.ROLE_TO_ANSWER if (is_staff and obj.status == "open") else hub.ROLE_ALL),
            answers_count=getattr(obj, "replies_count", 0),
            capabilities={
                hub.ACTION_ANSWER: can_reply,
                hub.ACTION_RESOLVE: can_resolve,
                hub.ACTION_REOPEN: (is_mine or is_staff) and obj.status == "resolved",
                "ask_follow_up": is_mine,
            },
            extra={"is_public": obj.is_public, "has_attachment": bool(obj.attachment)},
        )

    def thread(self, obj, ctx):
        from campus.models import CampusDoubtReply

        replies = CampusDoubtReply.objects.filter(doubt=obj, is_active=True).select_related("author")
        return [{
            "id": str(r.pk), "author": user_payload(r.author, ctx["request"]), "text": r.text,
            "created_at": r.created_at, "is_staff_reply": r.is_staff_reply, "is_best": False,
        } for r in replies]

    # ---- writes: delegate to campus.views.CampusDoubtViewSet --------------
    def dispatch_ask(self, request, payload):
        from campus.views import CampusDoubtViewSet

        section, subject = payload.get("target_id"), payload.get("subject_id")
        if not section or not subject:
            raise hub.HubError("target_id (section) and subject_id are required.", 400, "missing_target")
        view = CampusDoubtViewSet.as_view({"post": "create"})
        return call_view(
            view, user=request.user, method="post", path="/campus/doubts/",
            data={"section": section, "subject": subject, "text": payload.get("text", ""),
                  "is_public": bool(payload.get("is_public", False))},
            origin_request=request,
        )

    def dispatch_action(self, request, obj, action, payload, ctx):
        from campus.views import CampusDoubtViewSet

        name = {hub.ACTION_ANSWER: "reply", hub.ACTION_RESOLVE: "resolve", hub.ACTION_REOPEN: "reopen"}[action]
        view = CampusDoubtViewSet.as_view({"post": name})
        data = {"text": payload.get("text", "")} if action == hub.ACTION_ANSWER else {}
        return call_view(view, user=request.user, method="post",
                         path=f"/campus/doubts/{obj.pk}/{name}/", data=data,
                         origin_request=request, pk=str(obj.pk))

    def targets(self, user, ctx):
        # The same list the student's "My classes" screen shows.
        from campus.models import StudentEnrollment, SubjectTeacherassigments

        out = []
        enrolments = StudentEnrollment.objects.filter(
            student=user, status=StudentEnrollment.Status.ACTIVE
        ).select_related("section")
        for e in enrolments:
            subjects = (
                SubjectTeacherassigments.objects.filter(
                    section=e.section, status=SubjectTeacherassigments.Status.APPROVED
                ).select_related("subject").values_list("subject_id", "subject__name").distinct()
            )
            for subject_id, subject_name in subjects:
                out.append({
                    "source": self.source, "target_id": str(e.section_id), "subject_id": str(subject_id),
                    "name": f"{subject_name} · {e.section}", "kind": "subject_class",
                    "allows_anonymous": False,
                })
        return out
