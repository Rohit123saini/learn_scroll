# COMMUNICATION — Notice.kind / audience / status / scheduled_for / published_at / recipient_count.
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
from django.db import migrations, models
from django.db.models import F


def backfill_published_at(apps, schema_editor):
    """Every existing notice already went out when it was created."""
    Notice = apps.get_model("campus", "Notice")
    Notice.objects.filter(published_at__isnull=True).update(published_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0018_finance"),
    ]

    operations = [
        migrations.AddField(
            model_name="notice",
            name="kind",
            field=models.CharField(
                choices=[("normal", "Notice"), ("emergency", "Emergency broadcast")],
                db_index=True, default="normal", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="notice",
            name="audience",
            field=models.CharField(
                choices=[
                    ("everyone", "Students, parents and staff"),
                    ("students", "Students only"),
                    ("parents", "Parents only"),
                    ("staff", "Staff only"),
                ],
                default="everyone", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="notice",
            name="status",
            field=models.CharField(
                choices=[("published", "Published"), ("scheduled", "Scheduled"), ("cancelled", "Cancelled")],
                db_index=True, default="published", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="notice",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notice",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notice",
            name="recipient_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="notice",
            index=models.Index(fields=["status", "scheduled_for"], name="campus_notice_due_idx"),
        ),
        migrations.RunPython(backfill_published_at, migrations.RunPython.noop),
    ]
