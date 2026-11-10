# Recorded classes + class notes + private student notes.
# Hand-written to match campus.models exactly (explicit index / constraint names):
#   * Campus.class_recording_enabled / class_recording_auto
#   * CampusLiveSession: recording_* fields + (section, subject, recording_status) index
#   * ClassNote (teacher notes, optionally tied to one class)
#   * StudentNote (private to its author)
import campus.resource_rules
import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0013_live_class_one_tap_auto_attendance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ---- Campus knobs -------------------------------------------------
        migrations.AddField(
            model_name="campus",
            name="class_recording_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="campus",
            name="class_recording_auto",
            field=models.BooleanField(default=True),
        ),
        # ---- CampusLiveSession recording ----------------------------------
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_status",
            field=models.CharField(
                choices=[
                    ("none", "Not recorded"), ("recording", "Recording"), ("processing", "Processing"),
                    ("ready", "Ready"), ("failed", "Failed"),
                ],
                db_index=True, default="none", max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_egress_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_url",
            field=models.CharField(blank=True, max_length=600),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_stopped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_ready_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_duration_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_hidden",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_attempted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_error",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddIndex(
            model_name="campuslivesession",
            index=models.Index(fields=["section", "subject", "recording_status"], name="campus_live_rec_idx"),
        ),
        # ---- ClassNote ------------------------------------------------------
        migrations.CreateModel(
            name="ClassNote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField(blank=True)),
                (
                    "file",
                    models.FileField(
                        blank=True, upload_to="campus/notes/%Y/%m/",
                        validators=[
                            django.core.validators.FileExtensionValidator(
                                allowed_extensions=[
                                    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt",
                                    "jpg", "jpeg", "png", "webp", "mp4", "m4v", "mov", "webm",
                                ]
                            ),
                            campus.resource_rules.validate_resource_size,
                        ],
                    ),
                ),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("file_size", models.PositiveBigIntegerField(default=0)),
                ("is_published", models.BooleanField(default=True)),
                ("is_pinned", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="class_notes", to="campus.campus"
                    ),
                ),
                (
                    "live_session",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="class_notes", to="campus.campuslivesession",
                    ),
                ),
                (
                    "posted_by",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="campus_class_notes_posted", to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="class_notes", to="campus.section"
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="class_notes", to="campus.subject"
                    ),
                ),
            ],
            options={
                "ordering": ["-is_pinned", "-created_at"],
                "indexes": [
                    models.Index(fields=["section", "subject", "-created_at"], name="cnote_sec_subj_idx"),
                    models.Index(fields=["live_session", "-created_at"], name="cnote_live_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("body", ""), _negated=True) | models.Q(("file", ""), _negated=True),
                        name="class_note_has_content",
                    ),
                ],
            },
        ),
        # ---- StudentNote ----------------------------------------------------
        migrations.CreateModel(
            name="StudentNote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("at_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("text", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="campus_student_notes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="student_notes", to="campus.campus"
                    ),
                ),
                (
                    "live_session",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="student_notes", to="campus.campuslivesession",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="student_notes", to="campus.section"
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="student_notes", to="campus.subject"
                    ),
                ),
            ],
            options={
                "ordering": ["at_seconds", "created_at"],
                "indexes": [
                    models.Index(fields=["author", "live_session", "at_seconds"], name="snote_author_live_idx"),
                    models.Index(fields=["author", "subject", "-created_at"], name="snote_author_subj_idx"),
                ],
            },
        ),
    ]
