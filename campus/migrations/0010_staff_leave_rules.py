# Staff leave rules: holiday calendar + weekly off + per-type quota (Campus),
# half-day part, frozen working_days, edit / admin-applied bookkeeping
# (StaffLeave) and `part` on TeacherAbsence.
import uuid

import campus.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_working_days(apps, schema_editor):
    """Old rows had no calendar: cost = calendar days (0.5 for a half day)."""
    StaffLeave = apps.get_model("campus", "StaffLeave")
    for leave in StaffLeave.objects.filter(working_days=0).iterator():
        days = 0.5 if leave.is_half_day else float((leave.date_to - leave.date_from).days + 1)
        StaffLeave.objects.filter(pk=leave.pk).update(working_days=days)


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0009_staff_leave"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="campus",
            name="weekly_off_days",
            field=models.JSONField(blank=True, default=campus.models.default_weekly_off),
        ),
        migrations.AddField(
            model_name="campus",
            name="leave_quota",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="teacherabsence",
            name="part",
            field=models.CharField(
                choices=[("full", "Full day"), ("first_half", "First half"), ("second_half", "Second half")],
                default="full",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="staffleave",
            name="half_day_part",
            field=models.CharField(
                blank=True,
                choices=[("first_half", "First half"), ("second_half", "Second half")],
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="staffleave",
            name="working_days",
            field=models.DecimalField(decimal_places=1, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="staffleave",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="staffleave",
            name="applied_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="campus_staff_leaves_applied_for",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="CampusHoliday",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date", models.DateField(db_index=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="holidays", to="campus.campus"
                    ),
                ),
            ],
            options={
                "ordering": ["date"],
                "constraints": [
                    models.UniqueConstraint(fields=("campus", "date"), name="campus_holiday_unique_date")
                ],
            },
        ),
        migrations.RunPython(backfill_working_days, migrations.RunPython.noop),
    ]
