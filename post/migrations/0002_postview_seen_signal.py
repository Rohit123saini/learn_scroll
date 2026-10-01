from django.db import migrations, models
from django.db.models import Count, F


DELETE_CHUNK = 500


def dedupe_postviews(apps, schema_editor):
    """Collapse duplicate (post, user) PostView rows down to one.

    Survivor per group: the row with the furthest watch progress
    (`watch_seconds`, NULLs last), then the earliest `viewed_at`, then the
    lowest id. The survivor also inherits the group's earliest `viewed_at`
    so "first seen" is preserved. Rows with a NULL user are left alone
    (they can't collide under the constraint).
    """
    PostView = apps.get_model('post', 'PostView')

    groups = (
        PostView.objects.filter(user__isnull=False)
        .values('post_id', 'user_id')
        .annotate(n=Count('id'))
        .filter(n__gt=1)
    )

    for g in groups.iterator(chunk_size=2000):
        rows = list(
            PostView.objects.filter(post_id=g['post_id'], user_id=g['user_id'])
            .order_by(F('watch_seconds').desc(nulls_last=True), 'viewed_at', 'id')
            .values_list('id', 'viewed_at')
        )
        if len(rows) < 2:
            continue
        survivor_id = rows[0][0]
        earliest = min(r[1] for r in rows)
        loser_ids = [r[0] for r in rows[1:]]
        for i in range(0, len(loser_ids), DELETE_CHUNK):
            PostView.objects.filter(id__in=loser_ids[i:i + DELETE_CHUNK]).delete()
        PostView.objects.filter(id=survivor_id).update(viewed_at=earliest)


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='postview',
            name='is_counted',
            field=models.BooleanField(default=True),
        ),
        # Must run before the UniqueConstraint or it would fail on existing dupes.
        migrations.RunPython(dedupe_postviews, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='postview',
            constraint=models.UniqueConstraint(fields=('post', 'user'), name='uniq_postview_post_user'),
        ),
        migrations.AddIndex(
            model_name='postview',
            index=models.Index(fields=['user', '-viewed_at'], name='post_views_user_viewed_idx'),
        ),
    ]
