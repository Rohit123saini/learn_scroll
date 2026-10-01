# TASK — Cursor pagination for engagement_score-ranked feeds

**Problem.** `order_by('-engagement_score')` + page/offset pagination: the score is an
annotation that changes while the user scrolls (new likes/comments, and the freshness boost
in `_video_and_velocity_boost()` steps down at 3h / 12h / 48h). Page N is "rows
20(N-1)..20N of a list that was re-sorted since page N-1", so posts jump up/down: a post
seen at the end of page 1 shows again on page 2, and other posts are skipped.

**Two ways to fix it** (the task allowed either): (a) cursor/keyset pagination, (b) precompute the
ranked feed in Redis. They are complementary, so the work is split in two parts.

---

## Part 1 — Cursor (keyset) pagination on the single-query ranked feeds  ✅ DONE

Scope: endpoints whose whole ranking is ONE SQL query ordered by `engagement_score`.

* `GET /post/explore/`  (`ExploreFeedAPIView`)
* `GET /post/hashtag/<tag>/`  (`HashtagPostsAPIView`)

Changes
* `common/pagination.py` — new `EngagementCursorPagination`. Sort key is the total order
  `(-engagement_score, -created_at, -id)`; the cursor is an opaque base64 token holding the last
  row's `(score, created_at, id)`. Next page = rows strictly after that key
  (`score < s OR (score = s AND created_at < c) OR (score = s AND created_at = c AND id < i)`).
  Fetches `page_size + 1` rows to decide `next` (no `COUNT(*)`). Bad cursor -> 404. `page_size`
  is clamped by `settings.MAX_PAGE_SIZE`.
* `post/views.py` — both views use `pagination_class = EngagementCursorPagination`.
* `post/tests_cursor_pagination.py` — codec, full-walk, score-changes-mid-scroll (the actual bug),
  ties, clamping, bad cursor, auth. (Hashtag test runs on PostgreSQL only: JSONField `contains`
  is unsupported on SQLite.)

API contract change (client must follow it)
* Request: `?cursor=<token>&page_size=N`. Do **not** send `page` (ignored).
* Response: `{ "next": url|null, "previous": null, "results": [...] }`.
  **`count` is gone** (that COUNT is what makes deep OFFSET slow) and there is no "previous page";
  the client just keeps following `next` (infinite scroll).
* Flutter: replace `page += 1` with "GET the `next` URL as returned"; stop when `next == null`.

Guarantees / limits (honest version)
* Post that is already on screen is never re-sent because its score changed; no post below the
  cursor is skipped because of *other* posts' score changes.
* A post whose own score moves ACROSS the cursor between two requests can still be missed
  (moves above) or appear later (moves below). That is inherent to live ranking; Part 2 removes it.
* Suggested index for Postgres at scale: the current `(-likes_count, -created_at)` index helps
  hashtag/explore only partially because the score is a computed expression.

## Part 2 — Frozen ranked snapshot in Redis + cursor for the Home feed  ✅ DONE

Scope: `HomeFeedView` (`GET /post/feed/`). It is not one SQL query: `feed_mix.build_pool_ids` builds 3
ordered id pools (following / recommended / trending) and used to slice them by page number after
re-ranking on EVERY request.

How it works now
1. **First request (no `cursor`)**: pools are built once (with the `seen_cutoff` logic unchanged) and
   stored in the Django cache — `django-redis` when `REDIS_URL` is set — as
   `feed:snap:<user_id>:<snapshot_id>` (JSON: ordered id lists + ratios), TTL
   `settings.FEED_SNAPSHOT["ttl_seconds"]` (default 900 s, env `FEED_SNAPSHOT_TTL`).
2. **Cursor** = base64url JSON `{v, s: snapshot_id, o: {following, recommended, trending offsets}, c: seen_cutoff}`.
   Next page = `feed_mix.allocate_next(offsets, page_size, sizes, ratios)` (one step, shares
   `_take_step` with the old `allocate_page`, so the 60/30/10 mix and the "empty pool refills from the
   others" behaviour are identical) over the FROZEN lists -> order can't change during the session, and
   page N costs one `id IN (...)` query instead of rebuilding three ranked pools. `page_size` may differ per
   request.
3. **Fallback, never a 500 / never a loop**: snapshot missing (TTL, flushed Redis, cache down, per-process
   LocMem with several workers) -> pools are rebuilt with the SAME `seen_cutoff` from the cursor, the stored
   offsets are applied, and the snapshot is saved again under the same id. (= the old best-effort behaviour.)
4. **Isolation**: key contains the user id; another account's cursor is just a miss for them.
5. Deleted/moderated posts between pages are skipped when the ids are loaded (`_base_qs()`), as before.

API (Home feed)
* Request: first call `GET /post/feed/?page_size=20[&source=following]`, then only follow `next`.
* Response: `{count, next, previous: null, results}`. `count` is now the frozen snapshot size (stable).
  `previous` is always `null` (infinite scroll). `next` carries `cursor` (and `seen_cutoff`, kept for old clients).
* Old apps: `?page=N` (N > 1) without a cursor still runs the old offset path (`_list_legacy`), so nothing
  breaks until the Flutter app is updated. `?page=1` behaves like the first request.
* Kill switch: `FEED_SNAPSHOT_ENABLED=0` -> fully back to page/offset.
* Bad/garbage cursor -> 404 (`Invalid cursor.`).

Files
* `post/feed_snapshot.py` (new) — cursor codec + cache save/load.
* `post/feed_mix.py` — `_take_step`, `allocate_next` (`allocate_page` refactored on top, same results).
* `post/views.py` — `HomeFeedView.list` dispatcher, `_list_snapshot`, `_list_legacy`, `_serialize_slices`.
* `LearnScroll/settings.py` — `FEED_SNAPSHOT`.
* `post/tests_feed_snapshot.py` (new); `post/tests_feed_mix_seen.py`: one assertion updated
  (`previous` is `None` on the cursor path).

Not done on purpose: pre-warming snapshots with Celery. A pre-built snapshot would make "pull to refresh"
return an older ranking; the on-demand build already costs the same as the old page 1. Revisit only if page-1
latency becomes a problem.
Production note: multi-worker deployments need a shared cache (Redis) — settings already refuses
`DEBUG=False` without `REDIS_URL`.
