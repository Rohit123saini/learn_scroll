"""
post/post_options.py  (round 5)

Small rules shared by the create / edit serializers and the views of the "post options" task:

  zone_from_label(label)                 "IST (UTC+05:30)" -> tzinfo, used for an old client's naive schedule time
  scheduled_zone_label(raw)              clean the zone label the app sends (display only)
  can_comment(post, user)                (ok, code, message) - comments off / followers only / co-author rules
  snapshot_before_edit(post, editor, changed)   one PostEditVersion row holding the text BEFORE an edit
  edit_history_payload(post)             newest first, for GET /post/<id>/history/

Nothing here changes the database schema by itself; the models are in models.py (PostEditVersion, PostCollaborator).
"""
import re
from datetime import timedelta, timezone as dt_timezone

from django.db.models import Max

MAX_ZONE_LABEL = 64
_ZONE_OFFSET_RE = re.compile(r'UTC\s*([+-])(\d{1,2}):?(\d{2})')

EDITABLE_TEXT_FIELDS = ('title', 'content', 'hashtags')   # these three stamp the "Edited" mark
HISTORY_FIELDS = EDITABLE_TEXT_FIELDS + ('visibility',)   # these are written to the version history


def scheduled_zone_label(raw):
    """The zone label the app sends next to `scheduled_at` ("IST (UTC+05:30)"). Display only: trimmed, no control chars."""
    if not isinstance(raw, str):
        return ''
    return ''.join(ch for ch in raw if ch.isprintable()).strip()[:MAX_ZONE_LABEL]


def zone_from_label(label):
    """tzinfo for a zone label that contains "UTC+05:30" (what the app sends), else None."""
    m = _ZONE_OFFSET_RE.search(label or '')
    if not m:
        return None
    sign = 1 if m.group(1) == '+' else -1
    minutes = sign * (int(m.group(2)) * 60 + int(m.group(3)))
    if abs(minutes) > 14 * 60:
        return None
    return dt_timezone(timedelta(minutes=minutes))


def can_comment(post, user):
    """(ok, code, message). Comments off wins; 'followers' lets the author, co-authors and accepted followers in."""
    if post.is_comments_disabled:
        return False, 'comments_disabled', 'Comments are turned off for this post.'
    if getattr(post, 'comments_limit', 'everyone') != 'followers':
        return True, '', ''
    if user is None or not getattr(user, 'is_authenticated', False):
        return False, 'comments_followers_only', 'Only followers can comment on this post.'
    if user.id == post.user_id:
        return True, '', ''
    from .models import PostCollaborator
    if PostCollaborator.objects.filter(post=post, user=user, status=PostCollaborator.STATUS_ACCEPTED).exists():
        return True, '', ''
    from .audience import _follows
    if _follows(user, post.user_id):
        return True, '', ''
    return False, 'comments_followers_only', 'Only followers can comment on this post.'


RESHARE_OFF_CODE = 'post_reshare_off'
RESHARE_OFF_TEXT = 'The author turned off resharing for this post.'
SAVE_OFF_CODE = 'post_save_off'
SAVE_OFF_TEXT = 'The author turned off saving for this post.'


def can_reshare_post(post, user):
    """S4b - may `user` repost this post / send it in chat / share its link / put it on their story? The author always
    may; anonymous never; everybody else only while the author has not turned resharing off."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return user.id == post.user_id or bool(getattr(post, 'allow_reshare', True))


def can_save_post(post, user):
    """S4b - may `user` ADD this post to their saved posts? (Un-saving is never refused.)"""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return user.id == post.user_id or bool(getattr(post, 'allow_save', True))


def snapshot_before_edit(post, editor, changed):
    """Store what `post` looks like right now (call BEFORE the new values are set). `changed` = names of the fields
    this edit changes; an empty / non-history change stores nothing. Returns the PostEditVersion or None."""
    from .models import PostEditVersion
    changed = [f for f in changed if f in HISTORY_FIELDS]
    if not changed:
        return None
    last = PostEditVersion.objects.filter(post=post).aggregate(m=Max('version'))['m'] or 0
    return PostEditVersion.objects.create(
        post=post, version=last + 1, title=post.title or '', content=post.content or '',
        hashtags=list(post.hashtags or []), visibility=post.visibility, changed_fields=changed, edited_by=editor,
    )


def edit_history_payload(post):
    """Newest first. The first entry is the live post (so the screen can show "Current"), then every older text."""
    from .models import PostEditVersion
    rows = list(PostEditVersion.objects.filter(post=post).select_related('edited_by').order_by('-version')[:50])
    out = [{
        'version': (rows[0].version + 1) if rows else 1,
        'is_current': True,
        'title': post.title or '', 'content': post.content or '', 'hashtags': list(post.hashtags or []),
        'visibility': post.visibility,
        'changed_fields': list(rows[0].changed_fields) if rows else [],
        'at': (post.edited_at or post.updated_at or post.created_at).isoformat(),
    }]
    for r in rows:
        out.append({
            'version': r.version, 'is_current': False,
            'title': r.title, 'content': r.content, 'hashtags': list(r.hashtags or []), 'visibility': r.visibility,
            'changed_fields': list(r.changed_fields or []),
            'at': r.saved_at.isoformat(),
        })
    return out
