# CAMPUS SETTINGS — grading system, half-day weekdays, school profile.
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
import uuid

import django.db.models.deletion
from django.db import migrations, models

import campus.models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0020_safety"),
    ]

    operations = [
        migrations.AddField(
            model_name="campus",
            name="grading_system",
            field=models.CharField(
                choices=[("cbse", "CBSE letter grades"), ("percent", "Percentage only"), ("gpa", "GPA")],
                default="cbse", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="half_day_weekdays",
            field=models.JSONField(blank=True, default=campus.models.default_weekly_off),
        ),
        migrations.CreateModel(
            name="CampusProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tagline", models.CharField(blank=True, max_length=120)),
                ("board", models.CharField(
                    blank=True, max_length=12,
                    choices=[
                        ("cbse", "CBSE"), ("icse", "ICSE / ISC"), ("state", "State board"), ("ib", "IB"),
                        ("cambridge", "Cambridge (IGCSE)"), ("university", "University / college"), ("other", "Other"),
                    ],
                )),
                ("affiliation_no", models.CharField(blank=True, max_length=30)),
                ("udise_code", models.CharField(blank=True, max_length=11)),
                ("principal_name", models.CharField(blank=True, max_length=100)),
                ("established_year", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("address_line1", models.CharField(blank=True, max_length=150)),
                ("address_line2", models.CharField(blank=True, max_length=150)),
                ("city", models.CharField(blank=True, max_length=80)),
                ("state", models.CharField(blank=True, max_length=80)),
                ("pincode", models.CharField(blank=True, max_length=10)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("website", models.URLField(blank=True, max_length=200)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                related_name="profile", to="campus.campus")),
            ],
        ),
    ]
