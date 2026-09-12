# core/search.py
"""
F-4 — Unified cross-app search ("search everything": messages, posts,
classroom materials, campus notices), built as an extension of the
same Postgres FTS + trigram strategy `message/search_utils.py` already
established for `Message`.

Lives in `core`, not `message` — `core` is already this project's
shared cross-app integration point (see `core.models.Notification`'s
direct FKs into `liveclass`, and `campus/bridge.py`'s own "campus's
ONLY door into core/message" golden rule, which says nothing about
`core` itself being restricted from reaching into any app). Putting
this here also means `message`/`liveclass`/`campus`/`post` never need
to import each other directly just to power one search box — they
each only ever talk to `core`.

GOLDEN RULE THIS FILE FOLLOWS (same one `message/search_utils.py`
already follows, look at how `search_messages(qs, query)` takes an
already-filtered `qs` instead of building it itself): every function
below takes an ALREADY PERMISSION-SCOPED queryset as input. This
module never decides who can see what — that decision (which
conversations a user is a participant of, which notices a student's
enrollment/parent-link/staff-profile entitles them to, which posts
aren't from a blocked/blocking user, which classroom materials belong
to a classroom the user has access to) stays inside each app's own
view/queryset-building code, exactly where it already lives for
`message`. Reimplementing that scoping logic here — even partially, even
just for `Notice`, where I can see the model — would create a SECOND,
independently-maintained copy of "who can see this row" next to
whatever `NoticeViewSet`/`ConversationViewSet`/etc. already enforce.
Two independently-maintained copies of an access rule drift, and a
search endpoint that leaks one row an app's own view would have denied
is a worse failure than this feature simply not existing yet — so this
file deliberately stays a pure ranking/merging layer, never a
permission layer.

STATUS (this pass):
  - ✅ message — reuses `message.search_utils.search_messages` directly
    (Message already has a real `search_vector` column + trigger, no
    need to re-derive it with the generic on-the-fly path below).
  - ✅ campus notices (`campus.Notice`, `title`/`body`) — fully wired
    via `_search_generic_model` below, since `campus/models.py` was
    available this pass. Still needs a caller to pass in a properly
    scoped `Notice` queryset (see golden rule above) — this file does
    NOT know or guess campus/department/section visibility rules.
  - ❌ posts (`post` app) — STUB ONLY. `post/models.py` was never part
    of any upload, so `Post`'s searchable field name(s) are unknown.
    Wire up by adding a `SearchSource` to `SOURCES` below (see
    `NOTICE_SOURCE` for the shape) once that model is available.
  - ❌ classroom materials (`liveclass.ClassMaterial`) — STUB ONLY,
    same reason. The class exists (confirmed in an earlier
    `liveclass/models.py` upload) but its field list was never seen.

NEEDED TO FINISH THIS FILE: `post/models.py` (for `Post`'s searchable
field(s), and confirmation of how `post` already scopes visibility —
likely via `user_profile.BlockUser`/`RestrictUser` — so this doesn't
duplicate that logic) and the `ClassMaterial` model definition (same
two questions). Once those land, add one `SearchSource` entry each to
`SOURCES` and the corresponding view-side scoped-queryset builder — no
other change needed here.
"""
import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity
from django.db import connection
from django.db.models import F, Q, QuerySet

from message import search_utils as message_search_utils

logger = logging.getLogger(__name__)

# Same knobs message/search_utils.py already tuned for chat-length text —
# reused as-is rather than re-guessing thresholds per content type. If a
# future source's content is meaningfully longer/shorter than a chat
# message (e.g. a long notice body), revisit whether it needs its own
# threshold instead of assuming this one generalizes.
MIN_QUERY_LENGTH = message_search_utils.MIN_QUERY_LENGTH
TRIGRAM_SIMILARITY_THRESHOLD = message_search_utils.TRIGRAM_SIMILARITY_THRESHOLD


def _is_postgres() -> bool:
    return connection.vendor == 'postgresql'


