"""P-F7 -- unified search: ONE ranked search over people / posts / classes / tests / doubts.

    GET /core/search/unified/?q=<text>&tab=all|people|posts|classes|tests|doubts
                              &limit=10&offset=0 + per-tab filters (see FILTERS below)
    GET /core/search/trending/        what to show before the user types (hashtags, subjects, ...)

This module is the logic only; the HTTP layer is core/unified_search_views.py.

How it relates to the older core/search.py (`GET /core/search/`):
  * core/search.py stays as it is: it is the "messages / notices / assignments / test series" box and
    it merges heterogeneous FTS scores into one list. Nothing here replaces it.
  * This module never merges scores of different content types into one list. Every tab has its OWN
    relevance score (0..1) and its own ordering; the "all" tab simply returns a few rows of each
    section, so the "scores are not comparable across sources" caveat of core/search.py does not apply.

Rules this file follows
  1. VISIBILITY IS NEVER RE-INVENTED. Every queryset is built from the same helpers the app already
     uses (post.audience.filter_posts_visible_to, the same classroom rules as ClassroomViewSet, the same
     user exclusions as UserSearchView / core.views.SearchView: self, blocked either way, "hide me from
     search", minors for strangers). A new rule belongs in that helper, not here.
  2. TYPO TOLERANCE (pg_trgm). Pass 1 is a cheap substring match. Only when pass 1 returns fewer rows
     than `limit` does pass 2 run a trigram match (the `%` operator, which can use the trigram GIN
     indexes) and the rows are re-ranked by similarity. On SQLite / dev there is no pg_trgm: pass 1 only.
  3. BOUNDED WORK. Query length is capped, at most MAX_TOKENS words are used, offset is capped at
     MAX_OFFSET, limit at MAX_LIMIT (the view also has a scoped throttle `search_unified`).
  4. ONE BROKEN SOURCE NEVER BREAKS THE SCREEN. In the "all" tab each section is built in its own
     try/except; a failing section comes back empty and is listed in `errors`.

Query syntax: `#tag` searches posts by hashtag, `@name` searches people by handle prefix.
"""
import logging
import re
import unicodedata
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Case, ExpressionWrapper, F, FloatField, Q, QuerySet, Value, When
from django.db.models.functions import Greatest
from django.utils import timezone

logger = logging.getLogger(__name__)

TABS = ("all", "people", "posts", "classes", "tests", "doubts")
SECTION_TABS = ("people", "posts", "classes", "tests", "doubts")

MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 80
MAX_TOKENS = 5
DEFAULT_LIMIT = 10
MAX_LIMIT = 30
MAX_OFFSET = 200
ALL_TAB_PER_SECTION = 4  # rows per section on the "all" tab
TRIGRAM_MIN_SIMILARITY = 0.3  # same default as pg_trgm.similarity_threshold (what the `%` operator uses)
SNIPPET_CHARS = 160

TRENDING_CACHE_SECONDS = 600
TRENDING_HASHTAG_DAYS = 7
TRENDING_MAX = 12

# Filters each tab understands (anything else is ignored, never an error).
FILTERS: Dict[str, tuple] = {
    "people": ("relation",),  # relation=connections -> only people I follow / who follow me
    "posts": ("kind", "subject", "category", "period", "sort"),
    "classes": ("subject", "language", "min_rating", "sort"),
    "tests": ("price",),  # price=free|paid
    "doubts": ("subject", "status", "urgent", "sort"),  # status=solved|open
}
POST_KINDS = ("post", "reel", "resource", "poll")  # "doubt" is its own tab
PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
SORTS = ("relevance", "recent", "popular")

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_RE = re.compile(r"^[\w\u0900-\u097F]+$", re.UNICODE)


# --------------------------------------------------------------------------- query parsing
@dataclass
class ParsedQuery:
    text: str  # cleaned text as typed (max MAX_QUERY_CHARS)
    term: str  # text without a leading '#' / '@'
    tokens: List[str] = field(default_factory=list)
    tag: str = ""  # set for '#physics' -> 'physics'
    handle: bool = False  # set for '@name'

    @property
    def usable(self) -> bool:
        return len(self.term) >= MIN_QUERY_CHARS


