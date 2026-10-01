# message/user_notes.py
"""
M2-BE — Notes (Instagram-style status, 60 chars, 24h).

⚠️ `sticky_notes.py` se KOI lena-dena nahi — wo study-room ke collaborative
sticky notes hain. Ye alag feature hai (`UserNote` model), isliye naam bhi
alag (`user_notes`) rakha hai.

Ek jagah par saari rules (same pattern as `message_requests.py`), views.py
aur tasks.py sirf is module ko call karte hain.

RULES
    1. Ek user ka ek hi note (`UserNote.user` OneToOne). PUT naya note bana
       deta hai ya purane ko replace karta hai — dono me `expires_at = abhi + 24h`.
    2. `text` <= 60 chars (whitespace normalize hoti hai: newline/multiple
       spaces -> ek space). `emoji` optional. Dono me se kam se kam ek chahiye.
    3. `audience`:
         followers      — jo bhi mujhe (approved) follow karta hai
         close_friends  — sirf wo jinhe maine apni Close Friends list me daala
    4. GET (viewer ke liye) = active notes (`expires_at > now`) jinke author ko
       viewer follow karta hai (audience=followers) YA jo viewer ko apni close
       friends me rakhta hai (audience=close_friends). Apna note list me nahi
       aata (`my_note` alag key me). Dono taraf ka block hide hota hai.
    5. Expired notes READ time par filter hote hain (cleanup task late ho to
       bhi kuch leak nahi hota); `purge_expired_notes` sirf storage hygiene.

PRIVACY — FAIL-CLOSED: follow / close-friends lookup toot jaaye (model ya
field naam galat) to us audience ke notes dikhte hi NAHI (khaali list). Ye
`message_requests.is_following` ke fail-open ka ULTA hai, jaanbujh kar:
wahan bura case "spam inbox me aaya" tha, yahan "close-friends note leak
ho gaya" hoga. Error ek baar loud log hota hai.

SETTINGS (sab optional, defaults ke saath)
    MESSAGE_REQUESTS_FOLLOW_MODEL      = "user_profile.Follow"   (M1 jaisa hi)
    MESSAGE_REQUESTS_FOLLOWER_FIELD    = "follower"
    MESSAGE_REQUESTS_FOLLOWING_FIELD   = "following"
    NOTES_CLOSE_FRIEND_MODEL           = "post.CloseFriend"
    NOTES_CLOSE_FRIEND_OWNER_FIELD     = "user"     # list ka maalik (note ka author)
    NOTES_CLOSE_FRIEND_MEMBER_FIELD    = "friend"   # list me shamil banda (viewer)
"""
import logging

from django.apps import apps
from django.conf import settings
from django.db.models import BooleanField, Exists, OuterRef, Q, Value
from django.utils import timezone

from .models import BlockedUser, NoteAudience, UserNote
from .user_display import build_user_mini

logger = logging.getLogger(__name__)

NOTE_TTL = UserNote.NOTE_TTL
MAX_TEXT_LENGTH = UserNote.MAX_TEXT_LENGTH
MAX_EMOJI_LENGTH = UserNote.MAX_EMOJI_LENGTH

_logged_lookup_errors = set()


def _false():
    return Value(False, output_field=BooleanField())


def _fail_closed(label, builder):
    """`builder()` ek `Exists(...)` deta hai; kuch bhi toote to constant False (fail-closed)."""
    try:
        return builder()
    except Exception:
        if label not in _logged_lookup_errors:
            _logged_lookup_errors.add(label)
            logger.exception(
                "user_notes: %s lookup fail — is audience ke notes kisi ko nahi dikhenge "
                "(fail-closed). settings.* model/field naam check karo (user_notes.py docstring).",
                label,
            )
        return _false()


def _follow_exists(viewer_id):
    model = apps.get_model(getattr(settings, 'MESSAGE_REQUESTS_FOLLOW_MODEL', 'user_profile.Follow'))
    follower_field = getattr(settings, 'MESSAGE_REQUESTS_FOLLOWER_FIELD', 'follower')
    following_field = getattr(settings, 'MESSAGE_REQUESTS_FOLLOWING_FIELD', 'following')
    qs = model.objects.filter(**{
        f'{follower_field}_id': viewer_id,
        f'{following_field}_id': OuterRef('user_id'),
    })
    if any(f.name == 'status' for f in model._meta.get_fields()):
        qs = qs.filter(status__iexact='accepted')  # pending follow-request = follow nahi
    return Exists(qs)


