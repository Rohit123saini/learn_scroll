"""
post/management/commands/backfill_image_variants.py

C4-BE — generate the 320px / 720px variants + BlurHash for image `PostMedia`
rows that don't have them yet: every post uploaded BEFORE C4-BE shipped, plus
anything whose Celery enqueue was lost (broker outage, worker killed).

    python manage.py backfill_image_variants --dry-run          # just count
    python manage.py backfill_image_variants                    # enqueue to Celery, newest first
    python manage.py backfill_image_variants --batch-size 100 --sleep 2
    python manage.py backfill_image_variants --sync --limit 500 # run inline, no worker needed
    python manage.py backfill_image_variants --force            # regenerate even finished rows

Safe to run on a live system and to run repeatedly:
  * the work itself is `post.tasks.generate_image_variants`, which is idempotent
    (a finished row is skipped) and writes all outputs atomically;
  * default mode only selects rows still missing a variant or a BlurHash, so an
    interrupted run is resumed simply by running it again;
  * newest posts go first (that's what feeds actually show);
  * rows are walked with a (created_at, id) keyset cursor, never OFFSET, so it
    stays fast on a big table and can't skip or repeat rows while others finish;
  * `--batch-size` / `--sleep` meter how fast work is queued, so millions of old
    images can't bury the live upload queue.

Images Pillow can't decode (corrupt/not-an-image) never get variants, so they
are re-tried (and skipped with a log line) on every run — harmless, just noisy.
"""
import time
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from ...models import PostMedia

IMAGE_TYPES = ("image", "gif")


def parse_before(value):
    """`--before` as an aware datetime. Accepts '2025-03-01' or any ISO-8601
    datetime (a trailing 'Z' is fine); naive values are read in the project's
    current timezone. Raises ValueError on garbage."""
    if value is None:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)  # ValueError if invalid
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def candidate_queryset(force=False, before=None):
    """Image/gif rows that need work. Without `force`: only rows missing the
    320px thumb or the BlurHash (`generate_image_variants` skips a row that has
    both, so anything else would be a no-op round-trip)."""
    qs = PostMedia.objects.filter(media_type__in=IMAGE_TYPES).exclude(file="")
    if not force:
        qs = qs.filter(
            Q(thumb_320__isnull=True) | Q(thumb_320="") | Q(blur_hash__isnull=True) | Q(blur_hash="")
        )
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    return qs


class Command(BaseCommand):
    help = "Backfill 320px/720px image variants and BlurHash for existing PostMedia (C4-BE)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200,
                            help="Rows fetched/queued per round (default 200).")
        parser.add_argument("--limit", type=int, default=None,
                            help="Stop after this many rows (default: everything).")
        parser.add_argument("--sleep", type=float, default=0.0,
                            help="Seconds to pause between batches, to go easy on the queue/storage.")
        parser.add_argument("--sync", action="store_true",
                            help="Process in this process instead of queueing to Celery (no worker needed; slower).")
        parser.add_argument("--force", action="store_true",
                            help="Regenerate rows that already have variants (old files are deleted).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Only report how many rows would be processed.")
        parser.add_argument("--before", default=None,
                            help="Only rows created before this date/time (ISO-8601). Used to resume a --force run.")

    def handle(self, *args, **opts):
        batch_size, limit, sleep = opts["batch_size"], opts["limit"], opts["sleep"]
        force, sync, dry_run = opts["force"], opts["sync"], opts["dry_run"]
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

        base = candidate_queryset(force=force, before=before)
        total = base.count()
        target = min(total, limit) if limit else total
        mode = "regenerate ALL" if force else "missing only"
        self.stdout.write(f"{total} image row(s) match ({mode}); will process {target}.")

        if dry_run:
            self.stdout.write("Dry run — nothing queued or changed.")
            return
        if target == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        from ...tasks import generate_image_variants

        processed = generated = skipped = failed = 0
        failed_ids = []
        cursor = None  # (created_at, id) of the last row handled
        started = time.monotonic()
        try:
            while processed < target:
                qs = base.order_by("-created_at", "-id")
                if cursor is not None:
                    ts, last_id = cursor
                    qs = qs.filter(Q(created_at__lt=ts) | Q(created_at=ts, id__lt=last_id))
                rows = list(qs.values_list("id", "created_at")[: min(batch_size, target - processed)])
                if not rows:
                    break

                for media_id, created_at in rows:
                    media_id = str(media_id)
                    if sync:
                        try:
                            if generate_image_variants.run(media_id, force=force):
                                generated += 1
                            else:
                                skipped += 1  # unprocessable / already done / vanished (task logged why)
                        except Exception as exc:
                            failed += 1
                            failed_ids.append(media_id)
                            self.stderr.write(f"  failed {media_id}: {exc!r}")
                    else:
                        try:
                            generate_image_variants.delay(media_id, force=force)
                            generated += 1  # = queued
                        except Exception as exc:
                            raise CommandError(
                                f"Could not queue to Celery ({exc!r}). Is the broker up? "
                                f"Re-run once it is — default mode picks up where this stopped."
                            )
                    cursor = (created_at, media_id)

                processed += len(rows)
                self.stdout.write(f"  {processed}/{target} done ({time.monotonic() - started:.0f}s)")
                if sleep and processed < target:
                    time.sleep(sleep)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nInterrupted."))
            if force and cursor is not None:
                self.stdout.write(f"Resume this --force run with:  --force --before {cursor[0].isoformat()}")
            else:
                self.stdout.write("Just run the command again — it only selects rows that are still missing variants.")

        verb = "processed" if sync else "queued"
        self.stdout.write(self.style.SUCCESS(
            f"Finished: {generated} {verb}, {skipped} skipped, {failed} failed, of {processed} looked at."
        ))
        if failed_ids:
            self.stdout.write("Failed ids (first 20): " + ", ".join(failed_ids[:20]))
        if not sync:
            self.stdout.write("Queued jobs are being worked by the Celery workers; re-run --dry-run later to see what's left.")
