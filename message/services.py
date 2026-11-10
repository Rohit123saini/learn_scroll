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

import logging
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
    DoubtAnswer,
    DoubtQuestion,
    Group,
    GroupMember,
)

User = get_user_model()
logger = logging.getLogger(__name__)


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
        # Block policy: you can't pull someone into a group if either of you
        # blocked the other. Skipped silently (no hint of who blocked whom).
        from user_profile.services import blocked_user_ids

        _blocked = blocked_user_ids(created_by)
        if _blocked:
            ids = {i for i in ids if i not in _blocked and str(i) not in {str(b) for b in _blocked}}
        # S4: people who limited "who can add me to groups" are left out too (skipped silently, like a block).
        from user_profile import privacy as privacy_rules

        ids = privacy_rules.allowed_owner_ids(created_by, ids, 'group_add_from')
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
def add_members_to_group(*, group: Group, actor, user_ids: Iterable, pending_out=None, declined_out=None) -> list:
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

    S8: approval wale public group (`Group.join_approval_required`) ya minor target (`MINOR_PUBLIC_GROUP_JOIN_APPROVAL`)
    ko non-admin actor seedha add nahi kar sakta (`study_room_safety.can_add_directly`) — unke liye `GroupJoinRequest`
    (PENDING) banti hai, admin/mod approve karta hai. Aise user ids `pending_out` (list) me append hote hain (agar
    caller ne di ho); wo return list me NAHI aate. Admin/mod aur system caller (`actor=None`) ko ye rule nahi lagta.
    S4: jisne "mujhe group me kaun add kare" limit kiya hai (`PrivacySettings.group_add_from`) use `actor` add nahi kar
    sakta — aise user ids `declined_out` (list, agar caller ne di ho) me append hote hain, return list me NAHI aate.
    System caller (`actor=None`) ko ye rule nahi lagta.
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
    # Block policy (see create_group): the adder can't add someone they
    # blocked / who blocked them. System callers (actor=None, e.g. the
    # classroom bridge) are never filtered — a classroom roster is not a
    # social choice.
    if actor is not None:
        from user_profile.services import blocked_user_ids

        _blocked = {str(b) for b in blocked_user_ids(actor)}
        if _blocked:
            new_ids = [uid for uid in new_ids if str(uid) not in _blocked]
        # S4 -- "who can add me to groups".
        if new_ids:
            from user_profile import privacy as privacy_rules

            _ok = privacy_rules.allowed_owner_ids(actor, new_ids, 'group_add_from')
            _refused = [uid for uid in new_ids if privacy_rules.as_user_id(uid) not in _ok]
            if _refused:
                if declined_out is not None:
                    declined_out.extend(_refused)
                new_ids = [uid for uid in new_ids if privacy_rules.as_user_id(uid) in _ok]
    users = list(User.objects.filter(id__in=new_ids))

    # S8 — direct add allowed nahi => approval request.
    if actor is not None and users:
        from . import study_room_safety

        direct = []
        for user in users:
            if study_room_safety.can_add_directly(group, actor, user):
                direct.append(user)
                continue
            study_room_safety.create_or_reset_join_request(group, user)
            if pending_out is not None:
                pending_out.append(user.id)
        users = direct

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
# DOUBT QUEUE — answers (T1.2 notification, T1.3 multi-answer / best /
# teacher-verified).
#
# Same convention as the rest of this module: plain `ValueError` /
# `PermissionError` (never DRF exceptions); `DoubtQuestionViewSet` converts
# them at the HTTP boundary.
# ---------------------------------------------------------------------------

#: Ek doubt par total answers ka cap (spam / runaway thread guard).
MAX_DOUBT_ANSWERS_PER_DOUBT = 50
#: Ek (non-teacher) member ek doubt par kitne answers de sakta hai.
MAX_DOUBT_ANSWERS_PER_USER = 3


def _is_doubt_teacher(doubt, user) -> bool:
    """admin/moderator of the doubt's group. Context-pointer (testseries)
    doubts have no group -> nobody is a "group teacher" there."""
    if doubt.group_id is None:
        return False
    return is_group_admin_or_mod(doubt.group, user.id)


def refresh_doubt_answer_summary(doubt) -> None:
    """Recompute `DoubtQuestion`'s denormalized answer summary from its
    `DoubtAnswer` rows. Call inside the same transaction as any answer
    add/delete/best/verify change.

    Legacy mirror fields (`is_answered`/`answer_text`/`answered_by`/
    `answered_at`) point at the *primary* answer = first row in
    `DoubtAnswer.Meta.ordering` (best > teacher-verified > teacher answer >
    oldest), so old clients and `testseries/bridge.py` keep working."""
    answers = list(DoubtAnswer.objects.filter(doubt=doubt))
    primary = answers[0] if answers else None

    doubt.answers_count = len(answers)
    doubt.has_best_answer = any(a.is_best for a in answers)
    doubt.has_verified_answer = any(a.is_teacher_verified for a in answers)
    doubt.is_answered = primary is not None
    doubt.answer_text = primary.text if primary else ''
    doubt.answered_by_id = primary.author_id if primary else None
    doubt.answered_at = primary.created_at if primary else None
    doubt.save(update_fields=[
        'answers_count', 'has_best_answer', 'has_verified_answer',
        'is_answered', 'answer_text', 'answered_by', 'answered_at', 'updated_at',
    ])


