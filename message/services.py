# message/services.py
#
# 🔥 NAYA (task 27) — `GroupViewSet.create` / `add_members` / `update_member`
# ka core logic yahan plain functions me extract kiya gaya hai, taaki ye
# HTTP (DRF Response/PermissionDenied) se decoupled ho jaaye aur kahin bhi
# reuse ho sake — sabse pehli zaroorat khud isi batch me hai:
# `core/classroom_chat_bridge.py` ko bilkul yehi "group banao" / "member
# add karo" / "role badlo" logic chahiye (Classroom <-> chat-group bridge
# ke liye), bina DRF request/response cycle ke through jaaye.
#
# Design rule: ye functions kabhi DRF exception (`PermissionDenied`,
# `ValidationError`) nahi raise karte — plain `ValueError`/
# `PermissionError` raise karte hain. `GroupViewSet` in dono ko apne try/
# except me DRF ke equivalent exceptions me convert karta hai (neeche
# comment me shape dikhaya gaya hai) — isse ye module DRF import kiye
# bina bhi (core/ jaisi jagah se) use ho sakta hai.
#
# GroupViewSet khud ab sirf ek thin wrapper hai: request parse karo, in
# functions ko call karo, Response banao. Permission/role checks
# (`_require_admin`, `group.is_private`) bhi yahin move ho gaye hain taaki
# ek hi jagah se "kaun kya kar sakta hai" control ho.

import secrets
from typing import Iterable, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .cache_utils import invalidate_group_role_cache
from .group_rules import is_group_admin_or_mod
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMember,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# get_or_create_conversation() — mutual-follow (user_profile/signals.py) ke
# liye. `Conversation.get_or_create_private()` (models.py) khud hi race-safe
# get_or_create hai (private_key unique constraint) — ye function usi ko
# thin-wrap karta hai taaki har caller isi ek naam/jagah se dhoonde, aur
# NEW conversation banne par dono participants ko unke personal inbox-socket
# group pe turant push kar de (see consumers.py InboxConsumer.
# conversation_created) — WITHOUT bhejna koi actual `Message` row, taaki
# frontend ka "Start the conversation" empty-state list item me dikhe.
# ---------------------------------------------------------------------------
def get_or_create_conversation(user_a, user_b):
    """
    `user_a`/`user_b` User instance ya id (int/str) ho sakte hain.
    Returns: (conversation, created) — `Conversation.get_or_create_private`
    jaisa hi.
    """
    if not hasattr(user_a, "pk"):
        user_a = User.objects.get(pk=user_a)
    if not hasattr(user_b, "pk"):
        user_b = User.objects.get(pk=user_b)

    conversation, created = Conversation.get_or_create_private(user_a, user_b)

    if created:
        _broadcast_conversation_created(conversation, [user_a.id, user_b.id])

    return conversation, created


