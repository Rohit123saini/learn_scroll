# SAFETY — consent log, data delete requests, campus backups.
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q

import campus.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("campus", "0019_notice_communication"),
    ]

    operations = [
        migrations.CreateModel(
            name="CampusConsentRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("purpose", models.CharField(db_index=True, max_length=30)),
                ("status", models.CharField(choices=[("granted", "Granted"), ("withdrawn", "Withdrawn")], max_length=10)),
                ("policy_version", models.CharField(blank=True, max_length=20)),
                ("source", models.CharField(choices=[("app", "In the app"), ("admin_entry", "Entered by campus staff")],
                                            default="app", max_length=15)),
                ("note", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="consent_records", to="campus.campus")),
                ("recorded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name="campus_consents_recorded", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="campus_consents_about", to=settings.AUTH_USER_MODEL)),
                ("subject", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="campus_consents_given", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="campusconsentrecord",
            index=models.Index(fields=["campus", "-created_at"], name="consent_campus_time_idx"),
        ),
        migrations.AddIndex(
            model_name="campusconsentrecord",
            index=models.Index(fields=["campus", "purpose", "status"], name="consent_campus_purpose_idx"),
        ),
        migrations.AddIndex(
            model_name="campusconsentrecord",
            index=models.Index(fields=["subject", "purpose", "-created_at"], name="consent_subject_purpose_idx"),
        ),
        migrations.CreateModel(
            name="CampusDataDeletionRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("my_data", "My own data"), ("child_data", "My child's data")],
                                          max_length=12)),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("wants_account_deletion", models.BooleanField(default=False)),
                ("status", models.CharField(
                    choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"),
                             ("completed", "Completed"), ("cancelled", "Cancelled")],
                    db_index=True, default="pending", max_length=10)),
                ("due_at", models.DateTimeField(db_index=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.CharField(blank=True, max_length=500)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="data_deletion_requests", to="campus.campus")),
                ("requester", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                related_name="campus_deletion_requests", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name="campus_deletion_reviewed", to=settings.AUTH_USER_MODEL)),
                ("subject", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="campus_deletion_subject_of", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="campusdatadeletionrequest",
            index=models.Index(fields=["campus", "status", "-created_at"], name="delreq_campus_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="campusdatadeletionrequest",
            constraint=models.UniqueConstraint(
                condition=Q(("status__in", ["pending", "approved"])),
                fields=("campus", "subject"),
                name="unique_open_deletion_request",
            ),
        ),
        migrations.CreateModel(
            name="CampusBackup",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(
                    choices=[("manual", "Manual"), ("scheduled", "Scheduled"), ("pre_restore", "Taken before a restore")],
                    default="manual", max_length=12)),
                ("status", models.CharField(choices=[("ready", "Ready"), ("failed", "Failed")],
                                            default="ready", max_length=8)),
                ("groups", models.JSONField(blank=True, default=list)),
                ("file", models.FileField(blank=True, null=True, upload_to=campus.models._campus_backup_path)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("row_counts", models.JSONField(blank=True, default=dict)),
                ("note", models.CharField(blank=True, max_length=200)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="backups", to="campus.campus")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="campus_backups_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="campusbackup",
            index=models.Index(fields=["campus", "-created_at"], name="backup_campus_time_idx"),
        ),
    ]
