# tuitionclass/models/cache_versions.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import logging
from django.core.cache import cache
from django.utils import timezone


# ---------------------------------------------------------------------------
# CLASSROOM LIST CACHE VERSIONING
#
# The public Explore/search list (ClassroomViewSet.list, see views.py) is by
# far the highest-traffic read in this app — every visitor hits it, often
# while just browsing before ever buying a pass — yet it was hitting the DB
# (search/filter/order/paginate over every Classroom row) on every single
# request. Caching the rendered page is the obvious fix, but a flat TTL
# cache would show stale ratings/enrolled-counts/newly-posted classrooms for
# up to the TTL window, which is a bad tradeoff for a marketplace listing.
#
# Instead: a single cache-versioning counter. Every request builds its cache
# key as f"...:{version}:{query_string}"; the view never needs to know which
# rows changed, it just needs to know that SOMETHING did. Any change that
# could affect what the list looks like bumps the version once, which
# invalidates every previously-cached page in O(1) — no per-classroom key
# tracking, no partial-invalidation bugs.
#
# bump_classroom_list_cache_version() is called from a single Classroom
# post_save/post_delete receiver at the bottom of this file. That alone
# covers every case that matters: direct API edits, AND refresh_rating() /
# refresh_enrolled_count() / sync_flag_status() below, since all three work
# by calling self.save(update_fields=[...]) on the Classroom itself, which
# already fires Classroom's own post_save signal — no extra wiring needed
# at each call site.
# ---------------------------------------------------------------------------
CLASSROOM_LIST_CACHE_VERSION_KEY = "tuitionclass:classroom_list:cache_version"


def get_classroom_list_cache_version() -> int:
    version = cache.get(CLASSROOM_LIST_CACHE_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(CLASSROOM_LIST_CACHE_VERSION_KEY, version, timeout=None)
    return version


def bump_classroom_list_cache_version() -> None:
    try:
        cache.incr(CLASSROOM_LIST_CACHE_VERSION_KEY)
    except ValueError:
        # Key doesn't exist yet (cold cache / cache was cleared) — seed it.
        cache.set(CLASSROOM_LIST_CACHE_VERSION_KEY, 2, timeout=None)


# ---------------------------------------------------------------------------
# NOTE (fix — classroom stats realtime push): classroom_detail_screen.dart
# used to refresh enrolled_count/rating with a plain `Timer.periodic(30s)`
# GET, since there was no realtime push for it the way there is for
# join-request badges (see TuitionClassUserSocket / broadcast_to_user). This
# gives it one, following the exact same pattern: a per-CLASSROOM channel
# group (as opposed to broadcast_to_user's per-user one, or
# broadcast_to_session's per-live-session one) — `ClassroomConsumer` on a
# `ws/tuitionclass/classroom/<id>/` route, mirroring `UserConsumer` /
# `ws/tuitionclass/user/` in consumers.py/routing.py, and a `broadcast_to_classroom()`
# in realtime.py mirroring that module's existing `broadcast_to_user()`.
#
# Hooked at the two places Classroom.rating_avg/rating_count/enrolled_count
# actually change (refresh_rating/refresh_enrolled_count below) rather than
# at every call site that triggers them (PassPurchase/ClassroomReview save
# signals at the bottom of this file) — same one-place-covers-everything
# reasoning the cache-versioning block above already uses for this exact
# pair of methods.
#
# Best-effort / never-block-the-save, same contract as views.py's
# _safe_broadcast_to_user — a channel-layer hiccup degrades to "no live
# push, the screen's fallback poll (now a much longer interval, kept only
# as a correctness backstop) still catches it" rather than ever breaking
# the actual rating/enrollment recompute this runs after.
# ---------------------------------------------------------------------------
def _broadcast_classroom_stats(classroom) -> None:
    try:
        from ..realtime import broadcast_to_classroom

        broadcast_to_classroom(
            classroom.id,
            "classroom.stats",
            {
                "classroom_id": classroom.id,
                "rating_avg": float(classroom.rating_avg),
                "rating_count": classroom.rating_count,
                "enrolled_count": classroom.enrolled_count,
            },
        )
    except Exception:
        logging.getLogger("tuitionclass.models").exception(
            "Failed to push realtime stats for classroom %s "
            "(broadcast_to_classroom missing/unreachable?) — recompute "
            "still succeeds; frontend falls back to its backstop poll.",
            classroom.id,
        )


# ---------------------------------------------------------------------------
# PER-CLASSROOM NOTICE LIST CACHE VERSIONING
#
# NOTE (perf — fix): NoticeViewSet.list is read far more often than it's
# written (every enrolled student re-checks a classroom's notice board
# regularly; a teacher posts a handful of notices total). It was hitting
# the DB on every single request with no caching at all — same shape of gap
# as the Classroom Explore/search list before it got the version-based
# cache above, just scoped to one classroom's notices instead of the whole
# platform. Same pattern reused here rather than inventing a new one: one
# version counter PER CLASSROOM (not global — bumping one classroom's
# notices shouldn't invalidate every other classroom's cached notice page),
# bumped from a single Notice post_save/post_delete receiver below.
# ---------------------------------------------------------------------------
def _notice_list_cache_version_key(classroom_id) -> str:
    return f"tuitionclass:notice_list:cache_version:{classroom_id}"


def get_notice_list_cache_version(classroom_id) -> int:
    key = _notice_list_cache_version_key(classroom_id)
    version = cache.get(key)
    if version is None:
        version = 1
        cache.set(key, version, timeout=None)
    return version


def bump_notice_list_cache_version(classroom_id) -> None:
    key = _notice_list_cache_version_key(classroom_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, timeout=None)


# ---------------------------------------------------------------------------
# NOTE (fix — expired notices lingering in the cache): the version counter
# above only bumps on Notice post_save/post_delete — it has no idea that
# time itself just passed, so a notice whose expires_at has come and gone
# kept showing up in an already-cached page until either (a) someone
# created/edited/deleted *any* notice in that classroom, which bumps the
# version and busts every cached page for it, or (b) NoticeViewSet's own
# belt-and-braces LIST_CACHE_TTL_SECONDS finally lapsed. That left a real,
# reproducible window (up to LIST_CACHE_TTL_SECONDS) where an expired
# notice visibly kept showing on a quiet classroom's board.
#
# Fix: let the CALLER (NoticeViewSet.list) ask "what's the soonest an
# active notice in this classroom will expire?" and cap its cache write's
# timeout to that, instead of always using the flat default. That way the
# cache entry self-invalidates at (or just after) the moment a notice
# actually needs to disappear, with no extra signal, cron, or task needed
# — same DB, just one small indexed aggregate query per cache miss.
# ---------------------------------------------------------------------------
def seconds_until_next_notice_expiry(classroom_id, default_seconds: int) -> int:
    """Returns `default_seconds`, or fewer if some non-expired notice in this
    classroom expires sooner than that — never more than `default_seconds`,
    never less than 1 (so a cache write always gets a positive timeout)."""
    soonest = (
        Notice.objects.filter(classroom_id=classroom_id, expires_at__gt=timezone.now())
        .order_by("expires_at")
        .values_list("expires_at", flat=True)
        .first()
    )
    if soonest is None:
        return default_seconds
    seconds_left = (soonest - timezone.now()).total_seconds()
    return max(1, min(default_seconds, int(seconds_left) + 1))



# Forward references: used only inside method/function bodies, defined in a LATER module. Bound by models/__init__.py
# once every module is loaded (see _link_forward_refs there). `None` here only keeps linters quiet.
Notice = None  # -> .notices_queries
