"""testseries adapter — "ask the teacher about my checked attempt".

Stored in message.DoubtQuestion with context_type='testseries_attempt' (see
testseries/bridge.py). Group doubts live in the classroom_chat adapter
(`group__isnull=False`); this adapter takes the OTHER half
(`group__isnull=True`), so a row is never counted twice.

Visibility: the asker, and the creator of the series the attempt belongs to
(the only person who may answer — testseries/bridge.answer_query_on_series)."""
from __future__ import annotations

from django.db.models import Q

from .. import hub
from ..dispatch import call_view
from .base import NEVER, BaseAdapter, user_payload

CONTEXT_TYPE = "testseries_attempt"


class TestSeriesAdapter(BaseAdapter):
    __test__ = False  # not a pytest/unittest test class despite the name
    source = hub.SOURCE_TESTSERIES
    label = "Test series"
    supported_actions = frozenset({hub.ACTION_ANSWER})

    def build_ctx(self, user, request=None):
        from testseries.models import TestAttempt

        return {
            "user": user, "request": request, "attempt_map": {},
            # lazy subquery: attempts of series I created
            "teacher_attempts": TestAttempt.objects.filter(series__creator=user).values("id"),
        }

    def scoped_queryset(self, user, ctx):
        from message.models import DoubtQuestion

        return DoubtQuestion.objects.filter(
            context_type=CONTEXT_TYPE, group__isnull=True,
        ).filter(
            Q(author=user) | Q(context_id__in=ctx["teacher_attempts"])
        ).select_related("author", "answered_by")

    def status_q(self):
        return {
            hub.STATUS_OPEN: Q(is_answered=False),
            hub.STATUS_ANSWERED: Q(is_answered=True),
            hub.STATUS_RESOLVED: NEVER,
        }

    def to_answer_q(self, ctx):
        return Q(is_answered=False, context_id__in=ctx["teacher_attempts"]) & ~Q(author_id=ctx["user"].pk)

    def parent_filter_field(self):
        return "context_id"

    def prepare_objs(self, objs, ctx):
        from testseries.models import TestAttempt

        ids = {o.context_id for o in objs if o.context_id}
        if not ids:
            return
        rows = TestAttempt.objects.filter(pk__in=ids).select_related("series").values_list(
            "pk", "series_id", "series__title", "series__creator_id"
        )
        ctx["attempt_map"].update({pk: (sid, title, creator) for pk, sid, title, creator in rows})

    def to_item(self, obj, ctx):
        user, request = ctx["user"], ctx["request"]
        series_id, title, creator_id = ctx["attempt_map"].get(obj.context_id, (None, "Test", None))
        is_mine = obj.author_id == user.pk
        is_teacher = creator_id == user.pk
        can_answer = is_teacher and not obj.is_answered
        show_author = (not obj.is_anonymous) or is_mine or obj.is_revealed
        return hub.make_item(
            source=self.source, source_id=obj.pk, text=obj.text,
            status=hub.STATUS_ANSWERED if obj.is_answered else hub.STATUS_OPEN,
            created_at=obj.created_at, updated_at=obj.updated_at,
            parent={"type": "test_attempt", "id": str(obj.context_id), "name": title,
                    "series_id": str(series_id) if series_id else None},
            author=user_payload(obj.author, request) if show_author else None,
            is_anonymous=obj.is_anonymous, is_mine=is_mine,
            role=hub.ROLE_ASKED if is_mine else (hub.ROLE_TO_ANSWER if can_answer else hub.ROLE_ALL),
            answers_count=1 if obj.is_answered else 0, answer_preview=obj.answer_text,
            capabilities={hub.ACTION_ANSWER: can_answer},
        )

    def thread(self, obj, ctx):
        if not obj.is_answered:
            return []
        return [{
            "id": "answer", "author": user_payload(obj.answered_by, ctx["request"]),
            "text": obj.answer_text, "created_at": obj.answered_at, "is_staff_reply": True, "is_best": True,
        }]

    # ---- writes: delegate to testseries.views.TestAttemptViewSet ---------
    def dispatch_ask(self, request, payload):
        from testseries.views import TestAttemptViewSet

        attempt_id = payload.get("target_id")
        if not attempt_id:
            raise hub.HubError("target_id (attempt id) is required.", 400, "missing_target")
        view = TestAttemptViewSet.as_view({"post": "ask_query"})
        return call_view(
            view, user=request.user, method="post", path=f"/testseries/attempts/{attempt_id}/ask-query/",
            data={"text": payload.get("text", ""), "is_anonymous": bool(payload.get("is_anonymous", False))},
            origin_request=request, pk=str(attempt_id),
        )

    def dispatch_action(self, request, obj, action, payload, ctx):
        from testseries.views import TestAttemptViewSet

        view = TestAttemptViewSet.as_view({"post": "answer_query"})
        return call_view(
            view, user=request.user, method="post",
            path=f"/testseries/attempts/{obj.context_id}/answer-query/",
            data={"doubt_id": str(obj.pk), "answer_text": payload.get("text", "")},
            origin_request=request, pk=str(obj.context_id),
        )

    def targets(self, user, ctx):
        from testseries.models import TestAttempt

        rows = (
            TestAttempt.objects.filter(student=user, status=TestAttempt.Status.CHECKED)
            .select_related("series").order_by("-checked_at", "-pk")[:30]
        )
        return [{"source": self.source, "target_id": str(a.pk), "name": a.series.title,
                 "kind": "test_attempt", "allows_anonymous": True} for a in rows]