def _close_friend_exists(viewer_id):
    model = apps.get_model(getattr(settings, 'NOTES_CLOSE_FRIEND_MODEL', 'post.CloseFriend'))
    owner_field = getattr(settings, 'NOTES_CLOSE_FRIEND_OWNER_FIELD', 'user')
    member_field = getattr(settings, 'NOTES_CLOSE_FRIEND_MEMBER_FIELD', 'friend')
    return Exists(model.objects.filter(**{
        f'{owner_field}_id': OuterRef('user_id'),   # note ka author list ka maalik
        f'{member_field}_id': viewer_id,            # viewer uski close-friends list me
    }))


def _blocked_exists(viewer_id):
    return Exists(BlockedUser.objects.filter(
        Q(blocker_id=viewer_id, blocked_id=OuterRef('user_id'))
        | Q(blocker_id=OuterRef('user_id'), blocked_id=viewer_id)
    ))


def visible_notes_qs(viewer):
    """Viewer ko dikhne wale active notes — ek single query, DB me hi filter."""
    viewer_id = viewer.id
    return (
        UserNote.objects.filter(expires_at__gt=timezone.now())
        .exclude(user_id=viewer_id)
        .annotate(
            _followed=_fail_closed('follow', lambda: _follow_exists(viewer_id)),
            _in_close_friends=_fail_closed('close-friends', lambda: _close_friend_exists(viewer_id)),
            _blocked=_blocked_exists(viewer_id),
        )
        .filter(
            Q(audience=NoteAudience.FOLLOWERS, _followed=True)
            | Q(audience=NoteAudience.CLOSE_FRIENDS, _in_close_friends=True)
        )
        .filter(_blocked=False)
        .select_related('user')
        .order_by('-updated_at')
    )


def get_my_active_note(user):
    return UserNote.objects.filter(user=user, expires_at__gt=timezone.now()).select_related('user').first()


def set_note(user, text, emoji, audience):
    """Note banao / replace karo. `ValueError(msg)` — view ise 400 me badalta hai."""
    text = ' '.join((text or '').split())  # strip + newline/multi-space -> single space
    emoji = (emoji or '').strip()
    audience = audience or NoteAudience.FOLLOWERS

    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f'Note {MAX_TEXT_LENGTH} characters se zyada nahi ho sakta.')
    if len(emoji) > MAX_EMOJI_LENGTH:
        raise ValueError('Emoji bahut lamba hai.')
    if not text and not emoji:
        raise ValueError('Note me text ya emoji chahiye.')
    if audience not in NoteAudience.values:
        raise ValueError("audience 'followers' ya 'close_friends' hona chahiye.")

    note, _created = UserNote.objects.update_or_create(
        user=user,
        defaults={
            'text': text,
            'emoji': emoji,
            'audience': audience,
            'expires_at': timezone.now() + NOTE_TTL,
        },
    )
    return note


def delete_note(user):
    """Idempotent — note na ho to bhi theek. Returns deleted rows count."""
    deleted, _ = UserNote.objects.filter(user=user).delete()
    return deleted


def serialize_note(note, request=None):
    return {
        'id': str(note.id),
        'user': build_user_mini(note.user, request=request),
        'text': note.text,
        'emoji': note.emoji,
        'audience': note.audience,
        # `updated_at` hi "kab lagaya" hai — sirf PUT note ko save karta hai.
        'posted_at': note.updated_at.isoformat(),
        'expires_at': note.expires_at.isoformat(),
    }


def purge_expired_notes(batch_size=1000, max_batches=20):
    """Expired rows hard-delete (bounded — backlog agle tick me). Returns {'deleted': n}."""
    total = 0
    for _ in range(max_batches):
        ids = list(
            UserNote.all_objects.filter(expires_at__lte=timezone.now())
            .values_list('pk', flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted, _ = UserNote.all_objects.filter(pk__in=ids).delete()
        total += deleted
        if len(ids) < batch_size:
            break
    return {'deleted': total}
