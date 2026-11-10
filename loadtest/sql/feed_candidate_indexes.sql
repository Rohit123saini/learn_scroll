-- H2: CANDIDATE indexes for the feed. NOT applied by any migration.
--
-- Reading the code found no clearly missing index: get_seen_post_ids is already served by post_views_user_viewed_idx
-- (user_id, viewed_at DESC), the exclusion sub-selects use the unique (user, post) / (user, muted_user) /
-- (blocker, blocked) indexes, and the reposts check uses (original_post, created_at DESC).
-- `posts` is the hottest write table and already has 7 indexes + 6 single-column ones, so add one ONLY when
-- `manage.py feed_explain_analyze` shows the signal named under it. Always CONCURRENTLY, outside a transaction:
-- run each statement on its own in psql (not inside BEGIN, not through a normal atomic migration).
-- Then re-run feed_explain_analyze + the Locust run and keep the index only if the plan/p95 improved.

-- A) Discovery pools (trending 7 d, recommended 30 d, exploration, "older" fallback).
--    Signal: `Seq Scan on posts` (or a huge "Rows Removed by Filter") in the trending/recommended statement.
--    The predicate text must match what Django emits (`col = false`), otherwise the planner will not use it.
CREATE INDEX CONCURRENTLY IF NOT EXISTS posts_feed_public_recent_idx
    ON posts (created_at DESC)
    WHERE visibility = 'public' AND is_deleted = false AND is_archived = false
      AND is_scheduled = false AND is_sensitive = false AND moderation_status = 'approved';

-- B) Following pool (user_id IN (followed ids), newest first).
--    Signal: the following statement does Seq Scan on posts or a Bitmap Heap Scan that removes most rows,
--    for users who follow many accounts.
CREATE INDEX CONCURRENTLY IF NOT EXISTS posts_feed_user_recent_idx
    ON posts (user_id, created_at DESC)
    WHERE is_deleted = false AND is_archived = false AND is_scheduled = false
      AND is_sensitive = false AND moderation_status = 'approved';

-- C) Seen ids, index-only. Signal: get_seen_post_ids plan shows "Heap Fetches" in the thousands.
--    post_views is write-heavy (every feed scroll), so its visibility map is rarely clean - often NOT worth it.
CREATE INDEX CONCURRENTLY IF NOT EXISTS post_views_user_viewed_cover_idx
    ON post_views (user_id, viewed_at DESC) INCLUDE (post_id);

-- Roll back an index that did not help:
--   DROP INDEX CONCURRENTLY IF EXISTS posts_feed_public_recent_idx;
--   DROP INDEX CONCURRENTLY IF EXISTS posts_feed_user_recent_idx;
--   DROP INDEX CONCURRENTLY IF EXISTS post_views_user_viewed_cover_idx;
--
-- To keep one permanently, add it to Post.Meta.indexes / PostView.Meta.indexes as
--   models.Index(fields=['-created_at'], name='posts_feed_public_recent_idx', condition=Q(visibility='public', is_deleted=False, ...))
-- and ship it with django.contrib.postgres.operations.AddIndexConcurrently in a migration with `atomic = False`
-- (guard it for non-Postgres: the dev/test DB is sqlite when DATABASE_URL is unset).
