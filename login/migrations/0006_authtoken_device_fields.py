from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """L1 gap 11 -- device info on AuthToken (device list / per-device logout)."""

    dependencies = [
        ("login", "0005_user_onboarding_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="authtoken",
            name="device_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="device_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="platform",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="user_agent",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="authtoken",
            index=models.Index(fields=["user", "last_used_at"], name="login_auth_user_used_idx"),
        ),
    ]
