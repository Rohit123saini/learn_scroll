# Live class — one-tap "Go live" from the timetable + automatic attendance.
# Hand-written to match campus.models exactly (explicit index / constraint
# names, so `makemigrations --check` shows no drift):
#   * Campus.live_attendance_auto / _min_percent / _late_minutes
#   * CampusLiveSession: timetable_entry, occurrence_date, started_at, ended_at,
#     started_by, live_heartbeat_at, attendance_built_at + one-per-period-per-day
#     partial unique constraint
#   * CampusLiveParticipant (who was in the room, for how long)
#   * Attendance: source, minutes_present, live_session
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0012_class_resource_notified_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ---- Campus knobs -------------------------------------------------
        migrations.AddField(
            model_name="campus",
            name="live_attendance_auto",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="campus",
            name="live_attendance_min_percent",
            field=models.PositiveSmallIntegerField(
                default=50,
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="live_attendance_late_minutes",
            field=models.PositiveSmallIntegerField(
                default=10,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(120)],
            ),
        ),
        # ---- CampusLiveSession --------------------------------------------
        migrations.AddField(
            model_name="campuslivesession",
            name="timetable_entry",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="live_sessions", to="campus.timetableentry",
            ),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="occurrence_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="ended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="started_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="campus_live_sessions_started", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="live_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campuslivesession",
            name="attendance_built_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="campuslivesession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("timetable_entry__isnull", False), models.Q(("status", "cancelled"), _negated=True)),
                fields=("timetable_entry", "occurrence_date"),
                name="campus_live_one_per_entry_day",
            ),
        ),
        # ---- CampusLiveParticipant ----------------------------------------
        migrations.CreateModel(
            name="CampusLiveParticipant",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("joined_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("left_at", models.DateTimeField(blank=True, null=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="participants",
                        to="campus.campuslivesession",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="campus_live_participations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["joined_at"],
                "indexes": [models.Index(fields=["session", "user"], name="campus_liveprt_sess_user_idx")],
            },
        ),
        # ---- Attendance ----------------------------------------------------
        migrations.AddField(
            model_name="attendance",
            name="source",
            field=models.CharField(
                choices=[("manual", "Marked by a teacher"), ("auto", "Automatic (live class)")],
                default="manual", editable=False, max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="attendance",
            name="minutes_present",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="attendance",
            name="live_session",
            field=models.ForeignKey(
                blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="attendance_rows", to="campus.campuslivesession",
            ),
        ),
    ]