def normalize_query(raw) -> str:
    """NFKC, no control characters, collapsed whitespace, capped length."""
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = _CTRL_RE.sub("", text)
    return " ".join(text.split())[:MAX_QUERY_CHARS]


def parse_query(raw) -> ParsedQuery:
    text = normalize_query(raw)
    tag, handle, term = "", False, text
    if text.startswith("#"):
        term = text.lstrip("#").strip()
        candidate = term.casefold()
        if _TAG_RE.match(candidate):
            tag = candidate
    elif text.startswith("@"):
        term = text.lstrip("@").strip()
        handle = True
    tokens = [t for t in term.split(" ") if t][:MAX_TOKENS]
    return ParsedQuery(text=text, term=term, tokens=tokens, tag=tag, handle=handle)


def clamp_limit(raw, default: int = DEFAULT_LIMIT) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


def clamp_offset(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, MAX_OFFSET))


def _is_postgres() -> bool:
    return connection.vendor == "postgresql"


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- generic ranking
def _token_filter(tokens: List[str], fields: tuple) -> Q:
    """Every word must appear in at least one of the fields (AND over words, OR over fields)."""
    cond = Q()
    for tok in tokens:
        one = Q()
        for name in fields:
            one |= Q(**{f"{name}__icontains": tok})
        cond &= one
    return cond


def _relevance_case(term: str, *, exact: tuple = (), prefix: tuple = (), contains: Optional[Q] = None):
    """0..1 relevance for the substring pass: exact match 1.0, starts-with 0.85, contains 0.55."""
    whens = []
    for name in exact:
        whens.append(When(**{f"{name}__iexact": term}, then=Value(1.0)))
    for name in prefix:
        whens.append(When(**{f"{name}__istartswith": term}, then=Value(0.85)))
    if contains is not None:
        whens.append(When(contains, then=Value(0.55)))
    return Case(*whens, default=Value(0.0), output_field=FloatField())


WORD_SIM_WEIGHT = 0.55  # == the "contains" tier of _relevance_case: body matches rank below title exact / prefix


def _run_two_pass(qs: QuerySet, parsed: ParsedQuery, *, token_fields: tuple, sim_fields: tuple = (),
                  word_fields: tuple = (), exact: tuple = (), prefix: tuple = (),
                  want: int = DEFAULT_LIMIT, offset: int = 0):
    """Pass 1 = substring; pass 2 (only if pass 1 is short, Postgres only) = trigram.

    `sim_fields`  short columns (title, username, subject): whole-string trigram similarity (`%`, GIN-indexable).
    `word_fields` long text (post / doubt body): a query WORD is close to some word of the text (`<%`).
    Returns (queryset annotated with `_rel`, fuzzy_used)."""
    term = parsed.term
    contains = _token_filter(parsed.tokens, token_fields) if parsed.tokens else Q(pk__in=[])
    exact_prefix = _relevance_case(term, exact=exact, prefix=prefix, contains=contains)
    base = qs.filter(contains).annotate(_rel=exact_prefix)
    if not _is_postgres() or not (sim_fields or word_fields):
        return base, False
    try:
        # pass 1 is "short" when the page the user asked for is not full
        if base.order_by().values("pk")[: offset + want + 1].count() >= offset + want + 1:
            return base, False
    except Exception:  # never fail the search because of the count probe
        logger.exception("unified_search: pass-1 probe failed")
        return base, False

    from django.contrib.postgres.search import TrigramSimilarity, TrigramWordSimilarity

    pre = Q()
    for name in sim_fields:
        pre |= Q(**{f"{name}__trigram_similar": term})
    for name in word_fields:
        pre |= Q(**{f"{name}__trigram_word_similar": term})
    sims = [TrigramSimilarity(name, term) for name in sim_fields]
    # A query word found inside a long body is a "contains" hit, never better: without the weight a body that
    # mentions the word scores 1.0 and outranks a title that STARTS with it (0.85). Same tier as the substring pass.
    sims += [
        ExpressionWrapper(TrigramWordSimilarity(term, name) * Value(WORD_SIM_WEIGHT), output_field=FloatField())
        for name in word_fields
    ]
    sim = sims[0] if len(sims) == 1 else Greatest(*sims)
    fuzzy = (
        qs.filter(contains | pre)
        .annotate(_sim=sim)
        .annotate(_rel=Greatest(exact_prefix, F("_sim"), output_field=FloatField()))
    )
    return fuzzy, True


