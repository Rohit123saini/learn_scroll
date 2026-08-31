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
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import connection
from django.db.models import F, Q
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