# post/views/feed.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from django.db.models import (
    Case,
    Exists,
    F,
    FloatField,
    OuterRef,
    Q,
    Value,
    When,
)
from ..feed_resilience import FeedHomeThrottle
from user_profile.models import Follow
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from ..models import Post
from ..serializers import PostListSerializer
from rest_framework.response import Response
from ..services import exclude_hidden_and_muted
from rest_framework import generics
from rest_framework.decorators import permission_classes
from datetime import timedelta
from django.utils import timezone
from ._common import (
    HomeFeedPagination,
    logger,
)


# ===================== REPOST HELPER =====================
def _without_superseded_reposts(qs):
    """Duplicate-repost policy (see PostRepostAPIView): the same user may
    repost the same post more than once, but a feed/list should only show
    the LATEST of those. Hides any repost row that has a newer, non-deleted
    repost of the same original by the same user. Plain posts
    (`original_post IS NULL`) are never touched."""
    newer_repost = Post.objects.filter(
        user=OuterRef('user'), original_post=OuterRef('original_post'),
        is_deleted=False, created_at__gt=OuterRef('created_at'),
    )
    # A repost of a post its author archived disappears with it (the embedded preview would leak it).
    return qs.exclude(Q(original_post__isnull=False) & Exists(newer_repost)).exclude(original_post__is_archived=True)


# TASK G4 (growth_and_feature_tasks.md) — "Short-form video priority in
# feed ranking". Two boost components, shared by HomeFeedView and
# ExploreFeedAPIView so both rank the same way:
#
#   * video_boost — rewards videos people actually watch, not just scroll
#     past. NOW built by feed_mix.video_watch_boost() (completion rate x
#     confidence + average watch seconds; settings.FEED_WATCH_TIME); the
#     paragraph below describes the raw input. `video_completion_rate` (0.0-1.0, models.py) is 0.0 for every
#     non-video post and for videos with no watch-progress data yet, so
#     this is a pure no-op until PostVideoProgressAPIView starts recording
#     real watches — it never demotes anything.
#   * recent_velocity_boost — "likes/comments per hour", approximated with
#     recency buckets (3h/12h/48h) rather than a literal division. A
#     literal `(likes*3+comments*5) / hours_since_created` would need a
#     DB-side EXTRACT(epoch FROM ...) on a duration, which doesn't behave
#     the same way across this project's Postgres/SQLite split
#     (LearnScroll/settings.py) — bucketed multipliers give the same
#     "fresh + fast-moving beats old + steady" ranking shape and stay
#     portable. Same shape as the existing `is_recent` Case/When just
#     below in HomeFeedView.
def _video_and_velocity_boost():
    # Watch-time ranking (feed task, Part 1): confidence-weighted completion
    # rate + absolute watch time, see feed_mix.video_watch_boost().
    from .. import feed_mix

    video_boost = feed_mix.video_watch_boost()
    now = timezone.now()
    engagement = F('likes_count') * 3.0 + F('comments_count') * 5.0
    # Multipliers are fractions of the post's own engagement score, not
    # flat points — a fresh post with 0 engagement still gets 0 boost, and
    # a heavily-liked-but-old post never gets demoted, it just stops
    # earning the freshness bonus.
    recent_velocity_boost = Case(
        When(created_at__gte=now - timedelta(hours=3), then=engagement * 0.5),
        When(created_at__gte=now - timedelta(hours=12), then=engagement * 0.25),
        When(created_at__gte=now - timedelta(hours=48), then=engagement * 0.1),
        default=Value(0.0), output_field=FloatField(),
    )
    return video_boost, recent_velocity_boost


def _home_base_qs(user):
    """The Home feed's shared "safe to show" queryset (all three sources).

    Module-level (not a HomeFeedView method) so GET /post/<id>/why/ can build
    the exact same queryset the feed pools are made from.
    exclude_hidden_and_muted: "Not interested" posts + muted accounts never
    come back (also applied when a frozen snapshot is served, because the ids
    are re-fetched through this queryset)."""
    qs = exclude_hidden_and_muted(_without_superseded_reposts(
        Post.objects.select_related('user', 'original_post__user')
        .prefetch_related('media', 'original_post__media')
        .filter(is_deleted=False, is_archived=False, moderation_status='approved', is_sensitive=False, is_scheduled=False)
        .exclude(user=user)
    ), user)
    # T1 item 6: heavily reported posts are out of EVERY source (post/feed_quality.py, layer 1).
    from .. import feed_quality
    return feed_quality.exclude_reported_heavy(qs)


