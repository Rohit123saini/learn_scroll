# H14 (ReadingProgress - PDF page resume) + H15 (PostHelpful - "helpful for N students"). Hand-written.
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0023_topicfollow'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReadingProgress',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('last_page', models.PositiveIntegerField(default=1)),
                ('page_count', models.PositiveIntegerField(blank=True, null=True)),
                ('first_opened_at', models.DateTimeField(auto_now_add=True)),
                ('last_opened_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('attachment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reading_progress', to='post.postattachment')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reading_progress', to='post.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reading_progress', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_reading_progress',
                'ordering': ['-last_opened_at'],
                'constraints': [models.UniqueConstraint(fields=('user', 'attachment'), name='post_reading_progress_uniq')],
                'indexes': [models.Index(fields=['user', '-last_opened_at'], name='post_reading_user_recent_idx')],
            },
        ),
        migrations.CreateModel(
            name='PostHelpful',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='helpful_votes', to='post.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='helpful_votes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_helpful',
                'constraints': [models.UniqueConstraint(fields=('post', 'user'), name='post_helpful_uniq')],
                'indexes': [models.Index(fields=['post', '-created_at'], name='post_helpful_post_idx')],
            },
        ),
    ]
