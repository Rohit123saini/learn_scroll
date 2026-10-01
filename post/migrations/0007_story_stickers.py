# Stories upgrade, Part 2a (Stickers foundation + mentions + link sticker):
# the shared StorySticker table. Existing stories are untouched (they simply
# have no stickers).
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0006_story_close_friends'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StorySticker',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(
                    choices=[('mention', 'Mention'), ('link', 'Link'), ('poll', 'Poll'), ('question', 'Question')],
                    max_length=20,
                )),
                ('x', models.FloatField(default=0.5)),
                ('y', models.FloatField(default=0.5)),
                ('rotation', models.FloatField(default=0.0)),
                ('scale', models.FloatField(default=1.0)),
                ('z_index', models.PositiveSmallIntegerField(default=0)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('mentioned_user', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='story_mention_stickers',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('story', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stickers',
                    to='post.story',
                )),
            ],
            options={
                'db_table': 'story_stickers',
                'ordering': ['z_index', 'created_at'],
                'indexes': [
                    models.Index(fields=['story', 'kind'], name='story_stick_story_kind_idx'),
                    models.Index(fields=['mentioned_user', '-created_at'], name='story_stick_mention_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        condition=models.Q(('kind', 'mention')),
                        fields=('story', 'mentioned_user'),
                        name='unique_story_mention',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(('kind', 'mention'), _negated=True),
                            ('mentioned_user__isnull', False),
                            _connector='OR',
                        ),
                        name='story_mention_has_user',
                    ),
                ],
            },
        ),
    ]
