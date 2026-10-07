# TASK 9.2 — system-posted notices (e.g. "new test published in this class").
# `source_type`/`source_id` identify what the notice announces; the partial unique
# constraint makes posting it twice impossible (idempotent announce).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tuitionclass", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="notice",
            name="source_type",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
        migrations.AddField(
            model_name="notice",
            name="source_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="notice",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_id__isnull", False)),
                fields=("classroom", "source_type", "source_id"),
                name="uniq_notice_per_source",
            ),
        ),
    ]
