# Partitioning `post_events` (when the nightly prune is no longer enough)

**Not applied by H10.** The batched prune (`post/feed_retention.py`) keeps the table bounded and needs no downtime. Partition only
when `feed_retention` regularly reports `complete: false` (table grows faster than ~900 s of deleting per night) or autovacuum can't keep up.

Why it is a project and not a migration: PostgreSQL requires the partition key inside every unique key, but `PostEvent.id` is a UUID primary key
alone. So: new table + data copy + a model change (`pk` -> composite, or `managed = False`). Plan:

1. New table, monthly RANGE partitions on `created_at`:
   ```sql
   CREATE TABLE post_events_p (LIKE post_events INCLUDING DEFAULTS) PARTITION BY RANGE (created_at);
   ALTER TABLE post_events_p ADD PRIMARY KEY (id, created_at);
   CREATE INDEX ON post_events_p (post_id, event_type, created_at DESC);
   CREATE INDEX ON post_events_p (user_id, created_at DESC);
   CREATE TABLE post_events_2026_11 PARTITION OF post_events_p FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
   ```
2. Pre-create next month's partition from a monthly job (a missing partition = INSERT errors -> `POST /post/events/` 500s!). Alert on "no partition for next month".
3. Backfill the last 30 days in batches, swap names in one short transaction, point the model at it (`managed = False`, `db_table = "post_events"`).
4. Retention becomes `DROP TABLE post_events_YYYY_MM` (instant, no bloat). `prune_post_events` then finds nothing and is a no-op; keep it as a safety net.
5. `PostView` is NOT a partition candidate: unique `(post, user)` must hold across all time (it de-dupes `views_count`).
   Its "seen" rows are pruned by `prune_seen_post_views`; counted rows are small (one per viewer per post).
