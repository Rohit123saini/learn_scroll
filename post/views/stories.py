# post/views/stories.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..models import (
    CloseFriend,
    MutedAccount,
    Story,
    StoryReaction,
    StorySticker,
    StoryView,
)
from django.db.models import (
    Exists,
    OuterRef,
    Q,
)
from user_profile.models import Follow
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework.exceptions import PermissionDenied
from ..serializers import (
    PostShareRequestSerializer,
    StoryCreateSerializer,
    StoryMentionCandidateSerializer,
    StorySerializer,
    StoryViewerEntrySerializer,
    get_profile_pic_url,
    serialize_sticker,
    story_sticker_prefetch,
)
from rest_framework.response import Response
from ..story_sticker_responses import (
    ResponseError,
    build_interaction_stats,
    cast_quiz_answer,
    cast_vote,
    submit_answer,
)
from ..story_visibility import (
    STORY_RESHARE_OFF,
    STORY_RESHARE_OFF_MESSAGE,
    STORY_SAVE_OFF,
    STORY_SAVE_OFF_MESSAGE,
    can_reply_to_story,
    can_reshare_story,
    can_save_story,
    filter_stories_visible_to,
    get_visible_story_or_404,
)
from rest_framework.throttling import ScopedRateThrottle
from common.pagination import StandardPagination
from asgiref.sync import async_to_sync
from ..close_friends_views import blocked_user_ids
from rest_framework import (
    generics,
    parsers,
    status,
)
from channels.layers import get_channel_layer
from django.shortcuts import get_object_or_404
from ..idempotency import idempotent
from ..services import (
    notify_story_reacted,
    unnotify_story_reacted,
)
from rest_framework.decorators import (
    parser_classes,
    permission_classes,
)
from django.utils import timezone
from ._common import User


