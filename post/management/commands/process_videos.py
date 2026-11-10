"""
manage.py process_videos — V1 ("feed me video nahi chal rahi").

Converts uploaded videos to the one format every phone plays — MP4 / H.264 /
AAC / `+faststart` — using the same job that runs automatically after every new
upload (`post.tasks.process_video_for_streaming`). Use it for:

  * videos uploaded BEFORE this feature existed (phone-camera HEVC clips that
    don't play on some Androids):          process_videos --all
  * dev setups with no Celery worker running (the automatic job never ran, so
    new videos stay `pending`):             process_videos --sync
  * retrying rows the job marked `failed`:  process_videos --failed --sync
  * seek-bar preview for old videos:        process_videos --sprite --sync

Safe to re-run: a video that already plays everywhere is only probed (one
ffprobe call), never re-encoded. Needs `ffmpeg` + `ffprobe` on PATH.

    python manage.py process_videos --dry-run
    python manage.py process_videos --sync                 # pending/processing rows, inline
    python manage.py process_videos --all --sync --limit 50
    python manage.py process_videos --media-id <uuid> --sync
    python manage.py process_videos --all                  # queue to Celery instead
    python manage.py process_videos --sprite --sync        # thumbnail sprite for ready videos that have none
"""
import shutil

from django.core.management.base import BaseCommand, CommandError

from ...models import PostMedia


