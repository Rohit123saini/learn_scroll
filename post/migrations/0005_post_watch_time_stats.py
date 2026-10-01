# Feed ranking, Part 1 (watch-time): Post.video_watch_count + Post.video_avg_watch_seconds,
# backfilled from the existing PostView watch-progress rows so already-ranked videos
# keep their boost the moment this ships.
from django.db import migrations, models


def backfill_watch_stats(apps, schema_editor):
    Post = apps.get_model('post', 'Post')
    PostView = apps.get_model('post', 'PostView')
    acc = {}  # post_id -> [ratio_sum, watched_sum, n]
    rows = (
        PostView.objects.filter(watch_seconds__isnull=False, video_duration_seconds__gt=0)
        .values_list('post_id', 'watch_seconds', 'video_duration_seconds')
        .iterator(chunk_size=2000)
    )
    for post_id, watched, duration in rows:
        a = acc.setdefault(post_id, [0.0, 0.0, 0])
        a[0] += min(watched / duration, 1.0)
        a[1] += min(watched, duration)
        a[2] += 1
    for post_id, (ratio_sum, watched_sum, n) in acc.items():
        Post.objects.filter(id=post_id, post_type='video').update(
            video_completion_rate=ratio_sum / n,
            video_watch_count=n,
            video_avg_watch_seconds=watched_sum / n,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0004_feedfeedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='video_watch_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='post',
            name='video_avg_watch_seconds',
            field=models.FloatField(default=0.0),
        ),
        migrations.RunPython(backfill_watch_stats, migrations.RunPython.noop),
    ]