# ===================== STORIES =====================
# NEW — checklist items 54/55/57/60. Story model added in models.py.
class StoryCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Create a Story (24h auto-expiry)", tags=["Stories"])
    @idempotent("story_create")  # H20
    def post(self, request):
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)
        ok, code, why = precheck_upload(request.user, files=request.FILES.getlist("media"))
        if not ok:
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)
        # F2: shelf size. Checked BEFORE the publish gate so a refused story does not use up the spam-rate allowance.
        from .. import story_limits
        over = story_limits.check_active_limit(request.user)
        if over is not None:
            res = Response(
                {"success": False, "code": over["code"], "message": over["message"], "retry_after": over["retry_after"],
                 "limit": over["limit"], "active": over["active"]},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            res["Retry-After"] = str(over["retry_after"])
            return res
        from .. import publish_gate  # H23 publish gate (a story has no hold state: abusive/NSFW text is refused)
        verdict = publish_gate.evaluate(
            request.user, "story",
            texts=publish_gate.texts_from_request("story", request.data),
            files=request.FILES.getlist("media"),
        )
        if not verdict.ok:
            return verdict.response()
        serializer = StoryCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        story = serializer.save()
        publish_gate.after_create(verdict, request.user, "story", story)
        # Re-read with the stickers (and each mentioned user) joined in, so the
        # response has the same shape as the list endpoint without N+1 queries.
        story = (
            Story.objects.select_related("user")
            .prefetch_related(story_sticker_prefetch())
            .get(pk=story.pk)
        )
        return Response(StorySerializer(story, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StoryListAPIView(generics.ListAPIView):
    """Active (non-expired, non-deleted) stories from people the requester
    follows, plus their own. Expiry is enforced here in real time — the
    `expire_old_stories` celery task (tasks.py) only does housekeeping
    (soft-deleting rows so they don't pile up), it isn't what makes an
    expired story stop showing up."""
    serializer_class = StorySerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        # LV1: every story item gets `live` (author live right now, visible to
        # me) — computed once here, not per story.
        from ..story_live import live_map_for

        return {"request": self.request, "live_map": live_map_for(self.request.user)}

    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        # F3-AUD: a class story also reaches class members who do not follow the teacher.
        from ..audience import classroom_ids_of
        my_classrooms = list(classroom_ids_of(request_user))
        return (
            filter_stories_visible_to(
                Story.objects.select_related("user")
                .filter(is_deleted=False, expires_at__gt=timezone.now())
                .filter(
                    Q(user_id__in=following_ids) | Q(user=request_user)
                    | Q(audience=Story.AUDIENCE_CLASS, audience_classroom_id__in=my_classrooms)
                )
                # Mute covers stories too (P8: unless the mute is posts-only): a muted account's stories stay out of my tray.
                .exclude(user_id__in=MutedAccount.objects.filter(user=request_user, mute_stories=True).values("muted_user_id"))
                .prefetch_related(story_sticker_prefetch()),
                request_user,
            )
            .order_by("user_id", "-created_at")
        )


class StoryLimitsAPIView(APIView):
    """GET /post/stories/limits/ - F2: how many story slots I have left and how long a story lives."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My story limits (active cap, lifetime)", tags=["Stories"])
    def get(self, request):
        from .. import story_limits

        return Response(story_limits.limits_for(request.user))


class StoryLiveAPIView(APIView):
    """GET /post/stories/live/  — LV1: who is LIVE right now (for my story tray).

    The story list only returns users who HAVE an active story; a teacher who
    goes live without posting one still needs a ring, so the home screen polls
    this (~every 5s) and merges it into the tray. Each row:

        {"user": {"id", "username", "profile_picture"},
         "live": {"session_id", "classroom_id", "title", "started_at",
                  "viewer_count", "can_join"}}

    Newest first. Visibility = class members + followers of the teacher
    (see post/story_live.py); blocked / muted teachers are left out. When the
    teacher ends the class (or their app stops heartbeating) the row is gone
    on the next call. Has its own throttle scope so the 5s polling does not eat
    into the shared per-user rate budget.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "story_live"

    @extend_schema(summary="Users who are live right now (story tray LIVE rings)", tags=["Stories"])
    def get(self, request):
        from ..story_live import live_map_for

        live_map = live_map_for(request.user)
        users = User.objects.in_bulk(list(live_map.keys()))
        items = []
        for tid, live in live_map.items():
            user = users.get(tid)
            if user is None or not user.is_active:
                continue
            items.append({
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "profile_picture": get_profile_pic_url(user, request),
                },
                "live": live,
            })
        # F2: same priority as the app's tray - a class I can JOIN (member / staff / teacher) before a class I only
        # follow, then newest start first. (sort is stable: two passes = can_join desc, then started_at desc inside.)
        items.sort(key=lambda i: i["live"].get("started_at") or "", reverse=True)
        items.sort(key=lambda i: 0 if i["live"].get("can_join") else 1)
        return Response({"results": items})


class StoryDetailAPIView(APIView):
    """One active story by id - what a `story_mention` notification opens.

    Same visibility rule as view/react/reply (post/story_visibility.py): a
    Close Friends story the caller may not see is a 404, not a 403. An expired
    or deleted story is a 404 too, and so is one whose author has a block with
    the caller in either direction."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get one active story (with its stickers)", tags=["Stories"])
    def get(self, request, story_id):
        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired or story.user_id in blocked_user_ids(request.user):
            raise Http404("Story not found.")
        story = (
            Story.objects.select_related("user")
            .prefetch_related(story_sticker_prefetch())
            .get(pk=story.pk)
        )
        return Response(StorySerializer(story, context={"request": request}).data)

    @extend_schema(summary="Delete my own story (owner only)", request=None, tags=["Stories"])
    def delete(self, request, story_id):
        """ST-A (2): the owner's \"Delete\" in the story viewer. Soft delete (`Story.soft_delete`), 204. Someone else's story
        (or an unknown / already deleted one) is a 404, never a 403, so existence is not leaked. A story deleted BEFORE it
        expires is also gone from highlights (`highlightable_stories_q`), the same rule the highlight docs state."""
        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.user_id != request.user.id:
            raise Http404("Story not found.")
        story.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoryMentionCandidatesAPIView(APIView):
    """People the composer can @mention.

    GET /post/stories/mention-candidates/?q=<text>&audience=everyone|close_friends

    * no `q`  -> only the caller's own people (accepted followers + people they follow)
    * with `q`-> any active user whose username / name contains it
    * never the caller, never anyone with a block in either direction
    * `audience=close_friends` narrows to the caller's Close Friends list, because
      only those people may be tagged in a Close Friends story
    * `audience=class&classroom_id=<id>` (X5) narrows to the members of that ONE class (its teacher + students with an
      active pass), because only they may be tagged in a Class story; `class` without a usable `classroom_id` -> an
      empty list (never "everybody") so the picker cannot offer someone the server will refuse
    Connected people sort first, then by username. Row: {id, username, name, profile_picture}.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search people to @mention in a story",
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("audience", str, required=False, enum=["everyone", "followers", "close_friends", "class"]),
            OpenApiParameter("classroom_id", int, required=False),
        ],
        tags=["Stories"],
    )
    def get(self, request):
        me = request.user
        q = (request.query_params.get("q") or "").strip()
        audience = (request.query_params.get("audience") or Story.AUDIENCE_EVERYONE).strip()

        qs = (
            User.objects.filter(is_active=True)
            .exclude(id=me.id)
            .exclude(id__in=blocked_user_ids(me))
            .annotate(
                _is_following=Exists(Follow.objects.filter(
                    follower=me, following=OuterRef("pk"), status=Follow.Status.ACCEPTED,
                )),
                _is_follower=Exists(Follow.objects.filter(
                    following=me, follower=OuterRef("pk"), status=Follow.Status.ACCEPTED,
                )),
            )
        )
        if audience == Story.AUDIENCE_CLOSE_FRIENDS:
            qs = qs.filter(id__in=CloseFriend.objects.filter(owner=me).values("friend_id"))
        elif audience == Story.AUDIENCE_FOLLOWERS:  # F3-AUD: only people who could open the story
            qs = qs.filter(_is_follower=True)
        elif audience == Story.AUDIENCE_CLASS:  # X5 (4): only members of the story's class
            from ..audience import classroom_member_ids
            qs = qs.filter(id__in=classroom_member_ids(request.query_params.get("classroom_id")))
        if q:
            qs = qs.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
        elif audience != Story.AUDIENCE_CLASS:  # a class story lists the whole (small) class, connected or not
            qs = qs.filter(Q(_is_following=True) | Q(_is_follower=True))
        qs = qs.order_by("-_is_following", "-_is_follower", "username")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = StoryMentionCandidateSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)


