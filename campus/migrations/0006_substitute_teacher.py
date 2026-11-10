# Substitute teacher (absent teacher -> per-period, per-date substitute).
# Hand-written to match campus.models.TeacherAbsence / SubstituteAssignment
# exactly; index + constraint names are explicit in the models so
# `makemigrations --check` reports no drift.
#
# NOTE: if your tree already has a later campus migration than 0005, change the
# dependency below to your latest one (the two new tables are independent).

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0005_results_draft_lock_grading"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherAbsence",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date_from", models.DateField()),
                ("date_to", models.DateField()),
                ("reason", models.CharField(blank=True, max_length=200)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="teacher_absences", to="campus.campus")),
                ("marked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_teacher_absences_marked", to=settings.AUTH_USER_MODEL)),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="absences", to="campus.staffprofile")),
            ],
            options={
                "ordering": ["-date_from", "-created_at"],
                "indexes": [
                    models.Index(fields=["campus", "date_from", "date_to"], name="teacher_absence_range_idx"),
                    models.Index(fields=["staff", "is_active"], name="teacher_absence_staff_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("date_to__gte", models.F("date_from"))), name="teacher_absence_range_valid"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SubstituteAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("date", models.DateField()),
                ("note", models.CharField(blank=True, max_length=200)),
                ("status", models.CharField(choices=[("active", "Active"), ("cancelled", "Cancelled")], db_index=True, default="active", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("absence", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="substitutions", to="campus.teacherabsence")),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_substitutes_assigned", to=settings.AUTH_USER_MODEL)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="substitute_assignments", to="campus.campus")),
                ("entry", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="substitutions", to="campus.timetableentry")),
                ("original_staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="substituted_out", to="campus.staffprofile")),
                ("substitute", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="substitute_duties", to="campus.staffprofile")),
            ],
            options={
                "ordering": ["date", "entry__time_slot__start_time", "created_at"],
                "indexes": [
                    models.Index(fields=["campus", "date"], name="substitute_campus_date_idx"),
                    models.Index(fields=["substitute", "date"], name="substitute_staff_date_idx"),
                    models.Index(fields=["entry", "date"], name="substitute_entry_date_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("status", "active")), fields=("entry", "date"), name="unique_active_substitute_per_period"),
                ],
            },
        ),
    ]
