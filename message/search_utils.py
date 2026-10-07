# message/search_utils.py
"""
🔥 NAYA — `views.py` ka `ConversationViewSet.search` / `.search_all` pehle
se hi is module ko import kar rahe the (`from . import search_utils`) aur
`MIN_QUERY_LENGTH` / `apply_structured_filters()` / `search_messages()`
call kar rahe the — lekin ye file kabhi bani hi nahi thi, isliye dono
search endpoints guaranteed `NameError` deke crash karte the. Ye file ab
wahi 3 cheezein deti hai.

STRATEGY (Postgres):
  1. Stemmed/ranked match — `Message.search_vector` (tsvector column,
     trigger se auto-populate hota hai — migration
     `0900_message_search_vector.py` dekho) ke against `SearchQuery`.
     "running" query "run" wale message ko bhi match karega, stop-words
     ignore honge, aur `SearchRank` se relevance-order milta hai.
  2. Typo-tolerant match — `TrigramSimilarity` seedha `text` column pe.
     tsquery/stemming typos handle NAHI karta ("helo" != "hello" match
     nahi karega step 1 me), trigram similarity karta hai.
  Dono ko OR karte hain (ek match kare to result aana chahiye), aur order
  rank (FTS) phir similarity (trigram) se karte hain — jo strongest match
  hai wo upar.

Non-Postgres (sqlite, local dev/tests me common) pe `SearchVectorField`/
`pg_trgm` exist nahi karte, isliye `connection.vendor` check karke plain
unranked `icontains` pe fallback karte hain — behavior degrade hota hai
par crash nahi hota.
"""
import re
from urllib.parse import urlparse

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import connection
from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.db.models.functions import Concat
from django.utils.dateparse import parse_date

# WhatsApp/Insta jaisa — 1 character search bahut noisy/expensive hota hai
# (poori table trigram scan), isliye 2 char minimum.
MIN_QUERY_LENGTH = 2

# Postgres docs ka default similarity threshold 0.3 hai; chat messages
# chhote hote hain isliye thoda loose (0.25) rakha hai warna genuine
# typo-matches bhi drop ho jaate the.
TRIGRAM_SIMILARITY_THRESHOLD = 0.25

# `has_media=true/false` aur `media_type=<x>` filters ke liye — non-text
# message types. Poll/system/location/study_room "media" nahi maane jaate.
MEDIA_TYPES = {'image', 'video', 'audio', 'file', 'presentation'}


def _is_postgres() -> bool:
    return connection.vendor == 'postgresql'


def apply_structured_filters(qs, query_params):
    """
    `search` aur `search_all` dono is function ko call karte hain — sender
    / date_from / date_to / has_media / media_type filters yahi lagate
    hain (query-text se independent, isliye search_messages() se pehle
    lagana safe hai — chhota result-set banega jispe text-search chalega).

    Returns: (filtered_qs, error_message_or_None). Caller error_message
    None nahi hai to 400 return karta hai — is function ke andar khud
    Response nahi banate taaki dono callers (conversation-scoped +
    global) apna consistent error-format use kar sakein.
    """
    sender_id = (query_params.get('sender') or '').strip()
    if sender_id:
        qs = qs.filter(sender_id=sender_id)

    date_from = (query_params.get('date_from') or '').strip()
    if date_from:
        parsed = parse_date(date_from)
        if not parsed:
            return qs, "'date_from' YYYY-MM-DD format me hona chahiye."
        qs = qs.filter(created_at__date__gte=parsed)

    date_to = (query_params.get('date_to') or '').strip()
    if date_to:
        parsed = parse_date(date_to)
        if not parsed:
            return qs, "'date_to' YYYY-MM-DD format me hona chahiye."
        qs = qs.filter(created_at__date__lte=parsed)

    if date_from and date_to and parse_date(date_from) > parse_date(date_to):
        return qs, "'date_from' 'date_to' se pehle hona chahiye."

    has_media = query_params.get('has_media')
    if has_media is not None:
        val = has_media.strip().lower()
        if val in ('true', '1'):
            qs = qs.filter(type__in=MEDIA_TYPES)
        elif val in ('false', '0'):
            qs = qs.exclude(type__in=MEDIA_TYPES)
        else:
            return qs, "'has_media' true ya false hona chahiye."

    media_type = (query_params.get('media_type') or '').strip().lower()
    if media_type:
        if media_type not in MEDIA_TYPES:
            return qs, "'media_type' in me se ek hona chahiye: " + ', '.join(sorted(MEDIA_TYPES))
        qs = qs.filter(type=media_type)

    return qs, None