def _search_generic_model(qs: QuerySet, query: str, *, fields: Iterable[str], order_field: str = 'created_at'):
    """
    Same OR(ranked-FTS, trigram) strategy as
    `message.search_utils.search_messages`, generalized to any model
    that — unlike `Message` — has NO stored `search_vector` column.

    Computes `SearchVector(*fields)` fresh, per query, instead of
    reading a pre-populated trigger-maintained column. This is
    strictly slower under load (Postgres tokenizes every candidate
    row's text at query time, not at write time) and can't use a GIN
    index the way `Message.search_vector` can. Acceptable for a
    low-volume, already-narrowly-scoped table (e.g. one campus's
    notices) — NOT something to point at a large or ungated table
    as-is. A source that needs to scale should get its own stored
    `search_vector` column + trigger migration, the same way `Message`
    already has one, instead of leaning on this generic path forever.

    `fields` — searchable text column name(s), e.g. `("title",
    "body")`. Trigram similarity only ever runs against the FIRST
    field (Postgres trigram similarity doesn't compose across
    concatenated columns as cleanly as a tsvector does) — pass the
    single most important searchable field first.

    `order_field` — the model's own recency field, used both for the
    non-Postgres fallback's ordering and as the final tiebreaker after
    rank/similarity. Every model passed into this function must have
    it (defaults to `created_at`, which `Message`/`Notice` both have —
    override for a model that names it differently, e.g. `posted_at`).
    """
    fields = tuple(fields)
    if not fields:
        raise ValueError("_search_generic_model requires at least one field.")

    if not _is_postgres():
        # sqlite / local dev fallback — unranked substring match,
        # OR'd across every given field.
        filters = Q()
        for field in fields:
            filters |= Q(**{f"{field}__icontains": query})
        return qs.filter(filters).order_by(f"-{order_field}")

    search_query = SearchQuery(query, config='english')
    primary_field = fields[0]

    return (
        qs.annotate(
            _search_vector=SearchVector(*fields, config='english'),
            similarity=TrigramSimilarity(primary_field, query),
        )
        .annotate(rank=SearchRank(F('_search_vector'), search_query))
        .filter(Q(_search_vector=search_query) | Q(similarity__gt=TRIGRAM_SIMILARITY_THRESHOLD))
        .order_by('-rank', '-similarity', f"-{order_field}")
    )


@dataclass(frozen=True)
class SearchSource:
    """
    One pluggable "search everything" source. `run` takes the
    caller-supplied, already-permission-scoped queryset + the query
    string and returns an ordered, annotated queryset (ranked best
    match first) — the same contract `message.search_utils.
    search_messages(qs, query)` already has. `serialize` turns one
    result row into the common dict shape `search_everything()`
    returns, so heterogeneous models can be merged into one list.
    """

    name: str
    run: Callable[[QuerySet, str], QuerySet]
    serialize: Callable[[object], dict]


def _serialize_message(message) -> dict:
    return {
        "source": "message",
        "id": message.id,
        "title": None,
        "snippet": message.text,
        "created_at": message.created_at,
        "rank": getattr(message, "rank", None),
        "similarity": getattr(message, "similarity", None),
        "extra": {"conversation_id": message.conversation_id, "sender_id": message.sender_id},
    }


def _serialize_notice(notice) -> dict:
    return {
        "source": "campus_notice",
        "id": notice.id,
        "title": notice.title,
        "snippet": notice.body[:280],
        "created_at": notice.created_at,
        "rank": getattr(notice, "rank", None),
        "similarity": getattr(notice, "similarity", None),
        "extra": {"campus_id": notice.campus_id},
    }


MESSAGE_SOURCE = SearchSource(
    name="message",
    run=lambda qs, query: message_search_utils.search_messages(qs, query),
    serialize=_serialize_message,
)

NOTICE_SOURCE = SearchSource(
    name="campus_notice",
    run=lambda qs, query: _search_generic_model(qs, query, fields=("title", "body")),
    serialize=_serialize_notice,
)