def _order(qs: QuerySet, sort: str, *, recent: str, popular: Optional[str] = None, tie: str = "-pk"):
    if sort == "recent":
        return qs.order_by(f"-{recent}", tie)
    if sort == "popular" and popular:
        return qs.order_by(f"-{popular}", f"-{recent}", tie)
    return qs.order_by("-_rel", f"-{recent}", tie)


def _page(qs: QuerySet, offset: int, limit: int):
    rows = list(qs[offset: offset + limit + 1])
    return rows[:limit], len(rows) > limit


def _abs(request, file_field) -> Optional[str]:
    if not file_field:
        return None
    try:
        url = file_field.url
    except Exception:
        return None
    if request is not None and url and not str(url).startswith("http"):
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    return url


def _snippet(text, limit: int = SNIPPET_CHARS) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "\u2026"


def _score(row) -> float:
    return round(float(getattr(row, "_rel", 0.0) or 0.0), 3)


# --------------------------------------------------------------------------- viewer context
@dataclass
class Viewer:
    user: object
    blocked_ids: set
    muted_post_author_ids: set
    following_ids: set
    follower_ids: set

    @property
    def connection_ids(self) -> set:
        return self.following_ids | self.follower_ids


def load_viewer(user) -> Viewer:
    """The viewer's relationship sets, loaded once per request (four small id queries)."""
    from post.models import MutedAccount
    from user_profile.models import BlockUser, Follow

    blocked = {user.id}
    for blocker_id, blocked_id in BlockUser.objects.filter(
            Q(blocker=user) | Q(blocked=user)).values_list("blocker_id", "blocked_id"):
        blocked.add(blocker_id)
        blocked.add(blocked_id)
    blocked.discard(user.id)
    muted = set(MutedAccount.objects.filter(user=user, mute_posts=True).values_list("muted_user_id", flat=True))
    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
    following = set(accepted.filter(follower=user).values_list("following_id", flat=True))
    followers = set(accepted.filter(following=user).values_list("follower_id", flat=True))
    return Viewer(user=user, blocked_ids=blocked, muted_post_author_ids=muted,
                  following_ids=following, follower_ids=followers)


# --------------------------------------------------------------------------- PEOPLE
def _people_base(viewer: Viewer, filters: dict) -> QuerySet:
    from django.contrib.auth import get_user_model
    from common import minor_policy
    from user_profile import privacy as privacy_rules

    User = get_user_model()
    qs = User.objects.filter(is_active=True).exclude(id__in=viewer.blocked_ids | {viewer.user.id})
    qs = privacy_rules.exclude_hidden_from_search(qs, viewer.user)  # S4 "hide me from search"
    qs = minor_policy.exclude_minors_for_stranger(qs, viewer.user, feature=minor_policy.SEARCH)  # P-F16
    if filters.get("relation") == "connections":
        qs = qs.filter(id__in=viewer.connection_ids)
    return qs


def search_people(viewer: Viewer, parsed: ParsedQuery, filters: dict, *, limit: int, offset: int, request=None):
    qs = _people_base(viewer, filters)
    fields = ("username",) if parsed.handle else ("username", "first_name", "last_name")
    ranked, fuzzy = _run_two_pass(
        qs, parsed, token_fields=fields, sim_fields=("username", "first_name"),
        exact=("username",), prefix=("username", "first_name", "last_name"), want=limit, offset=offset)
    # connections first, then relevance, then newest account
    friend_boost = Case(When(id__in=viewer.connection_ids, then=Value(1)), default=Value(0))
    ranked = ranked.annotate(_friend=friend_boost).order_by("-_rel", "-_friend", "-date_joined", "-pk")
    rows, has_more = _page(ranked, offset, limit)
    items = []
    for u in rows:
        if u.id in viewer.following_ids:
            relation = "following"
        elif u.id in viewer.follower_ids:
            relation = "follower"
        else:
            relation = "none"
        full = f"{u.first_name} {u.last_name}".strip()
        items.append({
            "type": "person",
            "id": str(u.id),
            "title": u.username,
            "subtitle": full or None,
            "snippet": None,
            "image": _abs(request, getattr(u, "profile_photo", None)),
            "created_at": getattr(u, "date_joined", None),
            "score": _score(u),
            "meta": {"username": u.username, "first_name": u.first_name, "last_name": u.last_name,
                     "is_private": bool(getattr(u, "is_private", False)), "relation": relation},
        })
    return items, has_more, fuzzy