def _get_interactive_sticker(request, story_id, sticker_id):
    """The sticker a viewer wants to respond to. Same gate as reacting: the
    story must be visible to them (404 otherwise, so a Close Friends story is
    not leaked), not expired, and not written by someone with a block against
    the caller. A sticker id that belongs to another story is a 404 too."""
    story = get_visible_story_or_404(request.user, story_id)
    if story.is_expired or story.user_id in blocked_user_ids(request.user):
        raise Http404("Story not found.")
    sticker = get_object_or_404(StorySticker.objects.select_related("story"), id=sticker_id, story=story)
    return story, sticker


def _response_error(exc, request, sticker):
    body = {"detail": exc.message, "code": exc.code}
    if exc.status == status.HTTP_409_CONFLICT:
        # Show the state that already exists so the client can just render it.
        body["sticker"] = serialize_sticker(sticker, request)
    return Response(body, status=exc.status)


class StoryPollVoteAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/vote/   {"option": 0}

    One vote per viewer and it is final (409 with the existing state if they
    try again). The story owner can't vote (400). 201 returns the poll sticker
    with `my_vote` and `results` filled in."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Vote on a story poll sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            cast_vote(sticker, request.user, request.data.get("option"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryQuestionAnswerAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/answer/   {"text": "..."}

    One private answer per viewer (max 300 chars); only the story owner can
    read them (StoryStickerResponsesAPIView). The owner can't answer their own
    question (400); a second answer is a 409."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Answer a story question sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            submit_answer(sticker, request.user, request.data.get("text"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryQuizAnswerAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/quiz-answer/   {"option": 1}

    F3. One final answer per viewer (409 with the existing state if they try again). The story owner can't answer
    (400). 201 returns the quiz sticker with `my_answer`, `correct_index`, `explanation` and `results` filled in."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Answer a story quiz sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            cast_quiz_answer(sticker, request.user, request.data.get("option"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryStickerResponsesAPIView(generics.GenericAPIView):
    """GET /post/stories/<story_id>/stickers/<sticker_id>/responses/  - OWNER ONLY.

    poll     -> rows {id, user, option_index, option, created_at} + summary {counts, total}
    quiz     -> rows {id, user, option_index, option, is_correct, created_at}
                + summary {counts, total, correct, correct_index}                               (F3)
    question -> rows {id, user, text, created_at}               + summary {count}
    Paginated like the other lists (`count/next/previous/results`) with the
    extra `summary` key. Works on an expired story too (as long as it isn't
    deleted) so the owner can still read the answers."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List a poll's votes / a question's answers - owner only", tags=["Stories"])
    def get(self, request, story_id, sticker_id):
        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.user_id != request.user.id:
            raise PermissionDenied("Sirf apni story ke responses dekh sakte hain.")
        sticker = get_object_or_404(
            StorySticker, id=sticker_id, story=story,
            kind__in=[StorySticker.KIND_POLL, StorySticker.KIND_QUESTION, StorySticker.KIND_QUIZ],
        )

        def brief(user):
            return {
                "id": str(user.id),
                "username": user.username,
                "profile_picture": get_profile_pic_url(user, request),
            }

        paginator = StandardPagination()
        if sticker.kind == StorySticker.KIND_POLL:
            options = list((sticker.data or {}).get("options", []))
            qs = sticker.poll_votes.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {
                    "id": str(v.id), "user": brief(v.user), "option_index": v.option_index,
                    "option": options[v.option_index] if v.option_index < len(options) else "",
                    "created_at": v.created_at.isoformat(),
                }
                for v in page
            ]
            stats = build_interaction_stats([sticker], request.user)[sticker.id]
            summary = {"counts": stats["counts"], "total": stats["total"]}
        elif sticker.kind == StorySticker.KIND_QUIZ:
            options = list((sticker.data or {}).get("options", []))
            qs = sticker.quiz_answers.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {
                    "id": str(a.id), "user": brief(a.user), "option_index": a.option_index,
                    "option": options[a.option_index] if a.option_index < len(options) else "",
                    "is_correct": a.is_correct, "created_at": a.created_at.isoformat(),
                }
                for a in page
            ]
            stats = build_interaction_stats([sticker], request.user)[sticker.id]
            summary = {
                "counts": stats["counts"], "total": stats["total"], "correct": stats["correct"],
                "correct_index": (sticker.data or {}).get("correct_index"),
            }
        else:
            qs = sticker.answers.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {"id": str(a.id), "user": brief(a.user), "text": a.text, "created_at": a.created_at.isoformat()}
                for a in page
            ]
            summary = {"count": qs.count()}

        response = paginator.get_paginated_response(rows)
        response.data["summary"] = summary
        return response


class StoryViewAPIView(APIView):
    """Records a view (deduped per user via unique_together) and returns
    the current view count — mirrors PostDetailAPIView's PostView tracking."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark a story as viewed", tags=["Stories"])
    def post(self, request, story_id):
        from django.shortcuts import get_object_or_404

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"success": False, "message": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        StoryView.objects.get_or_create(story=story, user=request.user)
        story.refresh_from_db(fields=["views_count"])
        return Response({"success": True, "views_count": story.views_count})


