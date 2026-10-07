"""
manage.py backfill_media_dimensions — TASK 1.1-BE.

Fills the NULL/0 `width`, `height` and `duration_seconds` of existing
PostMedia rows (images/GIFs: width+height; videos: width+height+duration;
audio: duration) so the feed can reserve the right frame size and reels know a
clip's length. Safe to re-run: it only selects rows that are still blank and
only ever fills blanks. A file that can't be probed (corrupt, ffprobe missing)
simply stays blank and is retried on the next run.

    python manage.py backfill_media_dimensions --dry-run
    python manage.py backfill_media_dimensions                 # queue to Celery
    python manage.py backfill_media_dimensions --sync --limit 500
"""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils.dateparse import parse_date, parse_datetime

from ...models import PostMedia


def _blank(field):
    return Q(**{f"{field}__isnull": True}) | Q(**{field: 0})


def candidate_queryset(before=None):
    """PostMedia rows still missing something we can probe for."""
    dims = _blank("width") | _blank("height")
    qs = PostMedia.objects.filter(
        (Q(media_type__in=("image", "gif")) & dims)
        | (Q(media_type="video") & (dims | _blank("duration_seconds")))
        | (Q(media_type="audio") & _blank("duration_seconds"))
    ).exclude(file="")
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    return qs


def parse_before(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is None:
            raise ValueError(value)
        from datetime import datetime, time as dtime
        from django.utils import timezone

        parsed = timezone.make_aware(datetime.combine(day, dtime.min))
    return parsed


class Command(BaseCommand):
    help = "Backfill width/height/duration_seconds for existing PostMedia (1.1-BE)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200,
                            help="Rows fetched per round (default 200).")
        parser.add_argument("--limit", type=int, default=None,
                            help="Stop after this many rows (default: everything).")
        parser.add_argument("--sleep", type=float, default=0.0,
                            help="Seconds to pause between batches.")
        parser.add_argument("--sync", action="store_true",
                            help="Probe in this process instead of queueing to Celery (no worker needed; slower).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Only report how many rows would be processed.")
        parser.add_argument("--before", default=None,
                            help="Only rows created before this date/time (ISO-8601).")

    def handle(self, *args, **opts):
        batch_size, limit, sleep = opts["batch_size"], opts["limit"], opts["sleep"]
        sync, dry_run = opts["sync"], opts["dry_run"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")
        if limit is not None and limit < 1:
            raise CommandError("--limit must be at least 1.")
        if sleep < 0:
            raise CommandError("--sleep can't be negative.")
        try:
            before = parse_before(opts["before"])
        except ValueError:
            raise CommandError("--before must be an ISO-8601 date or datetime, e.g. 2025-03-01 or 2025-03-01T10:30:00Z.")

        base = candidate_queryset(before=before)
        total = base.count()
        target = min(total, limit) if limit else total
        self.stdout.write(f"{total} media row(s) are missing dimensions/duration; will process {target}.")
        if dry_run:
            by_type = {}
            for kind in base.values_list("media_type", flat=True):
                by_type[kind] = by_type.get(kind, 0) + 1
            self.stdout.write("  by type: " + (", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "-"))
            self.stdout.write("Dry run — nothing queued or changed.")
            return
        if target == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        from ...services import fill_media_metadata
        from ...tasks import probe_media_metadata

        processed = filled = skipped = failed = 0
        cursor = None  # (created_at, id) of the last row handled
        started = time.monotonic()
        try:
            while processed < target:
                qs = base.order_by("-created_at", "-id")
                if cursor is not None:
                    ts, last_id = cursor
                    qs = qs.filter(Q(created_at__lt=ts) | Q(created_at=ts, id__lt=last_id))
                rows = list(qs[: min(batch_size, target - processed)])
                if not rows:
                    break
                for media in rows:
                    if sync:
                        try:
                            if fill_media_metadata(media, allow_download=True):
                                filled += 1
                            else:
                                skipped += 1  # unprobeable file (reason is in the logs)
                        except Exception as exc:
                            failed += 1
                            self.stderr.write(f"  failed {media.id}: {exc!r}")
                    else:
                        try:
                            probe_media_metadata.delay(str(media.id))
                            filled += 1  # = queued
                        except Exception as exc:
                            raise CommandError(
                                f"Could not queue to Celery ({exc!r}). Is the broker up? "
                                f"Re-run once it is — it only selects rows that are still blank."
                            )
                    cursor = (media.created_at, str(media.id))
                processed += len(rows)
                self.stdout.write(f"  {processed}/{target} done ({time.monotonic() - started:.0f}s)")
                if sleep and processed < target:
                    time.sleep(sleep)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nInterrupted — re-run to continue (only blank rows are selected)."))

        verb = "filled" if sync else "queued"
        self.stdout.write(self.style.SUCCESS(
            f"Finished: {filled} {verb}, {skipped} skipped, {failed} failed, of {processed} looked at."
        ))
