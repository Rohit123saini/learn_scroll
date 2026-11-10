# N1 - per-type x per-channel notification switches. Hand-written (NOT generated, NOT run): run
# `python manage.py makemigrations --check --dry-run` once - it must say "No changes detected".
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_feed_doubt_for_teacher_notif_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationpreference",
            name="channel_overrides",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
