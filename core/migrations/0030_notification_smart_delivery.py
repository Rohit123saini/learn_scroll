# N5 - smart delivery: timezone auto/manual, focus-mode + low-priority roll-up switches.
# Hand-written (NOT generated, NOT run): run `python manage.py makemigrations --check --dry-run` once - it must say
# "No changes detected". All five columns are additive with defaults / NULL, so it is safe on a live table.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_notification_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationpreference",
            name="timezone_mode",
            field=models.CharField(choices=[("auto", "Auto"), ("manual", "Manual")], default="auto", max_length=6),
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="timezone_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="focus_priority_only",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="low_priority_rollup",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationpreference",
            name="last_rollup_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
