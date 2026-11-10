# P-F1: adds ONBOARDING_STEP to FunnelEvent.event (choices only - no schema change).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_new_device_login_notif_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='funnelevent',
            name='event',
            field=models.CharField(choices=[('install', 'Install (first app launch on a device)'), ('app_open', 'App open (max one per user/device/day)'), ('signup', 'Signup (account created)'), ('onboarding_completed', 'Onboarding finished or skipped'), ('first_doubt', 'First doubt asked'), ('first_test', 'First test attempt started'), ('interests_saved', 'Class/exam/interests saved (first time)'), ('first_feed', 'First personalised feed served'), ('retention_d1', 'Opened the app on day 1 after signup'), ('retention_d3', 'Opened the app on day 3 after signup'), ('retention_d7', 'Opened the app on day 7 after signup'), ('onboarding_step', 'Onboarding step viewed / completed / skipped')], max_length=32),
        ),
    ]
