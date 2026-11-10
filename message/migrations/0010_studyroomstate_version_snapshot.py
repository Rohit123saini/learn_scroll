# S3 — hand-written (makemigrations / migrate NAHI chalaya). `StudyRoomState.version` (default 0, purani
# rows safe) + naya table `StudyRoomStateSnapshot` (whiteboard history / undo). 0009 (S2) ke baad ki hai.
# `makemigrations --check --dry-run` se confirm karo ki Django koi aur diff nahi dikha raha.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0009_studyroomsession'),
    ]

    operations = [
        migrations.AddField(
            model_name='studyroomstate',
            name='version',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='StudyRoomStateSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('version', models.PositiveIntegerField(default=0)),
                ('state', models.JSONField(blank=True, default=dict)),
                ('notes', models.JSONField(blank=True, default=None, null=True)),
                ('reason', models.CharField(choices=[
                    ('periodic', 'Before overwrite'),
                    ('delete', 'Before clear'),
                    ('restore', 'Before restore'),
                ], db_index=True, max_length=16)),
                ('size_bytes', models.PositiveIntegerField(default=0)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_state_snapshots', to='message.conversation')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['conversation', '-created_at'], name='msg_srss_conv_created_idx'),
                ],
            },
        ),
    ]
