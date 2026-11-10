"""tuition_class adapter — tuitionclass.ClassQuery (a student's question to a
live-class teacher). Visibility mirrors ClassQueryViewSet: you see the
queries you asked, plus EVERY query of classrooms you manage (teacher,
co-teacher, moderator, or org staff)."""
from __future__ import annotations

from django.db.models import Q

from .. import hub
from ..dispatch import call_view
from .base import NEVER, BaseAdapter, user_payload


class TuitionAdapter(BaseAdapter):
    source = hub.SOURCE_TUITION
    label = "Live class"
    author_field = "asked_by_id"
    text_field = "question"
    supported_actions = frozenset({hub.ACTION_ANSWER})

    def build_ctx(self, user, request=None):
        from tuitionclass.models import Classroom, ClassroomStaff

        managed = set(
            Classroom.objects.filter(
                Q(teacher=user)
                | Q(staff__user=user,
                    staff__role__in=[ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR])
                | Q(staff__user=user, classroom_type=Classroom.ClassroomType.ORGANISATION)
            ).values_list("pk", flat=True).distinct()
        )
        return {"user": user, "request": request, "managed_ids": managed}

    def scoped_queryset(self, user, ctx):
        from tuitionclass.models import ClassQuery

        return ClassQuery.objects.filter(
            Q(asked_by=user) | Q(classroom_id__in=ctx["managed_ids"])
        ).select_related("asked_by", "answered_by", "classroom")

    def status_q(self):
        return {
            hub.STATUS_OPEN: Q(status="open"),
            hub.STATUS_ANSWERED: Q(status="answered"),
            hub.STATUS_RESOLVED: NEVER,
        }

    def to_answer_q(self, ctx):
        return Q(status="open", classroom_id__in=ctx["managed_ids"]) & ~Q(asked_by_id=ctx["user"].pk)

    def parent_filter_field(self):
        return "classroom_id"

    def to_item(self, obj, ctx):
        user, request = ctx["user"], ctx["request"]
        is_mine = obj.asked_by_id == user.pk
        is_manager = obj.classroom_id in ctx["managed_ids"]
        can_answer = is_manager and obj.status == "open"
        return hub.make_item(
            source=self.source, source_id=obj.pk, text=obj.question, status=obj.status,
            created_at=obj.created_at, updated_at=obj.answered_at or obj.created_at,
            parent={"type": "classroom", "id": str(obj.classroom_id), "name": obj.classroom.title,
                    "session_id": str(obj.session_id) if obj.session_id else None},
            author=user_payload(obj.asked_by, request), is_mine=is_mine,
            role=hub.ROLE_ASKED if is_mine else (hub.ROLE_TO_ANSWER if can_answer else hub.ROLE_ALL),
            answers_count=1 if obj.status == "answered" else 0, answer_preview=obj.answer,
            capabilities={hub.ACTION_ANSWER: can_answer},
        )

    def thread(self, obj, ctx):
        if obj.status != "answered":
            return []
        return [{
            "id": "answer", "author": user_payload(obj.answered_by, ctx["request"]),
            "text": obj.answer, "created_at": obj.answered_at, "is_staff_reply": True, "is_best": True,
        }]

    # ---- writes: delegate to tuitionclass.views.ClassQueryViewSet ---------
    def dispatch_ask(self, request, payload):
        from tuitionclass.views import ClassQueryViewSet

        classroom_id = payload.get("target_id")
        if not classroom_id:
            raise hub.HubError("target_id (classroom id) is required.", 400, "missing_target")
        data = {"classroom": classroom_id, "question": payload.get("text", "")}
        if payload.get("session_id"):
            data["session"] = payload["session_id"]
        view = ClassQueryViewSet.as_view({"post": "create"})
        return call_view(view, user=request.user, method="post", path="/tuitionclass/queries/",
                         data=data, origin_request=request)

    def dispatch_action(self, request, obj, action, payload, ctx):
        from tuitionclass.views import ClassQueryViewSet

        view = ClassQueryViewSet.as_view({"post": "answer"})
        # `?classroom=` makes ClassQueryViewSet.get_queryset() return every
        # query of a classroom the caller manages (not only their own).
        return call_view(view, user=request.user, method="post",
                         path=f"/tuitionclass/queries/{obj.pk}/answer/",
                         query={"classroom": obj.classroom_id}, data={"answer": payload.get("text", "")},
                         origin_request=request, pk=str(obj.pk))

    def targets(self, user, ctx):
        from tuitionclass.models import Classroom, PassPurchase

        # Same rule as Classroom.is_enrolled(): a successful, active pass.
        enrolled = Classroom.objects.filter(
            passes__purchases__student=user,
            passes__purchases__status=PassPurchase.Status.SUCCESS,
            passes__purchases__is_active=True,
        ).distinct()[:50]
        return [{"source": self.source, "target_id": str(c.pk), "name": c.title,
                 "kind": "classroom", "allows_anonymous": False} for c in enrolled]
