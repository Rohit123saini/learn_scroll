# Story audience / replies / reshare / save + "hide from" list + post_card sticker kind (F3-AUD).
# Written by hand, NOT generated and NOT run - run `makemigrations --check` once to confirm it matches models.py.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0032_story_quiz_addyours_f3'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='story',
            name='audience',
            field=models.CharField(
                choices=[
                    ('everyone', 'Everyone'), ('followers', 'Followers'),
                    ('close_friends', 'Close friends'), ('class', 'Class'),
                ],
                default='everyone',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='story',
            name='audience_classroom_id',
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='story',
            name='reply_setting',
            field=models.CharField(
                choices=[('everyone', 'Everyone'), ('followers', 'Followers'), ('off', 'Off')],
                default='everyone',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='story',
            name='allow_reshare',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='story',
            name='allow_save',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='storysticker',
            name='kind',
            field=models.CharField(
                choices=[
                    ('mention', 'Mention'), ('link', 'Link'), ('poll', 'Poll'), ('question', 'Question'),
                    ('location', 'Location'), ('countdown', 'Countdown'), ('quiz', 'Quiz'),
                    ('add_yours', 'Add yours'), ('gif', 'GIF'), ('post_card', 'Post card'),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='StoryHiddenFrom',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('story', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hidden_from',
                    to='post.story',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stories_hidden_from_me',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'story_hidden_from',
                'indexes': [
                    models.Index(fields=['user', 'story'], name='story_hidden_user_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('story', 'user'), name='unique_story_hidden_from'),
                ],
            },
        ),
    ]
