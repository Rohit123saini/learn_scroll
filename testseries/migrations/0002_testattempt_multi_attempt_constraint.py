# Task 28 — replace TestAttempt's (series, student)-only uniqueness
# with (series, student, attempt_number), so a second/third/etc.
# attempt for the same series+student can exist as its own row instead
# of colliding at the DB level with the first one.
#
# Depends directly on 0001_initial — the screenshot of
# testseries/migrations/ confirms that's the only migration in this
# app so far (just __init__.py + 0001_initial.py), so this is 0002,
# no placeholder guessing needed.
#
# No data migration needed: the OLD constraint made it impossible for
# more than one TestAttempt row to ever exist per (series, student), so
# every existing row already has attempt_number=1 (the field's
# default) — nothing to backfill or de-duplicate before adding the new
# constraint.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="testattempt",
            name="unique_attempt_per_student_per_series",
        ),
        migrations.AddConstraint(
            model_name="testattempt",
            constraint=models.UniqueConstraint(
                fields=["series", "student", "attempt_number"],
                name="unique_attempt_per_student_series_number",
            ),
        ),
    ]