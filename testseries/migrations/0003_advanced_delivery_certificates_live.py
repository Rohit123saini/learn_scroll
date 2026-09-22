# testseries/migrations/0003_advanced_delivery_certificates_live.py
#
# Advanced test-series features:
#   * TestSeries   — delivery mode / window, proctoring, pass mark +
#                    certificate, result release, share slug
#   * Question     — negative marks, topic, difficulty, explanation
#   * QuestionResponse — penalty, time spent
#   * TestAttempt  — server-side started_at / deadline, autosave draft,
#                    late flag, percentage / passed, integrity counter
#   * NEW models   — TestCertificate, TestLiveSession, TestRecording,
#                    TestProctorEvent
#
# Every new column on an existing table is either nullable or has a default
# that reproduces the old behaviour, so this migration is safe on a table
# that already has rows and needs no data backfill.
#
# NOTE: hand-written (no Django available where it was authored). After
# pulling it, run `python manage.py makemigrations --check --dry-run` — if it
# reports any difference, run `makemigrations testseries` and keep the
# generated file instead of this one.
import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("testseries", "0002_testattempt_multi_attempt_constraint"),
    ]

    operations = [
        # ------------------------------------------------------ TestSeries
        migrations.AddField(
            model_name="testseries",
            name="delivery_mode",
            field=models.CharField(
                choices=[("self_paced", "Self paced"), ("scheduled", "Scheduled window"), ("live", "Live (video)")],
                db_index=True,
                default="self_paced",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="testseries",
            name="starts_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testseries",
            name="ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testseries",
            name="late_entry_minutes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="testseries",
            name="proctoring",
            field=models.CharField(
                choices=[("off", "Off"), ("camera", "Camera (recorded)")], default="off", max_length=8
            ),
        ),
        migrations.AddField(
            model_name="testseries",
            name="record_live",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="testseries",
            name="pass_percentage",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="testseries",
            name="certificate_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="testseries",
            name="certificate_title",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="testseries",
            name="result_release",
            field=models.CharField(
                choices=[
                    ("instant", "Instantly after checking"),
                    ("after_end", "After the test window ends"),
                    ("manual", "When the creator releases them"),
                ],
                default="instant",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="testseries",
            name="results_released_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testseries",
            name="show_solutions",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="testseries",
            name="share_slug",
            field=models.CharField(blank=True, max_length=24, null=True, unique=True),
        ),
        # -------------------------------------------------------- Question
        migrations.AddField(
            model_name="question",
            name="negative_marks",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="question",
            name="topic",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="question",
            name="difficulty",
            field=models.CharField(
                blank=True,
                choices=[("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")],
                max_length=6,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="explanation",
            field=models.TextField(blank=True),
        ),
        # ------------------------------------------------ QuestionResponse
        migrations.AddField(
            model_name="questionresponse",
            name="penalty",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="questionresponse",
            name="time_spent_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        # ----------------------------------------------------- TestAttempt
        migrations.AddField(
            model_name="testattempt",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="deadline_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="draft_answers",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="draft_saved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="submitted_late",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="percentage",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="passed",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testattempt",
            name="integrity_flags",
            field=models.PositiveIntegerField(default=0),
        ),
        # ------------------------------------------------- TestCertificate
        migrations.CreateModel(
            name="TestCertificate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.CharField(db_index=True, max_length=24, unique=True)),
                ("title", models.CharField(max_length=200)),
                ("score", models.PositiveIntegerField()),
                ("total_marks", models.PositiveIntegerField()),
                ("percentage", models.FloatField()),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_reason", models.CharField(blank=True, max_length=200)),
                (
                    "attempt",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="certificate",
                        to="testseries.testattempt",
                    ),
                ),
                (
                    "series",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="certificates",
                        to="testseries.testseries",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="testseries_certificates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-issued_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("series", "student"), name="unique_certificate_per_student_series"
                    )
                ],
            },
        ),
        # ------------------------------------------------ TestLiveSession
        migrations.CreateModel(
            name="TestLiveSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("room_name", models.CharField(max_length=80, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("scheduled", "Scheduled"), ("live", "Live"), ("ended", "Ended")],
                        db_index=True,
                        default="scheduled",
                        max_length=10,
                    ),
                ),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "host",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="testseries_live_hosted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "series",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="live_session",
                        to="testseries.testseries",
                    ),
                ),
            ],
        ),
        # --------------------------------------------------- TestRecording
        migrations.CreateModel(
            name="TestRecording",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[("live_session", "Live session"), ("proctor", "Proctoring")],
                        db_index=True,
                        max_length=12,
                    ),
                ),
                ("room_name", models.CharField(max_length=80)),
                ("egress_id", models.CharField(db_index=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("recording", "Recording"), ("ready", "Ready"), ("failed", "Failed")],
                        db_index=True,
                        default="recording",
                        max_length=10,
                    ),
                ),
                ("url", models.URLField(blank=True, max_length=500)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("duration_seconds", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "attempt",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recordings",
                        to="testseries.testattempt",
                    ),
                ),
                (
                    "series",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recordings",
                        to="testseries.testseries",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
                "indexes": [models.Index(fields=["series", "kind"], name="ts_rec_series_kind_idx")],
            },
        ),
        # ------------------------------------------------ TestProctorEvent
        migrations.CreateModel(
            name="TestProctorEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("app_background", "App sent to background"),
                            ("tab_switch", "Switched app / tab"),
                            ("face_missing", "Face not visible"),
                            ("multiple_faces", "Multiple faces"),
                            ("camera_off", "Camera turned off"),
                            ("network_drop", "Network dropped"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="proctor_events",
                        to="testseries.testattempt",
                    ),
                ),
            ],
            options={
                "ordering": ["occurred_at"],
                "indexes": [models.Index(fields=["attempt", "event_type"], name="ts_pev_attempt_type_idx")],
            },
        ),
    ]