# --------------------------------------------------------------------------- POSTS + DOUBTS
def _posts_base(viewer: Viewer) -> QuerySet:
    from common import minor_policy
    from post.audience import filter_posts_visible_to
    from post.models import Post

    qs = Post.objects.filter(is_deleted=False, is_archived=False, is_scheduled=False,
                             moderation_status="approved").select_related("user")
    qs = filter_posts_visible_to(qs, viewer.user)
    qs = qs.exclude(user_id__in=viewer.blocked_ids | viewer.muted_post_author_ids)
    # an account that is private is only searchable by its own followers (same rule as discovery / profile tabs)
    qs = qs.filter(Q(user=viewer.user) | Q(user__is_private=False) | Q(user_id__in=viewer.following_ids))
    return minor_policy.exclude_minors_for_stranger(qs, viewer.user, feature=minor_policy.SEARCH, id_field="user_id")


def _apply_period(qs: QuerySet, period: str) -> QuerySet:
    days = PERIOD_DAYS.get(period)
    if not days:
        return qs
    return qs.filter(created_at__gte=timezone.now() - timedelta(days=days))


def _first_image(request, post) -> Optional[str]:
    for media in post.media.all():
        if media.media_type in ("image", "gif", "video"):
            return _abs(request, media.thumbnail) or (_abs(request, media.file) if media.media_type != "video" else None)
    return None


def _post_item(request, post, kind_label: str, score: float, meta_extra: Optional[dict] = None) -> dict:
    author = post.user
    meta = {"kind": post.kind, "post_type": post.post_type, "category": post.category,
            "likes_count": post.likes_count, "comments_count": post.comments_count,
            "author_id": str(author.id), "author_username": author.username,
            "author_photo": _abs(request, getattr(author, "profile_photo", None)),
            "hashtags": list(post.hashtags or [])[:5]}
    if meta_extra:
        meta.update(meta_extra)
    return {
        "type": kind_label,
        "id": str(post.id),
        "title": post.title or _snippet(post.content, 70) or None,
        "subtitle": f"@{author.username}",
        "snippet": _snippet(post.content) if post.title else None,
        "image": _first_image(request, post),
        "created_at": post.created_at,
        "score": score,
        "meta": meta,
    }


def search_posts(viewer: Viewer, parsed: ParsedQuery, filters: dict, *, limit: int, offset: int, request=None):
    qs = _posts_base(viewer).exclude(kind="doubt").exclude(post_type="repost")
    kind = filters.get("kind")
    if kind in POST_KINDS:
        qs = qs.filter(kind=kind)
    if filters.get("subject"):
        qs = qs.filter(subject__iexact=normalize_query(filters["subject"]))
    if filters.get("category"):
        qs = qs.filter(category__iexact=normalize_query(filters["category"]))
    qs = _apply_period(qs, filters.get("period", ""))
    sort = filters.get("sort") if filters.get("sort") in SORTS else "relevance"

    if parsed.tag:  # '#physics' -> hashtag lookup (exact tag, then the plain-text match as a fallback)
        # JSONField `contains` is not implemented on SQLite (dev / tests), `icontains` on the JSON text is.
        tag_q = (Q(hashtags__contains=[parsed.tag]) if _is_postgres()
                 else Q(hashtags__icontains='"%s"' % parsed.tag))
        tagged = (qs.filter(tag_q).prefetch_related("media")
                  .annotate(_rel=Value(1.0, output_field=FloatField())))
        rows, has_more = _page(_order(tagged, sort, recent="created_at", popular="likes_count"), offset, limit)
        return [_post_item(request, p, "post", 1.0) for p in rows], has_more, False

    ranked, fuzzy = _run_two_pass(
        qs, parsed, token_fields=("title", "content", "subject"), sim_fields=("title",), word_fields=("content",),
        exact=("title",), prefix=("title",), want=limit, offset=offset)
    ranked = ranked.prefetch_related("media")
    rows, has_more = _page(_order(ranked, sort, recent="created_at", popular="likes_count"), offset, limit)
    return [_post_item(request, p, "post", _score(p)) for p in rows], has_more, fuzzy


