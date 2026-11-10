# S8: Learning preferences on UserPreference (study reminder, focus-mode schedule, content level, content languages).
# All additive with safe defaults (reminder off, schedule off, no level, no language) - existing rows change nothing.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0017_privacysettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreference",
            name="study_reminder_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="study_reminder_time",
            field=models.TimeField(default=datetime.time(18, 0)),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="study_reminder_days",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="last_study_reminder_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="focus_schedule_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="focus_schedule",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="content_level",
            field=models.CharField(
                blank=True,
                choices=[("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")],
                default="",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="content_languages",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
