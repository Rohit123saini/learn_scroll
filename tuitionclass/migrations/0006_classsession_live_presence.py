# LV1 — Live in story tray: heartbeat + one-shot notify guard on ClassSession.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tuitionclass', '0005_referral_rewards_granted_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='classsession',
            name='live_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='classsession',
            index=models.Index(fields=['status', 'live_heartbeat_at'], name='cs_live_hb_idx'),
        ),
    ]
