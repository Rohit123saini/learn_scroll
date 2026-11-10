import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """S10 -- connected accounts: LinkedIdentity (Google link / unlink) and QRLoginRequest (QR sign-in). (hand-written)"""

    dependencies = [
        ("login", "0009_data_rights"),
    ]

    operations = [
        migrations.CreateModel(
            name="LinkedIdentity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("google", "Google")], default="google", max_length=16)),
                ("subject", models.CharField(max_length=64)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("display_name", models.CharField(blank=True, default="", max_length=120)),
                ("linked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="linked_identities", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("provider", "subject"), name="login_linkid_provider_subject_uniq"),
                    models.UniqueConstraint(fields=("user", "provider"), name="login_linkid_user_provider_uniq"),
                ],
            },
        ),
        migrations.CreateModel(
            name="QRLoginRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=64, unique=True)),
                ("secret_hash", models.CharField(max_length=64)),
                ("match_code", models.CharField(max_length=4)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("denied", "Denied"), ("consumed", "Consumed")], db_index=True, default="pending", max_length=10)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("requester_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("requester_device_name", models.CharField(blank=True, default="", max_length=120)),
                ("requester_platform", models.CharField(blank=True, default="", max_length=20)),
                ("requester_user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("requester_device_id", models.CharField(blank=True, default="", max_length=64)),
                ("wrong_match_attempts", models.PositiveSmallIntegerField(default=0)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("session_jti", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="qr_login_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
