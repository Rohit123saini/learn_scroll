# Attendance galti-sudhaar: edit window + reason + audit log.
# Hand-written to match campus.models (Campus.attendance_edit_window_hours,
# Attendance.marked_at / last_edited_at / edit_count, AttendanceEditLog);
# index names are explicit in the models so `makemigrations --check` reports no drift.
#
# Existing attendance rows get `marked_at = <time this migration runs>`, i.e. their
# teacher edit window starts now (they are not retro-locked).

import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0007_ptm_parent_chat"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="campus",
            name="attendance_edit_window_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(720),
                ],
            ),
        ),
        migrations.AddField(
            model_name="attendance",
            name="marked_at",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddField(
            model_name="attendance",
            name="last_edited_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="attendance",
            name="edit_count",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.CreateModel(
            name="AttendanceEditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("student_label", models.CharField(blank=True, max_length=150)),
                ("date", models.DateField()),
                (
                    "action",
                    models.CharField(
                        choices=[("edit", "Edit"), ("clear", "Clear")], default="edit", max_length=10
                    ),
                ),
                ("old_status", models.CharField(max_length=10)),
                ("new_status", models.CharField(blank=True, max_length=10)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("edited_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("original_marked_at", models.DateTimeField(blank=True, null=True)),
                ("was_override", models.BooleanField(default=False)),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_edit_logs",
                        to="campus.campus",
                    ),
                ),
                (
                    "edited_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_edits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "enrollment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_edit_logs",
                        to="campus.studentenrollment",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_edit_logs",
                        to="campus.section",
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_edit_logs",
                        to="campus.subject",
                    ),
                ),
            ],
            options={
                "ordering": ["-edited_at"],
                "indexes": [
                    models.Index(fields=["section", "date"], name="att_editlog_section_date_idx"),
                    models.Index(fields=["enrollment", "date"], name="att_editlog_enroll_date_idx"),
                    models.Index(fields=["campus", "-edited_at"], name="att_editlog_campus_time_idx"),
                ],
            },
        ),
    ]
