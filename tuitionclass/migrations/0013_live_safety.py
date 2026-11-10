# X12 - live safety: ClassSession.chat_filter_mode, SessionParticipant.recording_notice_ack_at / chat_muted_until,
# LiveModerationEvent. Hand-written on top of 0012 (nothing was run).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tuitionclass', '0012_auto_recording_chapters_saves'),
    ]

    operations = [
        migrations.AddField(
            model_name='classsession',
            name='chat_filter_mode',
            field=models.CharField(
                blank=True, default='', max_length=8,
                choices=[('', 'Default'), ('off', 'Off'), ('flag', 'Flag only'), ('mask', 'Mask bad words'), ('block', 'Block message')],
            ),
        ),
        migrations.AddField(
            model_name='sessionparticipant',
            name='recording_notice_ack_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sessionparticipant',
            name='chat_muted_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='LiveModerationEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    db_index=True, max_length=14,
                    choices=[
                        ('report', 'Report filed'), ('warn', 'Warning'), ('chat_mute', 'Chat muted'),
                        ('chat_unmute', 'Chat unmuted'), ('kick', 'Removed from session'),
                        ('ban', 'Banned from classroom'), ('filter_block', 'Message blocked by word filter'),
                        ('filter_mask', 'Message masked by word filter'), ('auto_mute', 'Automatic chat mute (strikes)'),
                        ('minor_guard', 'Minor-safety rule applied'), ('dismiss', 'Report dismissed'),
                    ],
                )),
                ('reason', models.CharField(blank=True, default='', max_length=40)),
                ('detail', models.CharField(blank=True, default='', max_length=255)),
                ('chat_message_id', models.PositiveIntegerField(blank=True, null=True)),
                ('content_report_id', models.PositiveIntegerField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('resolution', models.CharField(blank=True, default='', max_length=14)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
                ('resolved_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
                ('session', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='moderation_events',
                    to='tuitionclass.classsession',
                )),
                ('target_user', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='live_moderation_events', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddIndex(
            model_name='livemoderationevent',
            index=models.Index(fields=['session', '-created_at'], name='tc_livemod_session_idx'),
        ),
        migrations.AddIndex(
            model_name='livemoderationevent',
            index=models.Index(fields=['session', 'kind', 'resolved_at'], name='tc_livemod_queue_idx'),
        ),
        migrations.AddIndex(
            model_name='livemoderationevent',
            index=models.Index(fields=['target_user', 'kind'], name='tc_livemod_target_idx'),
        ),
    ]
