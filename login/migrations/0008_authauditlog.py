import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """L1 gap 19 -- append-only audit log of login / OTP / reset / session events."""

    dependencies = [
        ("login", "0007_user_phone_verified"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuthAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("event", models.CharField(db_index=True, max_length=40)),
                ("outcome", models.CharField(default="success", max_length=10)),
                ("identifier_kind", models.CharField(blank=True, default="", max_length=10)),
                ("identifier_masked", models.CharField(blank=True, default="", max_length=120)),
                ("identifier_hash", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("device_id", models.CharField(blank=True, default="", max_length=64)),
                ("platform", models.CharField(blank=True, default="", max_length=20)),
                ("reason", models.CharField(blank=True, default="", max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auth_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["event", "created_at"], name="login_audit_ev_created_idx"),
                    models.Index(fields=["user", "created_at"], name="login_audit_user_created_idx"),
                    models.Index(fields=["ip_address", "created_at"], name="login_audit_ip_created_idx"),
                    models.Index(fields=["identifier_hash", "created_at"], name="login_audit_idh_created_idx"),
                ],
            },
        ),
    ]
