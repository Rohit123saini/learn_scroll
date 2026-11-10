"""
tuitionclass/views/live_chat.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
import math
import time
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from ..models import (
    ChatMessage,
    ChatMessageRead,
    ChatMessageReport,
    ChatReaction,
    ClassMaterial,
    ClassSession,
    Classroom,
    LivePoll,
    PollResponse,
    PollTemplate,
)
from .. import live_abuse, live_moderation, live_safety
from ..live_cache import live_cache as cache   # L-D #3: slow mode must be shared by every worker (Redis), see live_cache.py
from ..moderation import screen_message
from ..realtime import broadcast_to_session
from ..serializers import (
    ChatMessageReadSerializer,
    ChatMessageReportSerializer,
    ChatMessageSerializer,
    ChatReactionSerializer,
    ClassMaterialSerializer,
    LivePollSerializer,
    PollResponseSerializer,
    PollTemplateSerializer,
    UserMiniSerializer,
)

from ._common import TuitionClassPagination
from .access import (
    _can_cohost_moderate,
    _can_manage_classroom,
    _can_moderate_session,
    _can_view_classroom_internals,
    _has_room_access,
)

logger = logging.getLogger(__name__)


def _enforce_slow_mode(session, user):
    """Slow mode: one message per `session.chat_slow_mode_seconds` per non-moderator participant.

    `cache.add` is atomic, so two fast taps cannot both pass. The stored value is the send time, so
    a rejected sender is told how many seconds are really left (DRF `Throttled(wait=...)` -> 429 +
    Retry-After). Moderators (teacher / co-teacher / moderator) are never slowed. If the cache is
    down the check fails OPEN - the existing `chat_message_create` scope throttle still applies.
    """
    seconds = int(getattr(session, "chat_slow_mode_seconds", 0) or 0)
    if seconds <= 0 or _can_cohost_moderate(session, user):
        return
    key = f"tc:chat_slow:{session.id}:{user.id}"
    try:
        now = time.time()
        if cache.add(key, now, seconds):
            return
        last = cache.get(key)
        wait = seconds if last is None else max(1, int(math.ceil(seconds - (now - float(last)))))
    except Exception:
        logger.exception("slow-mode cache check failed (session=%s)", getattr(session, "id", None))
        return
    raise Throttled(wait=wait, detail=f"Slow mode is on - you can send again in {wait}s.")


# ---------------------------------------------------------------------------
# 7. CLASS MATERIAL
# ---------------------------------------------------------------------------
class ClassMaterialViewSet(viewsets.ModelViewSet):
    """NOTE (fix): this previously had two gaps —
    1. perform_create only stamped uploaded_by; ANY authenticated user
       could upload a material row onto ANY classroom, not just its
       teacher/co-teacher/moderator. update/destroy had no checks either,
       so anyone could edit or delete anyone else's materials.
    2. get_queryset never restricted materials to people who actually have
       access to the classroom (has_access — a valid pass, or manage
       rights) — every signed-in user could read (and download the file
       for) ANY classroom's paid materials, whether or not they'd bought a
       pass, and listing with no ?classroom= leaked every material on the
       whole platform in one call.
    Both are fixed below, matching the has_access() gating already used by
    ClassQueryViewSet/ClassroomReviewViewSet for other paid content."""

    serializer_class = ClassMaterialSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = ClassMaterial.objects.all()

    def get_queryset(self):
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return ClassMaterial.objects.none()
        qs = ClassMaterial.objects.filter(classroom_id=classroom_id).select_related("uploaded_by")
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return ClassMaterial.objects.none()
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can upload materials."
            )
        # Refuse an exact copy of a file removed for copyright (similar copies are held after saving).
        from copyrights.services import precheck_upload
        _file = serializer.validated_data.get("file")
        if _file:
            _ok, _code, _why = precheck_upload(self.request.user, files=[_file])
            if not _ok:
                raise ValidationError({"file": _why, "code": _code})
        serializer.save(uploaded_by=self.request.user)

    # NOTE (fix — writable-classroom-FK reassigments sweep, item 5): both
    # `classroom` and `session` are writable on ClassMaterialSerializer,
    # but permission was only ever checked against the OLD
    # `serializer.instance.classroom` — same bug-class as Schedule/
    # Session/Pass above. Blocked outright; a material isn't meant to
    # move between classrooms/sessions after upload.
    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can edit this material."
            )
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A material can't be moved to a different classroom."})
        new_session = serializer.validated_data.get("session")
        if new_session is not None and new_session.id != serializer.instance.session_id:
            raise ValidationError({"session": "A material can't be reassigned to a different session."})
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can remove this material."
            )
        instance.delete()


# ---------------------------------------------------------------------------
# 8. CHAT MESSAGE
# ---------------------------------------------------------------------------
class ChatMessageViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """NOTE (fix): ?session=<id> previously returned EVERY chat message for
    that session to ANY authenticated user, and create() let anyone post a
    message into any session's chat — no check that the caller actually
    holds a valid pass for (or manages) that session's classroom. Both are
    now gated behind _has_room_access (teacher/org-staff bypass, or a
    currently valid pass) — same boundary used to gate joining the room
    itself in ClassSessionViewSet.join()."""

    serializer_class = ChatMessageSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (production): a long-running weekly classroom accumulates chat
    # history indefinitely — with no pagination this endpoint would return
    # every message ever sent in one response. Chronological ordering (see
    # ChatMessage.Meta) plus paging keeps a mobile client from having to
    # download months of chat just to open a session.
    pagination_class = TuitionClassPagination

    # NOTE (fix — spam/flood protection): posting a chat message had no
    # rate limit at all, unlike every other write action in this file
    # (session join/token, coupon validate). A single participant could
    # flood a live session's chat as fast as the network allows — annoying
    # at best, a denial-of-service on that session's chat for everyone else
    # at worst. Scoped (not global) so it only throttles the create()
    # action, never list(). Add e.g. {"chat_message_create": "20/min"} to
    # DRF's DEFAULT_THROTTLE_RATES in settings.py for this to take effect —
    # same pattern as session_join/session_token/coupon_validate below.
    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "chat_message_create"
            return [ScopedRateThrottle()]
        if self.action == "react":
            # NEW (Pass 12): a reaction is a single tap, not a typed
            # message, so it can legitimately fire more often than chat
            # sends — but "more often" still isn't "unbounded". Separate,
            # slightly looser scope than chat_message_create so the two
            # don't have to share one budget. Add e.g.
            # {"chat_reaction": "60/min"} to DEFAULT_THROTTLE_RATES.
            self.throttle_scope = "chat_reaction"
            return [ScopedRateThrottle()]
        if self.action in ("read", "read_receipts", "mark_read"):
            # NEW (read receipts) — a receipt fires roughly once per message
            # per viewer (or once per "mark everything visible as read"
            # batch), so it's a much lighter, more frequent call than
            # posting a message — same "own, looser scope" reasoning as
            # chat_reaction above. Add e.g. {"chat_read": "120/min"} to
            # DEFAULT_THROTTLE_RATES for this to take effect.
            self.throttle_scope = "chat_read"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def _session_or_none(self, session_id):
        return ClassSession.objects.filter(pk=session_id).select_related("classroom").first()

    def get_queryset(self):
        qs = ChatMessage.objects.filter(is_deleted=False).select_related("sender")
        # NEW (Pass 12 — chat reactions): ChatMessageSerializer.reaction_counts/
        # my_reaction iterate obj.reactions.all() per message — without this
        # prefetch that's one extra query PER message in a list response
        # (the classic N+1), the same trap get_result_counts() on
        # LivePollSerializer already avoids via LivePollViewSet's own
        # prefetch_related("responses").
        qs = qs.prefetch_related("reactions__user")
        # NEW (reply feature) — ChatMessageSerializer.get_reply_to_detail()
        # reads obj.reply_to.sender; without this select_related that's two
        # extra queries per replied-to message in a list response.
        qs = qs.select_related("reply_to", "reply_to__sender")
        # NEW (read receipts) — ChatMessageSerializer.read_count/seen_by_me
        # iterate obj.reads.all() per message; same N+1 avoidance as
        # reactions above.
        qs = qs.prefetch_related("reads__user")
        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs.none()
        session = self._session_or_none(session_id)
        if not session or not _has_room_access(session.classroom, self.request.user):
            return qs.none()
        qs = qs.filter(session_id=session_id)
        # Block applies in class chat too: lines from someone in a block relationship with me are
        # hidden - except the teacher's (announcements / paid-class content must not vanish).
        from user_profile.services import blocked_user_ids

        _hidden = blocked_user_ids(self.request.user)
        if _hidden:
            qs = qs.exclude(Q(sender_id__in=_hidden) & ~Q(sender_id=session.classroom.teacher_id))
        # NEW (message search) — ?search=<text> on the existing list
        # endpoint, scoped to the ?session= the caller already had access
        # to above (never a cross-session search — that would need its own
        # access check per session). Deliberately a plain case-insensitive
        # substring match, not full-text search: a single live session's
        # chat history is small enough (paginated at that) that a DB index
        # buys little, and this keeps the feature backend-agnostic (works
        # the same on SQLite in dev and Postgres in prod). Blank/whitespace-
        # only search terms are ignored rather than matching everything.
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(message__icontains=search)
        return qs

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        if not session or not _has_room_access(session.classroom, self.request.user):
            raise PermissionDenied("A valid pass is required to chat in this session.")
        user = self.request.user
        # X12 live safety, in this order (cheapest + most certain first; every guard fails OPEN on a cache blip):
        #   1. mute        a muted person gets 403 chat_muted (+ muted_until) - they can still watch and listen
        #   2. slow mode   (existing)
        #   3. flood       too many lines inside the window -> 429 chat_flood           (strike on the first breach)
        #   4. duplicate   the same line again -> 409 duplicate_message                 (strike unless a double tap)
        #   5. word filter off / flag / mask / block per session, + the minor contact-info rule (cannot be switched off)
        # The host tier (teacher / co-teacher / moderator, on-stage co-host) skips 3-5: they are the ones who run the room.
        live_abuse.ensure_can_chat(session, user)
        _enforce_slow_mode(session, user)
        text = serializer.validated_data.get("message") or ""
        result = None
        if not live_abuse.exempt(session, user):
            live_abuse.check_flood(session, user)
            live_abuse.check_duplicate(session, user, text)
        if not live_moderation.is_host_tier(session, user):
            result = live_safety.filter_chat_text(
                text, live_safety.effective_filter_mode(session),
                strict_contact=live_safety.strict_contact_rule(session, user, sender_is_staff=False),
            )
            if result.blocked:
                self._refuse_blocked_line(session, user, result)
        if result is not None and result.action == "mask":
            serializer.save(sender=user, message=result.text)
            live_moderation.log_event(
                session, "filter_mask", actor=None, target=user, reason=result.reason,
                chat_message_id=serializer.instance.id,
            )
        else:
            serializer.save(sender=user)

        # Word filter in "flag" mode: the line stays, a moderator sees it in the flagged-chat queue.
        if result is not None and result.action == "flag":
            flagged_reason = f"auto:{result.reason}"[:100]
            ChatMessage.objects.filter(pk=serializer.instance.pk).update(is_flagged=True, flagged_reason=flagged_reason)
            serializer.instance.is_flagged = True
            serializer.instance.flagged_reason = flagged_reason

        # NEW (Pass 14 — automatic profanity/spam filter). The other
        # trigger path for ChatMessage.is_flagged, alongside a human
        # report (see ChatMessageReportViewSet.create below) — that
        # path already existed, this one was documented on the model
        # but never actually wired up (screen_message was imported and
        # unused). Screened AFTER save, never before: a false positive
        # here must never block a real message from posting, it only
        # ever adds the message to a moderator's flagged-chat queue —
        # same "flag, don't delete" contract the report path already
        # follows.
        is_flagged, reason = (False, "")
        if not serializer.instance.is_flagged and (result is None or result.action == "allow"):
            is_flagged, reason = screen_message(serializer.instance.message)
        if is_flagged:
            ChatMessage.objects.filter(pk=serializer.instance.pk).update(
                is_flagged=True, flagged_reason=f"auto:{reason}"
            )
            serializer.instance.is_flagged = True
            serializer.instance.flagged_reason = f"auto:{reason}"

        # Realtime push (see realtime.py) — REST create() is still the
        # only write path (validation/throttling stays here); this just
        # fans the already-saved message out so listeners don't have to
        # poll for it.
        broadcast_to_session(session.id, "chat.message", ChatMessageSerializer(serializer.instance).data)

    def _refuse_blocked_line(self, session, user, result):
        """The line was blocked by the word filter / the minor contact rule: audit row, one strike (3 strikes inside the
        window = automatic chat mute), then 400 with a stable code the app switches on."""
        contact = result.reason == "contact_info"
        live_moderation.log_event(
            session, "minor_guard" if contact else "filter_block", actor=None, target=user, reason=result.reason,
        )
        strike = live_abuse.register_strike(session, user, "contact_info" if contact else "filter_block")
        extra = {"reason": result.reason}
        if strike:
            extra["muted_until"] = strike["until"].isoformat()
        if contact:
            raise live_abuse.LiveSafetyError(
                "Phone numbers, links and social handles cannot be shared in this class chat.",
                code="contact_info_blocked", status_code=400, **extra,
            )
        raise live_abuse.LiveSafetyError(
            "That message was not sent because it breaks the class chat rules.",
            code="message_blocked", status_code=400, **extra,
        )

    def perform_destroy(self, instance):
        # Sender can remove their own message; a host can moderate anyone's.
        if instance.sender_id != self.request.user.id and not _can_cohost_moderate(
            instance.session, self.request.user
        ):
            raise PermissionDenied("You can only delete your own messages.")
        instance.is_deleted = True  # soft delete
        instance.save(update_fields=["is_deleted"])
        broadcast_to_session(instance.session_id, "chat.message_deleted", {"id": instance.id})

    @action(detail=True, methods=["post", "delete"])
    def react(self, request, pk=None):
        """NEW (Pass 12) — chat reactions (👍❤️😂). Deliberately NOT a
        separate ChatReactionViewSet: a reaction only ever makes sense in
        the context of one specific message, so this stays a sub-resource
        of chat-messages/, same shape as LivePollViewSet.vote()/close()
        above (a detail action, not its own router entry).

        POST {"reaction": "heart"} — sets or changes the caller's own
        reaction on this message (upsert, same "changing your answer"
        shape as PollResponse.update_or_create in LivePollViewSet.vote()).
        DELETE — removes the caller's own reaction, if any (no-op, not an
        error, if they hadn't reacted — same "idempotent unreact" contract
        a toggle button on the frontend wants).

        Looked up directly rather than via get_object()/get_queryset(),
        same reason as LivePollViewSet.vote()/close(): get_queryset() only
        applies the ?session= filter for the 'list' action, so routing a
        detail action through it would 404 every call (no ?session= is
        sent on a POST to a detail URL).
        """
        message = ChatMessage.objects.filter(pk=pk, is_deleted=False).select_related("session__classroom").first()
        if message is None:
            raise NotFound("Chat message not found.")
        if not _has_room_access(message.session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to react in this session.")

        if request.method == "DELETE":
            ChatReaction.objects.filter(message=message, user=request.user).delete()
        else:
            serializer = ChatReactionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            ChatReaction.objects.update_or_create(
                message=message,
                user=request.user,
                defaults={"reaction": serializer.validated_data["reaction"]},
            )

        # Push the message's full, freshly-recomputed reaction_counts —
        # same "broadcast the recomputed tally, not just the one delta"
        # choice LivePollViewSet.vote() makes for poll.updated, so a
        # client that missed an earlier reaction event still ends up with
        # a correct count rather than a drifted one.
        fresh = ChatMessage.objects.prefetch_related("reactions__user").get(pk=message.pk)
        broadcast_to_session(
            message.session_id,
            "chat.reaction",
            {
                "message_id": fresh.id,
                "reaction_counts": ChatMessageSerializer(fresh).data["reaction_counts"],
            },
        )
        return Response(ChatMessageSerializer(fresh, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def pin(self, request, pk=None):
        """NEW (Pass 13) — pin an important message/announcement so it
        doesn't get lost in live-chat scroll. Host-only (same
        `_can_moderate_session` boundary as poll create/close), and
        deliberately AT MOST ONE pinned message per session: pinning a
        new one unpins whichever was pinned before, in the same call, so
        `is_pinned=True` on `ChatMessage` is never ambiguous about which
        message is "the" pin (see the field's own docstring in
        models.py for why this couldn't just be a DB constraint).
        """
        message = ChatMessage.objects.filter(pk=pk, is_deleted=False).select_related("session__classroom").first()
        if message is None:
            raise NotFound("Chat message not found.")
        if not _can_cohost_moderate(message.session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, moderator, or a co-host guest can pin a message.")

        ChatMessage.objects.filter(session_id=message.session_id, is_pinned=True).exclude(pk=message.pk).update(
            is_pinned=False, pinned_by=None, pinned_at=None
        )
        message.is_pinned = True
        message.pinned_by = request.user
        message.pinned_at = timezone.now()
        message.save(update_fields=["is_pinned", "pinned_by", "pinned_at"])

        broadcast_to_session(
            message.session_id, "chat.pinned", ChatMessageSerializer(message, context={"request": request}).data
        )
        return Response(ChatMessageSerializer(message, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def unpin(self, request, pk=None):
        message = ChatMessage.objects.filter(pk=pk, is_deleted=False).select_related("session__classroom").first()
        if message is None:
            raise NotFound("Chat message not found.")
        if not _can_cohost_moderate(message.session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, moderator, or a co-host guest can unpin a message.")

        message.is_pinned = False
        message.pinned_by = None
        message.pinned_at = None
        message.save(update_fields=["is_pinned", "pinned_by", "pinned_at"])
        broadcast_to_session(message.session_id, "chat.unpinned", {"id": message.id})
        return Response(ChatMessageSerializer(message, context={"request": request}).data)

    # -----------------------------------------------------------------
    # NEW — read receipts (WhatsApp-style "seen by"). See ChatMessageRead's
    # docstring in models.py for how this differs from the existing
    # SessionReadState unread-BADGE watermark (ClassSessionViewSet.mark_read
    # below) — that one is "how many messages has THIS user not looked at
    # yet"; this one is "who has looked at THIS message". Both stay, for
    # different UI.
    # -----------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        """Mark a single message as read/seen by the caller. Idempotent —
        re-marking an already-read message is a no-op, not an error, same
        "safe to call again" contract as `react`'s DELETE above. Looked up
        directly rather than via get_object()/get_queryset() for the same
        reason `react`/`pin` are: get_queryset()'s ?session= filter only
        applies to the 'list' action.
        """
        message = ChatMessage.objects.filter(pk=pk, is_deleted=False).select_related("session__classroom").first()
        if message is None:
            raise NotFound("Chat message not found.")
        if not _has_room_access(message.session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to read chat in this session.")

        _, created = ChatMessageRead.objects.get_or_create(message=message, user=request.user)
        if created:
            # Only push a socket event on an ACTUAL new receipt — re-marking
            # an already-read message must never spam the room with no-op
            # events, same "only broadcast on a real change" discipline as
            # perform_destroy's soft-delete above.
            broadcast_to_session(
                message.session_id,
                "chat.read",
                {
                    "message_id": message.id,
                    "user": UserMiniSerializer(request.user).data,
                    "read_count": message.reads.count(),
                },
            )
        return Response(ChatMessageSerializer(message, context={"request": request}).data)

    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request):
        """Bulk version of `read` above — POST {"session": <id>, "up_to": <message_id>}
        marks every not-yet-read, not-own message in that session up to and
        including `up_to` as read in one call. This is what the chat panel
        calls when it's opened (or scrolled to the bottom), instead of
        firing one `read` request per visible message — same "one call for
        a batch of implied reads" shape as SessionReadState's own
        `last_read_chat_message_id` watermark, just recording a receipt
        PER MESSAGE instead of a single pointer, since the whole point here
        is per-message "who's seen this", not just an unread count.
        """
        session_id = request.data.get("session")
        up_to = request.data.get("up_to")
        if not session_id or not up_to:
            raise ValidationError({"detail": "Both 'session' and 'up_to' are required."})
        session = self._session_or_none(session_id)
        if not session or not _has_room_access(session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to read chat in this session.")

        # Never a receipt on your own messages — you obviously "saw" what
        # you sent, and it would only clutter your own bubbles' seen-by list.
        candidate_ids = list(
            ChatMessage.objects.filter(session_id=session_id, is_deleted=False, id__lte=up_to)
            .exclude(sender_id=request.user.id)
            .values_list("id", flat=True)
        )
        already_read = set(
            ChatMessageRead.objects.filter(message_id__in=candidate_ids, user=request.user).values_list(
                "message_id", flat=True
            )
        )
        new_ids = [mid for mid in candidate_ids if mid not in already_read]
        if new_ids:
            ChatMessageRead.objects.bulk_create(
                [ChatMessageRead(message_id=mid, user=request.user) for mid in new_ids],
                ignore_conflicts=True,  # belt-and-suspenders against a racing single `read` call
            )
            # One broadcast per newly-read message, same event shape `read`
            # emits — a busy chat opening for the first time might fire a
            # short burst of these, which is fine: it's exactly as many
            # "someone read something" events as if each had come in one by
            # one via `read`.
            for mid in new_ids:
                broadcast_to_session(
                    session_id,
                    "chat.read",
                    {
                        "message_id": mid,
                        "user": UserMiniSerializer(request.user).data,
                        "read_count": ChatMessageRead.objects.filter(message_id=mid).count(),
                    },
                )
        return Response({"marked_read": len(new_ids)})

    @action(detail=True, methods=["get"], url_path="read-receipts")
    def read_receipts(self, request, pk=None):
        """GET — full "seen by" list (who + when) for one message, for the
        sender (or anyone else who can see the chat) to tap a message and
        check. Kept out of the main list-serializer payload deliberately —
        see read_count/seen_by_me's docstring on ChatMessageSerializer —
        and returned oldest-first (ChatMessageRead.Meta.ordering) so the
        UI can show "first seen by X, then Y" if it wants to.
        """
        message = ChatMessage.objects.filter(pk=pk, is_deleted=False).select_related("session__classroom").first()
        if message is None:
            raise NotFound("Chat message not found.")
        if not _has_room_access(message.session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to view this session's chat.")

        receipts = ChatMessageRead.objects.filter(message=message).select_related("user")
        return Response(ChatMessageReadSerializer(receipts, many=True).data)


# ---------------------------------------------------------------------------
# NEW (Pass 14 audit — per-message chat reports / abuse-reporting safety
# feature). A participant flags one chat message as abusive/spam/off-topic/
# inappropriate; the classroom's teacher/co-teacher/moderator (or platform
# staff) reviews the flagged-message queue and actions or dismisses it —
# same "queue + review" shape as ClassroomReportViewSet above, just scoped
# to a single message instead of a whole classroom. See ChatMessageReport
# in models.py and ChatMessageReportSerializer in serializers.py — both
# already existed; this viewset was the missing wiring.
#
#   POST chat-message-reports/                file a report
#                                              {"message": <id>, "reason": "abusive", "note": "..."}
#   GET  chat-message-reports/?session=<id>   moderator's flagged queue for one session
#                                              (optional &status=pending/actioned/dismissed)
#   GET  chat-message-reports/                 platform staff: everything; everyone else: reports THEY filed
#   POST chat-message-reports/{id}/review/    moderator/staff: {"status": "actioned"|"dismissed"}
# ---------------------------------------------------------------------------
class ChatMessageReportViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    serializer_class = ChatMessageReportSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = ChatMessageReport.objects.select_related(
        "message", "message__session", "message__session__classroom", "message__sender", "reporter", "reviewed_by"
    )

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")

        session_id = self.request.query_params.get("session")
        if session_id:
            # Moderator queue for one session's chat — same boundary as
            # ChatMessageViewSet.pin/unpin (host tier, not just "has a
            # valid pass"), so a student can't browse who reported whom.
            session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
            if not session or not (_can_moderate_session(session, user) or user.is_staff):
                return qs.none()
            qs = qs.filter(message__session_id=session_id)
            return qs.filter(status=status_filter) if status_filter else qs

        if user.is_staff:
            return qs.filter(status=status_filter) if status_filter else qs

        # Default (no ?session= filter, non-staff caller): only the
        # reports this user personally filed — never another user's.
        return qs.filter(reporter=user)

    def perform_create(self, serializer):
        message = serializer.validated_data["message"]
        if not _has_room_access(message.session.classroom, self.request.user):
            raise PermissionDenied("A valid pass is required to report a message in this session.")
        if message.sender_id == self.request.user.id:
            raise PermissionDenied("You can't report your own message.")

        # NOTE: `unique_together = ("message", "reporter")` on the model
        # (see its docstring in models.py) — re-filing a report against
        # the same message updates the existing row (fresh reason/note,
        # back to PENDING for re-review) rather than erroring on the
        # unique constraint or silently duplicating the queue entry.
        report, _created = ChatMessageReport.objects.update_or_create(
            message=message,
            reporter=self.request.user,
            defaults={
                "reason": serializer.validated_data.get("reason", ChatMessageReport.Reason.OTHER),
                "note": serializer.validated_data.get("note", ""),
                "status": ChatMessageReport.Status.PENDING,
            },
        )

        # A filed report is itself a strong-enough signal to pull the
        # message into the flagged-chat queue immediately — same "either
        # trigger path (profanity filter OR a report) lands the message
        # in the same queue" reasoning already documented on
        # ChatMessage.is_flagged.
        if not message.is_flagged:
            message.is_flagged = True
            message.flagged_reason = f"reported:{report.reason}"
            message.save(update_fields=["is_flagged", "flagged_reason"])

        # Bridge to the platform-wide report system (audit item 16): the classroom moderator
        # queue above stays as it is, but the same report now also counts in ContentReport, so
        # it shows up in the central audit trail, report_stats and the brigading guard.
        # off_topic is a classroom matter, not a platform violation, so it is not forwarded.
        _platform_reason = {"abusive": "harassment", "spam": "spam", "inappropriate": "nudity", "other": "other"}.get(
            report.reason)
        if _platform_reason:
            try:
                from user_profile.services import file_report

                file_report(self.request.user, "class_chat", message.id, _platform_reason, report.note)
            except Exception:  # never let the central log break the classroom report
                import logging

                logging.getLogger(__name__).exception("bridge to ContentReport failed")

        serializer.instance = report

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Classroom teacher/co-teacher/moderator, or platform staff.
        Moves a report out of PENDING into actioned/dismissed. Actioning
        one also soft-deletes the reported message itself (same
        moderation effect as ChatMessageViewSet.perform_destroy) so a
        moderator doesn't have to separately go delete it after acting on
        the report."""
        report = self.get_object()
        if not (_can_moderate_session(report.message.session, request.user) or request.user.is_staff):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can review a report.")

        new_status = request.data.get("status")
        valid_statuses = {
            s for s, _ in ChatMessageReport.Status.choices if s != ChatMessageReport.Status.PENDING
        }
        if new_status not in valid_statuses:
            raise ValidationError({"status": f"Must be one of {sorted(valid_statuses)}."})

        report.status = new_status
        report.reviewed_by = request.user
        report.reviewed_at = timezone.now()
        report.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        if new_status == ChatMessageReport.Status.ACTIONED and not report.message.is_deleted:
            report.message.is_deleted = True
            report.message.save(update_fields=["is_deleted"])
            broadcast_to_session(report.message.session_id, "chat.message_deleted", {"id": report.message_id})

        # P-F15: the reporter hears how it ended, and the bridged platform-wide ContentReport (see perform_create) is
        # closed in step with it so it does not stay "open" forever in the central queue / SLA sweep.
        try:
            from user_profile.models import ContentReport
            from user_profile.report_automation import notify_outcome

            _outcome = "actioned" if new_status == ChatMessageReport.Status.ACTIONED else "dismissed"
            ContentReport.objects.filter(
                reporter_id=report.reporter_id, target_type="class_chat", target_id=str(report.message_id),
                status__in=("open", "reviewed"),
            ).update(status=_outcome)
            notify_outcome([report.reporter_id], _outcome, "class_chat", report.message_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("classroom report outcome notification failed")

        return Response(ChatMessageReportSerializer(report).data)


# ---------------------------------------------------------------------------
# 9. LIVE POLL + RESPONSE
# ---------------------------------------------------------------------------
def _poll_chapter(session, poll, user):
    """X12: drop a "Poll: ..." / "Quiz: ..." marker on the recording timeline (silent no-op when nothing is recording)."""
    from ..recording_tools import add_chapter

    is_quiz = poll.correct_option_index is not None
    add_chapter(
        session, "quiz" if is_quiz else "poll", f"{'Quiz' if is_quiz else 'Poll'}: {poll.question[:80]}",
        user=user, ref_type="poll", ref_id=poll.id,
    )


class LivePollViewSet(viewsets.ModelViewSet):
    """NOTE (fix): several gaps here previously —
    1. ?session=<id> returned every poll (question/options/result_counts)
       for that session to ANY authenticated user, and no filter at all
       returned EVERY poll on the whole platform. List is now gated
       behind _has_room_access, same boundary as ChatMessageViewSet.
    2. perform_create let any signed-in user create a poll on any
       session — now restricted to the session's teacher/co-teacher/
       moderator via _can_moderate_session.
    3. retrieve/update/destroy had no object-level access check at all
       (any authenticated user could GET/PATCH/DELETE any poll by id) —
       get_object() below now enforces the same rule as list, and
       perform_update/perform_destroy are host-only.
    4. vote()/close() look the poll up directly rather than through
       get_object(), since get_queryset() below only applies the
       ?session= filter for the 'list' action — using it for detail
       routes too would 404 every vote/close call (no ?session= query
       param is sent on those POSTs).
    """

    serializer_class = LivePollSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    # NOTE (perf): get_result_counts() below reads obj.responses per poll —
    # prefetch_related turns that from one query per poll into one query
    # total for the whole list.
    queryset = LivePoll.objects.prefetch_related("responses")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            # Detail routes (retrieve/update/destroy) authorize via
            # get_object()/perform_* below instead of this queryset.
            return qs
        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs.none()
        session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
        if not session or not _has_room_access(session.classroom, self.request.user):
            return qs.none()
        return qs.filter(session_id=session_id)

    def get_object(self):
        obj = super().get_object()
        if self.action == "retrieve":
            if not _has_room_access(obj.session.classroom, self.request.user):
                raise PermissionDenied("A valid pass is required to view this poll.")
            self._show_answer = _can_moderate_session(obj.session, self.request.user)
        return obj

    def get_serializer_context(self):
        """QUIZ PUSH: only moderators get an unrevealed correct option back; everyone gets their own vote.

        create/update/partial_update are already moderator-gated in perform_*, so by the time the
        response is built the caller is a moderator. list works out the session from ?session=.
        """
        ctx = super().get_serializer_context()
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if request is None or user is None or not user.is_authenticated:
            return ctx
        if self.action in ("create", "update", "partial_update"):
            ctx["show_answer"] = True
        elif self.action == "retrieve":
            ctx["show_answer"] = getattr(self, "_show_answer", False)
        elif self.action == "list":
            sid = request.query_params.get("session")
            session = ClassSession.objects.filter(pk=sid).first() if str(sid or "").isdigit() else None
            if session is not None:
                ctx["show_answer"] = _can_moderate_session(session, user)
                ctx["my_votes"] = dict(
                    PollResponse.objects.filter(student=user, poll__session=session).values_list(
                        "poll_id", "selected_option_index"
                    )
                )
        return ctx

    def perform_create(self, serializer):
        session = serializer.validated_data["session"]
        if not _can_moderate_session(session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start a poll.")
        serializer.save(created_by=self.request.user)
        broadcast_to_session(session.id, "poll.created", LivePollSerializer(serializer.instance).data)
        _poll_chapter(session, serializer.instance, self.request.user)

    def perform_update(self, serializer):
        if not _can_moderate_session(serializer.instance.session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this poll.")
        # NOTE (fix — same class of gap as assigments/Notice/ClassHoliday
        # perform_update above, found in the same pass): `session` is
        # writable on `LivePollSerializer` and — unlike assigments/Query,
        # which cross-validate `session.classroom_id == classroom.id` in
        # their serializer's `validate()` — nothing here stops a PATCH from
        # reassigning a poll to ANY session, including one in a classroom
        # the caller doesn't moderate at all (only the poll's original
        # session was ever permission-checked). Pre-existing gap (this
        # `perform_update` already existed before this pass; only
        # PollApi.update() — which deliberately never sends `session` — is
        # new), closed the same way: the session can't be changed via
        # update, only question/options can.
        new_session = serializer.validated_data.get("session")
        if new_session is not None and new_session.id != serializer.instance.session_id:
            raise ValidationError({"session": "A poll can't be moved to a different session."})
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_moderate_session(instance.session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this poll.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def vote(self, request, pk=None):
        poll = LivePoll.objects.select_related("session__classroom").filter(pk=pk).first()
        if poll is None:
            raise NotFound("Poll not found.")
        if not _has_room_access(poll.session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to vote in this session.")
        if not poll.is_active:
            raise ValidationError("This poll is closed.")

        idx = request.data.get("selected_option_index")
        serializer = PollResponseSerializer(data={"poll": poll.id, "selected_option_index": idx})
        serializer.is_valid(raise_exception=True)

        # NOTE (fix): PollResponse has unique_together = ("poll", "student"),
        # but `student` is read-only on this serializer (set below, not from
        # the client), so DRF's automatic UniqueTogetherValidator never
        # fires. A student changing their answer (a second vote/() call on
        # the same poll) previously hit an unhandled IntegrityError -> 500
        # instead of either being rejected cleanly or, more usefully,
        # letting them change their vote. Upsert instead: create on first
        # vote, update the choice on any later call.
        response, _created = PollResponse.objects.update_or_create(
            poll=poll,
            student=request.user,
            defaults={"selected_option_index": serializer.validated_data["selected_option_index"]},
        )
        # Push fresh result_counts (a SerializerMethodField on
        # LivePollSerializer, recomputed from the DB below) rather than
        # just the one vote — every listening client's tally stays
        # correct even if it missed an earlier vote event.
        broadcast_to_session(
            poll.session_id, "poll.updated", LivePollSerializer(LivePoll.objects.get(pk=poll.pk)).data
        )
        return Response(PollResponseSerializer(response).data, status=201)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        # Looked up directly (not via get_queryset/get_object), same as
        # vote() above — get_queryset only accepts ?session= for the list
        # route; detail actions authorize explicitly instead.
        #
        # NOTE (fix): this used to only allow the poll's own `created_by`
        # to close it — inconsistent with perform_update/perform_destroy
        # right above, which correctly use _can_moderate_session (any
        # teacher/co-teacher/moderator on that session). As shipped, if a
        # co-teacher started a poll, the classroom's own TEACHER could not
        # close it themselves — a moderation gap where the most senior
        # person in the room was locked out of an action anyone with
        # host-tier access should be able to take. Aligned with the same
        # host-tier check used everywhere else in this viewset.
        poll = LivePoll.objects.select_related("session__classroom").filter(pk=pk).first()
        if poll is None:
            raise NotFound("Poll not found.")
        if not _can_moderate_session(poll.session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can close this poll.")
        poll.is_active = False
        poll.closed_at = timezone.now()
        poll.save(update_fields=["is_active", "closed_at"])
        broadcast_to_session(poll.session_id, "poll.closed", LivePollSerializer(poll).data)
        return Response(LivePollSerializer(poll, context={"show_answer": True}).data)

    @action(detail=True, methods=["post"])
    def reveal(self, request, pk=None):
        """QUIZ PUSH - host reveals the correct option. Closes the poll (no more votes), stamps
        `revealed_at` and pushes `poll.revealed` WITH the answer + explanation + correct_count to
        everyone. Only a quiz (correct_option_index set) can be revealed; calling it twice is a no-op
        (no second broadcast). Looked up directly like vote()/close() (get_queryset only filters list).
        """
        poll = LivePoll.objects.select_related("session__classroom").prefetch_related("responses").filter(pk=pk).first()
        if poll is None:
            raise NotFound("Poll not found.")
        if not _can_moderate_session(poll.session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can reveal the answer.")
        if poll.correct_option_index is None:
            raise ValidationError("This poll has no correct answer set - it isn't a quiz.")
        if poll.revealed_at is None:
            now = timezone.now()
            poll.revealed_at = now
            fields = ["revealed_at"]
            if poll.is_active:
                poll.is_active = False
                poll.closed_at = now
                fields += ["is_active", "closed_at"]
            poll.save(update_fields=fields)
            broadcast_to_session(poll.session_id, "poll.revealed", LivePollSerializer(poll).data)
        return Response(LivePollSerializer(poll, context={"show_answer": True}).data)

    @action(detail=False, methods=["post"], url_path="quick-create")
    def quick_create(self, request):
        """NEW (Pass 13) — fire a saved PollTemplate into a live session in
        one tap instead of retyping the same "Samajh aaya? Yes/No"-style
        question every time. Body: {"template": <id>, "session": <id>}.

        Deliberately its own list-level action (not a kwarg on regular
        create()) — the client sends a template id + session id, not a
        question/options payload, so it needs its own request shape and
        its own validation, same reasoning LivePollViewSet.vote()/close()
        already use for why those are separate detail actions rather than
        overloads of update()/destroy().
        """
        template_id = request.data.get("template")
        session_id = request.data.get("session")
        if not template_id or not session_id:
            raise ValidationError({"template": "Both `template` and `session` are required."})

        template = PollTemplate.objects.filter(pk=template_id).first()
        if template is None:
            raise NotFound("Poll template not found.")
        session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
        if session is None:
            raise NotFound("Session not found.")
        if template.classroom_id != session.classroom_id:
            raise ValidationError({"template": "This template belongs to a different classroom."})
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start a poll.")

        poll = LivePoll.objects.create(
            session=session, created_by=request.user, question=template.question, options=template.options,
            correct_option_index=template.correct_option_index, explanation=template.explanation,
        )
        broadcast_to_session(session.id, "poll.created", LivePollSerializer(poll).data)
        _poll_chapter(session, poll, request.user)
        return Response(LivePollSerializer(poll).data, status=201)


class PollTemplateViewSet(viewsets.ModelViewSet):
    """NEW (Pass 13) — see PollTemplate's docstring in models.py. Scoped to
    ?classroom=<id>, same _can_manage_classroom boundary assigments/
    Notice/ClassHoliday already use for their own classroom-scoped CRUD —
    a template is a teacher's own reusable shorthand for their classroom,
    not something students need to see or list.
    """

    serializer_class = PollTemplateSerializer
    permission_classes = [IsAuthenticated]
    queryset = PollTemplate.objects.select_related("classroom", "created_by")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return qs.none()
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_manage_classroom(classroom, self.request.user):
            return qs.none()
        return qs.filter(classroom_id=classroom_id)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can save a poll template.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this template.")
        # NOTE (fix — found during the Phase 3 audit sweep, same class of
        # gap as assigments/Notice/ClassHoliday/LivePoll/Submission/
        # ClassQuery above): `PollTemplateSerializer.classroom` is
        # writable, so without this a manager of the CURRENT classroom
        # could reassign a template to a different one via the same PATCH,
        # bypassing that classroom's own manage-permission check. Not part
        # of this pass's Dart CRUD work (PollTemplateApi.update() already
        # existed, unrelated to Phase 3's 7 APIs), but the same bug — fixed
        # while auditing every writable-classroom-field perform_update in
        # this file.
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A poll template can't be moved to a different classroom."})
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this template.")
        instance.delete()