def search_messages(qs, query: str):
    """
    Already-filtered/scoped `qs` pe ranked + typo-tolerant text search
    lagata hai. Query ki empty-check aur MIN_QUERY_LENGTH check caller
    (views.py) already kar chuka hota hai is function tak pahunchne se
    pehle.
    """
    if not _is_postgres():
        # sqlite / local dev fallback — unranked, sirf substring match.
        return qs.filter(text__icontains=query).order_by('-created_at')

    search_query = SearchQuery(query, config='english')

    return qs.annotate(
        rank=SearchRank(F('search_vector'), search_query),
        similarity=TrigramSimilarity('text', query),
    ).filter(
        Q(search_vector=search_query) | Q(similarity__gt=TRIGRAM_SIMILARITY_THRESHOLD)
    ).order_by('-rank', '-similarity', '-created_at')


# ----------------------------------------------------------------------
# 🔥 NAYA (M8-BE) — "Media, links and docs" library (har chat ke liye)
#   GET /message/conversations/<id>/media/?type=media|links|docs
# ----------------------------------------------------------------------
LIBRARY_TABS = ('media', 'links', 'docs')
LIBRARY_VISUAL_TYPES = {'image', 'video'}                    # tab "media"
LIBRARY_DOC_TYPES = {'file', 'presentation', 'audio'}        # tab "docs"
LINKS_PER_MESSAGE_CAP = 10  # ek message me 100 link paste ho to bhi response bounded rahe

# http(s)://... ya www.... — whitespace / < > " ' pe ruk jaata hai.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
# Sentence punctuation jo URL ke aakhir me chipak jaata hai ("see https://x.com/a.")
_URL_TRAILING = '.,;:!?)]}\'"'


def extract_urls(text, limit=LINKS_PER_MESSAGE_CAP):
    """Message text se unique, normalized (https:// prefix wale) URLs, order preserved."""
    if not text:
        return []
    seen, out = set(), []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(_URL_TRAILING)
        if not url:
            continue
        if url.lower().startswith('www.'):
            url = 'https://' + url
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def apply_library_filter(qs, tab):
    """`qs` ko tab ke hisaab se narrow karta hai. `tab` pehle se LIBRARY_TABS me validated hona chahiye."""
    if tab == 'media':
        return qs.filter(type__in=LIBRARY_VISUAL_TYPES)
    if tab == 'docs':
        return qs.filter(type__in=LIBRARY_DOC_TYPES)
    # links — sirf text messages (link-preview task bhi sirf TEXT pe chalta hai).
    # DB-level iregex sirf candidates chhaantta hai; asli URL extraction Python me hoti hai.
    return qs.filter(type='text', text__iregex=r'(https?://|www\.)')


def _domain_of(url):
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ''
    host = host.split('@')[-1].split(':')[0]
    return host[4:] if host.startswith('www.') else host


def build_library_items(message, tab):
    """
    Ek Message ko library-tab ke items me badalta hai. Media/docs ka shape
    `GroupMediaSerializer` jaisa rakha hai (file_type / file_url / thumbnail_url /
    file_size) taaki Flutter ki existing tiles reuse ho sakein. Multi-image
    message (`file_urls`) ek-ek item me expand hota hai.
    """
    base = {
        'message_id': str(message.id),
        'sender_id': str(message.sender_id) if message.sender_id else None,
        'sender_username': getattr(message.sender, 'username', None),
        'created_at': message.created_at.isoformat(),
    }
    if tab == 'links':
        preview = getattr(message, 'link_preview', None)  # VERIFY: field ka naam models.py me check karo
        return [
            {**base, 'url': u, 'domain': _domain_of(u), 'text': message.text,
             'link_preview': preview if isinstance(preview, dict) else None}
            for u in extract_urls(message.text)
        ]
    urls = list(message.file_urls or []) or ([message.file_url] if message.file_url else [])
    return [
        {**base, 'file_type': message.type, 'file_url': u,
         'thumbnail_url': message.thumbnail_url if len(urls) == 1 else None,
         'file_name': getattr(message, 'file_name', None),
         'file_size': getattr(message, 'file_size', None)}
        for u in urls
    ]


# ----------------------------------------------------------------------
# 🔥 NAYA (6.1) — People + Groups search (message search screen ke
# "People" / "Groups" sections)
#   GET /message/search/directory/?q=...&type=all|people|groups
# ----------------------------------------------------------------------
DIRECTORY_DEFAULT_LIMIT = 20
DIRECTORY_MAX_LIMIT = 50
DIRECTORY_TYPES = ('all', 'people', 'groups')


