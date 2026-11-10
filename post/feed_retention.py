"""
post/feed_retention.py - H10: keep the three write-heavy feed tables from growing forever.

    table        what it is                              what we do                                   default
    -----------  --------------------------------------  -------------------------------------------  -----------
    PostEvent    append-only impression/dwell/tap/skip   DELETE rows older than N days (batched)      30 days
    PostView     one row per (post, user): "seen" marker DELETE *seen-only* rows (is_counted=False,    max(60 d,
                 (is_counted=False) or "opened"          no watch progress) older than the seen        seen window
                 (is_counted=True)                       window + margin                               + 7 d)
                                                         SCRUB ip_address / user_agent of old rows     90 days
                                                         (the row stays: it de-dupes `views_count`,
                                                         feeds post insights and video watch stats)
    "seen"       = PostView(is_counted=False), see above - there is no separate table.

WHY a plain batched DELETE and not partition-drop: partitioning `post_events` needs a primary key that contains the
partition column (a Django UUID `id` pk does not) = a table rewrite + model change. The batched, time-budgeted prune
below is safe today and keeps the table bounded; the day the volume says "partition", follow
deploy/monitoring/PARTITIONING.md (monthly RANGE partitions, drop-a-partition = instant, and this job becomes a
no-op for that table because it finds nothing old).

Why the seen retention can't be shorter than the seen window: `feed_mix.get_seen_post_ids` looks back
FEED_SEEN_LIMITS["window_days"] (30). Deleting rows younger than that would make already-seen posts reappear. The
job therefore never goes below `window_days + 7`.

Every function is bounded (`max_seconds`) and sleeps `pause_ms` between batches, so a huge backlog is worked off over
several nights instead of hogging the DB; `dry_run=True` only counts.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Dict, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULTS = {
    "post_event_days": 30,
    "seen_days": 60,               # floor is enforced: never below the feed's seen window + 7
    "post_view_pii_days": 90,      # ip_address / user_agent are blanked after this
    "batch_size": 5000,
    "pause_ms": 50,                # breathing room between batches
    "max_seconds": 900,            # per table, per run
}


def get_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(getattr(settings, "FEED_RETENTION", None) or {})
    for key in ("post_event_days", "seen_days", "post_view_pii_days", "batch_size", "pause_ms", "max_seconds"):
        try:
            cfg[key] = max(0, int(cfg[key]))
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
    cfg["batch_size"] = max(100, cfg["batch_size"])
    return cfg


def effective_seen_days(cfg: Optional[dict] = None) -> int:
    """Seen rows are kept at least `window_days + 7` days, whatever the setting says."""
    cfg = cfg or get_config()
    try:
        from .feed_mix import get_seen_limits

        window = int(get_seen_limits()["window_days"])
    except Exception:
        window = 30
    return max(cfg["seen_days"], window + 7)


def _batched(qs, op, cfg, dry_run: bool) -> Dict[str, object]:
    """Run `op(pk_batch)` over `qs` in pk batches until the queryset is empty or the time budget is used up.
    -> {"rows": n, "complete": bool}. `op` returns the number of rows it changed."""
    if dry_run:
        return {"rows": qs.count(), "complete": True}
    deadline = time.monotonic() + cfg["max_seconds"]
    total = 0
    while True:
        ids = list(qs.order_by().values_list("pk", flat=True)[: cfg["batch_size"]])
        if not ids:
            return {"rows": total, "complete": True}
        total += op(ids)
        if time.monotonic() >= deadline:
            return {"rows": total, "complete": False}  # the rest is picked up by the next run
        if cfg["pause_ms"]:
            time.sleep(cfg["pause_ms"] / 1000.0)


def prune_post_events(days: Optional[int] = None, dry_run: bool = False, batch_size: Optional[int] = None) -> Dict[str, object]:
    from .models import PostEvent

    cfg = get_config()
    if batch_size:
        cfg["batch_size"] = max(1, int(batch_size))
    days = cfg["post_event_days"] if days is None else days
    cutoff = timezone.now() - timedelta(days=days)
    qs = PostEvent.objects.filter(created_at__lt=cutoff)
    out = _batched(qs, lambda ids: PostEvent.objects.filter(pk__in=ids).delete()[0], cfg, dry_run)
    out.update(table="post_events", cutoff=cutoff.isoformat(), dry_run=dry_run)
    return out


def prune_seen_post_views(days: Optional[int] = None, dry_run: bool = False) -> Dict[str, object]:
    """Delete "seen in the feed" markers (is_counted=False, no watch progress) older than the seen window."""
    from .models import PostView

    cfg = get_config()
    days = effective_seen_days(cfg) if days is None else max(days, effective_seen_days(cfg))
    cutoff = timezone.now() - timedelta(days=days)
    qs = PostView.objects.filter(is_counted=False, watch_seconds__isnull=True, viewed_at__lt=cutoff)
    out = _batched(qs, lambda ids: PostView.objects.filter(pk__in=ids).delete()[0], cfg, dry_run)
    out.update(table="post_views(seen)", cutoff=cutoff.isoformat(), dry_run=dry_run)
    return out


def scrub_post_view_pii(days: Optional[int] = None, dry_run: bool = False) -> Dict[str, object]:
    """Blank ip_address / user_agent of old PostView rows. The row itself stays (views_count de-dupe, insights)."""
    from django.db.models import Q

    from .models import PostView

    cfg = get_config()
    days = cfg["post_view_pii_days"] if days is None else days
    cutoff = timezone.now() - timedelta(days=days)
    qs = PostView.objects.filter(viewed_at__lt=cutoff).filter(Q(ip_address__isnull=False) | Q(user_agent__isnull=False))
    out = _batched(
        qs, lambda ids: PostView.objects.filter(pk__in=ids).update(ip_address=None, user_agent=None), cfg, dry_run,
    )
    out.update(table="post_views(pii)", cutoff=cutoff.isoformat(), dry_run=dry_run)
    return out


def run_all(dry_run: bool = False) -> Dict[str, dict]:
    """Everything, one table after the other; a failure in one never stops the rest."""
    results: Dict[str, dict] = {}
    for name, fn in (("post_events", prune_post_events), ("seen_post_views", prune_seen_post_views),
                     ("post_view_pii", scrub_post_view_pii)):
        try:
            results[name] = fn(dry_run=dry_run)
        except Exception as exc:
            logger.error("feed_retention: %s failed", name, exc_info=True)
            results[name] = {"error": repr(exc)}
    logger.info("feed_retention%s: %s", " (dry run)" if dry_run else "", results)
    return results
