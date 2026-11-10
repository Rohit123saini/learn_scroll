# N4 - inbox archive. Hand-written (NOT generated, NOT run): run
# `python manage.py makemigrations --check --dry-run` once - it must say "No changes detected".
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_notificationpreference_channel_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
