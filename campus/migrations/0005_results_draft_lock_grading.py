# Results: draft/publish + sheet lock on ResultEntry, per-campus grade scale.
# Hand-written to match campus.models exactly. Existing ResultEntry rows keep
# the old "immediately published" behaviour (status default = published), so
# no student's result disappears on deploy.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0004_homework_diary"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="campus",
            name="grade_scale",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="campus",
            name="pass_percent",
            field=models.DecimalField(decimal_places=2, default=33, max_digits=5),
        ),
        migrations.AddField(
            model_name="resultentry",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("published", "Published")],
                db_index=True, default="published", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="resultentry",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="resultentry",
            name="locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="resultentry",
            name="locked_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="results_locked", to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
