# H10: index for the PostView retention job (post/feed_retention.py). Hand-written.
# On a big production table build it without blocking writes, e.g. run once beforehand:
#   CREATE INDEX CONCURRENTLY post_views_viewed_at_idx ON post_views (viewed_at);
# and then `migrate --fake-initial`-style: this migration's AddIndex is a plain CREATE INDEX (fine on small / dev DBs).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0021_postaistudy'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='postview',
            index=models.Index(fields=['viewed_at'], name='post_views_viewed_at_idx'),
        ),
    ]
