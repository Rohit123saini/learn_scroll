# X7 reel sounds: SoundTakedown (blocked sounds) + SoundReport (user reports).
# Written by hand, NOT generated and NOT run - run `makemigrations --check` once to confirm it matches models.py.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0034_comment_reply_reel'),
    ]

    operations = [
        migrations.CreateModel(
            name='SoundTakedown',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(default='freesound', max_length=20)),
                ('sound_id', models.CharField(db_index=True, max_length=40)),
                ('title', models.CharField(blank=True, default='', max_length=100)),
                ('artist', models.CharField(blank=True, default='', max_length=60)),
                ('reason', models.CharField(
                    choices=[('copyright', 'Copyright complaint'), ('wrong_license', 'Not really CC0'),
                             ('offensive', 'Offensive / unsafe'), ('other', 'Other')],
                    default='copyright', max_length=20)),
                ('note', models.CharField(blank=True, default='', max_length=300)),
                ('status', models.CharField(
                    choices=[('blocked', 'Blocked'), ('restored', 'Restored')],
                    db_index=True, default='blocked', max_length=10)),
                ('reels_held', models.PositiveIntegerField(default=0)),
                ('owners_told', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('restored_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL)),
                ('restored_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'post_sound_takedowns', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SoundReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(default='freesound', max_length=20)),
                ('sound_id', models.CharField(db_index=True, max_length=40)),
                ('reason', models.CharField(default='copyright', max_length=20)),
                ('note', models.CharField(blank=True, default='', max_length=300)),
                ('status', models.CharField(
                    choices=[('open', 'Open'), ('actioned', 'Sound blocked'), ('dismissed', 'Dismissed')],
                    db_index=True, default='open', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='sound_reports',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'post_sound_reports', 'ordering': ['-updated_at']},
        ),
        migrations.AddConstraint(
            model_name='soundtakedown',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'blocked')), fields=('provider', 'sound_id'), name='uniq_blocked_sound'),
        ),
        migrations.AddIndex(
            model_name='soundtakedown',
            index=models.Index(fields=['provider', 'sound_id', 'status'], name='sound_td_lookup_idx'),
        ),
        migrations.AddConstraint(
            model_name='soundreport',
            constraint=models.UniqueConstraint(fields=('user', 'provider', 'sound_id'), name='uniq_sound_report_per_user'),
        ),
        migrations.AddIndex(
            model_name='soundreport',
            index=models.Index(fields=['status', '-updated_at'], name='sound_report_status_idx'),
        ),
    ]