def _send_doubt_answered_push(notification) -> None:
    try:
        from .push_utils import send_doubt_answered_push
        send_doubt_answered_push(notification)
    except Exception:  # push is best-effort, never breaks the answer itself
        logger.exception("doubt_answered push failed (notification=%s)", getattr(notification, 'id', None))


def notify_doubt_answered(answer):
    """T1.2 — bell row + push to the doubt's asker when `answer` is posted.

    * No self-notify (asker is never the answer author for group doubts, but
      the legacy `answer_doubt_question` path can't rule it out).
    * The notification never carries anything that identifies an anonymous
      asker to anyone else — it only goes to the asker themself.
    * Bell row is written inline (same transaction as the answer, so a
      rolled-back answer leaves no orphan notification); the push goes out
      `on_commit` so FCM is never hit for a rolled-back transaction.
    * Respects restrict / block / mute (`create_notification(actor=...)`) and
      NotificationPreference (`send_push_for_notification`) — a skipped row is
      simply `None` and nothing is pushed.

    Returns the `Notification` row or `None`."""
    from core.models import Notification
    from core.services import create_notification

    doubt = answer.doubt
    recipient_id = doubt.author_id
    if recipient_id is None or answer.author_id == recipient_id:
        return None

    group_name = ''
    if doubt.group_id is not None:
        group_name = (getattr(doubt.group, 'name', '') or '').strip()
    base = 'Teacher ne aapke doubt ka answer diya' if answer.is_teacher_answer else 'Aapke doubt par naya answer aaya'
    title = f"{base} · {group_name}" if group_name else base
    snippet = (answer.text or '').strip()
    if len(snippet) > 140:
        snippet = snippet[:137].rstrip() + '...'

    data = {
        'doubt_id': str(doubt.id),
        'answer_id': str(answer.id),
        'by_teacher': bool(answer.is_teacher_answer),
    }
    if doubt.group_id is not None:
        data['group_id'] = str(doubt.group_id)
    if doubt.conversation_id is not None:
        data['conversation_id'] = str(doubt.conversation_id)

    row = create_notification(
        recipient=recipient_id,
        notif_type=Notification.NotifType.DOUBT_ANSWERED,
        title=title[:150],
        message=snippet,
        data=data,
        actor=answer.author,
    )
    if row is not None:
        transaction.on_commit(lambda: _send_doubt_answered_push(row))
    return row


def add_doubt_answer(*, doubt, author, text: str):
    """T1.3 — any (non-banned) group member posts an answer to a classroom
    doubt. Group-membership itself is enforced by the caller
    (`DoubtQuestionViewSet.get_group`).

    Rules:
      * not on your own doubt (an answer would unmask an anonymous asker);
      * admin/moderator answers are flagged `is_teacher_answer` and
        auto-verified;
      * caps: `MAX_DOUBT_ANSWERS_PER_DOUBT` per doubt,
        `MAX_DOUBT_ANSWERS_PER_USER` per non-teacher member per doubt.

    Raises `ValueError` (validation) — returns the new `DoubtAnswer`."""
    text = (text or '').strip()
    if not text:
        raise ValueError('Answer khali nahi ho sakta.')
    if len(text) > 4000:
        raise ValueError('Answer 4000 characters se zyada nahi ho sakta.')
    if doubt.group_id is None:
        raise ValueError('Is doubt ka answer sirf testseries teacher de sakta hai.')
    if doubt.author_id == author.id:
        raise ValueError('Apne doubt ka answer khud nahi de sakte.')

    is_teacher = _is_doubt_teacher(doubt, author)
    now = timezone.now()

    with transaction.atomic():
        # Row lock serialises concurrent answers on the same doubt so the
        # caps below can't be raced past (no-op on SQLite).
        locked = DoubtQuestion.objects.select_for_update().get(pk=doubt.pk)
        existing = DoubtAnswer.objects.filter(doubt=locked)
        if existing.count() >= MAX_DOUBT_ANSWERS_PER_DOUBT:
            raise ValueError('Is doubt par answers ki limit pahunch chuki hai.')
        if not is_teacher and existing.filter(author=author).count() >= MAX_DOUBT_ANSWERS_PER_USER:
            raise ValueError(f'Aap ek doubt par maximum {MAX_DOUBT_ANSWERS_PER_USER} answers de sakte ho.')

        answer = DoubtAnswer.objects.create(
            doubt=locked,
            author=author,
            text=text,
            is_teacher_answer=is_teacher,
            is_teacher_verified=is_teacher,
            verified_by=author if is_teacher else None,
            verified_at=now if is_teacher else None,
        )
        refresh_doubt_answer_summary(locked)

    doubt.refresh_from_db()
    answer.doubt = doubt
    notify_doubt_answered(answer)
    return answer


