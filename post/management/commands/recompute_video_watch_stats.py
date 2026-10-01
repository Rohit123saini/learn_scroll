"""Recompute Post.video_completion_rate / video_watch_count / video_avg_watch_seconds
from the PostView watch-progress rows (same numbers the post_save signal maintains).

    python manage.py recompute_video_watch_stats            # all videos with watch data
    python manage.py recompute_video_watch_stats --reset    # also zero videos that have none

Migration 0005 already backfills once; run this after bulk-importing PostView rows,
or as a periodic safety net.
"""
from django.core.management.base import BaseCommand

from post.models import Post, PostView


class Command(BaseCommand):
    help = "Recompute video watch-time stats on posts from PostView rows."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Also set the stats of videos WITHOUT watch data back to 0.")

    def handle(self, *args, **opts):
        acc = {}
        rows = (
            PostView.objects.filter(watch_seconds__isnull=False, video_duration_seconds__gt=0)
            .values_list("post_id", "watch_seconds", "video_duration_seconds")
            .iterator(chunk_size=2000)
        )
        for post_id, watched, duration in rows:
            a = acc.setdefault(post_id, [0.0, 0.0, 0])
            a[0] += min(watched / duration, 1.0)
            a[1] += min(watched, duration)
            a[2] += 1
        updated = 0
        for post_id, (ratio_sum, watched_sum, n) in acc.items():
            updated += Post.objects.filter(id=post_id, post_type="video").update(
                video_completion_rate=ratio_sum / n, video_watch_count=n, video_avg_watch_seconds=watched_sum / n,
            )
        reset = 0
        if opts["reset"]:
            reset = (
                Post.objects.filter(post_type="video").exclude(id__in=list(acc))
                .update(video_completion_rate=0.0, video_watch_count=0, video_avg_watch_seconds=0.0)
            )
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} video(s); reset {reset}."))