def search_doubts(viewer: Viewer, parsed: ParsedQuery, filters: dict, *, limit: int, offset: int, request=None):
    qs = _posts_base(viewer).filter(kind="doubt").select_related("doubt_info")
    if filters.get("subject"):
        qs = qs.filter(doubt_info__subject__iexact=normalize_query(filters["subject"]))
    status = filters.get("status")
    if status == "solved":
        qs = qs.filter(doubt_info__solved_at__isnull=False)
    elif status == "open":
        qs = qs.filter(doubt_info__solved_at__isnull=True)
    if _truthy(filters.get("urgent")):
        qs = qs.filter(doubt_info__is_urgent=True)
    sort = filters.get("sort") if filters.get("sort") in SORTS else "relevance"

    ranked, fuzzy = _run_two_pass(
        qs, parsed, token_fields=("title", "content", "doubt_info__ocr_text", "doubt_info__subject",
                                  "doubt_info__chapter"),
        word_fields=("content",), exact=(), prefix=("title",), want=limit, offset=offset)
    # solved doubts first among equal matches: the asker finds an existing answer
    solved = Case(When(doubt_info__solved_at__isnull=False, then=Value(1)), default=Value(0))
    ranked = ranked.annotate(_solved=solved)
    if sort == "relevance":
        ranked = ranked.order_by("-_rel", "-_solved", "-created_at", "-pk")
    else:
        ranked = _order(ranked, sort, recent="created_at", popular="answers_count")
    rows, has_more = _page(ranked, offset, limit)
    items = []
    for p in rows:
        info = getattr(p, "doubt_info", None)
        extra = {"subject": getattr(info, "subject", "") or "", "is_solved": bool(info and info.solved_at),
                 "is_urgent": bool(info and info.is_urgent), "answers_count": p.answers_count,
                 "bounty_coins": getattr(info, "bounty_coins", 0) or 0}
        item = _post_item(request, p, "doubt", _score(p), extra)
        item["title"] = _snippet(p.content, 90) or item["title"]
        item["snippet"] = None
        items.append(item)
    return items, has_more, fuzzy


# --------------------------------------------------------------------------- CLASSES
def _classes_base(viewer: Viewer) -> QuerySet:
    from tuitionclass.models import Classroom

    # same rule as ClassroomViewSet.get_queryset() for the public Explore list
    qs = Classroom.objects.filter(
        Q(is_active=True, is_flagged=False) | Q(teacher=viewer.user), is_deleted=False, is_personal=False,
    ).select_related("teacher")
    return qs.exclude(teacher_id__in=viewer.blocked_ids)


def search_classes(viewer: Viewer, parsed: ParsedQuery, filters: dict, *, limit: int, offset: int, request=None):
    qs = _classes_base(viewer)
    if filters.get("subject"):
        qs = qs.filter(subject__iexact=normalize_query(filters["subject"]))
    if filters.get("language"):
        qs = qs.filter(language__iexact=normalize_query(filters["language"]))
    try:
        min_rating = float(filters.get("min_rating"))
    except (TypeError, ValueError):
        min_rating = None
    if min_rating:
        qs = qs.filter(rating_avg__gte=max(0.0, min(min_rating, 5.0)))
    sort = filters.get("sort") if filters.get("sort") in SORTS else "relevance"

    ranked, fuzzy = _run_two_pass(
        qs, parsed, token_fields=("title", "subject", "description", "teacher__first_name", "teacher__last_name",
                                  "teacher__username"),
        sim_fields=("title", "subject"), exact=("title", "subject"), prefix=("title", "subject"),
        want=limit, offset=offset)
    ranked = _order(ranked, sort, recent="created_at", popular="enrolled_count")
    rows, has_more = _page(ranked, offset, limit)
    items = []
    for c in rows:
        teacher = c.teacher
        items.append({
            "type": "class",
            "id": str(c.id),
            "title": c.title,
            "subtitle": c.subject or None,
            "snippet": _snippet(c.description),
            "image": _abs(request, c.cover_image),
            "created_at": c.created_at,
            "score": _score(c),
            "meta": {"subject": c.subject, "language": c.language,
                     "teacher_id": str(teacher.id), "teacher_username": teacher.username,
                     "organisation_name": c.organisation_name or None,
                     "rating_avg": float(c.rating_avg or 0), "rating_count": c.rating_count,
                     "enrolled_count": c.enrolled_count},
        })
    return items, has_more, fuzzy


