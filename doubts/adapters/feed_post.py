"""feed_post adapter — post.Post(post_type='doubt') + post.PostAnswer, the
public "Ask a doubt" card in the feed.

Visibility: your own doubts (any moderation state except deleted) plus other
people's PUBLIC + approved doubts, minus anyone you blocked / who blocked
you. 'connections'/'private' doubts of others are never surfaced in the hub
(the feed itself applies the follow-graph rules; the hub deliberately takes
the conservative subset)."""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from .. import hub
from ..dispatch import call_view
from .base import BaseAdapter, user_payload


class FeedPostAdapter(BaseAdapter):
    source = hub.SOURCE_FEED_POST
    label = "Feed post"
    author_field = "user_id"
    text_field = "content"
    can_ask = False  # created in the post composer (media/visibility/tags live there)
    supported_actions = frozenset({hub.ACTION_ANSWER, hub.ACTION_MARK_BEST})

    def build_ctx(self, user, request=None):
        from user_profile.services import blocked_user_ids

        return {"user": user, "request": request, "blocked": set(blocked_user_ids(user) or [])}

    def scoped_queryset(self, user, ctx):
        from post.models import Post, PostAnswer

        qs = (
            Post.objects.filter(post_type="doubt", is_deleted=False, is_archived=False)
            .filter(Q(user=user) | Q(visibility="public", moderation_status="approved", is_scheduled=False))
            .select_related("user")
            .annotate(_has_best=Exists(PostAnswer.objects.filter(post=OuterRef("pk"), is_best_answer=True)))
        )
        if ctx["blocked"]:
            qs = qs.exclude(Q(user_id__in=ctx["blocked"]) & ~Q(user=user))
        return qs

    def status_q(self):
        return {
            hub.STATUS_OPEN: Q(answers_count=0, _has_best=False),
            hub.STATUS_ANSWERED: Q(answers_count__gt=0, _has_best=False),
            hub.STATUS_RESOLVED: Q(_has_best=True),
        }

    def to_answer_q(self, ctx):
        # "Help someone": unanswered public doubts that aren't mine.
        return Q(answers_count=0, _has_best=False) & ~Q(user_id=ctx["user"].pk)

    def parent_filter_field(self):
        return "pk"

    @staticmethod
    def _status(obj):
        if getattr(obj, "_has_best", False):
            return hub.STATUS_RESOLVED
        return hub.STATUS_ANSWERED if obj.answers_count > 0 else hub.STATUS_OPEN

    def to_item(self, obj, ctx):
        user, request = ctx["user"], ctx["request"]
        is_mine = obj.user_id == user.pk
        status = self._status(obj)
        return hub.make_item(
            source=self.source, source_id=obj.pk, text=obj.content, status=status,
            created_at=obj.created_at, updated_at=getattr(obj, "updated_at", None) or obj.created_at,
            parent={"type": "post", "id": str(obj.pk), "name": "Feed"},
            author=user_payload(obj.user, request), is_mine=is_mine,
            role=hub.ROLE_ASKED if is_mine else (hub.ROLE_TO_ANSWER if status == hub.STATUS_OPEN else hub.ROLE_ALL),
            answers_count=obj.answers_count,
            capabilities={
                hub.ACTION_ANSWER: True,
                hub.ACTION_MARK_BEST: is_mine and obj.answers_count > 0,
            },
        )

    def thread(self, obj, ctx):
        from post.models import PostAnswer

        qs = PostAnswer.objects.filter(post=obj).select_related("user")
        if ctx["blocked"]:
            qs = qs.exclude(user_id__in=ctx["blocked"])
        return [{
            "id": str(a.pk), "author": user_payload(a.user, ctx["request"]), "text": a.content,
            "created_at": a.created_at, "is_staff_reply": False, "is_best": a.is_best_answer,
            "likes_count": a.likes_count,
        } for a in qs[:100]]

    # ---- writes: delegate to post.views --------------------------------
    def dispatch_ask(self, request, payload):
        raise hub.HubError(
            "Feed doubts are created from the post composer (choose 'Ask a doubt').", 400, "ask_unsupported"
        )

    def dispatch_action(self, request, obj, action, payload, ctx):
        from post.models import PostAnswer
        from post.views import PostAnswerListCreateAPIView, PostAnswerMarkBestAPIView

        if action == hub.ACTION_ANSWER:
            return call_view(
                PostAnswerListCreateAPIView.as_view(), user=request.user, method="post",
                path=f"/post/{obj.pk}/answers/", data={"content": payload.get("text", "")},
                origin_request=request, post_id=str(obj.pk),
            )
        if action == hub.ACTION_MARK_BEST:
            answer_id = payload.get("answer_id")
            if not answer_id:
                raise hub.HubError("answer_id is required.", 400, "missing_answer")
            try:
                belongs = PostAnswer.objects.filter(pk=answer_id, post=obj).exists()
            except Exception:  # malformed uuid
                belongs = False
            if not belongs:
                raise hub.HubError("That answer does not belong to this doubt.", 404, "answer_not_found")
            return call_view(
                PostAnswerMarkBestAPIView.as_view(), user=request.user, method="post",
                path=f"/post/answers/{answer_id}/mark-best/", origin_request=request,
                answer_id=str(answer_id),
            )
        raise hub.HubError("Unsupported action.", 400, "action_unsupported")
