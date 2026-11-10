# C16 — hand-written (makemigrations / migrate NAHI chalaya). Sirf naya table `CallRecordingConsent`
# (call recording consent / notice audit log); koi purani table / column nahi badli.
# 0007 (C6 CANCELLED choice) ke baad ki hai. `makemigrations --check --dry-run` se confirm karo
# ki Django koi aur diff nahi dikha raha.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0007_callstatus_cancelled'),
    ]

    operations = [
        migrations.CreateModel(
            name='CallRecordingConsent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('event', models.CharField(choices=[
                    ('started', 'Recording started'),
                    ('stopped', 'Recording stopped'),
                    ('notice_shown', 'Notice shown'),
                    ('acknowledged', 'Notice acknowledged'),
                    ('blocked_minor_start', 'Start blocked (minor in call)'),
                    ('blocked_minor_join', 'Join blocked (minor, recording on)'),
                ], db_index=True, max_length=32)),
                ('egress_id', models.CharField(blank=True, default='', max_length=100)),
                ('notice_version', models.CharField(blank=True, default='', max_length=20)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('call', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_consents', to='message.callsession')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='call_recording_consents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['call', 'egress_id', 'event'], name='msg_crc_call_egress_evt_idx'),
                    models.Index(fields=['call', 'user', 'event'], name='msg_crc_call_user_evt_idx'),
                ],
            },
        ),
    ]