# --------------------------------------------------------------------------- TESTS
def _tests_base(viewer: Viewer) -> QuerySet:
    """Same scope as core.views.SearchView's `testseries` (marketplace / own / attempted / my campus sections)."""
    from campus.models import StudentEnrollment
    from testseries.models import TestSeries

    user = viewer.user
    sections = StudentEnrollment.objects.filter(
        student=user, status=StudentEnrollment.Status.ACTIVE).values_list("section_id", flat=True)
    qs = TestSeries.objects.filter(
        Q(source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED)
        | Q(creator=user)
        | Q(attempts__student=user)
        | Q(source=TestSeries.Source.CAMPUS, context_type="section", context_id__in=sections)
    ).select_related("creator").distinct()
    return qs.exclude(creator_id__in=viewer.blocked_ids)


def search_tests(viewer: Viewer, parsed: ParsedQuery, filters: dict, *, limit: int, offset: int, request=None):
    qs = _tests_base(viewer)
    if filters.get("price") == "free":
        qs = qs.filter(is_paid=False)
    elif filters.get("price") == "paid":
        qs = qs.filter(is_paid=True)
    ranked, fuzzy = _run_two_pass(
        qs, parsed, token_fields=("title", "description", "creator__username"),
        sim_fields=("title",), exact=("title",), prefix=("title",), want=limit, offset=offset)
    ranked = _order(ranked, "relevance", recent="created_at")
    rows, has_more = _page(ranked, offset, limit)
    items = []
    for t in rows:
        items.append({
            "type": "test",
            "id": str(t.id),
            "title": t.title,
            "subtitle": f"@{t.creator.username}",
            "snippet": _snippet(t.description),
            "image": None,
            "created_at": t.created_at,
            "score": _score(t),
            "meta": {"is_paid": t.is_paid, "price_coins": t.price_coins, "duration_minutes": t.duration_minutes,
                     "total_marks": t.total_marks, "source_type": t.source, "creator_id": str(t.creator_id)},
        })
    return items, has_more, fuzzy


# --------------------------------------------------------------------------- dispatcher
SEARCHERS = {
    "people": search_people,
    "posts": search_posts,
    "classes": search_classes,
    "tests": search_tests,
    "doubts": search_doubts,
}


def clean_filters(tab: str, params) -> dict:
    """Only the filters of `tab`, each value normalized. Unknown / foreign filters are dropped."""
    out = {}
    for name in FILTERS.get(tab, ()):
        value = params.get(name)
        if value not in (None, ""):
            out[name] = normalize_query(value)
    return out


