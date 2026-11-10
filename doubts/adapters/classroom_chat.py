"""classroom_chat adapter — message.DoubtQuestion rows that belong to a group
(the per-classroom "Doubt Queue": upvotable, optionally anonymous, optionally
photo-only) and their `DoubtAnswer` thread (T1.3).

Answer model (since T1.3): ANY non-banned group member except the asker may
answer; admin/moderator answers are flagged `is_teacher_answer` and are
auto-verified; the asker or a teacher marks ONE answer "best"; a teacher can
"verify" any answer. DoubtQuestion keeps denormalised mirrors
(`is_answered`, `answers_count`, `has_best_answer`, `has_verified_answer`,
`answer_text` = the primary answer) which is what the hub reads for lists —
the thread itself is only loaded for the detail view.

Hub status mapping:
    open      no answers yet
    answered  has answers, none marked best
    resolved  an answer is marked best"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from .. import hub
from ..dispatch import call_view
from .base import BaseAdapter, user_payload


class ClassroomChatAdapter(BaseAdapter):
    source = hub.SOURCE_CLASSROOM_CHAT
    label = "Classroom chat"
    supported_actions = frozenset({
        hub.ACTION_ANSWER, hub.ACTION_UPVOTE, hub.ACTION_UNUPVOTE, hub.ACTION_REVEAL,
        hub.ACTION_MARK_BEST, hub.ACTION_VERIFY,
    })

    def build_ctx(self, user, request=None):
        from message.models import GroupMember

        members = GroupMember.objects.filter(user=user, is_banned=False)
        admin_ids = set(
            members.filter(role__in=[GroupMember.Role.ADMIN, GroupMember.Role.MODERATOR])
            .values_list("group_id", flat=True)
        )
        return {"user": user, "request": request, "admin_group_ids": admin_ids}

    def scoped_queryset(self, user, ctx):
        from message.models import DoubtQuestion, DoubtUpvote, GroupMember

        my_groups = GroupMember.objects.filter(user=user, is_banned=False).values("group_id")
        return (
            DoubtQuestion.objects.filter(group__isnull=False, group_id__in=my_groups)
            .select_related("author", "answered_by", "group")
            .annotate(_upvoted=Exists(DoubtUpvote.objects.filter(doubt=OuterRef("pk"), user=user)))
        )

    def status_q(self):
        return {
            hub.STATUS_OPEN: Q(is_answered=False),
            hub.STATUS_ANSWERED: Q(is_answered=True, has_best_answer=False),
            hub.STATUS_RESOLVED: Q(has_best_answer=True),
        }

    def to_answer_q(self, ctx):
        # "My queue" is the TEACHER's: unanswered doubts in groups I administer/moderate.
        # (Classmates may answer too — capabilities.answer — but it isn't their inbox.)
        return Q(is_answered=False, group_id__in=ctx["admin_group_ids"]) & ~Q(author_id=ctx["user"].pk)

    def parent_filter_field(self):
        return "group_id"

    @staticmethod
    def _status(obj):
        if obj.has_best_answer:
            return hub.STATUS_RESOLVED
        return hub.STATUS_ANSWERED if obj.is_answered else hub.STATUS_OPEN

    def to_item(self, obj, ctx):
        user, request = ctx["user"], ctx["request"]
        is_mine = obj.author_id == user.pk
        is_teacher = obj.group_id in ctx["admin_group_ids"]
        # Same anonymity rule as DoubtQuestionSerializer.get_author: hidden from
        # everyone (teachers included) until the asker or an explicit reveal.
        show_author = (not obj.is_anonymous) or is_mine or obj.is_revealed
        status = self._status(obj)
        upvoted = bool(getattr(obj, "_upvoted", False))
        has_answers = obj.answers_count > 0
        image_url = None
        if getattr(obj, "image", None):
            try:
                image_url = request.build_absolute_uri(obj.image.url) if request is not None else obj.image.url
            except Exception:
                image_url = None
        return hub.make_item(
            source=self.source, source_id=obj.pk, text=obj.text, status=status,
            created_at=obj.created_at, updated_at=obj.updated_at,
            parent={"type": "group", "id": str(obj.group_id), "name": obj.group.name},
            author=user_payload(obj.author, request) if show_author else None,
            is_anonymous=obj.is_anonymous, is_mine=is_mine,
            role=hub.ROLE_ASKED if is_mine else (
                hub.ROLE_TO_ANSWER if (is_teacher and status == hub.STATUS_OPEN) else hub.ROLE_ALL),
            answers_count=obj.answers_count, answer_preview=obj.answer_text,
            upvotes_count=obj.upvotes_count, upvoted_by_me=upvoted,
            capabilities={
                hub.ACTION_ANSWER: not is_mine,  # asker can't answer their own (would unmask anonymity)
                hub.ACTION_UPVOTE: not upvoted,
                hub.ACTION_UNUPVOTE: upvoted,
                hub.ACTION_REVEAL: is_teacher and obj.is_anonymous and not obj.is_revealed,
                hub.ACTION_MARK_BEST: (is_mine or is_teacher) and has_answers,
                hub.ACTION_VERIFY: is_teacher and has_answers,
            },
            extra={"image_url": image_url, "has_verified_answer": obj.has_verified_answer},
        )

    def thread(self, obj, ctx):
        from message.models import DoubtAnswer

        rows = DoubtAnswer.objects.filter(doubt=obj).select_related("author")  # Meta.ordering: best first
        return [{
            "id": str(a.pk), "author": user_payload(a.author, ctx["request"]), "text": a.text,
            "created_at": a.created_at, "is_staff_reply": a.is_teacher_answer,
            "is_best": a.is_best, "is_verified": a.is_teacher_verified,
        } for a in rows]

    # ---- writes: delegate to message.views.DoubtQuestionViewSet -----------
    def dispatch_ask(self, request, payload):
        from message.views import DoubtQuestionViewSet

        group_id = payload.get("target_id")
        if not group_id:
            raise hub.HubError("target_id (group id) is required.", 400, "missing_target")
        view = DoubtQuestionViewSet.as_view({"get": "list", "post": "create"})
        return call_view(
            view, user=request.user, method="post", path=f"/message/groups/{group_id}/doubts/",
            data={"text": payload.get("text", ""), "is_anonymous": bool(payload.get("is_anonymous", False))},
            origin_request=request, group_id=group_id,
        )

    def dispatch_action(self, request, obj, action, payload, ctx):
        from message.views import DoubtQuestionViewSet

        base = f"/message/groups/{obj.group_id}/doubts/{obj.pk}"
        kw = {"group_id": str(obj.group_id), "pk": str(obj.pk)}
        send = dict(user=request.user, origin_request=request)

        if action == hub.ACTION_ANSWER:
            view = DoubtQuestionViewSet.as_view({"get": "list_answers", "post": "create_answer"})
            return call_view(view, method="post", path=f"{base}/answers/",
                             data={"text": payload.get("text", "")}, **send, **kw)
        if action in (hub.ACTION_UPVOTE, hub.ACTION_UNUPVOTE):
            view = DoubtQuestionViewSet.as_view({"post": "upvote", "delete": "upvote"})
            return call_view(view, method="post" if action == hub.ACTION_UPVOTE else "delete",
                             path=f"{base}/upvote/", **send, **kw)
        if action == hub.ACTION_REVEAL:
            view = DoubtQuestionViewSet.as_view({"post": "reveal"})
            return call_view(view, method="post", path=f"{base}/reveal/", **send, **kw)
        if action in (hub.ACTION_MARK_BEST, hub.ACTION_VERIFY):
            answer_id = payload.get("answer_id")
            if not answer_id:
                raise hub.HubError("answer_id is required.", 400, "missing_answer")
            from message.models import DoubtAnswer
            try:
                belongs = DoubtAnswer.objects.filter(pk=answer_id, doubt=obj).exists()
            except Exception:  # malformed uuid
                belongs = False
            if not belongs:
                raise hub.HubError("That answer does not belong to this doubt.", 404, "answer_not_found")
            verb = "best" if action == hub.ACTION_MARK_BEST else "verify"
            view = DoubtQuestionViewSet.as_view({"post": verb, "delete": verb})
            return call_view(view, method="post", path=f"{base}/answers/{answer_id}/{verb}/",
                             answer_id=str(answer_id), **send, **kw)
        raise hub.HubError("Unsupported action.", 400, "action_unsupported")

    def targets(self, user, ctx):
        from message.models import GroupMember

        rows = (
            GroupMember.objects.filter(user=user, is_banned=False)
            .select_related("group").order_by("-group__updated_at")[:50]
        )
        return [{
            "source": self.source, "target_id": str(m.group_id), "name": m.group.name,
            "kind": "group", "allows_anonymous": bool(m.group.allow_anonymous_doubts),
        } for m in rows]