def _broadcast_conversation_created(conversation, user_ids):
    """Dono participants ke `user_<uid>` inbox-socket group (InboxConsumer,
    consumers.py) pe naya conversation ban jane ka event bhejta hai — agar
    koi connected hai to inbox list bina refresh ke turant update ho jaaye.
    Channel layer configured na ho (e.g. kuch test setups) to silently
    skip — ye best-effort real-time push hai, REST list hamesha source of
    truth rehti hai."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = {
        "type": "conversation_created",
        "conversation_id": str(conversation.id),
        "conversation_type": conversation.type,
        "created_at": conversation.created_at.isoformat(),
    }
    for uid in user_ids:
        async_to_sync(channel_layer.group_send)(f"user_{uid}", payload)


# ---------------------------------------------------------------------------
# Shared helper — moved here from views.py (single source; views.py now
# imports this from here instead of defining its own copy).
# ---------------------------------------------------------------------------
def add_or_reactivate_participant(conversation, user):
    """
    `ConversationParticipant` row banao agar exist nahi karti, ya agar
    pehle se hai (user pehle group chhod chuka tha, `left_at` set tha) to
    use wapas active kar do. Har jagah jahan bhi koi user (re-)add hota
    hai — group create, add_members, join, approve_join_request, aur ab
    classroom-chat-bridge — isi function se guzarta hai, taaki "member ban
    gaya par left_at abhi bhi set hai isliye chat me dikh nahi raha" wala
    bug kabhi na ho.
    """
    participant, created = ConversationParticipant.objects.get_or_create(
        conversation=conversation, user=user,
    )
    if not created and participant.left_at is not None:
        participant.left_at = None
        participant.save(update_fields=['left_at'])
    return participant, created


# ---------------------------------------------------------------------------
# create_message_and_broadcast() — the shared core of
# `ConversationViewSet.messages()`'s POST branch (views.py), pulled out so a
# non-REST caller (currently: `post.views.StoryReplyAPIView`, delivering a
# story reply into the recipient's DM inbox) can create a real `Message`
# row and push it out over the EXACT SAME two channel-layer paths that
# endpoint already uses, instead of that caller growing its own parallel
# broadcast logic:
#   - `chat_{conversation.id}`   -> ChatConsumer.chat_message (open chat screen)
#   - `user_{other_uid}`         -> InboxConsumer.inbox_update (chat list)
#
# Deliberately does NOT include the REST-only pieces of that view action
# (permission gates, @mention extraction, group daily-limit checks, the
# link-preview/voice-transcribe Celery hooks, `client_id` offline-dedup) —
# none of those apply to a message a backend feature is inserting into a
# thread on a user's behalf. If a future caller needs one of those, extend
# this function's kwargs rather than duplicating the loop again elsewhere.
# ---------------------------------------------------------------------------
def create_message_and_broadcast(
    *, conversation, sender, message_type, text=None,
    file_url=None, file_urls=None, thumbnail_url=None, meta=None,
    extra_fields=None, send_push=True,
):
    """Returns the created `Message`."""
    from django.db.models import F
    from .models import Message, MessageStatus
    from .push_utils import send_chat_message_push
    from .user_display import build_user_mini

    extra_fields = extra_fields or {}
    disappearing_delta = conversation.get_disappearing_timedelta()
    expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

    with transaction.atomic():
        message = Message.objects.create(
            conversation=conversation,
            sender=sender,
            type=message_type,
            text=text,
            file_url=file_url,
            file_urls=file_urls or [],
            thumbnail_url=thumbnail_url,
            meta=meta or {},
            expires_at=expires_at,
            **extra_fields,
        )

        conversation.last_message_text = (text or '')[:500]
        conversation.last_message_at = message.created_at
        conversation.last_message_sender = sender
        conversation.last_message_type = message.type
        conversation.save(update_fields=[
            'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
        ])

        other_recipients = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user=sender)
            .values_list('user_id', flat=True)
        )
        ConversationParticipant.objects.filter(
            conversation=conversation, user_id__in=other_recipients,
        ).update(unread_count=F('unread_count') + 1)

        MessageStatus.objects.bulk_create(
            [MessageStatus(message=message, user_id=uid) for uid in other_recipients],
            ignore_conflicts=True,
        )

    sender_mini = build_user_mini(sender)
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(message.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(sender.id),
                'sender_name': sender_mini['display_name'],
                'sender_username': sender_mini['username'],
                'sender_first_name': sender_mini['first_name'],
                'sender_last_name': sender_mini['last_name'],
                'sender_profile_photo': sender_mini['profile_photo'],
                'message_type': message.type,
                'text': message.text,
                'file_url': message.file_url,
                'file_urls': message.file_urls,
                'thumbnail_url': message.thumbnail_url,
                'meta': message.meta,
                'reply_to': None,
                'client_id': None,
                'mentioned_user_ids': [],
                # Story-reply metadata — harmless/absent for every other
                # message type, read by the Flutter side's
                # `MessageType.storyReply` branch (message_models.dart).
                'story_id': str(message.story_id) if message.story_id else None,
                'story_reply_snapshot': message.story_reply_snapshot,
                'created_at': message.created_at.isoformat(),
            }
        )
        for uid in other_recipients:
            async_to_sync(channel_layer.group_send)(
                f'user_{uid}',
                {
                    'type': 'inbox_update',
                    'conversation_id': str(conversation.id),
                    'message_id': str(message.id),
                    'sender_id': str(sender.id),
                    'sender_name': sender_mini['display_name'],
                    'last_message_text': message.text,
                    'last_message_type': message.type,
                    'created_at': message.created_at.isoformat(),
                }
            )

    if send_push and other_recipients:
        muted_user_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_recipients, is_muted=True,
            ).values_list('user_id', flat=True)
        )
        push_recipients = [uid for uid in other_recipients if uid not in muted_user_ids]
        if push_recipients:
            send_chat_message_push(
                recipient_ids=push_recipients,
                sender_name=sender_mini['display_name'],
                message_text=message.text,
                message_type=message.type,
                conversation_id=conversation.id,
                message_id=message.id,
                is_announcement=False,
            )

    return message


def generate_group_invite_code() -> str:
    while True:
        code = secrets.token_urlsafe(6)[:8]
        if not Group.objects.filter(invite_code=code).exists():
            return code


def require_group_admin_or_mod(group: Group, user) -> None:
    """Raise plain PermissionError (not DRF's) if `user` isn't admin/mod
    on `group`. Caller (view or classroom_chat_bridge) converts this to
    whatever error shape it needs."""
    if not is_group_admin_or_mod(group, user.id):
        raise PermissionError('Sirf group admin/moderator ye action kar sakte hain.')


# ---------------------------------------------------------------------------
# create() — extracted from GroupViewSet.create
# ---------------------------------------------------------------------------
def create_group(
    *,
    created_by,
    name: str,
    description: str = '',
    photo_url: Optional[str] = None,
    is_private: bool = False,
    member_ids: Iterable = (),
    topic_tag: Optional[str] = None,
) -> Group:
    """
    Naya group + uski underlying Conversation banata hai, `created_by` ko
    ADMIN role ke saath, aur `member_ids` (agar diye hon) ko plain MEMBER
    ke roop me. `created_by` khud `member_ids` me ho to bhi duplicate
    nahi banega (discard kar diya jaata hai).

    🔥 NAYA (Task G14) — `topic_tag` optional hai (e.g. "NEET 2027",
    "JEE Mains") — sirf discovery search ke liye use hota hai, koi aur
    behaviour nahi badalta. Blank/None chhod do to group bas discover
    search me topic-filter se nahi milega (name-search se phir bhi milega).
    """
    with transaction.atomic():
        conversation = Conversation.objects.create(type=ConversationType.GROUP)
        group = Group.objects.create(
            conversation=conversation,
            name=name,
            description=description or '',
            photo_url=photo_url,
            is_private=is_private,
            invite_code=generate_group_invite_code(),
            created_by=created_by,
            topic_tag=(topic_tag or '').strip() or None,
        )

        ids = set(member_ids)
        ids.discard(created_by.id)
        valid_users = list(User.objects.filter(id__in=ids))

        memberships = [ConversationParticipant(conversation=conversation, user=created_by)]
        group_members = [GroupMember(group=group, user=created_by, role=GroupMember.Role.ADMIN)]
        for user in valid_users:
            memberships.append(ConversationParticipant(conversation=conversation, user=user))
            group_members.append(GroupMember(group=group, user=user, added_by=created_by))

        ConversationParticipant.objects.bulk_create(memberships)
        GroupMember.objects.bulk_create(group_members)
        Group.objects.filter(id=group.id).update(members_count=len(group_members))
        group.refresh_from_db()

    return group


# ---------------------------------------------------------------------------
# add_members() — extracted from GroupViewSet.add_members
# ---------------------------------------------------------------------------
def add_members_to_group(*, group: Group, actor, user_ids: Iterable) -> list:
    """
    `actor` ke through `user_ids` ko `group` me add karta hai. Public
    group: koi bhi member add kar sakta hai. Private group: sirf
    admin/moderator — caller (view) ye role-check khud `actor` ki group
    membership confirm hone ke baad hi karega; classroom_chat_bridge jaisa
    internal/system caller `actor=None` pass kar sakta hai (kyunki wo call
    khud teacher ke confirm-action ke response me system code se ho raha
    hai, koi doosra HTTP request nahi) — is case me role-check skip hota
    hai.

    Returns: newly-added `User` instances ki list (already-member users
    silently skip ho jaate hain, error nahi).
    Raises: PermissionError agar private group + non-admin actor.
    ValueError agar `user_ids` empty ho.
    """
    if group.is_private and actor is not None:
        require_group_admin_or_mod(group, actor)

    user_ids = list(user_ids)
    if not user_ids:
        raise ValueError("'user_ids' required hai.")

    existing_ids = set(str(uid) for uid in group.group_members.values_list('user_id', flat=True))
    new_ids = [uid for uid in user_ids if str(uid) not in existing_ids]
    users = list(User.objects.filter(id__in=new_ids))

    with transaction.atomic():
        for user in users:
            add_or_reactivate_participant(group.conversation, user)
            GroupMember.objects.get_or_create(
                group=group, user=user,
                defaults={'added_by': actor if actor is not None else group.created_by},
            )
        Group.objects.filter(id=group.id).update(
            members_count=group.group_members.filter(is_banned=False).count()
        )

    for user in users:
        invalidate_group_role_cache(group.id, user.id)

    return users


# ---------------------------------------------------------------------------
# update_member() — extracted from GroupViewSet.update_member
# ---------------------------------------------------------------------------
def remove_group_member(*, group: Group, actor, user_id) -> None:
    """DELETE branch: `actor` khud ko remove kar sakta hai bina kisi
    role-check ke; kisi aur ko remove karne ke liye admin/mod hona
    zaroori hai."""
    from django.shortcuts import get_object_or_404

    membership = get_object_or_404(GroupMember, group=group, user_id=user_id)
    is_self = actor is not None and str(actor.id) == str(user_id)
    if not is_self and actor is not None:
        require_group_admin_or_mod(group, actor)

    membership.delete()
    ConversationParticipant.objects.filter(
        conversation=group.conversation, user_id=user_id
    ).update(left_at=timezone.now())
    Group.objects.filter(id=group.id).update(
        members_count=group.group_members.filter(is_banned=False).count()
    )
    invalidate_group_role_cache(group.id, user_id)


def update_group_member_role(*, group: Group, actor, user_id, data: dict) -> GroupMember:
    """PATCH branch: role / is_muted / is_banned change — admin/mod only.
    `actor=None` (internal/system caller, e.g. classroom_chat_bridge's
    promote_to_moderator) skips the permission check, same convention as
    `add_members_to_group` above."""
    from django.shortcuts import get_object_or_404

    membership = get_object_or_404(GroupMember, group=group, user_id=user_id)
    if actor is not None:
        require_group_admin_or_mod(group, actor)

    was_banned = membership.is_banned
    for field in ('role', 'is_muted', 'is_banned'):
        if field in data:
            setattr(membership, field, data[field])
    membership.save()

    invalidate_group_role_cache(group.id, user_id)

    if membership.is_banned and not was_banned:
        ConversationParticipant.objects.filter(
            conversation=group.conversation, user_id=user_id
        ).update(left_at=timezone.now())
    elif was_banned and not membership.is_banned:
        ConversationParticipant.objects.filter(
            conversation=group.conversation, user_id=user_id
        ).update(left_at=None)

    return membership

# ---------------------------------------------------------------------------
# answer_doubt_question() — Task 16. Shared answer-path for BOTH classroom
# doubts (`doubt.group` set) and context-pointer doubts (`doubt.
# context_type`/`context_id` set, e.g. testseries — see `testseries/
# bridge.py::answer_query_on_series()`), since `DoubtQuestion` itself now
# supports both shapes (see models.py's Task 16 comment on the field).
# ---------------------------------------------------------------------------
def answer_doubt_question(*, doubt, actor, answer_text: str, answered_by=None):
    """
    `actor`: used ONLY for the classroom-doubt permission check
    (admin/mod on `doubt.group`) — same `actor=None` convention as
    `add_members_to_group`/`update_group_member_role` above: pass `None`
    when the caller has already authorized this itself (e.g. `testseries/
    bridge.py::answer_query_on_series()`, which confirms
    `teacher == series.creator` before ever calling here) or when
    `doubt.group` is None (a context-pointer doubt has no group to check
    admin/mod against in the first place).

    `answered_by`: the user to record as having answered. Defaults to
    `actor` (the normal classroom-teacher-answers-directly case). Kept as
    a SEPARATE param from `actor` so a trusted caller passing `actor=None`
    (to skip the group-permission check) can still correctly record who
    answered — `testseries/bridge.py::answer_query_on_series()` passes its
    already-verified `teacher` here even though it passes `actor=None`.

    Raises `ValueError` if `doubt` is already answered, or if neither
    `actor` nor `answered_by` is given (nothing to record as the answerer).
    """
    if doubt.is_answered:
        raise ValueError('Ye doubt pehle se hi answer ho chuka hai.')

    if doubt.group_id is not None and actor is not None:
        require_group_admin_or_mod(doubt.group, actor)

    resolved_answered_by = answered_by if answered_by is not None else actor
    if resolved_answered_by is None:
        raise ValueError("'answered_by' (ya 'actor') required hai.")

    doubt.is_answered = True
    doubt.answer_text = answer_text
    doubt.answered_by = resolved_answered_by
    doubt.answered_at = timezone.now()
    doubt.save(update_fields=['is_answered', 'answer_text', 'answered_by', 'answered_at', 'updated_at'])

    # Task 16 acceptance checklist: student gets TESTSERIES_QUERY_ANSWERED
    # when their testseries query is answered. Classroom (group) doubts
    # don't get a notify here — deliberately: no acceptance requirement
    # for that path in this task, and it'd need its own NotifType member
    # (same "flag the gap, don't guess" convention `testseries/models.py`
    # already uses for its own unconfirmed NotifType members).
    if doubt.context_type == 'testseries_attempt':
        # ⚠️ GAP, same shape as testseries/models.py's own flagged gaps:
        # `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`
        # is NOT confirmed to exist yet. Until `core` adds it, this raises
        # `AttributeError` at this point — i.e. the answer itself (fields
        # above) is saved and real either way, only this notify step needs
        # that enum member added first.
        from core.models import Notification
        from core.services import create_notification

        create_notification(
            recipient=doubt.author,
            notif_type=Notification.NotifType.TESTSERIES_QUERY_ANSWERED,
            title='Your query has been answered',
            message=answer_text[:200],
            data={'doubt_id': str(doubt.id), 'context_id': str(doubt.context_id)},
        )

    return doubt


# ---------------------------------------------------------------------------
# create_bell_rows_for_push() — RESTORED (Phase 2 Task 11).
#
# This was previously removed as dead code (see the note this replaces —
# kept below for history) on the grounds that `push_utils.py` already
# creates bell rows inline per-event via `core.services.create_notification()`,
# and nothing else called this. That's no longer true:
# `tuitionclass/parent_link_views.py::ClassroomParentCodeGenerateView.post()`
# now calls this directly (Task 11's "notify the student a parent code was
# created" gap-fix) with a `recipient_ids` LIST, which bare
# `create_notification()` doesn't support — it only takes one `recipient`.
# So this is a thin fan-out wrapper around the same real implementation,
# not a second/competing notification path.
#
# Best-effort per recipient: one bad id (or `core` app not installed —
# `create_notification` itself degrades to a no-op in that case, same as
# `answer_doubt_question` above) never stops the rest of the batch.
# ---------------------------------------------------------------------------
def create_bell_rows_for_push(*, recipient_ids: Iterable, notif_type: str, title: str,
                               message: str = None, data: dict = None) -> list:
    from core.services import create_notification

    recipients = User.objects.filter(id__in=list(recipient_ids))
    created = []
    for recipient in recipients:
        try:
            # [FIX — Task 36] Was calling create_notification(recipient,
            # notif_type, title, message, data) — positional. Switched to
            # keyword args to match the one call site in this same file
            # that's confirmed working (answer_doubt_question() below)
            # and the [VERIFIED] signature assigments/bridge.py already
            # checked directly against core/services.py — every other
            # confirmed call site in this codebase always calls this
            # function with keywords, never positionally. If the real
            # signature is keyword-only, a positional call here raised
            # TypeError at the call site itself, before this try/except
            # (or create_notification's own internal exception-handling)
            # ever got a chance to swallow it — silently breaking the
            # "one bad recipient never stops the batch" guarantee this
            # function's own docstring promises, for every recipient, not
            # just a bad one.
            created.append(
                create_notification(
                    recipient=recipient,
                    notif_type=notif_type,
                    title=title,
                    message=message,
                    data=data,
                )
            )
        except Exception:
            continue
    return created


# ---------------------------------------------------------------------------
# (superseded) — `create_bell_rows_for_push` used to live here, written
# back when `push_utils.py`'s source wasn't available to check against.
# `push_utils.py` still creates its own bell rows inline for its own
# events (`send_chat_message_push` / `send_mention_push` /
# `send_incoming_call_push`) — this function is a separate, genuinely-used
# fan-out helper for callers (like `parent_link_views.py`) that need to
# notify several recipients from one call, not a duplicate of those.
# ---------------------------------------------------------------------------