class StoryReactAPIView(APIView):
    """Instagram-style quick reaction — toggle semantics: tapping the same
    emoji again removes it, tapping a different one replaces it. One
    reaction per (story, user), enforced by StoryReaction's
    unique_together (models.py)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="React to / un-react from a story", tags=["Stories"])
    def post(self, request, story_id):
        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        if not can_reply_to_story(story, request.user):
            return Response({"detail": "Replies are turned off for this story.", "code": "story_replies_off"},
                            status=status.HTTP_403_FORBIDDEN)

        emoji = (request.data.get("emoji") or "").strip()
        if not emoji:
            return Response({"detail": "'emoji' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        existing = StoryReaction.objects.filter(story=story, user=request.user).first()
        if existing and existing.emoji == emoji:
            existing.delete()
            reacted = False
            unnotify_story_reacted(story, request.user)  # Task 3.4
        else:
            StoryReaction.objects.update_or_create(
                story=story, user=request.user, defaults={"emoji": emoji},
            )
            reacted = True
            if existing is None:  # a changed emoji is not a new reaction
                notify_story_reacted(story, request.user, emoji)  # Task 3.4

        # Real-time — story owner's "who reacted" list/analytics updates
        # immediately, no polling. Same `user_<id>` inbox group
        # message/services.py's broadcasts already use; InboxConsumer's
        # `story_reaction` handler (consumers.py) is the only new bit.
        if str(story.user_id) != str(request.user.id):
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(
                    f"user_{story.user_id}",
                    {
                        "type": "story_reaction",
                        "story_id": str(story.id),
                        "user_id": str(request.user.id),
                        "username": request.user.username,
                        "emoji": emoji,
                        "reacted": reacted,
                    },
                )

        return Response({"success": True, "reacted": reacted, "emoji": emoji if reacted else None})


class StoryReplyAPIView(APIView):
    """Reply to a story. This does NOT invent a new reply model/thread —
    it reuses message app's Conversation/Message pipeline directly
    (`message.services.get_or_create_conversation` +
    `create_message_and_broadcast`), so the reply lands in the recipient's
    normal DM inbox exactly like any other message, with the story's
    thumbnail attached via `Message.story`/`story_reply_snapshot`."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Reply to a story (delivered as a DM)", tags=["Stories"])
    def post(self, request, story_id):
        # Local imports — post app must not need message app importable at
        # module-load time (avoids a hard circular-import dependency
        # between the two apps; message app already reaches back into
        # post.Story via a string FK for the same reason).
        from message.services import get_or_create_conversation, create_message_and_broadcast
        from message.models import MessageType
        from message.serializers import MessageSerializer

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if story.user_id == request.user.id:
            return Response(
                {"detail": "Apni khud ki story ko reply nahi kar sakte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not can_reply_to_story(story, request.user):  # F3-AUD: reply_setting = followers / off
            return Response({"detail": "Replies are turned off for this story.", "code": "story_replies_off"},
                            status=status.HTTP_403_FORBIDDEN)

        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "'text' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        # S4: the story owner limited who may start a chat with them (an existing chat is never cut off).
        from user_profile import privacy as privacy_rules

        if privacy_rules.new_chat_blocked(request.user, story.user):
            return Response(
                {"detail": privacy_rules.MESSAGE_BLOCKED_TEXT, "code": privacy_rules.MESSAGE_BLOCKED_CODE},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Minor-safety: no new chat from an adult to a minor who doesn't follow them.
        from message.minor_safety import MINOR_DM_BLOCKED_MESSAGE, dm_blocked_for_minor
        from message.models import Conversation as _Conv

        if dm_blocked_for_minor(request.user, story.user) and not _Conv.objects.filter(
            private_key=_Conv.make_private_key(request.user.id, story.user_id)
        ).exists():
            return Response({"detail": MINOR_DM_BLOCKED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)

        conversation, _ = get_or_create_conversation(request.user, story.user)

        try:
            snapshot_url = request.build_absolute_uri(story.media.url) if story.media else None
        except ValueError:
            snapshot_url = None

        message = create_message_and_broadcast(
            conversation=conversation,
            sender=request.user,
            message_type=MessageType.STORY_REPLY,
            text=text,
            extra_fields={"story": story, "story_reply_snapshot": snapshot_url},
        )

        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class StoryShareAPIView(APIView):
    """X1: POST /post/stories/<id>/share/ {"conversation_id": "..."} - send a story into a chat.

    Order of checks: visible (else 404, never leaks) -> not expired (404) -> `Story.allow_reshare` (403
    `story_reshare_off`; the owner is always allowed) -> conversation (404 / 403 participant)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Send a story into a chat conversation", request=PostShareRequestSerializer, tags=["Stories"])
    def post(self, request, story_id):
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

        from message.models import Conversation

        from ..story_share import share_story_to_conversation

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if not can_reshare_story(story, request.user):
            return Response({"detail": STORY_RESHARE_OFF_MESSAGE, "code": STORY_RESHARE_OFF},
                            status=status.HTTP_403_FORBIDDEN)

        body = PostShareRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            message = share_story_to_conversation(
                story, request.user, body.validated_data["conversation_id"], request=request,
            )
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
        except DjangoPermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            {"success": True, "data": {"message_id": str(message.id), "conversation_id": str(message.conversation_id)}},
            status=status.HTTP_201_CREATED,
        )


class StorySaveAPIView(APIView):
    """X1: POST /post/stories/<id>/save/ - the app asks "may I download this story?" right before it writes the file
    to the gallery. Same checks as share but with `Story.allow_save` (403 `story_save_off`). Answers
    {"success": true, "data": {"url", "media_type", "filename"}}. (A client that skips this call can still fetch the
    public media URL by hand - this gate keeps the app and honest clients in line, it is not DRM.)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get the downloadable media of a story (checks allow_save)", request=None, tags=["Stories"])
    def post(self, request, story_id):
        from ..story_share import saveable_media

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if not can_save_story(story, request.user):
            return Response({"detail": STORY_SAVE_OFF_MESSAGE, "code": STORY_SAVE_OFF},
                            status=status.HTTP_403_FORBIDDEN)
        data = saveable_media(story, request)
        if data is None:
            return Response({"detail": "Story has no media"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "data": data})


class StoryViewersAPIView(generics.ListAPIView):
    """Owner-only — Instagram's "Activity" list under your own story: who
    viewed it, with each viewer's reaction (if any) folded in."""
    serializer_class = StoryViewerEntrySerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List a story's viewers (+ their reactions) — owner only", tags=["Stories"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        story = get_object_or_404(Story, id=self.kwargs["story_id"], is_deleted=False)
        if story.user_id != self.request.user.id:
            raise PermissionDenied("Sirf apni story ke viewers dekh sakte hain.")
        self._story = story
        from user_profile.services import blocked_user_ids
        return (
            story.views.select_related("user")
            .exclude(user_id__in=blocked_user_ids(self.request.user))
            .order_by("-viewed_at")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        reactions = StoryReaction.objects.filter(story=self._story).select_related("user")
        ctx["reactions_by_user_id"] = {r.user_id: r for r in reactions}
        return ctx