def run_search(user, raw_query, *, tab: str = "all", params=None, limit=None, offset=None, request=None) -> dict:
    """Entry point used by the view. Raises ValueError("query_too_short" / "bad_tab")."""
    params = params or {}
    if tab not in TABS:
        raise ValueError("bad_tab")
    parsed = parse_query(raw_query)
    if not parsed.usable:
        raise ValueError("query_too_short")

    viewer = load_viewer(user)
    base = {"query": parsed.text, "tab": tab, "min_chars": MIN_QUERY_CHARS}

    if tab != "all":
        lim, off = clamp_limit(limit), clamp_offset(offset)
        items, has_more, fuzzy = SEARCHERS[tab](
            viewer, parsed, clean_filters(tab, params), limit=lim, offset=off, request=request)
        base.update({"items": items, "has_more": has_more, "next_offset": (off + lim) if has_more else None,
                     "fuzzy": fuzzy})
        if not items and off == 0:
            base["did_you_mean"] = did_you_mean(parsed)
        return base

    sections, errors, any_rows, any_fuzzy = {}, [], False, False
    per = clamp_limit(limit, default=ALL_TAB_PER_SECTION)
    per = min(per, ALL_TAB_PER_SECTION)
    for name in SECTION_TABS:
        if parsed.handle and name != "people":
            continue  # '@name' is a people search
        try:
            items, has_more, fuzzy = SEARCHERS[name](
                viewer, parsed, clean_filters(name, params), limit=per, offset=0, request=request)
        except Exception:
            logger.exception("unified_search: section %r failed for %r", name, parsed.text)
            errors.append(name)
            items, has_more, fuzzy = [], False, False
        sections[name] = {"items": items, "has_more": has_more}
        any_rows = any_rows or bool(items)
        any_fuzzy = any_fuzzy or fuzzy
    base.update({"sections": sections, "errors": errors, "fuzzy": any_fuzzy})
    if not any_rows and not errors:
        base["did_you_mean"] = did_you_mean(parsed)
    return base


# --------------------------------------------------------------------------- "did you mean" + trending
def did_you_mean(parsed: ParsedQuery) -> Optional[str]:
    """One spelling suggestion when nothing matched: the closest hashtag / class subject (pg_trgm). None on SQLite."""
    if not _is_postgres() or len(parsed.term) < 3:
        return None
    try:
        from django.contrib.postgres.search import TrigramSimilarity
        from post.models import Hashtag
        from tuitionclass.models import Classroom

        term = parsed.term.casefold()
        best, best_sim = None, 0.0
        tag = (Hashtag.objects.filter(posts_count__gt=0).annotate(sim=TrigramSimilarity("name", term))
               .filter(sim__gte=0.35).order_by("-sim", "-posts_count").values_list("name", "sim").first())
        if tag:
            best, best_sim = tag[0], tag[1]
        subject = (Classroom.objects.filter(is_active=True, is_deleted=False, is_personal=False)
                   .exclude(subject="").annotate(sim=TrigramSimilarity("subject", term))
                   .filter(sim__gte=0.35).order_by("-sim").values_list("subject", "sim").first())
        if subject and subject[1] > best_sim:
            best = subject[0]
        if best and best.casefold() != term:
            return best
    except Exception:
        logger.exception("unified_search: did_you_mean failed")
    return None


def trending(user) -> dict:
    """What the search screen shows before the user types: trending hashtags (last week's most used, public posts
    only) and the subjects with the most open doubts / classes. Same for everyone, so it is cached 10 minutes;
    nothing personal and no search history is stored server side (recent searches stay on the device)."""
    key = "unified_search:trending:v1"
    data = cache.get(key)
    if data is None:
        data = {"hashtags": _trending_hashtags(), "subjects": _trending_subjects()}
        cache.set(key, data, TRENDING_CACHE_SECONDS)
    return data


def _trending_hashtags() -> List[dict]:
    try:
        from post.models import Hashtag

        since = timezone.now() - timedelta(days=TRENDING_HASHTAG_DAYS)
        rows = (Hashtag.objects.filter(posts_count__gt=0, last_used_at__gte=since)
                .order_by("-posts_count", "name").values_list("name", "posts_count")[:TRENDING_MAX])
        return [{"tag": name, "count": count} for name, count in rows]
    except Exception:
        logger.exception("unified_search: trending hashtags failed")
        return []


def _trending_subjects() -> List[dict]:
    try:
        from django.db.models import Count
        from tuitionclass.models import Classroom

        rows = (Classroom.objects.filter(is_active=True, is_flagged=False, is_deleted=False, is_personal=False)
                .exclude(subject="").values("subject").annotate(n=Count("id")).order_by("-n", "subject")[:TRENDING_MAX])
        return [{"subject": r["subject"], "count": r["n"]} for r in rows]
    except Exception:
        logger.exception("unified_search: trending subjects failed")
        return []
