"""
manage.py backfill_thumbnails - T6.

Poster frames (thumbnails) for OLD video posts that have none, plus a pipeline report.

    python manage.py backfill_thumbnails --report            # counts only: thumbnail / HLS / ready / failed
    python manage.py backfill_thumbnails --dry-run           # list what would be generated
    python manage.py backfill_thumbnails --sync --limit 50   # generate in this process (no Celery worker needed)
    python manage.py backfill_thumbnails                     # queue to Celery
    python manage.py backfill_thumbnails --media-id <uuid> --sync
    python manage.py backfill_thumbnails --blurhash --sync   # P-E2: BlurHash (from the poster) for videos that have a poster but no BlurHash

The frame is taken at 1 s, then 0.5 s, then frame 0 (post.services.generate_video_thumbnail_file), so
very short videos get a poster too.
"""
import shutil

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from ...models import PostMedia


class Command(BaseCommand):
    help = "Generate missing video thumbnails (poster frames) for old posts; --report shows thumbnail/HLS coverage."

    def add_arguments(self, parser):
        parser.add_argument("--report", action="store_true", help="Only print coverage counts (thumbnail, HLS, status).")
        parser.add_argument("--media-id", help="Only this PostMedia id.")
        parser.add_argument("--limit", type=int, default=0, help="Stop after N videos (0 = no limit).")
        parser.add_argument("--sync", action="store_true", help="Run here instead of queueing to Celery.")
        parser.add_argument("--dry-run", action="store_true", help="Only list what would be generated.")
        parser.add_argument(
            "--blurhash", action="store_true",
            help="P-E2: only videos that HAVE a poster but no BlurHash yet (the BlurHash is made from the poster; no ffmpeg needed).",
        )

    def handle(self, *args, **opts):
        videos = PostMedia.objects.filter(media_type="video").exclude(file="")
        if opts["report"]:
            return self._report(videos)

        if opts["media_id"]:
            qs = videos.filter(pk=opts["media_id"])
        elif opts["blurhash"]:
            qs = videos.exclude(thumbnail="").filter(Q(blur_hash__isnull=True) | Q(blur_hash=""))
        else:
            qs = videos.filter(thumbnail="")
        qs = qs.order_by("created_at")
        if opts["limit"] > 0:
            qs = qs[: opts["limit"]]
        ids = [str(pk) for pk in qs.values_list("pk", flat=True)]
        self.stdout.write(f"{len(ids)} video(s) without {'BlurHash' if opts['blurhash'] else 'thumbnail'}.")
        if opts["dry_run"] or not ids:
            return
        if opts["sync"] and not opts["blurhash"] and shutil.which("ffmpeg") is None:
            raise CommandError("ffmpeg not found on PATH - install ffmpeg first.")

        from ...tasks import generate_video_thumbnail

        done = failed = 0
        for i, media_id in enumerate(ids, 1):
            if not opts["sync"]:
                generate_video_thumbnail.delay(media_id)
                continue
            generate_video_thumbnail.apply(args=[media_id]).get(propagate=False)
            if opts["blurhash"]:
                has = PostMedia.objects.filter(pk=media_id).exclude(Q(blur_hash__isnull=True) | Q(blur_hash="")).exists()
            else:
                has = PostMedia.objects.filter(pk=media_id).exclude(thumbnail="").exists()
            done += 1 if has else 0
            failed += 0 if has else 1
            self.stdout.write(f"[{i}/{len(ids)}] {media_id}: {'ok' if has else 'no thumbnail'}")
        if opts["sync"]:
            self.stdout.write(self.style.SUCCESS(f"Done: ok={done}, failed={failed}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Queued {len(ids)} video(s) to Celery."))

    def _report(self, videos):
        total = videos.count()
        no_thumb = videos.filter(thumbnail="").count()
        no_hls = videos.filter(hls_playlist="").count()
        self.stdout.write(f"videos total          : {total}")
        self.stdout.write(f"  without thumbnail   : {no_thumb}  (fix: backfill_thumbnails --sync)")
        no_hash = videos.exclude(thumbnail="").filter(Q(blur_hash__isnull=True) | Q(blur_hash="")).count()
        self.stdout.write(f"  poster, no BlurHash : {no_hash}  (fix: backfill_thumbnails --blurhash --sync)")
        self.stdout.write(f"  without HLS         : {no_hls}  (fix: process_videos --hls --sync)")
        for status, label in (
            (PostMedia.PROCESSING_PENDING, "pending"),
            (PostMedia.PROCESSING_RUNNING, "processing"),
            (PostMedia.PROCESSING_READY, "ready"),
            (PostMedia.PROCESSING_FAILED, "failed"),
        ):
            self.stdout.write(f"  status {label:<11}: {videos.filter(processing_status=status).count()}")
        self.stdout.write("Not faststart (moov at end) videos are fixed by: process_videos --all --sync")