# ===================== HOME FEED =====================
# DISCOVERY MIX — the feed is no longer "following only". Every page blends
# three sources (default 60% following / 30% recommended / 10% trending,
# tunable via settings.FEED_MIX_RATIOS). All the mixing/ranking lives in
# post/feed_mix.py; this view only wires it to HTTP. If one source runs dry
# (new user follows nobody, follows haven't posted) its slots are refilled
# from the others, so the feed is never empty. Response shape is unchanged:
# {count, next, previous, results}; each post additionally carries
# `feed_source` = "following" | "recommended" | "trending".
class HomeFeedView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination
    # H10: explicit per-user limit for the feed (scope `feed_home`, settings DEFAULT_THROTTLE_RATES). Replaces the
    # project-wide default throttles for this view, and fails OPEN when the cache (Redis) is down - the stock
    # throttle would turn a Redis outage into a 500 on every feed request.
    throttle_classes = [FeedHomeThrottle]

    def get_serializer_context(self):
        # TASK 2: hand the already-computed following-id set to the
        # serializer so each post card's `user.is_following` (Follow
        # button) doesn't run its own query per post. Falls back to None
        # (per-post query) when list() hasn't run, e.g. drf-spectacular.
        return {
            'request': self.request,
            'following_ids': getattr(self, '_following_ids', None),
            'feed_sources': getattr(self, '_feed_sources', None) or {},
            # T1 Part 3: ids that were woven in as exploration (new creator / test audience)
            'explore_ids': getattr(self, '_explore_ids', None) or set(),
        }

    def _filter(self):
        """H12: the chip filter of this request (?subject= / ?category= / ?exam=, see post/feed_chips.py), parsed
        once. A bad value is a 400 (DRF ValidationError); no request (schema generation) = no filter."""
        flt = getattr(self, '_feed_filter', None)
        if flt is None:
            from .. import feed_chips
            request = getattr(self, 'request', None)
            flt = self._feed_filter = (
                feed_chips.parse(request.query_params) if request is not None else feed_chips.NO_FILTER
            )
        return flt

    def _base_qs(self):
        """Shared "safe to show" queryset for all three sources (+ the H12 chip filter, so the ranking pools, the
        id -> post fetch of every page and the H10 fallback feed all see the same narrowed set)."""
        from .. import feed_chips
        return feed_chips.apply(_home_base_qs(self.request.user), self._filter())

    def get_queryset(self):
        # Kept for schema generation / backwards compatibility; the real
        # (mixed) result set is built in list().
        return self._base_qs().filter(visibility='public').order_by('-created_at')

    @extend_schema(
        summary="Home feed (following + recommended + trending mix)",
        parameters=[
            OpenApiParameter(
                name='cursor', type=str, required=False,
                description="Opaque cursor. Do NOT set it on the first request; follow the `next` URL "
                            "from the previous response. The ranking is frozen for the whole scrolling "
                            "session (see post/feed_snapshot.py)."),
            OpenApiParameter(name='page', type=int, required=False,
                             description="DEPRECATED - only used when no cursor is sent and page > 1 "
                                         "(old app versions)."),
            OpenApiParameter(name='page_size', type=int, required=False),
            OpenApiParameter(
                name='source', type=str, required=False,
                description="'mixed' (default, 60/30/10 blend) or 'following' (following-first; "
                            "discovery only fills in when there is nothing from followed accounts).",
            ),
            OpenApiParameter(
                name='subject', type=str, required=False,
                description="H12 chip: only posts of this subject (`subcategory` or a hashtag such as #physics). "
                            "Aliases: math/mathematics -> maths, phy -> physics, chem -> chemistry. Part of the "
                            "snapshot key and of the cursor: keep it the same on every page (a cursor of another "
                            "chip is a 404)."),
            OpenApiParameter(
                name='category', type=str, required=False,
                description="H12 chip: only posts of this `Post.category` (e.g. education). Same rules as `subject`."),
            OpenApiParameter(
                name='exam', type=str, required=False,
                description="H12 chip: only posts hashtagged for this exam: jee | neet | board (`boards` ok) | upsc. "
                            "Same rules as `subject`."),
            OpenApiParameter(
                name='seen_cutoff', type=str, required=False,
                description="Paging-stability token. Do NOT set it on page 1 - the server picks it and "
                            "returns it inside `next`/`previous`; clients just follow those URLs. Posts "
                            "marked seen AFTER this instant don't change the pages of this session.",
            ),
        ],
        tags=["Post Feed"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """H10: records latency + outcome of every feed request for the SLO (post/feed_ops.py), then runs the
        H2 stats wrapper. 4xx (bad cursor, auth) are the client's problem and do not count as errors; a 5xx, or a
        page that was empty BECAUSE even the fallback failed, does."""
        import time as _time
        from rest_framework.exceptions import APIException
        from .. import feed_ops, feed_resilience

        started = _time.perf_counter()
        status_code = 500
        self._degraded = False
        self._slo_error = False
        try:
            response = self._list_measured(request, *args, **kwargs)
            status_code = response.status_code
            if self._degraded:
                response[feed_resilience.DEGRADED_HEADER] = "1"
            return response
        except APIException as exc:
            status_code = exc.status_code
            raise
        finally:
            if status_code >= 500 or self._slo_error:
                outcome = feed_ops.OUTCOME_ERROR
            elif self._degraded:
                outcome = feed_ops.OUTCOME_DEGRADED
            else:
                outcome = feed_ops.OUTCOME_OK
            feed_ops.record_request((_time.perf_counter() - started) * 1000.0, outcome)

    def _list_measured(self, request, *args, **kwargs):
        """H2: thin wrapper. Normally just `_list_guarded`; with settings.FEED_LOADTEST_STATS (staging
        load test only) it also records how many SQL statements this page cost (post/feed_stats.py)."""
        from .. import feed_stats

        if not feed_stats.enabled():
            return self._list_guarded(request, *args, **kwargs)
        self._stat_label = 'legacy'  # overwritten by _list_snapshot
        response, n_queries = feed_stats.measure(lambda: self._list_guarded(request, *args, **kwargs))
        if response.status_code == 200:
            feed_stats.record_queries(self._stat_label, n_queries)
        return response

    def _list_guarded(self, request, *args, **kwargs):
        """H10 last line of defence: any unexpected error while ranking / paging / serializing is answered with
        the simple fallback feed (following by recency + recent public) instead of a 500. Deliberate HTTP errors
        (bad cursor 404, 401/403, ...) pass through untouched."""
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
        from rest_framework.exceptions import APIException
        from .. import feed_ops, feed_resilience

        if not feed_resilience.is_enabled():
            return self._list_dispatch(request, *args, **kwargs)
        try:
            return self._list_dispatch(request, *args, **kwargs)
        except (APIException, Http404, DjangoPermissionDenied):
            raise
        except Exception as exc:
            feed_ops.note("pool_timeout" if feed_resilience.is_timeout_error(exc) else "pool_error")
            logger.error("feed: unexpected error - serving the fallback feed", exc_info=True)
            try:
                return self._list_snapshot(request, force_degraded=True)
            except (APIException, Http404, DjangoPermissionDenied):
                raise
            except Exception:
                feed_ops.note("fallback_failed")
                logger.error("feed: fallback feed failed too - empty page", exc_info=True)
                self._degraded = True
                self._slo_error = True
                return Response({'count': 0, 'next': None, 'previous': None, 'results': []})

    def _list_dispatch(self, request, *args, **kwargs):
        """Cursor + frozen Redis snapshot by default; the old page/offset code
        path stays for (a) old app builds that send `?page=N` (N > 1) without
        a cursor and (b) settings.FEED_SNAPSHOT["enabled"] = False."""
        from .. import feed_snapshot

        self._filter()  # H12: a bad ?subject= / ?category= / ?exam= is a clean 400 before any ranking work
        if feed_snapshot.is_enabled() and (
            request.query_params.get('cursor') or self._requested_page(request) <= 1
        ):
            return self._list_snapshot(request)
        return self._list_legacy(request)

    @staticmethod
    def _requested_page(request):
        try:
            return max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            return 1

    def _page_size(self, request):
        paginator = self.paginator
        try:
            page_size = int(request.query_params.get(paginator.page_size_query_param, paginator.page_size))
        except (TypeError, ValueError):
            page_size = paginator.page_size
        return max(1, min(page_size, paginator.max_page_size))

    def _variant_overrides(self):
        """T1 item 7: the viewer's A/B-variant overrides (mix / diversity / author_cap / quality /
        explore / signals / context), resolved once per request."""
        ov = getattr(self, '_variant_overrides_cache', None)
        if ov is None:
            from .. import feed_experiment
            ov = self._variant_overrides_cache = feed_experiment.resolve(self.request.user.pk)['overrides']
        return ov

    def _ratios(self, request):
        from .. import feed_mix

        if request.query_params.get('source') == 'following':
            # Following-first tab: only following has ratio; discovery is
            # used purely as a last-resort fill (see allocate_page).
            return {feed_mix.SOURCE_FOLLOWING: 1.0}
        return feed_mix.get_ratios(self._variant_overrides().get('mix'))

    def _serialize_slices(self, pools, slices, ratios, history=None):
        """ids slice per source -> interleaved, serialized post list."""
        from .. import feed_mix

        chunks = {src: pools[src][start:end] for src, (start, end) in slices.items()}

        wanted_ids = [pid for ids in chunks.values() for pid in ids]
        posts_by_id = {
            p.id: p for p in
            self._base_qs().filter(id__in=wanted_ids)
        } if wanted_ids else {}

        # a post can be deleted/moderated between the id query and this
        # fetch - just skip it instead of failing the whole page.
        chunk_posts = {
            src: [posts_by_id[pid] for pid in ids if pid in posts_by_id]
            for src, ids in chunks.items()
        }
        merged = feed_mix.interleave(chunk_posts, ratios)
        # T1 Part 1: spread clumps (same author / type / category) inside this
        # page. Reorder only - never drops a post, so paging is unaffected.
        from django.conf import settings as dj_settings
        from .. import feed_diversity
        div_cfg = getattr(dj_settings, 'FEED_DIVERSITY', None)
        if self._variant_overrides().get('diversity'):  # T1 item 7: A/B variant override
            div_cfg = {**(div_cfg or {}), **self._variant_overrides()['diversity']}
        # `history` = tail of the previous page (from the cursor, Part 2), so the
        # spacing rules also hold across the page border. None = first page.
        merged = feed_diversity.diversify_posts(merged, div_cfg, history)
        self._diversity_tail = feed_diversity.page_tail(merged, div_cfg, history)
        ordered_posts = [post for _, post in merged]
        self._feed_sources = {post.id: src for src, post in merged}
        self._explore_ids = {post.id for _, post in merged if str(post.id) in getattr(self, '_explore_id_strs', ())}
        return self.get_serializer(ordered_posts, many=True).data

    def _list_snapshot(self, request, force_degraded=False):
        """PART 2 of the cursor-pagination task: rank once, freeze in Redis,
        page through it with an opaque cursor (post/feed_snapshot.py).

        H10: ranking runs under a DB timeout; if it fails (or `force_degraded`) the simple fallback feed is frozen
        into the snapshot instead. Parallel page-1 calls of one user build the pools once (single-flight lock).
        Redis (snapshot / candidate cache) down -> the page is still served, only the freezing is lost."""
        from rest_framework.utils.urls import replace_query_param, remove_query_param
        from .. import feed_cache, feed_mix, feed_ops, feed_resilience, feed_snapshot

        page_size = self._page_size(request)
        token = request.query_params.get('cursor')
        state = feed_snapshot.decode_cursor(token) if token else None  # bad cursor -> 404
        flt = self._filter()
        if state and state['filter'] != flt.token:
            # H12: the cursor belongs to a scrolling session of ANOTHER chip (or none). Its offsets are meaningless
            # for this list - 404 like any bad cursor; the app starts a fresh session for the chip it shows.
            from rest_framework.exceptions import NotFound
            raise NotFound("Invalid cursor.")

        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        self._following_ids = following_ids

        snapshot = None if force_degraded else (
            feed_snapshot.load(request.user.pk, state['snapshot_id'], scope=flt.token or None) if state else None
        )
        # H2: which kind of page this is, for post/feed_stats.py (only read when FEED_LOADTEST_STATS is on)
        self._stat_label = 'page1' if not state else ('next_hit' if snapshot is not None else 'next_miss')
        if snapshot is not None:
            # HIT: the ranking is exactly what page 1 saw. No pool queries.
            pools, ratios = snapshot['pools'], snapshot['ratios']
            self._explore_id_strs = set((snapshot.get('meta') or {}).get('explore_ids') or [])
            self._degraded = bool((snapshot.get('meta') or {}).get('degraded'))
            snapshot_id, offsets = state['snapshot_id'], state['offsets']
            cutoff_str = state['seen_cutoff']
        else:
            # NEW session (no cursor) or MISS (expired / cache flushed / another
            # user's cursor): (re)build the pools. On a miss reuse the cursor's
            # own seen_cutoff so the rebuilt pools match the original ones as
            # closely as possible, and continue from its offsets.
            ratios = self._ratios(request)
            if state:
                snapshot_id, offsets = state['snapshot_id'], state['offsets']
                seen_cutoff = feed_mix.resolve_seen_cutoff(state['seen_cutoff'])
            else:
                snapshot_id = feed_snapshot.new_snapshot_id()
                offsets = {src: 0 for src in feed_mix.SOURCES}
                seen_cutoff = feed_mix.resolve_seen_cutoff(request.query_params.get('seen_cutoff'))
            cutoff_str = feed_mix.format_seen_cutoff(seen_cutoff)
            meta = {}
            pools = None
            saved = False
            if not force_degraded:
                pools, meta, ratios, saved = self._rank_pools(
                    request, state, snapshot_id, seen_cutoff, following_ids, ratios,
                )
            if pools is None:
                # ranking failed / timed out (or the outer guard asked for it): fallback feed
                meta = {}
                pools = feed_resilience.fallback_pools(request.user, self._base_qs(), following_ids, meta)
                if request.query_params.get('source') != 'following':
                    ratios = feed_resilience.fallback_ratios()
                if meta.get('fallback_failed'):
                    self._slo_error = True
                saved = False
            # H17: a brand-new user must never get an empty feed - last-resort backfill (page 1 / rebuilds only,
            # never for a chip filter or Exam Mode, see post/feed_coldstart.py).
            if (not flt.token and not meta.get('fallback_failed') and not any(pools.get(s) for s in pools)
                    and not self._exam_mode_on(request.user)):
                from .. import feed_coldstart
                filled = feed_coldstart.backfill(request.user, self._base_qs(), following_ids, pools, meta)
                if filled is not pools:
                    pools = filled
                    saved = False
                    if request.query_params.get('source') != 'following':
                        ratios = {src: (1.0 if src == feed_mix.SOURCE_TRENDING else 0.0) for src in feed_mix.SOURCES}
                    self._slo_error = False
            self._degraded = bool(meta.get('degraded'))
            self._explore_id_strs = set(meta.get('explore_ids') or [])
            self._cold_start_backfill = bool(meta.get('cold_start_backfill'))
            if not saved and not meta.get('fallback_failed'):
                if not feed_snapshot.save(request.user.pk, snapshot_id, pools, ratios, meta=meta, scope=flt.token or None):
                    feed_ops.note("snapshot_store_down")

        sizes = {src: len(ids) for src, ids in pools.items()}
        total = sum(sizes.values())
        slices = feed_mix.allocate_next(offsets, page_size, sizes, ratios)
        data = self._serialize_slices(pools, slices, ratios, history=(state or {}).get('tail'))

        new_offsets = {src: end for src, (_, end) in slices.items()}
        has_next = any(new_offsets[src] < sizes[src] for src in feed_mix.SOURCES)
        if force_degraded and not feed_snapshot.is_enabled():
            has_next = False  # the legacy path ignores cursors; one fallback page is all we can honestly page
        next_url = None
        if has_next:
            url = remove_query_param(request.build_absolute_uri(), 'page')
            url = replace_query_param(url, 'cursor', feed_snapshot.encode_cursor(
                snapshot_id, new_offsets, cutoff_str, tail=getattr(self, '_diversity_tail', None),
                filter_token=flt.token,
            ))
            # kept for older clients / tooling that read it; the cursor is what counts
            if cutoff_str:
                url = replace_query_param(url, 'seen_cutoff', cutoff_str)
            next_url = url
        if data:
            self._note_first_feed(request, total, state)
        payload = {
            'count': total,
            'next': next_url,
            'previous': None,
            'results': data,
        }
        if getattr(self, '_cold_start_backfill', False):
            payload['cold_start'] = True  # H17: additive flag - old clients ignore it
        return Response(payload)

    @staticmethod
    def _exam_mode_on(user):
        try:
            from .. import feed_exam
            return bool(feed_exam.load_profile(user).exam_mode)
        except Exception:
            return True  # cannot tell -> do not break the Exam Mode promise

    def _note_first_feed(self, request, total, state):
        """H17: funnel - the first page-1 feed of this user (server-side, once; a cache flag keeps the 2nd..Nth
        request from touching the DB). Best effort, never raises."""
        if state or request.query_params.get('source') == 'following':
            return
        try:
            from django.core.cache import cache
            from core import funnel
            from .. import feed_coldstart

            user = request.user
            if not cache.add(f'funnel:first_feed:{user.pk}', 1, 24 * 3600):
                return
            funnel.record_first_feed(
                user, count=total, personalised=feed_coldstart.is_personalised(user),
                cold_start_backfill=getattr(self, '_cold_start_backfill', False),
            )
        except Exception:
            logger.warning("feed: first_feed funnel event failed", exc_info=True)

    def _rank_pools(self, request, state, snapshot_id, seen_cutoff, following_ids, ratios):
        """H10: the real ranking, behind the DB timeout and the single-flight lock.

        -> (pools, meta, ratios, snapshot_saved). pools is None when ranking failed / timed out; the caller then
        serves the fallback feed. Which lock applies:
          * new session, cache allowed  -> one builder per (user, ratio): racers read the 30 s candidate cache
          * cursor miss (snapshot gone) -> one builder per snapshot id: racers read the rebuilt snapshot
          * pull-to-refresh / pinned seen_cutoff -> no lock (these must rebuild by definition)"""
        from .. import feed_cache, feed_mix, feed_resilience, feed_snapshot

        user = request.user
        flt = self._filter()
        scope = flt.token or None  # H12: part of every cache / snapshot / lock key below
        rec_ratio = feed_mix.normalize_ratios(ratios).get(feed_mix.SOURCE_RECOMMENDED, 0.0)
        # T1 Part 5: short-TTL candidate cache. Skipped for pull-to-refresh (?refresh=1), for a pinned
        # seen_cutoff and for cursor rebuilds, whose pools must match the original session exactly.
        use_cache = (
            not state
            and not request.query_params.get('seen_cutoff')
            and request.query_params.get('refresh') not in ('1', 'true', 'True')
        )
        salt = feed_cache.salt_for(user)

        def build():
            # Seen ids are loaded ONCE and handed to build_pool_ids (window / cap from settings.FEED_SEEN_LIMITS).
            built_meta = {}
            with feed_resilience.statement_timeout():  # Postgres: cancel a runaway ranking query
                seen_ids = feed_mix.get_seen_post_ids(user, until=seen_cutoff)
                built = feed_mix.build_pool_ids(
                    user, self._base_qs(), following_ids, _video_and_velocity_boost,
                    seen_ids=seen_ids, ratios=ratios, meta=built_meta, category=flt.category or None,
                )
            if use_cache:
                feed_cache.put(user.pk, scope, rec_ratio, built, built_meta, salt)
            res = {"pools": built, "meta": built_meta, "ratios": ratios, "saved": False}
            if state:  # cursor rebuild: freeze it NOW so waiting racers find it
                res["saved"] = feed_snapshot.save(user.pk, snapshot_id, built, ratios, meta=built_meta, scope=scope)
            return res

        def probe_candidates():
            hit = feed_cache.get(user.pk, scope, rec_ratio, salt)
            return None if hit is None else {"pools": hit["pools"], "meta": hit["meta"], "ratios": ratios, "saved": False}

        def probe_snapshot():
            hit = feed_snapshot.load(user.pk, snapshot_id, scope=scope)
            return None if hit is None else {"pools": hit["pools"], "meta": hit["meta"], "ratios": hit["ratios"], "saved": True}

        def run():
            if use_cache:
                hit = probe_candidates()
                if hit is not None:
                    return hit
                return feed_resilience.single_flight(
                    feed_resilience.lock_key_for("cand", user.pk, salt, int(round(rec_ratio * 100)), scope or "-"),
                    probe_candidates, build,
                )
            if state:
                return feed_resilience.single_flight(
                    feed_resilience.lock_key_for("snap", user.pk, snapshot_id), probe_snapshot, build,
                )
            return build()

        result, failed = feed_resilience.guarded_build(run)
        if failed:
            return None, {}, ratios, False
        return result["pools"], result["meta"], result["ratios"], result["saved"]

    def _list_legacy(self, request, *args, **kwargs):
        """Old stateless page/offset path (pools rebuilt on every request)."""
        from rest_framework.utils.urls import replace_query_param, remove_query_param
        from .. import feed_mix

        paginator = self.paginator
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get(paginator.page_size_query_param, paginator.page_size))
        except (TypeError, ValueError):
            page_size = paginator.page_size
        page_size = max(1, min(page_size, paginator.max_page_size))

        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        self._following_ids = following_ids

        ratios = feed_mix.get_ratios(self._variant_overrides().get('mix'))
        if request.query_params.get('source') == 'following':
            # Following-first tab: only following has ratio; discovery is
            # used purely as a last-resort fill (see allocate_page).
            ratios = {feed_mix.SOURCE_FOLLOWING: 1.0}

        # PAGING STABILITY: pools are rebuilt on every request, so posts the
        # client marks as seen while scrolling (POST /post/feed/seen/) would
        # shrink/reorder them and shift page 2+. Page 1 fixes a `seen_cutoff`
        # (now); `next`/`previous` carry it along, and only PostView rows with
        # viewed_at <= cutoff count as seen for the whole session. Same code
        # path for source=mixed and source=following.
        seen_cutoff = feed_mix.resolve_seen_cutoff(request.query_params.get('seen_cutoff'))
        # Seen ids are loaded ONCE here and handed to build_pool_ids. Window /
        # cap / on-off switch come from settings.FEED_SEEN_LIMITS.
        seen_ids = feed_mix.get_seen_post_ids(request.user, until=seen_cutoff)

        meta = {}
        pools = feed_mix.build_pool_ids(
            request.user, self._base_qs(), following_ids, _video_and_velocity_boost,
            seen_ids=seen_ids, ratios=ratios, meta=meta, category=self._filter().category or None,
        )
        self._explore_id_strs = set(meta.get('explore_ids') or [])
        sizes = {src: len(ids) for src, ids in pools.items()}
        total = sum(sizes.values())
        slices = feed_mix.allocate_page(page, page_size, sizes, ratios)
        data = self._serialize_slices(pools, slices, ratios)

        consumed_after = sum(end for _, end in slices.values())
        has_next = consumed_after < total
        # `source`, `page_size`, ... are already part of the request URL, so
        # they are forwarded as-is; `seen_cutoff` is pinned on top of them.
        url = replace_query_param(
            request.build_absolute_uri(), 'seen_cutoff', feed_mix.format_seen_cutoff(seen_cutoff),
        )
        next_url = replace_query_param(url, 'page', page + 1) if has_next else None
        prev_url = None
        if page > 1:
            prev_url = replace_query_param(url, 'page', page - 1) if page > 2 else remove_query_param(url, 'page')
        return Response({
            'count': total,
            'next': next_url,
            'previous': prev_url,
            'results': data,
        })
