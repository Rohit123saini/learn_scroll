# Hand-written for P-F5 follow-up (incremental season scoring). Nothing was run: check with
# `python manage.py makemigrations --check --dry-run` -> "No changes detected" (else regenerate with makemigrations social_learning).
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("social_learning", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="studyseason",
            name="rollup_through",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studyseason",
            name="rollup_sig",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.CreateModel(
            name="SeasonUserTotal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("minutes", models.PositiveIntegerField(default=0)),
                ("pomodoros", models.PositiveIntegerField(default=0)),
                ("active_days", models.PositiveIntegerField(default=0)),
                ("season", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rollup_totals", to="social_learning.studyseason")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="seasonusertotal",
            constraint=models.UniqueConstraint(fields=("season", "user"), name="sl_one_rollup_per_user"),
        ),
    ]
