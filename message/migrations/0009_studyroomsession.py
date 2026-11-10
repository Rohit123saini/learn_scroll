# S2 — hand-written (makemigrations / migrate NAHI chalaya). Sirf naya table `StudyRoomSession`
# (study room ka current session DB me, cache me nahi); koi purani table / column nahi badli.
# 0008 (C16 CallRecordingConsent) ke baad ki hai. `makemigrations --check --dry-run` se confirm karo
# ki Django koi aur diff nahi dikha raha (index / constraint ke naam model se match karte hain).
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0008_callrecordingconsent'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudyRoomSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('room_name', models.CharField(max_length=150, unique=True)),
                ('started_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_room_sessions', to='message.conversation')),
                ('started_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['conversation', '-started_at'], name='msg_srs_conv_started_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        condition=models.Q(('ended_at__isnull', True), ('is_deleted', False)),
                        fields=('conversation',),
                        name='unique_active_study_room_session_per_conv',
                    ),
                ],
            },
        ),
    ]
