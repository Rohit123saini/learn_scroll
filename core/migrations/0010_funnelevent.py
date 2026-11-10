import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_doubt_answered_notif_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FunnelEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.CharField(choices=[('install', 'Install (first app launch on a device)'), ('app_open', 'App open (max one per user/device/day)'), ('signup', 'Signup (account created)'), ('onboarding_completed', 'Onboarding finished or skipped'), ('first_doubt', 'First doubt asked'), ('first_test', 'First test attempt started')], max_length=32)),
                ('device_id', models.CharField(blank=True, default='', max_length=64)),
                ('platform', models.CharField(blank=True, default='', max_length=16)),
                ('app_version', models.CharField(blank=True, default='', max_length=32)),
                ('props', models.JSONField(blank=True, default=dict)),
                ('dedupe_key', models.CharField(blank=True, max_length=120, null=True, unique=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='funnel_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'core_funnel_event',
                'indexes': [
                    models.Index(fields=['event', 'created_at'], name='funnel_event_created_idx'),
                    models.Index(fields=['user', 'event'], name='funnel_user_event_idx'),
                    models.Index(fields=['device_id', 'event'], name='funnel_device_event_idx'),
                ],
            },
        ),
    ]