def set_best_answer(*, answer, actor, best: bool = True):
    """T1.3 — mark (or un-mark) `answer` as the doubt's Best Answer. Allowed:
    the doubt's asker, or a group admin/moderator. Marking replaces any
    previous best (one best per doubt — also a DB partial-unique constraint).

    Raises `PermissionError` / `ValueError`; returns the refreshed answer."""
    doubt = answer.doubt
    if not (doubt.author_id == actor.id or _is_doubt_teacher(doubt, actor)):
        raise PermissionError('Sirf doubt poochne wala ya teacher best answer chun sakta hai.')

    with transaction.atomic():
        locked = DoubtQuestion.objects.select_for_update().get(pk=doubt.pk)
        if best:
            DoubtAnswer.objects.filter(doubt=locked, is_best=True).exclude(pk=answer.pk).update(
                is_best=False, best_marked_by=None,
            )
            DoubtAnswer.objects.filter(pk=answer.pk).update(is_best=True, best_marked_by=actor)
        else:
            DoubtAnswer.objects.filter(pk=answer.pk, is_best=True).update(
                is_best=False, best_marked_by=None,
            )
        refresh_doubt_answer_summary(locked)

    answer.refresh_from_db()
    doubt.refresh_from_db()
    answer.doubt = doubt
    return answer


def set_answer_verified(*, answer, actor, verified: bool = True):
    """T1.3 — teacher (group admin/moderator) marks an answer "Verified by
    teacher" (or removes it). A teacher's *own* answer is always verified, so
    un-verifying it is rejected.

    Raises `PermissionError` / `ValueError`; returns the refreshed answer."""
    doubt = answer.doubt
    if not _is_doubt_teacher(doubt, actor):
        raise PermissionError('Sirf group admin/moderator (teacher) answer verify kar sakte hain.')
    if not verified and answer.is_teacher_answer:
        raise ValueError('Teacher ka apna answer hamesha verified rehta hai.')

    with transaction.atomic():
        locked = DoubtQuestion.objects.select_for_update().get(pk=doubt.pk)
        if verified:
            DoubtAnswer.objects.filter(pk=answer.pk).update(
                is_teacher_verified=True, verified_by=actor, verified_at=timezone.now(),
            )
        else:
            DoubtAnswer.objects.filter(pk=answer.pk).update(
                is_teacher_verified=False, verified_by=None, verified_at=None,
            )
        refresh_doubt_answer_summary(locked)

    answer.refresh_from_db()
    doubt.refresh_from_db()
    answer.doubt = doubt
    return answer


def delete_doubt_answer(*, answer, actor):
    """T1.3 — answer ka author apna answer, ya teacher koi bhi answer hata
    sakta hai. Returns the (refreshed) parent doubt."""
    doubt = answer.doubt
    if not (answer.author_id == actor.id or _is_doubt_teacher(doubt, actor)):
        raise PermissionError('Aap ye answer delete nahi kar sakte.')

    with transaction.atomic():
        locked = DoubtQuestion.objects.select_for_update().get(pk=doubt.pk)
        answer.delete()
        refresh_doubt_answer_summary(locked)

    doubt.refresh_from_db()
    return doubt


# ---------------------------------------------------------------------------
# answer_doubt_question() — Task 16. Single-shot "teacher answers" path kept
# for `testseries/bridge.py::answer_query_on_series()` (context-pointer
# doubts, `doubt.group` is None) — one authoritative answer, no thread.
# Classroom doubts now go through `add_doubt_answer()` above (multi-answer);
# this function still works for them too (e.g. old callers) and records the
# answer as a teacher-verified, best `DoubtAnswer` so both models agree.
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

    now = timezone.now()
    with transaction.atomic():
        answer = DoubtAnswer.objects.create(
            doubt=doubt,
            author=resolved_answered_by,
            text=answer_text,
            is_teacher_answer=True,
            is_best=True,
            best_marked_by=resolved_answered_by,
            is_teacher_verified=True,
            verified_by=resolved_answered_by,
            verified_at=now,
        )
        refresh_doubt_answer_summary(doubt)

    if doubt.context_type == 'testseries_attempt':
        # testseries path: its own notif type (unchanged behaviour).
        from core.models import Notification
        from core.services import create_notification

        create_notification(
            recipient=doubt.author,
            notif_type=Notification.NotifType.TESTSERIES_QUERY_ANSWERED,
            title='Your query has been answered',
            message=answer_text[:200],
            data={'doubt_id': str(doubt.id), 'context_id': str(doubt.context_id)},
        )
    else:
        # T1.2 — classroom doubt: DOUBT_ANSWERED bell row + push.
        notify_doubt_answered(answer)

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
            # and the [VERIFIED] signature assignments/bridge.py already
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