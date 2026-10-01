# Feed feedback controls - Part 2: FeedFeedback ("Show fewer like this" negative ranking signal).
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0003_post_feed_feedback_controls'),
    ]

    operations = [
        migrations.CreateModel(
            name='FeedFeedback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(
                    choices=[('category', 'Category'), ('hashtag', 'Hashtag'), ('author', 'Author')],
                    max_length=10)),
                ('key', models.CharField(max_length=100)),
                ('weight', models.FloatField(default=1.0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feed_feedback', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_feed_feedback',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='feedfeedback',
            constraint=models.UniqueConstraint(fields=('user', 'kind', 'key'), name='uniq_feed_feedback_user_kind_key'),
        ),
        migrations.AddConstraint(
            model_name='feedfeedback',
            constraint=models.CheckConstraint(condition=models.Q(weight__gt=0), name='feed_feedback_weight_positive'),
        ),
        migrations.AddIndex(
            model_name='feedfeedback',
            index=models.Index(fields=['user', '-updated_at'], name='post_feedback_user_upd_idx'),
        ),
    ]
