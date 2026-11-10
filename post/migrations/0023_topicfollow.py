# H13: TopicFollow ("Follow this topic" in the feed card's Study tools sheet). Hand-written.
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0022_postview_viewed_at_idx'),
    ]

    operations = [
        migrations.CreateModel(
            name='TopicFollow',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('topic', models.CharField(max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='followed_topics', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_topic_follows',
                'ordering': ['-created_at'],
                'constraints': [models.UniqueConstraint(fields=('user', 'topic'), name='post_topic_follow_uniq')],
                'indexes': [models.Index(fields=['user', '-created_at'], name='post_topic_follow_user_idx')],
            },
        ),
    ]