# ❌ post / classroom-material sources intentionally NOT registered
# yet — see module docstring STATUS. Add them here, same shape as
# NOTICE_SOURCE, once their models are available:
#
# POST_SOURCE = SearchSource(
#     name="post",
#     run=lambda qs, query: _search_generic_model(qs, query, fields=(...)),
#     serialize=_serialize_post,
# )
# CLASS_MATERIAL_SOURCE = SearchSource(
#     name="class_material",
#     run=lambda qs, query: _search_generic_model(qs, query, fields=(...)),
#     serialize=_serialize_class_material,
# )

SOURCES = {
    MESSAGE_SOURCE.name: MESSAGE_SOURCE,
    NOTICE_SOURCE.name: NOTICE_SOURCE,
}


def search_everything(
    scoped_querysets: dict,
    query: str,
    *,
    sources: Optional[Iterable[str]] = None,
    limit_per_source: int = 10,
    total_limit: int = 30,
) -> list:
    """
    The single "search everything" entry point. Callers (a view) build
    ONE already-permission-scoped queryset per source they want
    included and pass them in as `scoped_querysets` — e.g.:

        search_everything(
            {
                "message": Message.objects.filter(conversation__participants=request.user),
                "campus_notice": Notice.objects.filter(campus__in=my_campus_ids, ...),
            },
            query="exam schedule",
        )

    Only keys present in BOTH `scoped_querysets` and `SOURCES` (and, if
    given, `sources`) are actually searched — an unregistered or
    unscoped source is silently skipped, not an error, so a caller can
    always pass every scoped queryset it has even before every source
    above is wired up.

    Returns a single flat list of normalized dicts (see
    `_serialize_message`/`_serialize_notice` for the shape), merged
    across sources and sorted by `rank` (falling back to `similarity`,
    then `created_at`) — capped to `total_limit` overall, after first
    capping each individual source to `limit_per_source` so one noisy
    source can't crowd out every other one.

    CAVEAT this doesn't try to solve: `rank`/`similarity` are Postgres
    tsvector/trigram scores computed independently per model/content —
    they are NOT guaranteed to be on a directly comparable scale across
    different sources (a notice's rank and a message's rank aren't the
    same "unit"). This is an inherent limit of merging heterogeneous
    full-text scores, not a bug to silently paper over — if this
    matters for your UI (e.g. a "top result" callout), consider
    grouping/sectioning results by source in the frontend instead of
    trusting a single global sort order to mean anything precise.

    Raises `ValueError` if `query` is shorter than `MIN_QUERY_LENGTH` —
    same check `message/search_utils.py`'s callers already do before
    reaching `search_messages`, applied once here for every source
    instead of per-caller.
    """
    if len(query.strip()) < MIN_QUERY_LENGTH:
        raise ValueError(f"Query must be at least {MIN_QUERY_LENGTH} characters.")

    active_source_names = sources if sources is not None else SOURCES.keys()

    results = []
    for source_name in active_source_names:
        source = SOURCES.get(source_name)
        base_qs = scoped_querysets.get(source_name)
        if source is None or base_qs is None:
            continue
        try:
            matched_qs = source.run(base_qs, query)[:limit_per_source]
            results.extend(source.serialize(obj) for obj in matched_qs)
        except Exception:
            # One source's query erroring (e.g. a caller passed a qs
            # missing an expected field) shouldn't take down every
            # other source's results — log and continue, same
            # degrade-not-crash posture campus/bridge.py already takes
            # for a missing bridge function.
            logger.exception("Unified search source %r failed for query %r.", source_name, query)
            continue

    def _sort_key(item):
        rank = item.get("rank") or 0
        similarity = item.get("similarity") or 0
        created_at = item.get("created_at")
        return (rank, similarity, created_at)

    results.sort(key=_sort_key, reverse=True)
    return results[:total_limit]