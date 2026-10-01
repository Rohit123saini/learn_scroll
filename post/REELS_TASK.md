# Reels feed - backend (Part 1 feed + Part 2 payload / settings / tests / Flutter contract)

Endpoint: `GET /post/reels/`  (`?start=<post_id>`, `?cursor=<opaque>`, `?page_size=`). Read-only.
Full reference + Flutter contract: `post_app.md` -> "Addendum - Reels feed".

## Payload (lean `ReelSerializer`, not `PostListSerializer`)
`id`, `video{url, thumbnail, duration, width, height, blur_hash}`, `caption`, `hashtags`,
`author{id, username, names, profile_photo, is_following}`, `counts{likes, comments, shares, saves}`,
`is_liked`, `my_reaction`, `is_saved`, `feed_source` (`following` | `recommended`).
Home's N+1 fixes are reused: one following-id query (`following_ids` context), prefetched media, plus two batched
viewer-state queries per page (reactions, saves). Query count is constant in the page size (tested).

## Candidates
`post_type='video'` (no reposts), approved, public, not sensitive, not deleted, not own, author not blocked,
`exclude_hidden_and_muted`. Video media needs a known `duration_seconds` (<= `FEED_REELS["max_duration_seconds"]`,
default 180) and `height/width >= FEED_REELS["min_aspect"]` (default 1.2; unknown dimensions allowed).

## Ranking (one pool, reuses feed_mix pieces)
engagement + `video_watch_boost` + velocity + interest + taste + friend-of-follow + author affinity - show-fewer
penalty; `following_bonus` (default 10) for followed authors. Seen videos excluded (top-up when the pool is tiny).

## Pagination
Cursor + frozen ranking per scrolling session (same idea as `feed_snapshot.py`, namespace `reels`), so the order does
not jump while swiping. `?start=<post_id>` puts that video first.

## Seen / actions
Reuse `POST /post/feed/seen/` (seen) and `POST /post/<id>/video-progress/` (watch time). No new action endpoints.

## Settings `FEED_REELS`
`enabled`, `min_aspect`, `max_duration_seconds`, `following_bonus`, `page_size` (+ internal `pool_cap`,
`max_page_size`). Env: `REELS_ENABLED`, `REELS_MIN_ASPECT`, `REELS_MAX_DURATION_SECONDS`, `REELS_FOLLOWING_BONUS`,
`REELS_PAGE_SIZE`, `REELS_POOL_CAP`. (Renamed from `settings.REELS`.)

## Files
- `post/reels.py`          config, candidates, ranking, start handling, `page_queryset`, `viewer_state`
- `post/reels_views.py`    `ReelsFeedView`
- `post/serializers.py`    `ReelSerializer`, `reel_video_media`
- `post/feed_snapshot.py`  namespaced single-list snapshot + list cursor
- `post/urls.py`           `reels/` -> name `reels-feed`
- `LearnScroll/settings.py` `FEED_REELS = {...}` (+ env overrides)
- `post/tests_reels.py`    78 tests
- `lib/post/services/reels_service.dart`  Flutter service + models (no UI)

## Flutter contract
See `post_app.md`. Summary: first page without cursor (optional `?start=`), then follow `next` verbatim, 404 on a
cursor -> new session, skip reels with `video == null`, report seen in batches via `/post/feed/seen/`.

## Not in this part
Flutter Reels screen (vertical PageView, `?start=` from profile / Home, `video-progress` + `feed/seen` reporting).
