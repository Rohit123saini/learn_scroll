# Story studio F3 (part 2): quiz answers table + the add_yours chain pointer on StorySticker.
# Written by hand, NOT generated and NOT run - run `makemigrations --check` once to confirm it matches models.py.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0031_story_sticker_kinds_f3'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='storysticker',
            name='chain_root',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='chain_members',
                to='post.storysticker',
            ),
        ),
        migrations.CreateModel(
            name='StoryQuizAnswer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('option_index', models.PositiveSmallIntegerField()),
                ('is_correct', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sticker', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='quiz_answers',
                    to='post.storysticker',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='story_quiz_answers',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'story_quiz_answers',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['sticker', 'option_index'], name='story_quizans_opt_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('sticker', 'user'), name='unique_story_quiz_answer'),
                ],
            },
        ),
    ]
