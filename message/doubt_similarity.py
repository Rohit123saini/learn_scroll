# message/doubt_similarity.py
"""
T1.5 (D5) — "Similar doubt" suggestion.

Jab student naya doubt type kar raha ho, client (debounced) ye dekhne ke liye
`GET /message/groups/<group_id>/doubts/similar/?q=<text>` call karta hai:
"kya ye doubt is classroom me pehle se pucha ja chuka hai?" Answered doubts
upar dikhte hain taaki student ko jawab turant mil jaaye aur duplicate doubt
na bane (aur upvote-count ek hi doubt pe jama ho).

STRATEGY
  Postgres:
    1. Prefilter (OR) — pg_trgm ke index-backed operators:
         text %  q   (`trigram_similar`)       -> poore-text similarity
         text %> q   (`trigram_word_similar`)  -> query, doubt-text ke kisi
                                                   continuous hisse se match
         ILIKE per keyword (AND)               -> saare keywords maujood
       Teeno `DoubtQuestion.text` pe `doubt_text_trgm_gin` (gin_trgm_ops) use
       kar sakte hain (migration 0003).
    2. Score = max(similarity, word_similarity, keyword-match 0.6); answered
       doubts ko chhota sa boost (ANSWERED_BOOST) milta hai. Phir upvotes,
       phir naya-pehle.
  Non-Postgres (sqlite local dev/tests) YA Postgres jahan pg_trgm kuch na
  de (e.g. Devanagari text + `C` locale database — pg_trgm non-ASCII letters
  ko tab ignore karta hai):
    Python token-overlap fallback — group ke latest `FALLBACK_CANDIDATES`
    doubts pe query-token coverage. Behavior degrade hota hai, crash nahi.

SCOPE: sirf us `group` ke doubts (membership check view me hota hai).
Anonymous doubts bhi aate hain, par author serializer (`get_author`) hi hide
karta hai — yahan author kabhi expose nahi hota.
"""
import re
from functools import reduce
from operator import and_

from django.db import connection
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.functions import Greatest

from .models import DoubtQuestion

MIN_QUERY_CHARS = 8      # isse chhota query pe suggestion noisy hota hai
MAX_QUERY_CHARS = 300    # long paste pe trigram query mehngi na ho
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
ANSWERED_BOOST = 0.10    # same-score pe answered doubt upar
KEYWORD_MATCH_SCORE = 0.60  # query ke SAARE keywords doubt me hain (typo-free)
MAX_KEYWORDS = 6
FALLBACK_CANDIDATES = 300
FALLBACK_MIN_COVERAGE = 0.5

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "is", "are", "a", "an", "of", "to", "in", "on", "and", "or", "for",
    "what", "how", "why", "this", "that", "with", "me", "my", "i", "you",
    "hai", "hain", "kya", "ka", "ki", "ke", "ko", "se", "me", "mein", "kaise",
    "kyu", "kyun", "ye", "yeh", "wo", "woh", "aur", "nahi", "nhi",
})


def normalize_query(raw) -> str:
    """Whitespace collapse + trim + MAX_QUERY_CHARS pe truncate."""
    text = _WS_RE.sub(" ", (raw or "")).strip()
    return text[:MAX_QUERY_CHARS]


def clamp_limit(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _tokens(text: str):
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1 and t not in _STOPWORDS]


def _is_postgres() -> bool:
    return connection.vendor == "postgresql"


def _base_queryset(group):
    # `objects` = SoftDeleteManager (is_deleted=False already filtered).
    return (
        DoubtQuestion.objects.filter(group=group)
        .select_related("author", "answered_by")
        .prefetch_related("upvotes")
    )


def _postgres_search(group, query, limit):
    from django.contrib.postgres.search import TrigramSimilarity, TrigramWordSimilarity

    # Keyword containment: "photosynthesis temperature" lambe doubt text ke
    # andar alag-alag jagah ho to `%>` (continuous extent) ka strict 0.6
    # threshold ise miss kar deta hai. Saare keywords ILIKE se check karte
    # hain — gin_trgm_ops LIKE/ILIKE ko bhi index se chalata hai.
    keywords = _tokens(query)[:MAX_KEYWORDS]
    keyword_q = reduce(and_, [Q(text__icontains=t) for t in keywords]) if keywords else None

    prefilter = Q(text__trigram_similar=query) | Q(text__trigram_word_similar=query)
    keyword_score = Value(0.0)
    if keyword_q is not None:
        prefilter |= keyword_q
        keyword_score = Case(
            When(keyword_q, then=Value(KEYWORD_MATCH_SCORE)),
            default=Value(0.0),
            output_field=FloatField(),
        )

    score = Greatest(
        TrigramSimilarity("text", query),
        TrigramWordSimilarity(query, "text"),
        keyword_score,
        output_field=FloatField(),
    )
    rows = (
        _base_queryset(group)
        .filter(prefilter)
        .annotate(
            similarity=score,
            _rank=score + Case(
                When(is_answered=True, then=Value(ANSWERED_BOOST)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
        )
        .order_by("-_rank", "-upvotes_count", "-created_at")[:limit]
    )
    return [(d, round(float(d.similarity), 3)) for d in rows]


def _fallback_search(group, query, limit):
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []
    needed = 1 if len(q_tokens) == 1 else 2

    scored = []
    candidates = _base_queryset(group).order_by("-upvotes_count", "-created_at")[:FALLBACK_CANDIDATES]
    for doubt in candidates:
        d_tokens = set(_tokens(doubt.text))
        shared = len(q_tokens & d_tokens)
        if shared < needed:
            continue
        coverage = shared / len(q_tokens)
        if coverage < FALLBACK_MIN_COVERAGE:
            continue
        jaccard = shared / len(q_tokens | d_tokens)
        score = round(0.7 * coverage + 0.3 * jaccard, 3)
        rank = score + (ANSWERED_BOOST if doubt.is_answered else 0.0)
        scored.append((rank, doubt.upvotes_count, doubt.created_at, doubt, score))

    scored.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    return [(r[3], r[4]) for r in scored[:limit]]


def find_similar_doubts(group, query, limit=DEFAULT_LIMIT):
    """
    Returns `[(DoubtQuestion, score_0_to_1), ...]`, best match pehle.
    `query` pehle `normalize_query()` se guzra hona chahiye aur
    MIN_QUERY_CHARS ka check caller (view) kar chuka hota hai.
    """
    if _is_postgres():
        results = _postgres_search(group, query, limit)
        # pg_trgm ne kuch nahi diya par query me non-ASCII letters hain —
        # C-locale DB me trigram blind hota hai; Python fallback try karo.
        if not results and not query.isascii():
            results = _fallback_search(group, query, limit)
        return results
    return _fallback_search(group, query, limit)
