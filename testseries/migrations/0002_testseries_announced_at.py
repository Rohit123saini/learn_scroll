# TASK 9.1 + 9.2
#   * `announced_at` — idempotency guard for the "series published" announcement
#     (notification + notice board entry) of class / campus series.
#   * data fix — class / campus series are ALWAYS free (policy.ALWAYS_FREE_SOURCES).
#     Rows created while a tuition-class teacher could still charge are zeroed
#     here; `TestSeries.save()` only fixes a row the next time it is saved.
#   * every already-published class / campus series is stamped as announced, so
#     deploying this never re-announces old tests to students.
from django.db import migrations, models
from django.db.models import F, Q

CONTEXT_SOURCES = ["campus", "tuitionclass"]


def force_free_and_stamp_announced(apps, schema_editor):
    TestSeries = apps.get_model("testseries", "TestSeries")
    context_rows = TestSeries.objects.filter(source__in=CONTEXT_SOURCES)
    context_rows.filter(Q(is_paid=True) | Q(price_coins__gt=0)).update(is_paid=False, price_coins=0)
    context_rows.filter(status="published", announced_at__isnull=True).update(announced_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="testseries",
            name="announced_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(force_free_and_stamp_announced, migrations.RunPython.noop),
    ]