def clamp_directory_limit(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DIRECTORY_DEFAULT_LIMIT
    return max(1, min(value, DIRECTORY_MAX_LIMIT))


def search_people(user, query, limit=DIRECTORY_DEFAULT_LIMIT):
    """
    Followers / following / mutual users jinka naam ya username `query`
    se match kare. Returns (users_list, has_more). Har user pe 3 annotated
    attribute hote hain: `i_follow`, `follows_me`, `is_mutual` (serializer
    inhi se `relation` banata hai) — koi per-row extra query nahi.

    Rules:
      - Sirf ACCEPTED follow count hota hai (PENDING request = follow nahi).
      - Private account tabhi aata hai jab main usko (accepted) follow
        karta hoon — `MessageContactSearchView` wala hi rule, taaki wo log
        jinki profile mujhe dikhti hi nahi, yahan se leak na hon.
      - Block dono directions me hide hota hai — dono tables check hote
        hain (`user_profile.BlockUser` aur `message.BlockedUser`).
      - Khud ko aur inactive accounts ko exclude karte hain.
    Ranking: mutual -> following -> follower, phir prefix match, phir username.
    """
    # Local import — message app module-load time pe user_profile pe depend
    # na kare (circular-import safe).
    from django.contrib.auth import get_user_model
    from user_profile.models import BlockUser, Follow
    from .models import BlockedUser

    User = get_user_model()
    q = query.lstrip('@').strip()
    if len(q) < MIN_QUERY_LENGTH:
        return [], False

    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
    i_follow = Exists(accepted.filter(follower_id=user.id, following_id=OuterRef('pk')))
    follows_me = Exists(accepted.filter(follower_id=OuterRef('pk'), following_id=user.id))
    blocked_user_table = Exists(BlockUser.objects.filter(
        Q(blocker_id=user.id, blocked_id=OuterRef('pk'))
        | Q(blocker_id=OuterRef('pk'), blocked_id=user.id)
    ))
    blocked_chat_table = Exists(BlockedUser.objects.filter(
        Q(blocker_id=user.id, blocked_id=OuterRef('pk'))
        | Q(blocker_id=OuterRef('pk'), blocked_id=user.id)
    ))

    qs = (
        User.objects.filter(is_active=True)
        .exclude(pk=user.pk)
        .annotate(
            i_follow=i_follow,
            follows_me=follows_me,
            _blocked_a=blocked_user_table,
            _blocked_b=blocked_chat_table,
            _full_name=Concat('first_name', Value(' '), 'last_name'),
        )
        .filter(Q(i_follow=True) | Q(follows_me=True))
        .filter(Q(is_private=False) | Q(i_follow=True))
        .filter(_blocked_a=False, _blocked_b=False)
        .filter(Q(username__icontains=q) | Q(_full_name__icontains=q))
        .annotate(
            _relation_rank=Case(
                When(i_follow=True, follows_me=True, then=Value(0)),
                When(i_follow=True, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            _prefix_rank=Case(
                When(
                    Q(username__istartswith=q) | Q(first_name__istartswith=q) | Q(last_name__istartswith=q),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by('_relation_rank', '_prefix_rank', 'username', 'pk')
    )

    rows = list(qs[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    for u in rows:
        u.is_mutual = bool(u.i_follow and u.follows_me)
    return rows, has_more


def search_groups(user, query, limit=DIRECTORY_DEFAULT_LIMIT):
    """
    Jin groups ka `user` abhi ACTIVE member hai (banned nahi, chat leave
    nahi kiya, message-request pending/declined nahi) unme se naam /
    topic_tag match karne wale. Returns (groups_list, has_more).
    Discoverable-but-not-joined public groups yahan nahi aate (wo
    `GroupViewSet.discover` ka kaam hai).
    """
    from .models import Group, GroupMember, ConversationParticipant, RequestStatus

    q = query.strip()
    if len(q) < MIN_QUERY_LENGTH:
        return [], False

    active_group_member = Exists(GroupMember.objects.filter(
        group_id=OuterRef('pk'), user_id=user.id, is_banned=False,
    ))
    active_participant = Exists(ConversationParticipant.objects.filter(
        conversation_id=OuterRef('conversation_id'), user_id=user.id,
        left_at__isnull=True, request_status=RequestStatus.ACCEPTED,
    ))

    qs = (
        Group.objects.annotate(_member=active_group_member, _participant=active_participant)
        .filter(_member=True, _participant=True)
        .filter(Q(name__icontains=q) | Q(topic_tag__icontains=q))
        .select_related('conversation')
        .annotate(_prefix_rank=Case(
            When(name__istartswith=q, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ))
        .order_by(
            '_prefix_rank',
            F('conversation__last_message_at').desc(nulls_last=True),
            'name',
            'pk',
        )
    )

    rows = list(qs[:limit + 1])
    return rows[:limit], len(rows) > limit