class Command(BaseCommand):
    help = "Convert videos to H.264/AAC + faststart so they play on every phone (V1)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="Check EVERY video, including ones already marked ready (old uploads).")
        parser.add_argument("--failed", action="store_true",
                            help="Only rows a previous run marked failed.")
        parser.add_argument("--media-id", help="Process just this PostMedia id.")
        parser.add_argument("--limit", type=int, default=0, help="Stop after N videos (0 = no limit).")
        parser.add_argument("--sync", action="store_true",
                            help="Run in this process instead of queueing to Celery (no worker needed).")
        parser.add_argument("--dry-run", action="store_true", help="Only list what would be processed.")
        parser.add_argument("--hls", action="store_true",
                            help="Only build the HLS rendition (360p/540p/720p) for ready videos that have none yet "
                                 "(no MP4 conversion). Add --all to rebuild existing ones.")
        parser.add_argument("--sprite", action="store_true",
                            help="Only build the seek-bar thumbnail sprite for ready videos that have none yet "
                                 "(no MP4 conversion). Add --all to rebuild existing ones.")

    def handle(self, *args, **opts):
        qs = PostMedia.objects.filter(media_type="video").exclude(file="").order_by("created_at")
        if opts["hls"] and opts["sprite"]:
            raise CommandError("Use --hls and --sprite one at a time.")
        if opts["hls"]:
            return self._handle_hls(qs, opts)
        if opts["sprite"]:
            return self._handle_sprite(qs, opts)
        if opts["media_id"]:
            qs = qs.filter(pk=opts["media_id"])
        elif opts["failed"]:
            qs = qs.filter(processing_status=PostMedia.PROCESSING_FAILED)
        elif not opts["all"]:
            qs = qs.filter(processing_status__in=(PostMedia.PROCESSING_PENDING, PostMedia.PROCESSING_RUNNING))
        if opts["limit"] > 0:
            qs = qs[: opts["limit"]]
        ids = [str(pk) for pk in qs.values_list("pk", flat=True)]
        self.stdout.write(f"{len(ids)} video(s) selected.")
        if opts["dry_run"] or not ids:
            return

        if opts["sync"] and (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None):
            raise CommandError("ffmpeg/ffprobe not found on PATH — install ffmpeg first.")

        from ...tasks import process_video_for_streaming

        results = {}
        for i, media_id in enumerate(ids, 1):
            if opts["sync"]:
                # Call the task body directly (no broker needed). force=True so
                # rows already marked `ready` are still probed with --all.
                outcome = process_video_for_streaming.apply(args=[media_id, True]).get(propagate=False)
                outcome = outcome if isinstance(outcome, str) else "error"
                self.stdout.write(f"[{i}/{len(ids)}] {media_id}: {outcome}")
                results[outcome] = results.get(outcome, 0) + 1
            else:
                PostMedia.objects.filter(pk=media_id).update(processing_status=PostMedia.PROCESSING_PENDING)
                process_video_for_streaming.delay(media_id, True)
        if opts["sync"]:
            self.stdout.write(self.style.SUCCESS("Done: " + ", ".join(f"{k}={v}" for k, v in sorted(results.items()))))
        else:
            self.stdout.write(self.style.SUCCESS(f"Queued {len(ids)} video(s) to Celery."))

    def _handle_hls(self, qs, opts):
        if opts["media_id"]:
            qs = qs.filter(pk=opts["media_id"])
        else:
            qs = qs.filter(processing_status=PostMedia.PROCESSING_READY)
            if not opts["all"]:
                qs = qs.filter(hls_playlist="")
        if opts["limit"] > 0:
            qs = qs[: opts["limit"]]
        ids = [str(pk) for pk in qs.values_list("pk", flat=True)]
        self.stdout.write(f"{len(ids)} video(s) selected for HLS.")
        if opts["dry_run"] or not ids:
            return
        if opts["sync"] and (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None):
            raise CommandError("ffmpeg/ffprobe not found on PATH — install ffmpeg first.")
        from ...tasks import build_hls_rendition

        force = bool(opts["all"] or opts["media_id"])
        results = {}
        for i, media_id in enumerate(ids, 1):
            if opts["sync"]:
                outcome = build_hls_rendition.apply(args=[media_id, force]).get(propagate=False)
                outcome = outcome if isinstance(outcome, str) else "error"
                self.stdout.write(f"[{i}/{len(ids)}] {media_id}: {outcome}")
                results[outcome] = results.get(outcome, 0) + 1
            else:
                build_hls_rendition.delay(media_id, force)
        if opts["sync"]:
            self.stdout.write(self.style.SUCCESS("Done: " + ", ".join(f"{k}={v}" for k, v in sorted(results.items()))))
        else:
            self.stdout.write(self.style.SUCCESS(f"Queued {len(ids)} video(s) to Celery."))

    def _handle_sprite(self, qs, opts):
        if opts["media_id"]:
            qs = qs.filter(pk=opts["media_id"])
        else:
            qs = qs.filter(processing_status=PostMedia.PROCESSING_READY)
        # the sprite lives in `metadata["sprite"]` (JSON, no column), so "has none yet" is checked per row below
        rebuild = bool(opts["all"] or opts["media_id"])
        ids = []
        for media in qs.only("id", "metadata"):
            if not rebuild and media.metadata and isinstance(media.metadata.get("sprite"), dict) and media.metadata["sprite"].get("name"):
                continue
            ids.append(str(media.pk))
            if opts["limit"] > 0 and len(ids) >= opts["limit"]:
                break
        self.stdout.write(f"{len(ids)} video(s) selected for the thumbnail sprite.")
        if opts["dry_run"] or not ids:
            return
        if opts["sync"] and (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None):
            raise CommandError("ffmpeg/ffprobe not found on PATH — install ffmpeg first.")
        from ...tasks import build_video_sprite

        results = {}
        for i, media_id in enumerate(ids, 1):
            if opts["sync"]:
                outcome = build_video_sprite.apply(args=[media_id, rebuild]).get(propagate=False)
                outcome = outcome if isinstance(outcome, str) else "error"
                self.stdout.write(f"[{i}/{len(ids)}] {media_id}: {outcome}")
                results[outcome] = results.get(outcome, 0) + 1
            else:
                build_video_sprite.delay(media_id, rebuild)
        if opts["sync"]:
            self.stdout.write(self.style.SUCCESS("Done: " + ", ".join(f"{k}={v}" for k, v in sorted(results.items()))))
        else:
            self.stdout.write(self.style.SUCCESS(f"Queued {len(ids)} video(s) to Celery."))
