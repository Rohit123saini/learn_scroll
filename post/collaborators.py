"""
post/collaborators.py  (round 5) - co-author / collaborator invites for a post.

  invite(post, inviter, user_ids)   -> {"invited": [...], "skipped": [{"user_id", "code"}]}   author only, max MAX_COLLABORATORS
  respond(post, user, accept)       -> PostCollaborator   the invitee accepts / declines
  remove(post, actor, user_id)      author removes anyone, an invitee removes himself ("leave")
  payload(post)                     accepted co-authors for the `collaborators` output field

An accepted co-author is shown on the post ("with @x"), may comment when comments are "followers only", and can see
the post even when it is not public. Co-authors cannot edit or delete the post (that stays with the author).
Invites go out as a POST_TAG-type notification (no new notification type, so no schema change).
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_COLLABORATORS = 5


class CollabError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _clean_ids(raw):
    ids = []
    for item in raw or []:
        if isinstance(item, bool):
            continue
        try:
            uid = int(item)
        except (TypeError, ValueError):
            continue
        if uid not in ids:
            ids.append(uid)
    return ids


def parse_ids(raw):
    """`collaborator_ids` from a request (list, JSON string or comma string) -> [int]."""
    import json
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith('['):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        else:
            raw = [p for p in raw.split(',') if p.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    return _clean_ids(raw)


def _notify(post, invitee, inviter):
    from .services import _Notification, _create_notification_row, _post_preview_media
    if _Notification is None:
        return
    try:
        media_url, media_type = _post_preview_media(post)
        name = inviter.get_full_name() or inviter.username
        _create_notification_row(
            invitee, _Notification.NotifType.POST_TAG, f'{name} invited you to co-author a post',
            (post.content or '')[:200],
            data={'post_id': str(post.id), 'media_url': media_url, 'media_type': media_type, 'collab_invite': True},
            actor=inviter,
        )
    except Exception:  # a notification must never break the post flow
        logger.exception('collab notify failed (post=%s user=%s)', post.pk, invitee.pk)


@transaction.atomic
def invite(post, inviter, user_ids):
    from .models import PostCollaborator
    if post.user_id != inviter.id:
        raise CollabError('not_owner', 'Only the author can invite co-authors.', 403)
    if post.post_type == 'repost':
        raise CollabError('repost', "Reposts can't have co-authors.")
    if post.visibility == 'private':
        raise CollabError('private_post', "A post that only you can see can't have co-authors.")
    User = get_user_model()
    ids = _clean_ids(user_ids)
    existing = {c.user_id: c for c in PostCollaborator.objects.filter(post=post)}
    active = sum(1 for c in existing.values() if c.status != PostCollaborator.STATUS_DECLINED)
    invited, skipped = [], []
    blocked = set()
    try:
        from user_profile.services import blocked_user_ids
        blocked = {int(i) for i in blocked_user_ids(inviter)}
    except Exception:
        pass
    for uid in ids:
        if uid == inviter.id:
            skipped.append({'user_id': uid, 'code': 'self'})
            continue
        if uid in blocked:
            skipped.append({'user_id': uid, 'code': 'unavailable'})  # same answer as "no such user": a block is not revealed
            continue
        user = User.objects.filter(pk=uid, is_active=True).first()
        if user is None:
            skipped.append({'user_id': uid, 'code': 'unavailable'})
            continue
        row = existing.get(uid)
        if row is not None and row.status != PostCollaborator.STATUS_DECLINED:
            skipped.append({'user_id': uid, 'code': 'already_invited'})
            continue
        if active >= MAX_COLLABORATORS:
            skipped.append({'user_id': uid, 'code': 'limit'})
            continue
        if row is None:
            row = PostCollaborator.objects.create(post=post, user=user, invited_by=inviter)
        else:  # invited again after a decline
            row.status, row.responded_at, row.invited_by = PostCollaborator.STATUS_PENDING, None, inviter
            row.save(update_fields=['status', 'responded_at', 'invited_by'])
        active += 1
        invited.append(uid)
        if not post.is_scheduled:
            transaction.on_commit(lambda u=user: _notify(post, u, inviter))
    return {'invited': invited, 'skipped': skipped}


@transaction.atomic
def respond(post, user, accept):
    from .models import PostCollaborator
    row = PostCollaborator.objects.select_for_update().filter(post=post, user=user).first()
    if row is None:
        raise CollabError('not_invited', 'You were not invited to this post.', 404)
    if row.status == PostCollaborator.STATUS_ACCEPTED and accept:
        return row
    row.status = PostCollaborator.STATUS_ACCEPTED if accept else PostCollaborator.STATUS_DECLINED
    row.responded_at = timezone.now()
    row.save(update_fields=['status', 'responded_at'])
    return row


@transaction.atomic
def remove(post, actor, user_id):
    from .models import PostCollaborator
    if actor.id != post.user_id and actor.id != user_id:
        raise CollabError('not_allowed', 'Not allowed.', 403)
    PostCollaborator.objects.filter(post=post, user_id=user_id).delete()


def notify_pending_invites(post):
    """Invites are not sent while a post is scheduled; the publish sweep (tasks.publish_scheduled_posts) calls this
    once the post is live. Sends to every still-pending invitee."""
    from .models import PostCollaborator
    for row in PostCollaborator.objects.filter(post=post, status=PostCollaborator.STATUS_PENDING).select_related('user', 'invited_by'):
        _notify(post, row.user, row.invited_by)


def accepted_rows(post):
    from .models import PostCollaborator
    cached = getattr(post, '_prefetched_objects_cache', {}).get('collaborators')
    if cached is not None:
        return [c for c in cached if c.status == PostCollaborator.STATUS_ACCEPTED]
    return list(PostCollaborator.objects.filter(post=post, status=PostCollaborator.STATUS_ACCEPTED).select_related('user'))


def _user_payload(user, request=None):
    from .serializers import get_profile_pic_url
    try:
        pic = get_profile_pic_url(user, request)
    except Exception:
        pic = None
    return {'id': user.id, 'username': user.username, 'name': user.get_full_name() or user.username, 'profile_picture': pic}


def payload(post, request=None):
    """Accepted co-authors, oldest first - what every viewer sees."""
    return [_user_payload(c.user, request) for c in accepted_rows(post)]


def owner_payload(post, request=None):
    """The author's view: every row incl. pending / declined."""
    from .models import PostCollaborator
    rows = PostCollaborator.objects.filter(post=post).select_related('user').order_by('created_at')
    return [dict(_user_payload(c.user, request), status=c.status) for c in rows]


def is_accepted_collaborator(post, user):
    from .models import PostCollaborator
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return PostCollaborator.objects.filter(post=post, user=user, status=PostCollaborator.STATUS_ACCEPTED).exists()
