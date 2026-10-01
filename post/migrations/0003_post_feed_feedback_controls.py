# Feed feedback controls - Part 1: PostHide ("Not interested") + MutedAccount.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0002_postview_seen_signal'),
    ]

    operations = [
        migrations.CreateModel(
            name='PostHide',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.CharField(
                    choices=[('not_interested', 'Not interested'), ('not_relevant', 'Not relevant to me'),
                             ('seen_too_often', 'Seeing this too often'), ('other', 'Other')],
                    default='not_interested', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hides', to='post.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_hides', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_hides',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MutedAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('muted_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='muted_by_accounts', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='muted_accounts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_muted_accounts',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='posthide',
            constraint=models.UniqueConstraint(fields=('user', 'post'), name='uniq_post_hide_user_post'),
        ),
        migrations.AddIndex(
            model_name='posthide',
            index=models.Index(fields=['user', '-created_at'], name='post_hide_user_created_idx'),
        ),
        migrations.AddConstraint(
            model_name='mutedaccount',
            constraint=models.UniqueConstraint(fields=('user', 'muted_user'), name='uniq_muted_account'),
        ),
        migrations.AddConstraint(
            model_name='mutedaccount',
            constraint=models.CheckConstraint(condition=models.Q(user=models.F('muted_user'), _negated=True), name='muted_account_no_self_mute'),
        ),
        migrations.AddIndex(
            model_name='mutedaccount',
            index=models.Index(fields=['user', '-created_at'], name='post_muted_user_created_idx'),
        ),
    ]
