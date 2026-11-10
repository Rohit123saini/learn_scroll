# S4 — hand-written (makemigrations / migrate NAHI chalaya). `StudyRoomSession` pe host-control fields
# (is_locked, waiting_room_enabled, max_participants, share_policy) + naye tables `StudyRoomParticipant`,
# `StudyRoomReport`. 0010 (S3) ke baad ki hai. `makemigrations --check --dry-run` se confirm karo.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0010_studyroomstate_version_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='studyroomsession',
            name='is_locked',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='studyroomsession',
            name='waiting_room_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='studyroomsession',
            name='max_participants',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroomsession',
            name='share_policy',
            field=models.CharField(choices=[('everyone', 'Everyone'), ('host_only', 'Host / co-hosts only')], default='everyone', max_length=16),
        ),
        migrations.CreateModel(
            name='StudyRoomParticipant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('role_override', models.CharField(blank=True, choices=[('host', 'Host'), ('co_host', 'Co-host'), ('participant', 'Participant'), ('viewer', 'Viewer')], max_length=16, null=True)),
                ('status', models.CharField(choices=[('waiting', 'Waiting room'), ('admitted', 'Admitted'), ('denied', 'Denied'), ('removed', 'Removed')], db_index=True, default='admitted', max_length=16)),
                ('hand_raised', models.BooleanField(default=False)),
                ('hand_raised_at', models.DateTimeField(blank=True, null=True)),
                ('joined_at', models.DateTimeField(blank=True, null=True)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='participants', to='message.studyroomsession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_participations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['session', 'status'], name='msg_srp_session_status_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('session', 'user'), name='unique_study_room_participant_per_session'),
                ],
            },
        ),
        migrations.CreateModel(
            name='StudyRoomReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('reason', models.CharField(choices=[('harassment', 'Harassment / bullying'), ('inappropriate', 'Inappropriate content'), ('spam', 'Spam / disruption'), ('unsafe_minor', 'Child safety concern'), ('other', 'Other')], db_index=True, max_length=24)),
                ('details', models.TextField(blank=True, default='')),
                ('status', models.CharField(choices=[('open', 'Open'), ('reviewed', 'Reviewed'), ('dismissed', 'Dismissed')], db_index=True, default='open', max_length=16)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_reports', to='message.conversation')),
                ('reported_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_reports_received', to=settings.AUTH_USER_MODEL)),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_reports_made', to=settings.AUTH_USER_MODEL)),
                ('session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='message.studyroomsession')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['status', '-created_at'], name='msg_srr_status_created_idx'),
                ],
            },
        ),
    ]
