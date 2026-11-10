"""
message/views/conversations.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .. import message_requests, search_utils
from ..admin_only import admin_only_block_response
from ..group_rules import (
    check_daily_message_limit,
    check_group_permission,
    is_group_admin_or_mod,
)
from ..media_utils import create_group_media_for_message
from ..mentions import extract_mentioned_user_ids
from ..models import (
    BlockedUser,
    Conversation,
    ConversationParticipant,
    ConversationType,
    DisappearingDuration,
    Group,
    GroupMember,
    Message,
    MessageStatus,
    MessageType,
    Poll,
    PollOption,
    RequestStatus,
)
from ..offline_queue import flush_offline_queue
from ..push_utils import send_chat_message_push, send_mention_push
from ..serializers import (
    ConversationListSerializer,
    ConversationSettingsSerializer,
    ConversationWallpaperSerializer,
    MessageCreateSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    PollCreateSerializer,
    PollSerializer,
    ScheduleMessageSerializer,
)
from ..services import add_or_reactivate_participant
from ..tasks import generate_link_preview_task, transcribe_voice_message_task
from ..throttles import MessageSendIPThrottle, MessageSendThrottle
from ..user_display import build_user_mini

from ._common import (
    MessagePagination,
    StandardPagination,
    User,
    is_blocked_pair,
    with_message_list_prefetch,
)

logger = logging.getLogger(__name__)


# ======================================================================
# CONVERSATIONS
# ======================================================================
class ConversationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConversationListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = Conversation.objects.filter(
            memberships__user=self.request.user, memberships__left_at__isnull=True,
        ).distinct().select_related('group_detail', 'last_message_sender').prefetch_related(
            # 🔥 FIX (N+1) — see `ConversationListSerializer._membership`'s
            # own comment: this used to be re-queried (twice!) per row by
            # the serializer. One prefetch here = one extra query for the
            # WHOLE page instead of up to 2*N.
            Prefetch(
                'memberships',
                queryset=ConversationParticipant.objects.filter(user=self.request.user),
                to_attr='my_membership_list',
            ),
            # P-C5: the OTHER side of every private chat in one query for the whole page (was 1 query PER private
            # row in `get_other_participant`). Private chats only - a group page would otherwise pull every member.
            Prefetch(
                'memberships',
                queryset=ConversationParticipant.objects.filter(
                    conversation__type=ConversationType.PRIVATE,
                ).exclude(user=self.request.user).select_related('user'),
                to_attr='other_membership_list',
            ),
        # 🔥 FIX — a brand-new conversation (new group just created, or the
        # empty thread mutual-follow auto-creates — see
        # `message.services.get_or_create_conversation`) has
        # `last_message_at = NULL` until someone actually sends a message.
        # Plain `order_by('-last_message_at', '-created_at')` left where
        # NULL lands entirely up to the DB's default NULL-ordering, which
        # differs by engine: Postgres puts NULLs FIRST on DESC (new chat on
        # top), SQLite treats NULL as the smallest value so DESC puts it
        # LAST (new chat sinks to the bottom) — exactly the bug reported.
        # `Coalesce` removes the ambiguity: sort by "last activity time",
        # falling back to `created_at` when there's no message yet, so a
        # freshly created conversation sorts by *when it was created* (the
        # most recent thing that happened to it) and lands at the top on
        # every DB engine, consistently.
        ).annotate(
            _sort_ts=Coalesce('last_message_at', 'created_at')
        ).order_by('-_sort_ts', '-created_at')

        # ⚠️ IMPORTANT: filters below apply ONLY to `list` — every detail
        # action (`messages`, `settings`, `disappearing_messages`, ...)
        # also resolves through `get_object()` -> this same `get_queryset()`.
        # If the archived-exclude default applied there too, opening an
        # archived chat's messages (or any detail action on it) would 404
        # for its own owner — a real regression, not just an unused filter.
        if self.action != 'list':
            return qs

        # 🔥 NAYA — chat-list ab filterable hai. Pehle `is_archived`/
        # `is_pinned`/`is_muted` sirf per-user settings the (PATCH
        # `/settings/` se set hote the) par list endpoint unhe kabhi filter
        # nahi karta tha — matlab "Archived Chats" jaisi standard chat-app
        # screen backend me possible hi nahi thi (frontend ko client-side
        # poore list se chhaanna padta, jo scale pe galat/inefficient hai).
        # Default behavior WhatsApp jaisa: archived chats normal list se
        # bahar (jab tak explicitly `?archived=true` na maanga jaaye).
        #
        #   GET /conversations/                -> archived chhod ke sab
        #   GET /conversations/?archived=true   -> sirf archived
        #   GET /conversations/?pinned=true     -> sirf pinned
        #   GET /conversations/?muted=true      -> sirf muted
        #   GET /conversations/?unread=true     -> sirf jinme unread > 0
        # NOTE: `unique_together = ('conversation', 'user')` on
        # ConversationParticipant means there's at most one membership row
        # per (conversation, me) pair — so scoping every extra condition
        # below with `memberships__user=self.request.user` in its own
        # `.filter()` call still lands on that same single row even though
        # each call is its own join; kept as separate calls for readable
        # optional filters instead of one big conditional dict.
        params = self.request.query_params
        me = self.request.user
        archived_param = params.get('archived')
        if archived_param is not None:
            qs = qs.filter(memberships__user=me, memberships__is_archived=(archived_param.lower() == 'true'))
        else:
            qs = qs.exclude(memberships__user=me, memberships__is_archived=True)

        if (pinned := params.get('pinned')) is not None:
            qs = qs.filter(memberships__user=me, memberships__is_pinned=(pinned.lower() == 'true'))

        if (muted := params.get('muted')) is not None:
            qs = qs.filter(memberships__user=me, memberships__is_muted=(muted.lower() == 'true'))

        if params.get('unread') == 'true':
            qs = qs.filter(memberships__user=me, memberships__unread_count__gt=0)

        # 🔥 NAYA (M1-BE) — pending/declined message-requests normal inbox me
        # NAHI aate (wo `GET /message/requests/` me hain). Sirf `list` pe —
        # detail actions (messages/read_all/...) pending chat ke liye bhi
        # chalne chahiye taaki receiver request khol ke padh sake.
        qs = qs.filter(memberships__user=me, memberships__request_status=RequestStatus.ACCEPTED)

        return qs

    def get_serializer_context(self):
        return {'request': self.request}

    # 🔥 FIX — see throttles.py's own setup comment: message-send spam/abuse
    # guard was written but never actually applied here.
    def get_throttles(self):
        if self.action in ('messages', 'offline_queue_flush') and self.request.method == 'POST':
            # 🔥 NAYA — per-user (`MessageSendThrottle`) ke SAATH per-IP
            # safety net bhi (`MessageSendIPThrottle`). DRF dono list me hon
            # to dono check karta hai — jo bhi pehle trip ho, request block.
            # `offline_queue_flush` yahan bhi shamil hai kyunki wo bhi
            # (batch me) real messages banata hai — same abuse surface,
            # ek nayi throttle-scope banane ki zaroorat nahi (wo hi
            # ek-scope-settings.py-me-add-karna-bhool-jaana bug class hai
            # jo throttles.py me pehle bhi mila tha).
            return [MessageSendThrottle(), MessageSendIPThrottle()]
        return super().get_throttles()

    # 🔥 NAYA — GET /conversations/unread-count/
    # App-icon/bottom-nav badge ke liye ek hi lightweight number chahiye
    # hota hai — pehle iske liye frontend ko poori paginated chat-list
    # fetch karke client-side sum karna padta (galat approach: page size
    # se bada total ho to ye ganit hi galat aa jaata, aur ek extra bhaari
    # request hai sirf ek number ke liye). Ye single aggregate query hai —
    # koi row-level data wapas nahi jaati.
    #   GET /conversations/unread-count/               -> muted included
    #   GET /conversations/unread-count/?exclude_muted=true -> muted skip
    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        # `left_at__isnull=True` is the app's actual "still in this chat"
        # signal (see `bulk_delete`/leave-group above — a deleted/left chat
        # sets `left_at`, `Conversation` itself is never soft-deleted).
        # 🔥 NAYA (M1-BE) — pending/declined requests inbox badge me count
        # nahi hote; unka alag `message_requests_count` milta hai.
        qs = ConversationParticipant.objects.filter(
            user=request.user, left_at__isnull=True, request_status=RequestStatus.ACCEPTED,
        )
        if str(request.query_params.get('exclude_muted', '')).lower() == 'true':
            qs = qs.exclude(is_muted=True)
        total = qs.aggregate(total=Sum('unread_count'))['total'] or 0
        requests_count = ConversationParticipant.objects.filter(
            user=request.user, left_at__isnull=True, request_status=RequestStatus.PENDING,
            conversation__type=ConversationType.PRIVATE,
        ).count()
        return Response({'unread_count': total, 'message_requests_count': requests_count})

    # ======================================================================
    # 🔥 NAYA (TASK 29 — suggested facility) — CHAT / MEDIA EXPORT
    # ======================================================================
    # GET /message/conversations/<id>/export/
    #   ?type=chat   (default) -> full text transcript: every message this
    #                              user can still see (same "still visible
    #                              to me" rule as everywhere else — skips
    #                              deleted-for-everyone and deleted-for-me)
    #   ?type=media             -> only messages carrying a file, detected
    #                              by `file_url`/`file_urls` being set —
    #                              not a hardcoded MessageType list, since
    #                              this pass doesn't have visibility into
    #                              every media MessageType this app defines
    #   ?since=<ISO datetime>   -> only messages at/after this time
    #   ?until=<ISO datetime>   -> only messages strictly before this time
    #
    # Returned as one JSON payload with a `Content-Disposition: attachment`
    # header so hitting this URL downloads a file instead of rendering an
    # in-app API response.
    #
    # Scope/limits (documented rather than silently hit):
    #   - This bundles metadata + URLs, not the media bytes themselves.
    #     Actually fetching and zipping every file would mean downloading
    #     each one inside this request — for a media-heavy chat that's
    #     slow, memory-heavy work that belongs in a background job (this
    #     app already runs Celery for other async work in `tasks.py`), not
    #     something to bolt onto a synchronous GET. Not built here because
    #     the task list didn't specify the desired bundle format (zip? one
    #     archive per media type?) and guessing that wrong would mean
    #     redoing it — the client gets the direct file URLs instead and
    #     can fetch/zip them itself, or this can become a real async
    #     export job once the desired format is confirmed.
    #   - Hard-capped at `EXPORT_MAX_MESSAGES` per call (with `has_more` in
    #     the response) instead of an unbounded query, so exporting a
    #     multi-year group chat can't turn into one giant blocking request.
    #     A caller that needs the rest pages forward with `until` set to
    #     the oldest `created_at` it already has.
    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        conversation = self.get_object()

        export_type = (request.query_params.get('type') or 'chat').strip().lower()
        if export_type not in ('chat', 'media'):
            return Response(
                {'detail': "'type' must be 'chat' or 'media'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        messages_qs = (
            Message.objects.filter(conversation=conversation)
            .exclude(deleted_for_everyone=True)
            .exclude(deleted_for_users=request.user)
            .select_related('sender')
            .order_by('created_at')
        )

        since = request.query_params.get('since')
        if since:
            parsed = parse_datetime(since)
            if not parsed:
                return Response({'detail': "'since' must be an ISO datetime."}, status=status.HTTP_400_BAD_REQUEST)
            messages_qs = messages_qs.filter(created_at__gte=parsed)

        until = request.query_params.get('until')
        if until:
            parsed = parse_datetime(until)
            if not parsed:
                return Response({'detail': "'until' must be an ISO datetime."}, status=status.HTTP_400_BAD_REQUEST)
            messages_qs = messages_qs.filter(created_at__lt=parsed)

        EXPORT_MAX_MESSAGES = 5000
        # Fetch one extra row so we can tell "exactly at the cap" apart
        # from "more exist beyond the cap" without a separate count query.
        rows = list(messages_qs[:EXPORT_MAX_MESSAGES + 1])
        has_more = len(rows) > EXPORT_MAX_MESSAGES
        rows = rows[:EXPORT_MAX_MESSAGES]

        if export_type == 'media':
            rows = [m for m in rows if m.file_url or m.file_urls]

        items = [
            {
                'message_id': str(m.id),
                'type': m.type,
                'sender_id': str(m.sender_id) if m.sender_id else None,
                'sender_username': getattr(m.sender, 'username', None),
                'text': m.text,
                'file_url': m.file_url,
                'file_urls': m.file_urls,
                'thumbnail_url': m.thumbnail_url,
                'is_forwarded': m.is_forwarded,
                'is_edited': getattr(m, 'is_edited', False),
                'created_at': m.created_at.isoformat(),
            }
            for m in rows
        ]

        payload = {
            'conversation_id': str(conversation.id),
            'exported_by': str(request.user.id),
            'exported_at': timezone.now().isoformat(),
            'type': export_type,
            'count': len(items),
            'has_more': has_more,
            'messages': items,
        }

        filename = f"chat_export_{conversation.id}_{export_type}.json"
        return Response(payload, headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @action(detail=False, methods=['post'], url_path='start_private')
    def start_private(self, request):
        other_user_id = request.data.get('user_id')
        if not other_user_id:
            return Response({'detail': "'user_id' required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if str(other_user_id) == str(request.user.id):
            return Response({'detail': 'Khud se chat nahi bana sakte.'}, status=status.HTTP_400_BAD_REQUEST)

        other_user = get_object_or_404(User, id=other_user_id)

        blocked = BlockedUser.objects.filter(
            Q(blocker=request.user, blocked=other_user) | Q(blocker=other_user, blocked=request.user)
        ).exists()
        if blocked:
            return Response({'detail': 'Ye user block hai, chat start nahi ho sakti.'}, status=status.HTTP_403_FORBIDDEN)

        # S4 -- the other person limited who may start a chat with them (Settings > Privacy). Existing chats keep working.
        from user_profile import privacy as privacy_rules

        if privacy_rules.new_chat_blocked(request.user, other_user):
            return Response(
                {'detail': privacy_rules.MESSAGE_BLOCKED_TEXT, 'code': privacy_rules.MESSAGE_BLOCKED_CODE},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Minor-safety: an adult can't open a NEW chat with a minor who
        # doesn't follow them (message/minor_safety.py). Existing chats are
        # left alone.
        from ..minor_safety import MINOR_DM_BLOCKED_MESSAGE, dm_blocked_for_minor

        if dm_blocked_for_minor(request.user, other_user) and not Conversation.objects.filter(
            private_key=Conversation.make_private_key(request.user.id, other_user.id)
        ).exists():
            return Response({'detail': MINOR_DM_BLOCKED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)

        conversation, created = Conversation.get_or_create_private(request.user, other_user)
        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['patch'], url_path='settings')
    def update_settings(self, request, pk=None):
        conversation = self.get_object()
        membership = get_object_or_404(ConversationParticipant, conversation=conversation, user=request.user)
        serializer = ConversationSettingsSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # 🔧 GAP FIX (this session) — `message_api_service.dart` (getWallpaper/
    # setWallpaper/clearWallpaper) and `PROJECT_ARCHITECTURE.md` document
    # `GET/PATCH /conversations/<id>/wallpaper/` as its own endpoint,
    # separate from the combined `settings` action above. Per-user (same
    # `ConversationParticipant` row as mute/pin/label), so nobody else in
    # the chat sees or is affected by it.
    # GET  -> {"wallpaper_url": "..."} or {"wallpaper_url": null} if unset.
    # PATCH {"wallpaper_url": "..."} -> sets it; {"wallpaper_url": ""} or
    #       {"wallpaper_url": null} -> clears it back to the client default.
    @action(detail=True, methods=['get', 'patch'], url_path='wallpaper')
    def wallpaper(self, request, pk=None):
        conversation = self.get_object()
        membership = get_object_or_404(ConversationParticipant, conversation=conversation, user=request.user)

        if request.method == 'GET':
            return Response(ConversationWallpaperSerializer(membership).data)

        serializer = ConversationWallpaperSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # 🔥 Same-user multi-device sync (e.g. phone sets wallpaper, laptop
        # session open in the same chat should update live too) — NOT
        # broadcast to the other participant(s), this is private per-user
        # styling, unlike `disappearing_messages_updated` above which is a
        # shared conversation-level setting. Matches the `user_<uid>` group
        # pattern already used elsewhere in this file for personal sync.
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'user_{request.user.id}',
            {
                'type': 'conversation_wallpaper_updated',
                'conversation_id': str(conversation.id),
                'wallpaper_url': membership.wallpaper_url,
            }
        )

        return Response(ConversationWallpaperSerializer(membership).data)

    # 🔥 NAYA — chat list se ek ya kai chats ek saath delete karne ke liye.
    # Ye sirf REQUESTING USER ke liye chat hide karta hai (WhatsApp jaisa
    # "delete chat" — dusre participant/group members ko koi farak nahi
    # padta, unki chat waisi hi rehti hai). Membership row delete nahi
    # karte (taaki messages/unread history intact rahe agar chat wapas
    # khule to), sirf `left_at` set karte hain — jo `get_queryset()` me
    # already filter ho raha hai (`memberships__left_at__isnull=True`),
    # isliye delete hote hi ye conversation list se turant gayab ho jaati hai.
    # 🔥 NAYA — Temporary chat (disappearing messages) on/off ya duration
    # change karne ke liye. Poori conversation ke liye ek hi setting hai
    # (dono/sabhi participants ko wahi duration dikhta/lagta hai — WhatsApp
    # jaisa), isliye per-user settings (`update_settings` action, jo
    # `ConversationParticipant` pe hai) se ye alag rakha hai.
    # Group chat me sirf admin/moderator change kar sakte hain; private chat
    # me dono me se koi bhi (WhatsApp me bhi private chat ka disappearing
    # toggle dono side use kar sakte hain).
    @action(detail=True, methods=['patch'], url_path='disappearing_messages')
    def disappearing_messages(self, request, pk=None):
        conversation = self.get_object()
        duration = request.data.get('duration')
        valid_values = [choice[0] for choice in DisappearingDuration.choices]
        if duration not in valid_values:
            return Response(
                {'detail': f"'duration' invalid hai. Valid values: {valid_values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation.type == ConversationType.GROUP:
            # 🔥 FIX — pehle yahan is check ka apna alag raw
            # `GroupMember.objects.filter(...)` query tha, jabki
            # `group_rules.is_group_admin_or_mod` (ab cached) exact
            # same rule already implement karta hai. Teen jagah (yahan,
            # `add_participant_to_conversation`, `GroupViewSet.
            # _require_admin`) same "admin/mod, not banned" logic
            # independently duplicate thi — single source pe unify kiya.
            if not is_group_admin_or_mod(conversation.group_detail, request.user.id):
                raise PermissionDenied('Sirf group admin/moderator ye setting change kar sakte hain.')

        conversation.disappearing_messages_duration = duration
        conversation.save(update_fields=['disappearing_messages_duration'])

        # 🔥 Sabhi connected participants ko live inform karo taaki chat
        # screen me turant (naya) banner/label dikh jaaye, refresh ki
        # zaroorat na pade.
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'disappearing_messages_updated',
                'conversation_id': str(conversation.id),
                'duration': duration,
                'updated_by': str(request.user.id),
            }
        )

        return Response({'detail': 'Disappearing messages setting update ho gayi.', 'duration': duration})

    # 🔥 NAYA — Chat ko apna custom naam/nickname dene ke liye (sirf
    # tumhare account ke liye — dusre participant/group members ko nahi
    # dikhega). Empty string bhejo to label clear ho jaayega aur wapas
    # default naam (participant/group ka naam) dikhne lagega.
    @action(detail=True, methods=['patch'], url_path='label')
    def update_label(self, request, pk=None):
        conversation = self.get_object()
        membership = get_object_or_404(ConversationParticipant, conversation=conversation, user=request.user)

        label = request.data.get('label', '')
        if not isinstance(label, str):
            return Response({'detail': "'label' string honi chahiye."}, status=status.HTTP_400_BAD_REQUEST)
        label = label.strip()
        if len(label) > 100:
            return Response(
                {'detail': 'Label 100 characters se zyada nahi ho sakta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.label = label or None
        membership.save(update_fields=['label'])
        return Response({'detail': 'Label update ho gaya.', 'label': membership.label})

    @action(detail=False, methods=['post'], url_path='bulk_delete')
    def bulk_delete(self, request):
        conversation_ids = request.data.get('conversation_ids', [])
        if not isinstance(conversation_ids, list) or not conversation_ids:
            return Response(
                {'detail': "'conversation_ids' (list) required hai."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = ConversationParticipant.objects.filter(
            conversation_id__in=conversation_ids,
            user=request.user,
            left_at__isnull=True,
        ).update(left_at=timezone.now())

        return Response({
            'detail': f'{updated} chat(s) delete ho gayi.',
            'deleted_count': updated,
            'conversation_ids': conversation_ids,
        })

    # 🔥 NAYA — existing conversation me naya member add karne ke liye.
    # Sirf GROUP conversations ke liye valid hai (private 1-1 chat me
    # teesra banda add nahi ho sakta — usके liye group hi banao). Group
    # ke apne `/groups/<id>/members/` action jaisa hi behavior hai, bas
    # yahan conversation_id se entry point diya gaya hai jaisa frontend
    # ka `Participants` API contract expect karta hai.
    @action(detail=True, methods=['post'], url_path='participants')
    def add_participant_to_conversation(self, request, pk=None):
        conversation = self.get_object()
        if conversation.type != ConversationType.GROUP:
            return Response(
                {'detail': 'Sirf group conversation me participant add ho sakta hai.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'detail': "'user_id' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        group = getattr(conversation, 'group_detail', None)
        if group and group.is_private:
            # 🔥 FIX — same unification as `disappearing_messages` above.
            if not is_group_admin_or_mod(group, request.user.id):
                raise PermissionDenied('Sirf group admin/moderator member add kar sakte hain.')

        target_user = get_object_or_404(User, id=user_id)

        with transaction.atomic():
            add_or_reactivate_participant(conversation, target_user)
            if group:
                GroupMember.objects.get_or_create(group=group, user=target_user, defaults={'added_by': request.user})
                Group.objects.filter(id=group.id).update(
                    members_count=group.group_members.filter(is_banned=False).count()
                )

        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        conversation = self.get_object()

        if request.method == 'GET':
            # 🔥 NAYA — disappearing messages jinki expiry nikal chuki hai
            # unhe list se hide karo (hard delete cleanup ke liye alag se
            # `cleanup_expired_messages` command chalta hai, yahan sirf
            # defensive filter hai taaki cleanup thoda late chale to bhi
            # user ko expired message na dikhe).
            qs = conversation.all_messages.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            ).exclude(
                # 🔧 GAP FIX — `is_scheduled`/`scheduled_for` fields already
                # existed on the model, but nothing filtered them out of the
                # normal timeline — a "send later" message would've shown up
                # to every participant immediately, defeating the whole
                # point. Scheduled-but-not-yet-sent messages are visible
                # only via `schedule_message`/`scheduled_messages_list`
                # (sender-only) until `send_scheduled_messages` delivers them.
                is_scheduled=True,
            ).select_related(
                'sender', 'reply_to', 'reply_to__sender'
            ).prefetch_related(
                'all_reactions', 'all_reactions__user',
                # 🔥 NAYA — poll messages ke liye. Ek chat me poll rare hote
                # hain (star/is_read jaisa hi chhota per-row query rehta agar
                # prefetch na hota), lekin jab hote hain to option/vote count
                # ke liye har poll message pe 1+N option query lagti, isliye
                # yahi prefetch kar dena behtar hai (bade groups me poll
                # zyada options ke saath ho to farak padta hai).
                'poll', 'poll__options', 'poll__options__votes',
            )
            qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — see helper above
            paginator = MessagePagination()
            page = paginator.paginate_queryset(qs, request, view=self)
            serializer = MessageSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        # 🔥 NAYA — 1-1 chat me agar dono me se koi ek doosre ko block kiye
        # hue hai to REST se message send nahi hone dena (pehle sirf naya
        # `start_private` conversation banate waqt block check hota tha,
        # existing conversation ke is endpoint pe kabhi nahi — is wajah se
        # block karne ke baad bhi dusra bandaa normally message bhej sakta
        # tha). Group conversations me ye check skip karte hain, block wahan
        # applicable hi nahi hai.
        # 🔥 FIX (Feature 11 — Announcements): sender group admin/mod hai ya
        # nahi, ye yahin capture kar lete hain (private chat ke liye hamesha
        # False) — isi ek value ko aage `Message.is_announcement` aur dono
        # push calls (`send_chat_message_push`/`send_mention_push`) me pass
        # karenge. Pehle ye check group-permission-gate ke liye hota tha
        # lekin result kahin store/reuse nahi hota tha, isliye announcement
        # flag REST se bheje gaye kisi bhi message pe kabhi set hi nahi hota
        # tha — push-side (`push_utils.py`) already sahi se handle karta hai
        # `is_announcement=True` ko, bas ye REST path use kabhi bhejta hi
        # nahi tha.
        is_announcement = False

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se message nahi bheja ja sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # 🔥 NAYA — group ka message_permission + daily_message_limit
            # enforce karo (pehle in settings ka backend pe koi asar hi
            # nahi padta tha).
            group = getattr(conversation, 'group_detail', None)
            if group:
                # M9a — admin-only: explicit code ('admins_only') ke saath 403
                blocked = admin_only_block_response(group, request.user.id)
                if blocked is not None:
                    return blocked
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)
                allowed, reason = check_daily_message_limit(group, request.user, conversation)
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_429_TOO_MANY_REQUESTS)
                # 🔥 FIX (Feature 11) — same admin/mod check jo permission-gate
                # ke liye already ho raha hai, uska result reuse karke
                # announcement flag decide karte hain (extra query nahi lagti,
                # cached `is_group_admin_or_mod` hi hai).
                is_announcement = is_group_admin_or_mod(group, request.user.id)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client_id = serializer.validated_data.get('client_id')
        if client_id:
            existing = conversation.all_messages.filter(sender=request.user, client_id=client_id).first()
            if existing:
                return Response(MessageSerializer(existing, context={'request': request}).data)

        # 🔥 NAYA — conversation ki current disappearing-messages duration se
        # is message ka expiry snapshot nikaal lo (settings baad me badle to
        # is message pe asar nahi padega, sirf naye messages pe padega).
        disappearing_delta = conversation.get_disappearing_timedelta()
        expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

        with transaction.atomic():
            message = serializer.save(
                conversation=conversation, sender=request.user, expires_at=expires_at,
                is_announcement=is_announcement,
            )

            conversation.last_message_text = (message.text or '')[:500]
            conversation.last_message_at = message.created_at
            conversation.last_message_sender = request.user
            conversation.last_message_type = message.type
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])

            ConversationParticipant.objects.filter(conversation=conversation).exclude(
                user=request.user
            ).update(unread_count=F('unread_count') + 1)

            # 🔥 FIX — REST se message bhejte waqt `MessageStatus` rows
            # (delivery/read tracking) pehle kabhi nahi banti thi — sirf WS
            # `ChatConsumer.save_message` ye karta tha. Har media/location
            # message (jo hamesha REST se jaate hain, WS text-first flow
            # ke against) is wajah se kabhi bhi "delivered" (double-tick)
            # state nahi dikhata tha jab tak recipient use actively read na
            # kar de — kyunki `mark_undelivered_as_delivered` (WS connect())
            # sirf EXISTING rows ko update karta hai, naya row nahi banata.
            # Ab dono paths consistent hain.
            other_participant_ids = list(
                ConversationParticipant.objects.filter(conversation=conversation)
                .exclude(user=request.user)
                .values_list('user_id', flat=True)
            )
            MessageStatus.objects.bulk_create(
                [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
                ignore_conflicts=True,
            )

            # 🔥 NAYA — @mentions resolve karke message pe attach karo.
            # Sirf conversation ke ACTIVE members hi mention ho sakte hain.
            mentioned_ids = extract_mentioned_user_ids(message.text, conversation, sender_id=message.sender_id)
            mentioned_ids = [uid for uid in mentioned_ids if uid != request.user.id]
            if mentioned_ids:
                message.mentioned_users.set(mentioned_ids)

            # 🔥 BUG FIX — group gallery ke liye GroupMedia row (pehle
            # kahin bhi nahi banti thi, see media_utils.py).
            create_group_media_for_message(message)

        # 🔥 NAYE (ADVANCED FEATURES) — dono background/async chalte hain
        # (Celery `.delay()`), request ko bilkul block nahi karte. Transaction
        # commit ho chuke hone ke BAAD enqueue kiye hain (with-block ke bahar)
        # taaki worker ko guaranteed committed row mile, race condition na ho.
        if message.type == MessageType.TEXT and message.text:
            generate_link_preview_task.delay(str(message.id))
        elif message.type == MessageType.AUDIO and message.file_url:
            transcribe_voice_message_task.delay(str(message.id))

        # NOTE: this payload must carry the same sender_* fields as
        # ChatConsumer.handle_new_message's websocket payload. This REST
        # endpoint is the path used for every media/location message and
        # for any text message sent while the socket briefly reconnects,
        # so leaving sender_name/sender_profile_photo out here made every
        # group member see "Unknown" as the sender for those messages.
        sender = build_user_mini(request.user)
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(message.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(request.user.id),
                'sender_name': sender['display_name'],
                'sender_username': sender['username'],
                'sender_first_name': sender['first_name'],
                'sender_last_name': sender['last_name'],
                'sender_profile_photo': sender['profile_photo'],
                'message_type': message.type,
                'text': message.text,
                'file_url': message.file_url,
                'file_urls': message.file_urls,
                'thumbnail_url': message.thumbnail_url,
                'meta': message.meta,
                'reply_to': str(message.reply_to.id) if message.reply_to else None,
                'client_id': client_id,
                'mentioned_user_ids': [str(uid) for uid in mentioned_ids],
                'created_at': message.created_at.isoformat(),
            }
        )

        other_recipients = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user=request.user)
            .values_list('user_id', flat=True)
        )
        sender_name = sender['display_name']

        # 🔥 NAYA — REST se gaya message (media message ya socket-down
        # fallback) bhi ConversationsScreen ko turant update karwaye.
        # `chat_{conversation.id}` group sirf unhi ko milta hai jo abhi
        # isi chat ke andar hain (ChatConsumer se joined); yahan har
        # recipient ke apne global `user_<id>` inbox group ko bhi ek
        # halka event bhejte hain (InboxConsumer se connect hota hai).
        # 🔥 NAYA (M1-BE) — recipient ke `request_status` ke hisaab se event:
        # accepted -> `inbox_update` (jaisa pehle), pending -> `message_request`,
        # declined -> kuch nahi.
        inbox_payload = {
            'type': 'inbox_update',
            'conversation_id': str(conversation.id),
            'message_id': str(message.id),
            'sender_id': str(request.user.id),
            'sender_name': sender_name,
            'last_message_text': message.text,
            'last_message_type': message.type,
            'created_at': message.created_at.isoformat(),
        }
        for uid, inbox_event in message_requests.inbox_events_for(
            conversation.id, other_recipients, inbox_payload,
        ):
            async_to_sync(channel_layer.group_send)(f'user_{uid}', inbox_event)

        # 🔥 FIX — jinhone ye conversation mute kar rakha hai unhe push
        # notification NAHI jaani chahiye (WhatsApp jaisa: chat list me
        # unread badge/message update to dikhta rahega — wo upar already
        # ho chuka hai — sirf phone pe notification popup/sound nahi
        # aayega). Pehle sirf `is_muted` DB me save ho raha tha, lekin
        # yahan check hi nahi ho raha tha isliye muted logon ko bhi
        # notification chali jaati thi.
        muted_user_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_recipients, is_muted=True,
            ).values_list('user_id', flat=True)
        )
        # 🔥 NAYA — mention hone waale members ko ALAG "mention" push
        # milta hai (mute state ko override karta hai — WhatsApp isi
        # tarah karta hai), isliye unhe generic chat-message push ki
        # list se nikaal dete hain taaki double notification na jaaye.
        mentioned_set = set(mentioned_ids)
        push_recipients = [
            uid for uid in other_recipients if uid not in muted_user_ids and uid not in mentioned_set
        ]

        if push_recipients:
            send_chat_message_push(
                recipient_ids=push_recipients,
                sender_name=sender_name,
                message_text=message.text,
                message_type=message.type,
                conversation_id=conversation.id,
                message_id=message.id,
                is_announcement=is_announcement,
            )

        if mentioned_ids:
            send_mention_push(
                recipient_ids=mentioned_ids,
                sender_name=sender_name,
                message_text=message.text,
                conversation_id=conversation.id,
                message_id=message.id,
                is_announcement=is_announcement,
            )

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    # ======================================================================
    # 🔧 GAP FIX (task 49) — offline-queue flush endpoint. `offline_queue.
    # flush_offline_queue()` was fully implemented but unreachable — no
    # `@action`, no `path()`, so the Flutter client's reconnect flow (see
    # that module's own "FRONTEND CONTRACT" footer) had nowhere to POST
    # its locally-queued messages. Registered on `ConversationViewSet`
    # (router-based, so no urls.py change needed — same as `messages`/
    # `pinned`/etc. above) since it's conceptually the same "send into
    # this conversation" action, just batched + idempotent.
    # ======================================================================
    # POST /message/conversations/<id>/offline-queue/
    #   body: {"messages": [{"client_id": "...", "type": "text", ...}, ...]}
    #   (see `offline_queue.flush_offline_queue`'s docstring for the full
    #   per-item shape and the response shape it returns)
    #
    # ⚠️ Same permission/block gating as the live `messages()` POST above
    # — this can create real messages too, so it needs the exact same
    # guards, not a lighter set just because it's a reconnect/batch path.
    # `check_daily_message_limit` is checked once for the whole batch
    # (not per item) — it's a "is this conversation over its daily cap
    # right now" gate, same as the live path calls it once per request.
    MAX_OFFLINE_QUEUE_BATCH = 100  # defensive cap — one reconnect shouldn't be able to submit an unbounded batch in a single request

    @action(detail=True, methods=['post'], url_path='offline-queue')
    def offline_queue_flush(self, request, pk=None):
        conversation = self.get_object()

        queued_messages = request.data.get('messages')
        if not isinstance(queued_messages, list) or not queued_messages:
            return Response(
                {'detail': "'messages' (non-empty list) required hai."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(queued_messages) > self.MAX_OFFLINE_QUEUE_BATCH:
            return Response(
                {'detail': f"Ek baar me zyada se zyada {self.MAX_OFFLINE_QUEUE_BATCH} messages flush kiye ja sakte hain."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se message nahi bheja ja sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            group = getattr(conversation, 'group_detail', None)
            if group:
                # M9a — admin-only: explicit code ('admins_only') ke saath 403
                blocked = admin_only_block_response(group, request.user.id)
                if blocked is not None:
                    return blocked
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)
                allowed, reason = check_daily_message_limit(group, request.user, conversation)
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        results = flush_offline_queue(
            conversation=conversation, sender=request.user, queued_messages=queued_messages,
        )
        return Response({'results': results})

    # ======================================================================
    # 🔥 NAYA — POLL MESSAGES (WhatsApp-style group poll)
    # ======================================================================
    # POST /message/conversations/<id>/poll/
    #   body: {"question": "...", "options": ["A", "B", ...], "allow_multiple_answers": false}
    #
    # Ek poll = ek naya `Message` (type=POLL, text=question) + ek `Poll`
    # row + N `PollOption` rows. Same permission/block/throttle checks
    # jaise normal `messages()` POST (group message_permission +
    # daily_message_limit, private-chat block-check) — koi bhi jo normal
    # text message nahi bhej sakta, poll bhi nahi bhej sakega.
    @action(detail=True, methods=['post'], url_path='poll')
    def create_poll(self, request, pk=None):
        conversation = self.get_object()

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se poll nahi bheja ja sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            group = getattr(conversation, 'group_detail', None)
            if group:
                # M9a — admin-only: explicit code ('admins_only') ke saath 403
                blocked = admin_only_block_response(group, request.user.id)
                if blocked is not None:
                    return blocked
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)
                allowed, reason = check_daily_message_limit(group, request.user, conversation)
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        serializer = PollCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        disappearing_delta = conversation.get_disappearing_timedelta()
        expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation, sender=request.user,
                type=MessageType.POLL, text=data['question'], expires_at=expires_at,
            )
            poll = Poll.objects.create(
                message=message, question=data['question'],
                allow_multiple_answers=data['allow_multiple_answers'],
            )
            PollOption.objects.bulk_create([
                PollOption(poll=poll, text=opt_text, order=i)
                for i, opt_text in enumerate(data['options'])
            ])

            preview_text = f"📊 {data['question']}"[:500]
            conversation.last_message_text = preview_text
            conversation.last_message_at = message.created_at
            conversation.last_message_sender = request.user
            conversation.last_message_type = message.type
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])

            other_participant_ids = list(
                ConversationParticipant.objects.filter(conversation=conversation)
                .exclude(user=request.user)
                .values_list('user_id', flat=True)
            )
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_participant_ids,
            ).update(unread_count=F('unread_count') + 1)

            MessageStatus.objects.bulk_create(
                [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
                ignore_conflicts=True,
            )

        sender = build_user_mini(request.user)
        poll_data = PollSerializer(poll, context={'request': request}).data
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(message.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(request.user.id),
                'sender_name': sender['display_name'],
                'sender_username': sender['username'],
                'sender_first_name': sender['first_name'],
                'sender_last_name': sender['last_name'],
                'sender_profile_photo': sender['profile_photo'],
                'message_type': message.type,
                'text': message.text,
                'poll': poll_data,
                'reply_to': None,
                'client_id': None,
                'created_at': message.created_at.isoformat(),
            }
        )

        for uid in other_participant_ids:
            async_to_sync(channel_layer.group_send)(
                f'user_{uid}',
                {
                    'type': 'inbox_update',
                    'conversation_id': str(conversation.id),
                    'message_id': str(message.id),
                    'sender_id': str(request.user.id),
                    'sender_name': sender['display_name'],
                    'last_message_text': preview_text,
                    'last_message_type': message.type,
                    'created_at': message.created_at.isoformat(),
                }
            )

        muted_user_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_participant_ids, is_muted=True,
            ).values_list('user_id', flat=True)
        )
        push_recipients = [uid for uid in other_participant_ids if uid not in muted_user_ids]
        if push_recipients:
            send_chat_message_push(
                recipient_ids=push_recipients,
                sender_name=sender['display_name'],
                message_text=preview_text,
                message_type=message.type,
                conversation_id=conversation.id,
                message_id=message.id,
            )

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='read_all')
    def read_all(self, request, pk=None):
        """
        FIXED: pehle for loop me update_or_create se database locked ho rha tha.
        Ab bulk_update + bulk_create + ignore_conflicts use kiya hai.

        Task 10 — is method ke andar hi ab `core.Notification` (source
        'message', notif_type CHAT_MESSAGE) ke corresponding records bhi
        read mark kiye jaate hain. Isse pehle sirf `MessageStatus.read_at`
        set hota tha; Home ka Chat-tab badge `NotificationService.
        getUnreadCount(source: 'message')` se aata hai, jo notification-
        records ka unread-count deta hai — us table ko yahin se bhi
        touch na karna hi badge-not-clearing bug ka root cause tha (dono
        table ek doosre se disconnect the). Backend-side fix isliye
        (frontend-side bulk-call ke bajaye) taaki ye hamesha single
        source-of-truth rahe — push-notifications/bell dono isi record pe
        depend karte hain, is se sync rehna zaroori hai.
        """
        conversation = self.get_object()
        # 🔥 NAYA (M1-BE) — pending/declined request kholne par read-receipt
        # sender tak NAHI jaani chahiye: kuch bhi read-mark mat karo (no-op).
        # Accept ke baad normal read_all chalega aur tab messages read honge.
        if not message_requests.is_request_accepted(conversation.id, request.user.id):
            return Response({'detail': 'Saare messages read mark ho gaye.'})
        now = timezone.now()
        try:
            self._mark_message_notifications_read(request.user, conversation.id, now)
        except Exception:
            # Best-effort — a notification-table hiccup should never block
            # the actual message read-receipts below from being written.
            logger.exception('Task 10: failed to sync core.Notification read-state for conversation %s', conversation.id)

        try:
            # Seedha is user ke unread MessageStatus rows pe ek hi UPDATE —
            # pehle `[:500]` cap tha, jisse lambi chat me 500 se purane/naye
            # messages unread reh jaate the. MessageStatus rows send ke time
            # hi ban jaati hain (REST/WS/services), isliye bulk_create ki
            # ab zaroorat nahi.
            MessageStatus.objects.filter(
                user=request.user,
                message__conversation=conversation,
                is_read=False,
            ).update(is_read=True, read_at=now, is_delivered=True, delivered_at=now)

            ConversationParticipant.objects.filter(
                conversation=conversation,
                user=request.user
            ).update(
                unread_count=0,
                last_read_at=now
            )
        except Exception:
            # Agar bhi lock ho jaye to kam se kam unread 0 kar do, 500 error mat do
            ConversationParticipant.objects.filter(
                conversation=conversation,
                user=request.user
            ).update(unread_count=0, last_read_at=now)

        return Response({'detail': 'Saare messages read mark ho gaye.'})

    # Task 10 — shared helper: `core.Notification` rows for this
    # recipient that are (a) still unread and (b) tagged to this
    # conversation get read-marked too. Called from both `read_all`
    # (bulk conversation open/read) and `MessageViewSet.mark_read`
    # (single-message read) below, so a badge desync can't creep back in
    # via one call site while the other stays fixed.
    #
    # `data__conversation_id` is a JSONField key lookup — CHAT_MESSAGE
    # notifications always carry `conversation_id` as a string (see
    # `push_utils.py`'s `create_notification(..., data={"conversation_id":
    # str(conversation_id), ...})`), so the comparison value here is
    # stringified to match exactly.
    @staticmethod
    def _mark_message_notifications_read(user, conversation_id, now):
        from core.models import Notification

        # CHAT_MESSAGE ke saath MENTION aur INCOMING_CALL rows bhi clear
        # hote hain — teeno MESSAGE_APP_TYPES hain aur Chats badge
        # (?source=message) me count hote hain, aur teeno `data.conversation_id`
        # carry karte hain.
        Notification.objects.for_user(user).unread().filter(
            notif_type__in=[
                Notification.NotifType.CHAT_MESSAGE,
                Notification.NotifType.MENTION,
                Notification.NotifType.INCOMING_CALL,
            ],
            data__conversation_id=str(conversation_id),
        ).update(is_read=True, read_at=now)

    # ==================================================================
    # SEARCH — ek conversation ke andar text search
    # ==================================================================
    # GET /message/conversations/<id>/search/?q=...&sender=<id>&date_from=
    #     ...&date_to=...&has_media=true|false
    #
    # 🔥 UPGRADED (this session) — pehle plain `icontains` tha. Ab Postgres
    # full-text search (ranked, stemmed) + trigram similarity (typo-
    # tolerant) `search_utils.search_messages()` se, plus sender/date-
    # range/has_media filters. `conversation` FK pe already index hai to
    # ye query ek conversation tak hi scoped rehti hai; global search
    # `search_all` neeche hai. Deleted/expired/scheduled messages exclude
    # karte hain, jaisa normal message-list me hota hai.
    @action(detail=True, methods=['get'], url_path='search')
    def search(self, request, pk=None):
        conversation = self.get_object()
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < search_utils.MIN_QUERY_LENGTH:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = conversation.all_messages.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).exclude(
            is_scheduled=True,  # 🔧 GAP FIX — see note in `messages` GET above
        ).select_related('sender', 'reply_to')

        qs, filter_error = search_utils.apply_structured_filters(qs, request.query_params)
        if filter_error:
            return Response({'detail': filter_error}, status=status.HTTP_400_BAD_REQUEST)

        qs = search_utils.search_messages(qs, query)
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # SEARCH — sabhi conversations me ek saath (global chat search)
    # ==================================================================
    # GET /message/conversations/search_all/?q=...&sender=<id>&date_from=
    #     ...&date_to=...&has_media=true|false
    #
    # Ye `detail=False` hai isliye alag URL `search_all/` par baithta hai
    # (router `search/` already `search` action ke through detail route
    # pe bana chuka hai). Sirf un conversations ke messages aate hain jinka
    # user abhi ACTIVE member hai (chat delete/leave kar chuka ho to us
    # conversation ke results nahi aayenge). 🔥 UPGRADED (this session) —
    # same ranked/typo-tolerant search + filters as the in-conversation
    # search above. `sender` filters by sender across ALL conversations —
    # combine with a specific conversation's own `/search/` endpoint if
    # you want "messages from X in THIS chat only".
    @action(detail=False, methods=['get'], url_path='search_all')
    def search_all(self, request):
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < search_utils.MIN_QUERY_LENGTH:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = Message.objects.filter(
            conversation__memberships__user=request.user,
            conversation__memberships__left_at__isnull=True,
            # 🔥 NAYA (M1-BE) — pending/declined requests ke messages global
            # search me nahi aate (jaise wo inbox me nahi hain).
            conversation__memberships__request_status=RequestStatus.ACCEPTED,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).exclude(
            is_scheduled=True,  # 🔧 GAP FIX — see note in `messages` GET above
        ).select_related(
            'sender', 'conversation', 'conversation__group_detail',
        ).distinct()

        # 🔧 NOTE — `conversation_id` scope param, so the client can reuse
        # this one global endpoint instead of needing two code paths, e.g.
        # "search everywhere" vs "search in this chat" with the same UI.
        conversation_id = request.query_params.get('conversation_id')
        if conversation_id:
            qs = qs.filter(conversation_id=conversation_id)

        qs, filter_error = search_utils.apply_structured_filters(qs, request.query_params)
        if filter_error:
            return Response({'detail': filter_error}, status=status.HTTP_400_BAD_REQUEST)

        qs = search_utils.search_messages(qs, query)
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSearchResultSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # PINNED MESSAGES — is conversation ke saare currently-pinned messages
    # ==================================================================
    # GET /message/conversations/<id>/pinned/
    @action(detail=True, methods=['get'], url_path='pinned')
    def pinned(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.all_messages.filter(is_pinned=True).select_related(
            'sender', 'pinned_by',
        ).order_by('-pinned_at')
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def
        serializer = MessageSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    # ==================================================================
    # 🔥 NAYA (M8-BE) — MEDIA / LINKS / DOCS (private + group dono)
    # ==================================================================
    # GET /message/conversations/<id>/media/?type=media|links|docs[&page=&page_size=]
    #   media -> image + video          (grid)
    #   docs  -> file + presentation + audio
    #   links -> text messages me se extract kiye hue URLs (+ `link_preview`
    #            agar generate_link_preview_task ne Message pe store kiya ho)
    # `GroupViewSet.media` (`GroupMedia` table) sirf groups ke liye hai aur
    # `links` nahi deta; ye endpoint seedha Message table se chalta hai,
    # isliye DM pe bhi kaam karta hai. Visibility rules `search` jaise hi
    # hain (deleted-for-me/everyone, expired, scheduled hidden). Pagination
    # message-level hai; multi-image / multi-link message ek page me
    # ek se zyada item de sakta hai.
    @action(detail=True, methods=['get'], url_path='media')
    def media(self, request, pk=None):
        conversation = self.get_object()
        tab = (request.query_params.get('type') or 'media').strip().lower()
        if tab not in search_utils.LIBRARY_TABS:
            return Response(
                {'detail': "'type' in me se ek hona chahiye: " + ', '.join(search_utils.LIBRARY_TABS)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = conversation.all_messages.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).exclude(
            is_scheduled=True,
        ).select_related('sender').order_by('-created_at', '-id')
        qs = search_utils.apply_library_filter(qs, tab)

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        items = []
        for message in page:
            items.extend(search_utils.build_library_items(message, tab))
        return paginator.get_paginated_response(items)

    # ==================================================================
    # 🔥 NAYA (ADVANCED FEATURE) — SCHEDULED MESSAGES / "SEND LATER"
    # ==================================================================
    # Model pe `Message.is_scheduled` / `scheduled_for` fields already the
    # (kisi purani session me add hue the) lekin end-to-end kahin bhi wire
    # nahi the — na koi endpoint inhe set karta tha, na koi job inhe
    # actually deliver karta tha, na normal message-list inhe hide karta
    # tha. Ye poora feature ab yahan complete hai:
    #
    #   POST /message/conversations/<id>/schedule-message/
    #        body = MessageCreateSerializer ke saare fields + "scheduled_for"
    #        (future ISO datetime). Message DB me ban jaata hai lekin
    #        `is_scheduled=True` ke saath — is wajah se `messages` GET /
    #        `search` / `search_all` me kisi ko bhi (sender samet, dusre
    #        screen pe) nahi dikhta jab tak bhej na diya jaaye. Koi
    #        broadcast/push/unread-count yahan NAHI hota — wo sab
    #        `scheduled_messages.finalize_scheduled_message()` karta hai,
    #        jab `send_scheduled_messages` management command (cron/
    #        Celery-beat se har minute chalao) isko pick karta hai.
    #   GET  /message/conversations/<id>/scheduled-messages/
    #        sirf apne bheje hue, abhi tak pending scheduled messages is
    #        chat ke — taaki UI me "Scheduled" tab dikha sako.
    #   DELETE/PATCH /message/messages/<id>/schedule/ (MessageViewSet)
    #        cancel karo ya reschedule/text-edit karo, jab tak bhej na
    #        diya ho.
    #
    # Permission checks (block / group message_permission) yahan bhi
    # utni hi lagti hain jitni normal send me — lekin `daily_message_limit`
    # ka count actual-send-time pe stale ho sakta hai (schedule karte
    # waqt limit ke andar tha, ab tak limit cross ho chuki ho) — ye ek
    # chhota known edge-case hai, WhatsApp/Gmail bhi isi tarah handle
    # karte hain (schedule-time pe hi check, deliver-time pe nahi).
    @action(detail=True, methods=['post'], url_path='schedule-message')
    def schedule_message(self, request, pk=None):
        conversation = self.get_object()

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se message schedule nahi ho sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            group = getattr(conversation, 'group_detail', None)
            if group:
                # M9a — admin-only: explicit code ('admins_only') ke saath 403
                blocked = admin_only_block_response(group, request.user.id)
                if blocked is not None:
                    return blocked
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = ScheduleMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = serializer.save(
            conversation=conversation,
            sender=request.user,
            is_scheduled=True,
            # `expires_at` (disappearing messages) jaan-boojh kar yahan
            # NAHI compute karte — conversation ki duration setting badal
            # sakti hai schedule aur actual-send ke beech; asli send-time
            # (finalize_scheduled_message) pe hi snapshot lena sahi hai,
            # jaisa normal messages ke liye already hota hai.
        )
        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='scheduled-messages')
    def scheduled_messages_list(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.all_messages.filter(
            is_scheduled=True, sender=request.user,
        ).order_by('scheduled_for')
        serializer = MessageSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)
