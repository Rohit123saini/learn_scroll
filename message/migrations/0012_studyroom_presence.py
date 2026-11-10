# S5 — hand-written (makemigrations / migrate NAHI chalaya). `StudyRoomAttendance` pe presence columns
# (total_seconds, join_count, first_joined_at, last_left_at, presence_tracked) + naya table `StudyRoomPresence`.
# 0011 (S4) ke baad ki hai. Purani attendance rows `presence_tracked=False` rehti hain (streak me sirf join kaafi),
# isliye kisi ki purani streak nahi tootti. `makemigrations --check --dry-run` se confirm karo.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0011_studyroom_host_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='studyroomattendance',
            name='total_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='studyroomattendance',
            name='join_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='studyroomattendance',
            name='first_joined_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroomattendance',
            name='last_left_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroomattendance',
            name='presence_tracked',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='StudyRoomPresence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('room_name', models.CharField(max_length=150)),
                ('joined_at', models.DateTimeField()),
                ('left_at', models.DateTimeField(blank=True, null=True)),
                ('duration_seconds', models.PositiveIntegerField(default=0)),
                ('end_reason', models.CharField(blank=True, default='', max_length=24)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_presences', to='message.conversation')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_presences', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['room_name', 'left_at'], name='msg_srpres_room_open_idx'),
                    models.Index(fields=['left_at', 'joined_at'], name='msg_srpres_open_age_idx'),
                    models.Index(fields=['conversation', 'user', 'joined_at'], name='msg_srpres_conv_user_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('room_name', 'user', 'joined_at'), name='unique_study_room_presence_interval'),
                ],
            },
        ),
    ]
