"""
message/views/groups.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import asyncio
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from common.ws_payload import json_safe
from ..admin_only import is_admins_only, normalize_permission, post_permission_system_message
from .. import doubt_similarity  # T1.5 — similar-doubt suggestions
from .. import study_room_events as sr_events      # S8 — rules badalne par room ko event
from .. import study_room_safety as sr_safety      # S8 — join approval / rules / minor masking
from ..cache_utils import invalidate_group_role_cache
from ..group_rules import is_group_admin_or_mod
from ..models import DoubtAnswer, DoubtQuestion, DoubtUpvote, Group, GroupJoinRequest, GroupMember
from ..permissions import IsGroupAdminOrModerator
from ..serializers import (
    DoubtAnswerCreateSerializer,
    DoubtAnswerItemSerializer,
    DoubtCreateSerializer,
    DoubtQuestionDetailSerializer,
    DoubtQuestionSerializer,
    GroupCreateSerializer,
    GroupDiscoverSerializer,
    GroupJoinRequestSerializer,
    GroupMediaSerializer,
    GroupMemberSerializer,
    GroupSerializer,
)
from ..services import (
    add_members_to_group,
    add_or_reactivate_participant,
    add_doubt_answer,
    delete_doubt_answer,
    set_answer_verified,
    set_best_answer,
    create_group,
    remove_group_member,
    update_group_member_role,
)
from ..throttles import (
    DoubtActionThrottle,
    DoubtAnswerThrottle,
    DoubtCreateThrottle,
    DoubtImageUploadThrottle,
    DoubtReadThrottle,
    DoubtSimilarThrottle,  # T1.5
    DoubtUpvoteThrottle,
    GroupCreateThrottle,
)

from ._common import StandardPagination

logger = logging.getLogger(__name__)


# ======================================================================
# GROUPS
# ======================================================================
class GroupViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return GroupCreateSerializer
        return GroupSerializer

    def get_queryset(self):
        return Group.objects.filter(
            group_members__user=self.request.user, group_members__is_banned=False,
        ).distinct().select_related('conversation', 'created_by')

    def get_permissions(self):
        # 🔥 NAYA — 'add_members' yahan se hata diya: public group me KOI
        # BHI member add kar sakta hai, private group me sirf admin/mod —
        # ye do-tarah ka rule ek single permission class se express nahi ho
        # sakta (usko group object dekhna padega), isliye check ab
        # `add_members()` method ke andar khud manually hota hai.
        if self.action in ('update', 'partial_update'):
            return [IsAuthenticated(), IsGroupAdminOrModerator()]
        return [IsAuthenticated()]

    # M9a — message_permission change par (a) FE alias 'admins_mods' ->
    # 'admins_only' normalize, (b) badalne par group me system message.
    def update(self, request, *args, **kwargs):
        data = request.data
        for key in ('message_permission', 'call_permission', 'study_room_permission'):
            if key in data and data.get(key) == 'admins_mods':
                data = data.copy() if hasattr(data, 'copy') else dict(data)
                data[key] = normalize_permission(data[key])
        request._full_data = data  # DRF: normalized payload serializer ko mile
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        old_value = serializer.instance.message_permission
        old_rules = serializer.instance.room_rules or ''   # S8
        group = serializer.save()
        if is_admins_only(group) != (old_value in ('admins_only', 'admins_mods')):
            post_permission_system_message(group, self.request.user, is_admins_only(group))
        # 🔥 S8 — rules text badla => version +1 (F() — do admin ek saath edit kare to bhi har edit ek version
        # badhata hai) => purane accept ki value ghat jaati hai, members ko dobara accept karna padega.
        if (group.room_rules or '') != old_rules:
            Group.objects.filter(id=group.id).update(room_rules_version=F('room_rules_version') + 1)
            group.refresh_from_db(fields=['room_rules_version'])
            sr_events.broadcast(group.conversation_id, sr_events.EVT_RULES_CHANGED,
                                {'version': group.room_rules_version})

    # 🔥 FIX — mass-group-creation spam guard (`GroupCreateThrottle`)
    # existed in throttles.py but was never wired in.
    def get_throttles(self):
        if self.action == 'create':
            return [GroupCreateThrottle()]
        return super().get_throttles()

    # 🔥 NAYA — Delete group (ADMIN ONLY — moderator bhi nahi, sirf role
    # exactly 'admin' wale). Pehle ye action `ModelViewSet` ke default
    # `DestroyModelMixin` se bina kisi restriction ke chal raha tha —
    # matlab group ka KOI BHI member (chahe simple 'member' role ho) poora
    # group delete kar sakta tha. Ab `get_object()` khud hi queryset se
    # aata hai (jo already sirf group-members tak limited hai), uske baad
    # yahan explicit admin-role check lagaya hai.
    #
    # 🔧 GAP FIX — pehle ye seedha `conversation.delete()` (HARD delete,
    # CASCADE) karta tha, jabki `BaseModel.is_deleted`/`soft_delete()`
    # already model pe maujood tha aur kahin bhi use nahi ho raha tha
    # ("dead field" — koi bhi row ise set hi nahi karti thi). Ek admin ka
    # accidental tap poora group — saare messages/media/calls/doubts sab —
    # permanently, unrecoverably mita deta tha. Ab `soft_delete()` use
    # karte hain: `Conversation.objects`/`Group.objects` (default manager,
    # `SoftDeleteManager`) is row ko turant har jagah hide kar dete hain
    # (list/detail/messages sab — is behaviour ke liye alag se kahin bhi
    # `.exclude(is_deleted=True)` likhne ki zaroorat nahi, manager khud
    # karta hai), par row DB me `GROUP_SOFT_DELETE_GRACE_DAYS` din tak
    # rehti hai — `purge_soft_deleted_conversations` (tasks.py) us window
    # ke baad hi actually CASCADE-hard-delete karta hai. Isse ek galti se
    # delete hui group ko us window ke andar `all_objects` se admin/support
    # dwara recover (`.restore()`) kiya ja sakta hai.
    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        is_admin = GroupMember.objects.filter(
            group=group, user=request.user,
            role=GroupMember.Role.ADMIN, is_banned=False,
        ).exists()
        if not is_admin:
            raise PermissionDenied('Sirf group admin hi group delete kar sakta hai.')

        conversation = group.conversation
        group_id = str(group.id)
        conversation_id = str(conversation.id)

        # 🔥 Delete se PEHLE broadcast karo — taaki sabhi connected members
        # (jo abhi is chat me hain) ko turant pata chal jaaye group delete
        # ho gaya, aur unki app apne-aap chat screen se conversations list
        # pe wapas nikaal de. Soft-delete ke baad bhi `chat_{id}` group is
        # request ke process me hi exist karta hai, par consistency ke liye
        # order same rakha hai jo pehle tha.
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation_id}',
            {
                'type': 'group_deleted',
                'group_id': group_id,
                'conversation_id': conversation_id,
                'deleted_by': str(request.user.id),
            }
        )

        # Soft-delete — actual CASCADE hard-delete `purge_soft_deleted_
        # conversations` grace-period ke baad karta hai (see tasks.py).
        group.soft_delete()
        conversation.soft_delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 🔧 FIX — this used to duplicate `services.create_group()`
        # line-for-line inline (see that function for the `member_ids`
        # integer-type note, which still applies — `GroupCreateSerializer`
        # already gives us ints, so no `str(uid)` conversion needed).
        group = create_group(
            created_by=request.user,
            name=data['name'],
            description=data.get('description', ''),
            photo_url=data.get('photo_url'),
            is_private=data.get('is_private', False),
            member_ids=data.get('member_ids', []),
            topic_tag=data.get('topic_tag', ''),
        )

        return Response(GroupSerializer(group, context={'request': request}).data, status=status.HTTP_201_CREATED)

    # 🔥 NAYA (Task G14) — "Study Groups" discovery. `get_queryset()` (top
    # of this ViewSet) sirf caller ke apne groups tak limited hai (list/
    # retrieve/update/destroy sab isi se aate hain) — is action ko isliye
    # `get_object()`/`get_queryset()` reuse nahi karta, seedha `Group`
    # model se query karta hai, taaki EK BHI authenticated user PUBLIC
    # groups dhoond sake chahe wo member ho ya na ho (private groups
    # kabhi is list me nahi aate — invite-code/direct-add hi unka raasta
    # rehta hai, jaisa pehle tha).
    #
    # `?q=` — name/description/topic_tag pe case-insensitive search.
    # `?topic=` — exact topic_tag match (chip-tap se filter, e.g. "NEET 2027").
    # Ordering: sabse zyada members wale (trending) pehle, phir naye-se-purane.
    # Simple offset pagination (`?page=`) — is discovery list ke liye poori
    # `PageNumberPagination` class alag se banane ki zaroorat nahi, DRF ka
    # default hi kaafi hai kyunki `page_size` fix rakha hai.
    @action(detail=False, methods=['get'], url_path='discover')
    def discover(self, request):
        PAGE_SIZE = 20
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1

        qs = Group.objects.filter(is_private=False).select_related('created_by')
        # P-F16 -- a PUBLIC group created by an under-18 account is not listed to adult / unknown-age strangers (the minor's own
        # peers and people with an accepted follow link still see it). Joining stays under the S8 approval rules.
        from common import minor_policy

        qs = minor_policy.exclude_minors_for_stranger(
            qs, request.user, feature=minor_policy.GROUP_DISCOVER, id_field='created_by_id'
        )

        q = request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(topic_tag__icontains=q))

        topic = request.query_params.get('topic', '').strip()
        if topic:
            qs = qs.filter(topic_tag__iexact=topic)

        qs = qs.order_by('-members_count', '-created_at')

        start = (page - 1) * PAGE_SIZE
        groups = list(qs[start:start + PAGE_SIZE])
        has_more = qs[start + PAGE_SIZE:start + PAGE_SIZE + 1].exists()

        # 🔥 Perf — ek hi query se batao ye caller kin-kin returned groups
        # ka already member hai (N+1 `.exists()` per row se bachne ke liye).
        # `GroupDiscoverSerializer.get_is_member` isi attribute ko padhta hai.
        member_group_ids = set(
            GroupMember.objects.filter(
                group_id__in=[g.id for g in groups], user=request.user, is_banned=False,
            ).values_list('group_id', flat=True)
        )
        # 🔥 S8 — ek hi query: is viewer ne kin groups me request bhej rakhi hai (pending / rejected) =>
        # discover card pe "Requested" state; `join_needs_approval` serializer me viewer ke hisaab se nikalta hai.
        request_states = sr_safety.request_status_map(request.user, [g.id for g in groups])
        for g in groups:
            g._viewer_member_ids = member_group_ids
            g._viewer_request_status = request_states.get(g.id)

        return Response({
            'results': GroupDiscoverSerializer(groups, many=True, context={'request': request}).data,
            'page': page,
            'has_more': has_more,
        })

    # 🔥 GAP FIX (this session) — `message_api_service.dart` calls
    # `DELETE /groups/<id>/photo/` (and `PROJECT_ARCHITECTURE.md` documents
    # it as "remove photo (admin/mod)"), but no such action existed on
    # `GroupViewSet` — `photo_url` was only ever settable at creation time.
    # Added here, reusing the same admin/mod gate as the rest of this
    # ViewSet's write-actions (`_require_admin`).
    @action(detail=True, methods=['delete'], url_path='photo')
    def remove_photo(self, request, pk=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)

        if not group.photo_url:
            return Response({'detail': 'Group ki koi photo set nahi hai.'}, status=status.HTTP_400_BAD_REQUEST)

        group.photo_url = None
        group.save(update_fields=['photo_url'])
        return Response(GroupSerializer(group, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='members')
    def add_members(self, request, pk=None):
        group = self.get_object()
        # 🔥 NAYA — Public group: group ka koi bhi member (role kuch bhi ho)
        # doosre users ko seedha add kar sakta hai. Private group: sirf
        # admin/moderator (jaisa pehle tha). `get_permissions()` ab is
        # action ko sirf `IsAuthenticated` tak khula chhodta hai — `get_
        # object()` khud hi queryset se aata hai (jo already sirf group-
        # members tak limited hai), isliye caller ka member hona to already
        # confirm hai, bas role-check yahan manually lagana hai.
        #
        # 🔧 FIX — this used to duplicate `services.add_members_to_group()`
        # inline (permission check, existing-member filtering, `bulk`
        # add-or-reactivate loop, cache invalidation — all of it). That
        # function raises plain `PermissionError`/`ValueError` (by design —
        # see `services.py`'s header comment), converted to their DRF
        # equivalents right here.
        user_ids = request.data.get('user_ids', [])
        # 🔥 S8 — approval wale group / minor target ko non-admin seedha add nahi kar sakta: unke liye
        # `GroupJoinRequest` (PENDING) banti hai, admin/mod approve karta hai. `pending_out` me un user ids ki list aati hai.
        pending_out = []
        declined_out = []  # S4: people whose privacy setting does not let this actor add them
        try:
            users = add_members_to_group(
                group=group, actor=request.user, user_ids=user_ids, pending_out=pending_out, declined_out=declined_out,
            )
        except PermissionError as e:
            raise PermissionDenied(str(e))
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        body = dict(GroupSerializer(group, context={'request': request}).data)
        body['pending_approval_count'] = len(pending_out)
        body['pending_approval_user_ids'] = [str(u) for u in pending_out]
        body['not_added_count'] = len(declined_out)  # S4
        body['not_added_user_ids'] = [str(u) for u in declined_out]
        return Response(body)

    # 🔥 NAYA — invite-code se group join karna. Public group me turant
    # member ban jaate ho; private group me `GroupJoinRequest` (PENDING)
    # ban jaata hai, admin/moderator approve/reject karega. Non-member bhi
    # ise call kar sakta hai (`detail=False` isliye `get_object()`/
    # `get_queryset()` — jo sirf existing members tak limited hai — beech
    # me nahi aata, group seedha `invite_code` se dhoondte hain).
    @action(detail=False, methods=['post'], url_path='join')
    def join(self, request):
        invite_code = request.data.get('invite_code')
        if not invite_code:
            return Response({'detail': "'invite_code' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        group = Group.objects.filter(invite_code=invite_code).select_related('conversation').first()
        if not group:
            return Response({'detail': 'Invalid invite code.'}, status=status.HTTP_404_NOT_FOUND)

        existing_membership = GroupMember.objects.filter(group=group, user=request.user).first()
        if existing_membership:
            if existing_membership.is_banned:
                return Response({'detail': 'Aapko is group se ban kiya gaya hai.'}, status=status.HTTP_403_FORBIDDEN)
            return Response({'detail': 'Aap already is group ke member hain.'}, status=status.HTTP_400_BAD_REQUEST)

        # 🔥 S8 — instant join ya admin approval: private group, admin ka `join_approval_required`, ya minor +
        # `MINOR_PUBLIC_GROUP_JOIN_APPROVAL` (post/minor_privacy.py). Group admin/mod pehle se member hote hain.
        decision = sr_safety.join_decision(group, request.user)
        if not decision.needs_approval:
            # Public group — koi approval nahi chahiye, turant member.
            with transaction.atomic():
                add_or_reactivate_participant(group.conversation, request.user)
                GroupMember.objects.get_or_create(group=group, user=request.user)
                Group.objects.filter(id=group.id).update(
                    members_count=group.group_members.filter(is_banned=False).count()
                )
            # 🔥 FIX — same class of gap as `add_members`/`approve_join_request`.
            invalidate_group_role_cache(group.id, request.user.id)
            return Response(
                {'status': 'joined', 'group': GroupSerializer(group, context={'request': request}).data,
                 'rules_required': sr_safety.rules_required_for(group, request.user)},
                status=status.HTTP_201_CREATED,
            )

        # Approval path — pehle se koi row ho (reject wali bhi) to naya banane ke bajaye usi ko wapas PENDING pe
        # reset karo (history bhi preserve rehti hai ki pehle kya hua tha).
        _row, state = sr_safety.create_or_reset_join_request(group, request.user)
        if state == 'pending':
            return Response({'status': 'pending', 'reason': decision.reason, 'detail': 'Request pehle se pending hai.'})
        return Response(
            {'status': 'pending', 'reason': decision.reason, 'detail': sr_safety.join_request_message(decision.reason)},
            status=status.HTTP_202_ACCEPTED,
        )

    # 🔥 S8 — "room rules" pre-join sheet. Public group ke rules non-member bhi padh sakta hai (join karne se
    # pehle dekhna chahiye); private group sirf member ko. `get_object()` yahan jaanbujh ke nahi (wo sirf members tak
    # limited hai). Response `sr_safety.rules_payload`: default rules (EN+HI), custom rules, version, accepted, ...
    @action(detail=True, methods=['get'], url_path='room-rules')
    def room_rules(self, request, pk=None):
        group = Group.objects.filter(id=pk).select_related('conversation').first()
        if group is None:
            return Response({'detail': 'Group nahi mila.'}, status=status.HTTP_404_NOT_FOUND)
        if group.is_private and not GroupMember.objects.filter(group=group, user=request.user, is_banned=False).exists():
            return Response({'detail': 'Group nahi mila.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(sr_safety.rules_payload(group, request.user))

    # 🔥 NAYA — private group ki pending join-requests (admin/moderator only).
    @action(detail=True, methods=['get'], url_path='join-requests')
    def join_requests(self, request, pk=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        qs = group.join_requests.filter(
            status=GroupJoinRequest.Status.PENDING
        ).select_related('user').order_by('-created_at')
        return Response(GroupJoinRequestSerializer(qs, many=True, context={'request': request}).data)

    # 🔥 NAYA — ek pending request approve karna (admin/moderator only) —
    # approve hote hi requester `GroupMember` + `ConversationParticipant`
    # ban jaata hai, `add_members` jaisa hi effect.
    @action(detail=True, methods=['post'], url_path=r'join-requests/(?P<request_id>[^/.]+)/approve')
    def approve_join_request(self, request, pk=None, request_id=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        join_request = get_object_or_404(
            GroupJoinRequest, id=request_id, group=group, status=GroupJoinRequest.Status.PENDING,
        )

        # 🔥 S8 — request ke baad user ban ho gaya ho to approve se wapas room/chat access nahi milna chahiye
        # (`GroupMember.is_banned` row rehti hai, par `add_or_reactivate_participant` participant ko wapas active kar deta).
        if GroupMember.objects.filter(group=group, user_id=join_request.user_id, is_banned=True).exists():
            join_request.status = GroupJoinRequest.Status.REJECTED
            join_request.responded_by = request.user
            join_request.responded_at = timezone.now()
            join_request.save(update_fields=['status', 'responded_by', 'responded_at'])
            return Response({'detail': 'Ye user is group se ban hai, request reject kar di gayi.'},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            add_or_reactivate_participant(group.conversation, join_request.user)
            GroupMember.objects.get_or_create(group=group, user=join_request.user, defaults={'added_by': request.user})
            join_request.status = GroupJoinRequest.Status.APPROVED
            join_request.responded_by = request.user
            join_request.responded_at = timezone.now()
            join_request.save(update_fields=['status', 'responded_by', 'responded_at'])
            Group.objects.filter(id=group.id).update(
                members_count=group.group_members.filter(is_banned=False).count()
            )

        # 🔥 FIX — same gap as `add_members` above; cache_utils.py's setup
        # docstring names `approve_join_request` explicitly too.
        invalidate_group_role_cache(group.id, join_request.user_id)

        member = GroupMember.objects.select_related('user').get(group=group, user=join_request.user)
        return Response(GroupMemberSerializer(member, context={'request': request}).data)

    # 🔥 NAYA — ek pending request reject karna (admin/moderator only).
    @action(detail=True, methods=['post'], url_path=r'join-requests/(?P<request_id>[^/.]+)/reject')
    def reject_join_request(self, request, pk=None, request_id=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        join_request = get_object_or_404(
            GroupJoinRequest, id=request_id, group=group, status=GroupJoinRequest.Status.PENDING,
        )
        join_request.status = GroupJoinRequest.Status.REJECTED
        join_request.responded_by = request.user
        join_request.responded_at = timezone.now()
        join_request.save(update_fields=['status', 'responded_by', 'responded_at'])
        return Response({'detail': 'Request reject ho gayi.'}, status=status.HTTP_200_OK)

    # 🔧 FIX — invite-code generation used to be duplicated here too
    # (identical `secrets.token_urlsafe` + uniqueness-loop). `create()`
    # now goes through `services.create_group()`, which calls `services.
    # generate_group_invite_code()` — this static method has no remaining
    # caller.

    @staticmethod
    def _require_admin(group_id, user):
        # 🔥 FIX — pehle yahan is check ka apna alag raw query tha
        # (`GroupMember.objects.filter(...)`), jabki `group_rules.
        # is_group_admin_or_mod` bhi EXACT same rule (admin/mod, not
        # banned) implement karta hai. Do independent implementations ek
        # hi rule ki — bilkul wahi class ka risk jo `MAX_PINNED_PER_
        # CONVERSATION` pehle duplicate hone se hua tha (dono kabhi
        # silently drift kar sakte the). Ab dono ek hi (cached) function
        # use karte hain — single source of truth.
        from ..models import Group
        group = Group.objects.get(id=group_id)
        if not is_group_admin_or_mod(group, user.id):
            raise PermissionDenied('Sirf group admin/moderator ye action kar sakte hain.')

    @action(detail=True, methods=['patch', 'delete'], url_path=r'members/(?P<user_id>[^/.]+)')
    def update_member(self, request, pk=None, user_id=None):
        group = self.get_object()

        # 🔧 FIX — both branches used to duplicate `services.
        # remove_group_member()` / `services.update_group_member_role()`
        # inline (self-removal bypass, admin/mod gate, the `is_banned` <->
        # `left_at` sync, cache invalidation — all of it, verbatim). Both
        # services functions raise plain `PermissionError` on the same
        # rule `_require_admin` used to check directly; converted to DRF's
        # `PermissionDenied` right here, at the view boundary, exactly as
        # `services.py`'s header comment describes.
        if request.method == 'DELETE':
            try:
                remove_group_member(group=group, actor=request.user, user_id=user_id)
            except PermissionError as e:
                raise PermissionDenied(str(e))
            return Response(status=status.HTTP_204_NO_CONTENT)

        try:
            membership = update_group_member_role(
                group=group, actor=request.user, user_id=user_id, data=request.data,
            )
        except PermissionError as e:
            raise PermissionDenied(str(e))

        return Response(GroupMemberSerializer(membership, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='media')
    def media(self, request, pk=None):
        group = self.get_object()
        qs = group.gallery.select_related('sender').order_by('-created_at')
        file_type = request.query_params.get('type')
        if file_type:
            qs = qs.filter(file_type=file_type)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = GroupMediaSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


# ======================================================================
# 🔥 NAYA — DOUBT QUEUE
#
# Persistent, upvotable question board per classroom (group). Hand-raise
# (`study_room` / call flows) sirf live-moment ke liye hai; ye alag se hai
# taaki students apna doubt kabhi bhi post kar sakein, dusre students
# "मुझे भी yahi doubt hai" upvote kar sakein, aur teacher (admin/moderator)
# sabse-upvoted wala pehle answer kare (`DoubtQuestion.Meta.ordering`).
#
# "Ask Anonymously" (per-classroom, teacher-toggleable via
# `Group.allow_anonymous_doubts` — same PATCH `/groups/<id>/` endpoint
# `GroupViewSet` already exposes for message/call/study-room permissions)
# rides along in the SAME model: `is_anonymous` hides the author from
# everyone except the asker until the teacher explicitly calls `reveal`
# (see `DoubtQuestionSerializer.get_author` for the exact visibility rule).
#
# Nested under a group (`/message/groups/<group_id>/doubts/...` — see
# urls.py), isliye `ModelViewSet`/router use nahi kiya — `GenericViewSet`
# + explicit `list`/`create` + 3 `@action`s, group-membership `get_group()`
# se ek hi jagah enforce hoti hai (list/create/upvote/answer/reveal sab
# isi se guzarte hain).
# ======================================================================
class DoubtQuestionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    Doubt Queue endpoints (all under `/message/groups/<group_id>/doubts/`,
    see urls.py — explicit paths, no router):

      GET    doubts/?status=&mine=            list (paginated, upvotes-first)
      POST   doubts/                          create — JSON, or multipart with `image` (T1.1)
      GET    doubts/<id>/                     detail incl. full answer thread (T1.3)
      POST/DELETE doubts/<id>/upvote/
      GET/POST    doubts/<id>/answers/        list thread / post an answer (any member) (T1.3)
      DELETE doubts/<id>/answers/<aid>/       author or teacher
      POST/DELETE doubts/<id>/answers/<aid>/best/     asker or teacher (T1.3)
      POST/DELETE doubts/<id>/answers/<aid>/verify/   teacher only     (T1.3)
      POST   doubts/<id>/answer/              LEGACY alias of POST answers/ (returns 200)
      POST   doubts/<id>/reveal/              teacher only

    `?status=` — answered | unanswered | needs_review (has answers but none
    teacher-verified: the teacher's triage queue) | verified.
    `?mine=1` — only doubts I asked.

    Every answer-mutating endpoint returns the refreshed doubt WITH its
    answer thread (`DoubtQuestionDetailSerializer`) so a client replaces its
    local copy in one go.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DoubtQuestionSerializer
    # T1.1 — create() accepts multipart (photo) as well as the old JSON body.
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    # --------------------------------------------------------------
    # T1.4 (D7) — throttling. Rates live in settings.DEFAULT_THROTTLE_RATES.
    # --------------------------------------------------------------
    def get_throttles(self):
        action_name = getattr(self, 'action', None)
        if action_name == 'create':
            classes = [DoubtCreateThrottle]
            try:
                has_image = bool(self.request.FILES.get('image'))
            except Exception:  # malformed body -> serializer will 400 it properly
                has_image = False
            if has_image:
                classes.append(DoubtImageUploadThrottle)
        elif action_name in ('answer', 'create_answer'):
            classes = [DoubtAnswerThrottle]
        elif action_name == 'upvote':
            classes = [DoubtUpvoteThrottle]
        elif action_name == 'similar':
            classes = [DoubtSimilarThrottle]  # T1.5
        elif action_name in ('reveal', 'best', 'verify', 'delete_answer', 'public'):
            classes = [DoubtActionThrottle]
        else:  # list / retrieve / list_answers
            classes = [DoubtReadThrottle]
        return [c() for c in classes]

    def get_group(self):
        # Memoised per request: list() reaches this via get_queryset() and
        # the write actions call it directly — one membership check each.
        cached = getattr(self, '_group_cache', None)
        if cached is not None:
            return cached
        group = get_object_or_404(Group, id=self.kwargs['group_id'])
        is_member = GroupMember.objects.filter(
            group=group, user=self.request.user, is_banned=False,
        ).exists()
        if not is_member:
            raise PermissionDenied("Aap is classroom/group ke member nahi hain.")
        self._group_cache = group
        return group

    # --------------------------------------------------------------
    # T1.4 (D6) — list query shape. For a page of N doubts this is a FIXED
    # number of queries regardless of N:
    #   1  COUNT(*) (paginator)
    #   1  SELECT doubts + author + answered_by (select_related) +
    #      `upvoted_flag` (EXISTS subquery — replaces the old
    #      `prefetch_related('upvotes')` that pulled EVERY upvote row of
    #      every doubt on the page just to answer "did I upvote this?")
    #   1  best-answer prefetch (only is_best rows, + author/verified_by)
    # `answers_count` / `has_best_answer` / `has_verified_answer` are
    # denormalized columns, so no per-row COUNT/EXISTS on DoubtAnswer.
    # --------------------------------------------------------------
    def _base_queryset(self, group):
        upvoted = DoubtUpvote.objects.filter(doubt=OuterRef('pk'), user=self.request.user)
        return (
            DoubtQuestion.objects
            .filter(group=group)
            .select_related('author', 'answered_by')
            .annotate(upvoted_flag=Exists(upvoted))
        )

    def get_queryset(self):
        group = self.get_group()
        qs = self._base_queryset(group).prefetch_related(
            Prefetch(
                'answers',
                queryset=DoubtAnswer.objects.filter(is_best=True).select_related('author', 'verified_by'),
                to_attr='best_answer_list',
            )
        )
        # `?status=` — Doubts tab filters.
        status_filter = self.request.query_params.get('status')
        if status_filter == 'answered':
            qs = qs.filter(is_answered=True)
        elif status_filter == 'unanswered':
            qs = qs.filter(is_answered=False)
        elif status_filter == 'needs_review':
            qs = qs.filter(is_answered=True, has_verified_answer=False)
        elif status_filter == 'verified':
            qs = qs.filter(has_verified_answer=True)
        if self.request.query_params.get('mine') in ('1', 'true', 'True'):
            qs = qs.filter(author=self.request.user)
        return qs

    def _detail_object(self, group, doubt_id):
        """Doubt + full thread, viewer-annotated — for detail endpoint,
        answer actions and broadcasts."""
        qs = self._base_queryset(group).prefetch_related(
            Prefetch(
                'answers',
                queryset=DoubtAnswer.objects.select_related('author', 'verified_by'),
            )
        )
        doubt = get_object_or_404(qs, id=doubt_id)
        # `best_answer` preview comes from the already-prefetched thread.
        doubt.best_answer_list = [a for a in doubt.answers.all() if a.is_best]
        return doubt

    def _detail_response(self, group, doubt_id, *, http_status=status.HTTP_200_OK, event=None):
        doubt = self._detail_object(group, doubt_id)
        out = DoubtQuestionDetailSerializer(doubt, context={'request': self.request}).data
        if event:
            self._broadcast(group, event, doubt)
        return Response(out, status=http_status)

    def list(self, request, *args, **kwargs):
        paginator = StandardPagination()
        qs = self.filter_queryset(self.get_queryset())
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = self.get_serializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def retrieve(self, request, pk=None, group_id=None, *args, **kwargs):
        group = self.get_group()
        return self._detail_response(group, pk)

    def create(self, request, *args, **kwargs):
        group = self.get_group()
        serializer = DoubtCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        is_anonymous = data['is_anonymous']
        if is_anonymous and not group.allow_anonymous_doubts:
            return Response(
                {'detail': 'Is classroom me anonymous doubts teacher ne band kar rakhe hain.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        doubt = DoubtQuestion.objects.create(
            group=group, conversation_id=group.conversation_id, author=request.user,
            text=data.get('text', ''), is_anonymous=is_anonymous,
            image=data.get('image'),  # already sanitised ContentFile, or None
        )
        out = DoubtQuestionSerializer(doubt, context={'request': request}).data
        self._broadcast(group, 'doubt_created', doubt)
        return Response(out, status=status.HTTP_201_CREATED)

    # T1.5 (D5) — "similar doubt" suggestion. Student naya doubt type karte
    # waqt (debounced) client ye call karta hai; already-pucha hua (aur
    # answered) doubt dikha do to duplicate banta hi nahi. Same `get_group()`
    # membership rule as every other action here. Too-short query pe 200 +
    # empty list (typing ke beech 400 noisy hota) — missing `q` pe 400.
    # Response: {query, count, results: [<DoubtQuestionSerializer> + similarity]}
    @action(
        detail=False, methods=['get'], url_path='similar',
        throttle_classes=[DoubtSimilarThrottle],
    )
    def similar(self, request, group_id=None):
        group = self.get_group()
        raw_q = request.query_params.get('q')
        if raw_q is None:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)

        query = doubt_similarity.normalize_query(raw_q)
        limit = doubt_similarity.clamp_limit(request.query_params.get('limit'))
        if len(query) < doubt_similarity.MIN_QUERY_CHARS:
            return Response({
                'query': query, 'count': 0, 'results': [],
                'min_chars': doubt_similarity.MIN_QUERY_CHARS,
            })

        matches = doubt_similarity.find_similar_doubts(group, query, limit)
        data = DoubtQuestionSerializer(
            [d for d, _ in matches], many=True, context={'request': request},
        ).data
        results = []
        for row, (_, score) in zip(data, matches):
            row = dict(row)
            row['similarity'] = score
            results.append(row)
        return Response({'query': query, 'count': len(results), 'results': results})

    # 🔥 "मुझे भी yahi doubt hai" — POST to upvote, DELETE to un-upvote.
    # Ek user ek doubt ko ek hi baar count karta hai (`DoubtUpvote.
    # unique_together`) — repeat POST bas idempotent no-op hai (`get_or_
    # create`), koi error nahi.
    @action(detail=True, methods=['post', 'delete'], url_path='upvote')
    def upvote(self, request, pk=None, group_id=None):
        group = self.get_group()
        doubt = get_object_or_404(DoubtQuestion, id=pk, group=group)

        if request.method == 'DELETE':
            deleted_count, _ = DoubtUpvote.objects.filter(doubt=doubt, user=request.user).delete()
            if deleted_count:
                DoubtQuestion.objects.filter(id=doubt.id).update(upvotes_count=F('upvotes_count') - 1)
        else:
            _, created = DoubtUpvote.objects.get_or_create(doubt=doubt, user=request.user)
            if created:
                DoubtQuestion.objects.filter(id=doubt.id).update(upvotes_count=F('upvotes_count') + 1)

        doubt.refresh_from_db()
        out = DoubtQuestionSerializer(doubt, context={'request': request}).data
        self._broadcast(group, 'doubt_upvoted', doubt)
        return Response(out)

    # --------------------------------------------------------------
    # T1.3 — answers
    # --------------------------------------------------------------
    @staticmethod
    def _answer_error(exc):
        """services.py's plain exceptions -> DRF/HTTP."""
        if isinstance(exc, PermissionError):
            raise PermissionDenied(str(exc))
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def list_answers(self, request, pk=None, group_id=None):
        group = self.get_group()
        doubt = get_object_or_404(DoubtQuestion, id=pk, group=group)
        qs = DoubtAnswer.objects.filter(doubt=doubt).select_related('author', 'verified_by')
        return Response(DoubtAnswerItemSerializer(qs, many=True, context={'request': request}).data)

    def create_answer(self, request, pk=None, group_id=None):
        """Any group member (except the asker) answers; teacher answers are
        auto-verified. 201 + refreshed doubt thread."""
        group = self.get_group()
        doubt = get_object_or_404(DoubtQuestion.objects.select_related('group'), id=pk, group=group)

        serializer = DoubtAnswerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_doubt_answer(doubt=doubt, author=request.user, text=serializer.validated_data['text'])
        except (ValueError, PermissionError) as e:
            return self._answer_error(e)
        return self._detail_response(group, doubt.id, http_status=status.HTTP_201_CREATED, event='doubt_answered')

    # LEGACY (pre-T1.3 clients): POST doubts/<id>/answer/ {answer_text}.
    # Used to be teacher-only/single-answer; now an alias of `create_answer`
    # (same rules, any member) that keeps the old 200 status + doubt shape.
    @action(detail=True, methods=['post'], url_path='answer')
    def answer(self, request, pk=None, group_id=None):
        group = self.get_group()
        doubt = get_object_or_404(DoubtQuestion.objects.select_related('group'), id=pk, group=group)

        serializer = DoubtAnswerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_doubt_answer(doubt=doubt, author=request.user, text=serializer.validated_data['text'])
        except (ValueError, PermissionError) as e:
            return self._answer_error(e)
        return self._detail_response(group, doubt.id, event='doubt_answered')

    def _get_answer(self, group, pk, answer_id):
        doubt = get_object_or_404(DoubtQuestion.objects.select_related('group'), id=pk, group=group)
        answer = get_object_or_404(DoubtAnswer.objects.select_related('author'), id=answer_id, doubt=doubt)
        answer.doubt = doubt
        return answer

    def best(self, request, pk=None, group_id=None, answer_id=None):
        """POST = mark as best answer, DELETE = un-mark. Asker or teacher."""
        group = self.get_group()
        answer = self._get_answer(group, pk, answer_id)
        try:
            set_best_answer(answer=answer, actor=request.user, best=(request.method == 'POST'))
        except (ValueError, PermissionError) as e:
            return self._answer_error(e)
        return self._detail_response(group, answer.doubt_id, event='doubt_answer_updated')

    def verify(self, request, pk=None, group_id=None, answer_id=None):
        """POST = "Verified by teacher", DELETE = remove. Teacher only."""
        group = self.get_group()
        answer = self._get_answer(group, pk, answer_id)
        try:
            set_answer_verified(answer=answer, actor=request.user, verified=(request.method == 'POST'))
        except (ValueError, PermissionError) as e:
            return self._answer_error(e)
        return self._detail_response(group, answer.doubt_id, event='doubt_answer_updated')

    def delete_answer(self, request, pk=None, group_id=None, answer_id=None):
        group = self.get_group()
        answer = self._get_answer(group, pk, answer_id)
        try:
            delete_doubt_answer(answer=answer, actor=request.user)
        except (ValueError, PermissionError) as e:
            return self._answer_error(e)
        return self._detail_response(group, answer.doubt_id, event='doubt_answer_deleted')

    # 🔥 Teacher explicitly asks to see who asked an anonymous doubt (§2 —
    # "unless teacher explicitly reveal maange for follow-up"). One-way:
    # once revealed it stays revealed for every future admin/moderator view
    # of this doubt (see `DoubtQuestionSerializer.get_author`).
    @action(detail=True, methods=['post'], url_path='reveal')
    def reveal(self, request, pk=None, group_id=None):
        group = self.get_group()
        self._require_teacher(group, request.user)
        doubt = get_object_or_404(DoubtQuestion, id=pk, group=group)

        if doubt.is_anonymous and not doubt.is_revealed:
            doubt.is_revealed = True
            doubt.save(update_fields=['is_revealed'])
            # Sirf broadcast karte hain agar kuch actually badla — reveal
            # ek hi baar "news" hai, isliye WS bhi sirf pehli baar milta hai.
            out = DoubtQuestionSerializer(doubt, context={'request': request}).data
            self._broadcast(group, 'doubt_revealed', doubt)
            return Response(out)

        return Response(DoubtQuestionSerializer(doubt, context={'request': request}).data)

    # TASK T2.6 — POST publishes an answered, non-anonymous doubt as a public
    # SEO Q&A page (`/doubts/<slug>/`); DELETE takes it down again. Teacher
    # (admin/moderator) only: the answer is the class's content, and the
    # asker's identity is never shown on the page either way.
    @action(detail=True, methods=['post', 'delete'], url_path='public')
    def public(self, request, pk=None, group_id=None):
        group = self.get_group()
        self._require_teacher(group, request.user)
        doubt = get_object_or_404(DoubtQuestion, id=pk, group=group)

        if request.method == 'DELETE':
            doubt.make_private()
        else:
            try:
                doubt.make_public()
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(DoubtQuestionSerializer(doubt, context={'request': request}).data)

    @staticmethod
    def _require_teacher(group, user):
        if not is_group_admin_or_mod(group, user.id):
            raise PermissionDenied("Sirf group admin/moderator (teacher) ye action kar sakte hain.")

    def _broadcast(self, group, event, doubt):
        """WS fan-out to `chat_{conversation_id}` (ChatConsumer already
        listens there — no new WS group). The payload is serialised
        VIEWER-NEUTRAL (`broadcast=True`) and always carries the full answer
        thread: the same bytes go to every member, so nothing here may
        depend on who triggered the event (an anonymous asker's own name /
        `is_mine` / `upvoted_by_me` must not leak). Receivers keep their own
        per-viewer fields locally. Best-effort: the DB write already
        succeeded, a channel-layer outage must not turn it into a 500."""
        try:
            # T0.2 — serializer `.data` me UUID objects hote hain; channels_redis
            # (msgpack) unhe serialize nahi kar paata => json_safe() se plain JSON.
            payload = json_safe(self._broadcast_payload(group, doubt))
            channel_layer = get_channel_layer()
            message = json_safe({
                'type': 'doubt_broadcast',
                'event': event,
                'doubt': payload,
                'group_id': str(group.id),
            })

            # M1 — time-bound: Redis atka/down ho to ye call request ko seconds
            # tak rok sakta tha (POST 201 me 8.5s). Ab max 2s, uske baad skip —
            # DB write ho chuka hai, client apna REST response already use karta hai.
            async def _send():
                await asyncio.wait_for(
                    channel_layer.group_send(f'chat_{group.conversation_id}', message),
                    timeout=2,
                )

            async_to_sync(_send)()
        except Exception:
            logger.exception("doubt broadcast failed (event=%s doubt=%s)", event, getattr(doubt, 'id', None))

    def _broadcast_payload(self, group, doubt):
        fresh = (
            DoubtQuestion.objects.filter(pk=doubt.pk)
            .select_related('author', 'answered_by')
            .prefetch_related(Prefetch(
                'answers', queryset=DoubtAnswer.objects.select_related('author', 'verified_by'),
            ))
            .first()
        )
        if fresh is None:
            return None
        fresh.best_answer_list = [a for a in fresh.answers.all() if a.is_best]
        return DoubtQuestionDetailSerializer(
            fresh, context={'request': self.request, 'broadcast': True},
        ).data
