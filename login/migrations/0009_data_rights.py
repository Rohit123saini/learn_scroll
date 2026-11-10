import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """S5 -- DPDP data rights: DataExportRequest, AccountDeletionRequest, ConsentRecord. (hand-written)"""

    dependencies = [
        ("login", "0008_authauditlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="DataExportRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "Queued"), ("processing", "Processing"), ("ready", "Ready"), ("failed", "Failed"), ("expired", "Expired")], db_index=True, default="queued", max_length=12)),
                ("requested_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ready_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("file_name", models.CharField(blank=True, default="", max_length=255)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("download_count", models.PositiveIntegerField(default=0)),
                ("error", models.CharField(blank=True, default="", max_length=255)),
                ("notified_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="data_exports", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-requested_at", "-id"],
                "indexes": [models.Index(fields=["user", "-requested_at"], name="login_dexp_user_req_idx")],
            },
        ),
        migrations.CreateModel(
            name="AccountDeletionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("cancelled", "Cancelled"), ("completed", "Completed")], db_index=True, default="pending", max_length=10)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("reminder_sent_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deletion_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-requested_at", "-id"],
                "constraints": [models.UniqueConstraint(condition=models.Q(("status", "pending")), fields=("user",), name="login_delreq_one_pending_per_user")],
            },
        ),
        migrations.CreateModel(
            name="ConsentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purpose", models.CharField(db_index=True, max_length=30)),
                ("granted", models.BooleanField()),
                ("notice_version", models.CharField(blank=True, default="", max_length=20)),
                ("source", models.CharField(blank=True, default="app", max_length=20)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consent_records", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["user", "purpose", "-created_at"], name="login_consent_up_idx")],
            },
        ),
    ]
