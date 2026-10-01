# Stories upgrade, Part 2b (poll + question stickers): the tables that hold what
# viewers send back - one poll vote / one question answer per (sticker, viewer).
# No existing table is altered.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0007_story_stickers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StoryPollVote',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('option_index', models.PositiveSmallIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sticker', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='poll_votes',
                    to='post.storysticker',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='story_poll_votes',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'story_poll_votes',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['sticker', 'option_index'], name='story_pollvote_opt_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('sticker', 'user'), name='unique_story_poll_vote'),
                ],
            },
        ),
        migrations.CreateModel(
            name='StoryQuestionAnswer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('text', models.CharField(max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sticker', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='answers',
                    to='post.storysticker',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='story_question_answers',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'story_question_answers',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['sticker', '-created_at'], name='story_qanswer_recent_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('sticker', 'user'), name='unique_story_question_answer'),
                ],
            },
        ),
    ]
