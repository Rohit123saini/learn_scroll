# Poll + Quiz upgrade: 2-6 options, image options, duration, anonymous / public votes, multiple choice,
# results visibility, quiz mode (is_correct + explanation + graded votes), multi-vote constraint swap and
# the poll -> Test Series question link.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check`.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0027_resource_papers_versions_text'),
    ]

    operations = [
        # ---- PostPoll
        migrations.AddField(model_name='postpoll', name='allow_multiple',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='postpoll', name='max_choices',
                            field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name='postpoll', name='is_anonymous',
                            field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name='postpoll', name='results_visibility',
            field=models.CharField(
                choices=[('always', 'Always'), ('after_vote', 'After voting'), ('after_end', 'After the poll ends')],
                default='after_vote', max_length=12)),
        migrations.AddField(model_name='postpoll', name='is_quiz',
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name='postpoll', name='explanation',
                            field=models.CharField(blank=True, default='', max_length=500)),
        # ---- PostPollOption
        migrations.AlterField(model_name='postpolloption', name='text',
                              field=models.CharField(blank=True, default='', max_length=200)),
        migrations.AddField(model_name='postpolloption', name='image',
                            field=models.ImageField(blank=True, null=True, upload_to='post/poll_options/')),
        migrations.AddField(model_name='postpolloption', name='is_correct',
                            field=models.BooleanField(default=False)),
        # ---- PostPollVote: one row per (poll, user, option) instead of one per (poll, user)
        migrations.AddField(model_name='postpollvote', name='is_correct',
                            field=models.BooleanField(blank=True, null=True)),
        migrations.AlterUniqueTogether(name='postpollvote', unique_together=set()),
        migrations.AddConstraint(
            model_name='postpollvote',
            constraint=models.UniqueConstraint(fields=('poll', 'user', 'option'), name='uniq_poll_user_option')),
        migrations.AddIndex(
            model_name='postpollvote',
            index=models.Index(fields=['poll', 'user'], name='pollvote_poll_user_idx')),
        migrations.AddIndex(
            model_name='postpollvote',
            index=models.Index(fields=['poll', 'is_correct', 'created_at'], name='pollvote_poll_correct_idx')),
        # ---- poll -> Test Series question
        migrations.CreateModel(
            name='PostPollQuestionLink',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('series_id', models.UUIDField(db_index=True)),
                ('question_id', models.UUIDField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='poll_question_links', to=settings.AUTH_USER_MODEL)),
                ('poll', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='question_links', to='post.postpoll')),
            ],
            options={'db_table': 'post_poll_question_links'},
        ),
        migrations.AddConstraint(
            model_name='postpollquestionlink',
            constraint=models.UniqueConstraint(fields=('poll', 'series_id'), name='uniq_poll_series_link')),
    ]
