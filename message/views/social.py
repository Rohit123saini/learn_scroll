"""
message/views/social.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging

from django.db import OperationalError
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from common.db_retry import retry_on_db_lock
from .. import message_requests, search_utils, user_notes
from .. import push_reliability  # N3
from ..cache_utils import get_presence_snapshot, set_last_seen_cache
from ..models import BlockedUser, ConversationType, DeviceToken, Message, RequestStatus
from ..serializers import (
    BlockedUserSerializer,
    ConversationListSerializer,
    DirectoryGroupSerializer,
    DirectoryPersonSerializer,
    ReadReceiptSettingsSerializer,
    UserPresenceSerializer,
)
from ..user_display import build_user_mini

from ._common import StandardPagination, User, _conversation_list_qs_for

logger = logging.getLogger(__name__)


# ======================================================================
# 🔥 NAYA (6.1) — PEOPLE + GROUPS SEARCH (message search screen)
# ======================================================================
# GET /message/search/directory/?q=<text>[&type=all|people|groups][&limit=20]
#
# Response:
#   {
#     "query": "ra",
#     "people": [{id, username, first_name, last_name, display_name,
#                 profile_photo, is_mutual, relation}],   # relation: mutual|following|follower
#     "people_has_more": false,
#     "groups": [{id, conversation_id, name, photo_url, topic_tag,
#                 members_count, is_private}],
#     "groups_has_more": false
#   }
#
# People = mere ACCEPTED followers / following (mutual pehle). Private
# account tabhi jab main usko follow karta hoon; blocked (dono direction)
# hide. Groups = jinme main abhi active member hoon. `type=people|groups`
# sirf wahi section compute karta hai (client "See all" ke liye `limit=50`
# ke saath use kar sakta hai). Rules ka detail: search_utils.search_people.
class ChatDirectorySearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query.lstrip('@').strip()) < search_utils.MIN_QUERY_LENGTH:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        kind = (request.query_params.get('type') or 'all').strip().lower()
        if kind not in search_utils.DIRECTORY_TYPES:
            return Response(
                {'detail': "'type' in me se ek hona chahiye: " + ', '.join(search_utils.DIRECTORY_TYPES)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = search_utils.clamp_directory_limit(request.query_params.get('limit'))

        ctx = {'request': request}
        people, people_more, groups, groups_more = [], False, [], False
        if kind in ('all', 'people'):
            rows, people_more = search_utils.search_people(request.user, query, limit)
            people = DirectoryPersonSerializer(rows, many=True, context=ctx).data
        if kind in ('all', 'groups'):
            rows, groups_more = search_utils.search_groups(request.user, query, limit)
            groups = DirectoryGroupSerializer(rows, many=True, context=ctx).data

        return Response({
            'query': query,
            'people': people,
            'people_has_more': people_more,
            'groups': groups,
            'groups_has_more': groups_more,
        })


class MessageRequestListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        me = request.user
        has_visible_message = Message.objects.filter(
            conversation_id=OuterRef('pk'), is_scheduled=False,
        )
        # Single filter() call => teeno membership conditions ek hi row pe lagti hain.
        qs = (
            _conversation_list_qs_for(me)
            .filter(
                type=ConversationType.PRIVATE,
                memberships__user=me,
                memberships__left_at__isnull=True,
                memberships__request_status=RequestStatus.PENDING,
            )
            # scheduled-but-undelivered first message se khaali request na bane
            .annotate(_has_msg=Exists(has_visible_message)).filter(_has_msg=True)
            .annotate(_sort_ts=Coalesce('last_message_at', 'created_at'))
            .order_by('-_sort_ts', '-created_at')
        )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = ConversationListSerializer(page, many=True, context={'request': request}).data
        for item, conversation in zip(data, page):
            item['requester'] = (
                build_user_mini(conversation.last_message_sender)
                if conversation.last_message_sender_id else None
            )
        return paginator.get_paginated_response(data)


class _MessageRequestRespondView(APIView):
    permission_classes = [IsAuthenticated]
    target_status = None  # subclass set karta hai

    def post(self, request, conversation_id):
        try:
            membership, _changed = message_requests.set_request_status(
                request.user, conversation_id, self.target_status,
            )
        except LookupError:
            return Response({'detail': 'Message request nahi mili.'}, status=status.HTTP_404_NOT_FOUND)
        except message_requests.RequestStateError:
            return Response(
                {'detail': 'Sirf pending request decline ho sakti hai.'},
                status=status.HTTP_409_CONFLICT,
            )

        payload = {
            'conversation_id': str(conversation_id),
            'request_status': membership.request_status,
        }
        if self.target_status == RequestStatus.ACCEPTED:
            conversation = _conversation_list_qs_for(request.user).filter(pk=conversation_id).first()
            if conversation is not None:
                payload['conversation'] = ConversationListSerializer(
                    conversation, context={'request': request},
                ).data
        return Response(payload)


class MessageRequestAcceptView(_MessageRequestRespondView):
    target_status = RequestStatus.ACCEPTED


class MessageRequestDeclineView(_MessageRequestRespondView):
    target_status = RequestStatus.DECLINED


# ======================================================================
# 🔥 NAYA (M2-BE) — NOTES (Instagram-style status, 60 chars, 24h)
# ----------------------------------------------------------------------
#   GET    /message/notes/       -> mere followed + close-friends ke active notes
#                                   (paginated) + `my_note` (apna active note ya null)
#   PUT    /message/notes/me/    {"text", "emoji", "audience"} -> note set/replace, +24h
#   DELETE /message/notes/me/    -> note hatao (idempotent, 204)
#
# `message/sticky_notes.py` (study-room sticky notes) se koi lena-dena nahi.
# Rules / queries / privacy (fail-closed): `user_notes.py`.
# ======================================================================
class UserNotesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = user_notes.visible_notes_qs(request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = [user_notes.serialize_note(n, request=request) for n in page]
        response = paginator.get_paginated_response(data)
        mine = user_notes.get_my_active_note(request.user)
        response.data['my_note'] = user_notes.serialize_note(mine, request=request) if mine else None
        return response


class MyNoteView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        data = request.data
        try:
            note = user_notes.set_note(
                request.user,
                text=data.get('text'),
                emoji=data.get('emoji'),
                audience=data.get('audience'),
            )
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)})
        note.user = request.user  # extra query na lage
        return Response(user_notes.serialize_note(note, request=request))

    def delete(self, request):
        user_notes.delete_note(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ======================================================================
# BLOCKED USERS
# ======================================================================
class BlockedUserViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Block / unblock users — WhatsApp/Insta jaisa.

    POST   /blocked-users/           {"blocked": "<user_id>"}  -> block
    GET    /blocked-users/           -> meri poori block list
    DELETE /blocked-users/<lookup>/  -> unblock

    🔥 FIX: pehle `perform_create()` seedha `serializer.save()` karta tha
    — agar wahi user dobara block kiya jaata (ya frontend se double-tap
    ho jaata) to `unique_together = ('blocker', 'blocked')` ki wajah se
    500 (IntegrityError) aata tha. Ab `get_or_create` use kiya hai, isliye
    duplicate block pe error nahi, existing record hi wapas mil jaata hai.

    🔥 NAYA — unblock ab do tarike se ho sakta hai:
      1. `BlockedUser` record ki apni id se (jaisa pehle tha)
      2. seedha TARGET USER ki id se (frontend ke liye zyada natural —
         usko block-record ka internal id track karne ki zaroorat nahi,
         bas jis user ko unblock karna hai uski id bhejni hai)
    `get_object()` override karke dono lookups try kiye jaate hain.
    """
    serializer_class = BlockedUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return BlockedUser.objects.filter(blocker=self.request.user).select_related('blocked')

    def get_serializer_context(self):
        return {'request': self.request}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocked_user = serializer.validated_data['blocked']

        if blocked_user.id == request.user.id:
            raise ValidationError('Khud ko block nahi kar sakte.')

        obj, created = BlockedUser.objects.get_or_create(
            blocker=request.user, blocked=blocked_user,
        )
        return Response(
            BlockedUserSerializer(obj, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def get_object(self):
        lookup_value = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        try:
            obj = self.get_queryset().filter(
                Q(id=lookup_value) | Q(blocked_id=lookup_value)
            ).first()
        except (ValueError, TypeError):
            obj = None
        if obj is None:
            raise Http404('Ye block record nahi mila.')
        self.check_object_permissions(self.request, obj)
        return obj

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': 'Unblock ho gaya.'}, status=status.HTTP_204_NO_CONTENT)


# ======================================================================
# PRESENCE
# ======================================================================
class UserPresenceView(generics.RetrieveAPIView):
    serializer_class = UserPresenceSerializer
    permission_classes = [IsAuthenticated]

    # 🔥 FIX — cache_utils.py's own docstring says this view is meant to be
    # one of the two read sites for `get_presence_cached` (the other being
    # `ChatConsumer`'s presence update), but it was never wired in — every
    # single presence check (chat header "online"/"last seen", opening a
    # user's profile, etc.) was hitting the DB directly, defeating the
    # point of a 15s-TTL cache for a value that changes this often.
    def get_object(self):
        presence = self._get_presence()
        # Issue #2 (RestrictUser): if the profile owner restricted the
        # requester, show them "offline, last seen unknown" — via an
        # UNSAVED copy so the real row/cache is never altered.
        from user_profile.services import has_restricted
        target_id = self.kwargs['user_id']
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return presence
        # S4: also hidden when the owner limited who may see their online status (Settings > Privacy).
        from user_profile import privacy as privacy_rules
        if target_id != self.request.user.id and (
            has_restricted(target_id, self.request.user.id)
            or not privacy_rules.can_see_online_status(target_id, self.request.user)
        ):
            from ..models import UserPresence
            return UserPresence(user=presence.user, is_online=False, last_seen_at=None)
        return presence

    def _get_presence(self):
        from ..models import UserPresence
        user_id = self.kwargs['user_id']

        # T0.1 — online flag ab cache/Redis counter se aata hai (ChatConsumer DB me
        # `is_online` nahi likhta), isliye reader bhi wahi se padhe. Cache me
        # last_seen mil gaya to DB touch hi nahi hota.
        snapshot = get_presence_snapshot(user_id)
        if snapshot is not None:
            # Lightweight, unsaved UserPresence so the existing ModelSerializer
            # (needs `.user` for the nested UserMiniSerializer) can render it.
            user = get_object_or_404(User, id=user_id)
            return UserPresence(
                user=user,
                is_online=snapshot['is_online'],
                last_seen_at=snapshot['last_seen_at'],
            )

        # Cache miss (kabhi offline nahi hua / cache flush): DB se last_seen lo.
        # Yahan `get_or_create` ek-baar-ka hai (row na ho to bane), read-path pe hi.
        presence, _ = UserPresence.objects.select_related('user').get_or_create(user_id=user_id)
        if presence.last_seen_at:
            set_last_seen_cache(user_id, presence.last_seen_at)
        # DB `is_online` ab authoritative nahi — counter 0 hai (upar snapshot miss) => offline.
        presence.is_online = False
        return presence


# 🔥 NAYA — apni read-receipt privacy setting dekhna/badalna.
#   GET   /message/presence/read-receipts/  -> {"show_read_receipts": true|false}
#   PATCH /message/presence/read-receipts/  body: {"show_read_receipts": false}
# Hamesha `request.user` par operate karta hai — koi `user_id` param nahi,
# isliye koi bhi doosre ka toggle change nahi kar sakta. Effect turant
# `MessageViewSet.read_status` pe lagu ho jaata hai (koi cache invalidate
# karne ki zaroorat nahi — wo action hamesha live query karta hai).
class ReadReceiptSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from ..models import UserPresence
        presence, _ = UserPresence.objects.get_or_create(user=request.user)
        return Response(ReadReceiptSettingsSerializer(presence).data)

    def patch(self, request):
        from ..models import UserPresence
        presence, _ = UserPresence.objects.get_or_create(user=request.user)
        serializer = ReadReceiptSettingsSerializer(presence, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ======================================================================
# DEVICE TOKENS
# ======================================================================
class DeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('token')
        platform = push_reliability.clean_platform(request.data.get('platform'))
        if not token:
            return Response({'detail': "'token' required hai"}, status=400)
        # N3b - bad input is a 400, never a DB error: token must be a clean string (<= 255), platform one of the choices.
        if not push_reliability.valid_fcm_token(token):
            return Response({'detail': "'token' valid FCM token nahi hai"}, status=400)
        if platform is None:
            return Response({'detail': "'platform' android / ios / web me se ek hona chahiye"}, status=400)

        # C5 — iOS PushKit/VoIP token (optional). Sirf iOS par maane jaate hain;
        # key hi na aaye to purana voip_token chhua nahi jaata (purane app
        # versions ka registration isse nahi bigadta). Khaali string => clear.
        voip_provided = 'voip_token' in request.data
        voip_token = None
        if voip_provided and platform == 'ios':
            voip_token = (str(request.data.get('voip_token') or '').strip() or None)
            if voip_token and len(voip_token) > 255:
                return Response({'detail': "'voip_token' bahut lamba hai"}, status=400)
        elif voip_provided:
            voip_provided = False  # android/web par VoIP token ka koi matlab nahi

        # The token is unique per phone. If it MOVES from another account to
        # this one (same phone, new sign-up), anyone who blocked the previous
        # owner with "also block new accounts" now blocks this account too.
        #
        # T0.1 — `update_or_create` ek lamba transaction + SELECT FOR UPDATE tha
        # (SQLite pe writer-lock ke liye sabse bura). Ab: `get_or_create` (aksar
        # sirf SELECT) + sirf tab ek chhota `.update()` jab user/platform sach me
        # badla ho. Transient "database is locked" par ek retry; phir bhi fail
        # ho to 500 nahi, 503 + Retry-After (client ye call dobara karta hi hai).
        # N3 — device metadata (X-Device-Id header ya body `device_id`; app_version / os_version / device_name body me,
        # sab length-capped + informational) aur is login session ka jti (logout par is session ke tokens hat jaate hain).
        meta = push_reliability.clean_device_meta({
            'device_id': request.data.get('device_id') or request.META.get('HTTP_X_DEVICE_ID', ''),
            'device_name': request.data.get('device_name') or request.META.get('HTTP_X_DEVICE_NAME', ''),
            'app_version': request.data.get('app_version'),
            'os_version': request.data.get('os_version'),
        })
        auth = getattr(request, 'auth', None)
        session_jti = ''
        try:
            session_jti = str(auth.get('session_jti') or '') if auth is not None else ''
        except Exception:
            session_jti = ''

        def _register():
            # Upsert + same (user, device_id) ki purani rows hatana (FCM token rotation) + per-user cap + last_seen_at
            # refresh — sab message/push_reliability.register_device_token me.
            _obj, _created, moved_from = push_reliability.register_device_token(
                user=request.user, token=token, platform=platform, meta=meta, session_jti=session_jti,
                voip_provided=voip_provided, voip_token=voip_token,
            )
            return moved_from

        try:
            previous_user = retry_on_db_lock(_register)
        except OperationalError:
            logger.warning("device-token: DB busy even after retry user=%s", request.user.pk)
            return Response(
                {'detail': 'Server busy, please retry shortly.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={'Retry-After': '2'},
            )
        if previous_user is not None:
            from user_profile.services import block_new_accounts_of
            block_new_accounts_of(previous_user, request.user)
        return Response({'detail': 'Device registered'})

    def delete(self, request):
        # N3b - token optional: a phone that could not read its FCM token at sign-out still ends its pushes (this login
        # session's rows, else this device_id's). Only the caller's own rows, same as with a token.
        token = request.data.get('token')
        if token is not None and not isinstance(token, str):
            return Response({'detail': "'token' string hona chahiye"}, status=400)
        auth = getattr(request, 'auth', None)
        try:
            session_jti = str(auth.get('session_jti') or '') if auth is not None else ''
        except Exception:
            session_jti = ''
        meta = push_reliability.clean_device_meta({
            'device_id': request.data.get('device_id') or request.META.get('HTTP_X_DEVICE_ID', ''),
        })
        push_reliability.revoke_tokens_for_request(
            request.user, token=(token or '').strip() or None, session_jti=session_jti, device_id=meta['device_id'],
        )
        return Response({'detail': 'Device unregistered'})
