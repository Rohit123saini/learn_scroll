# Task 8.1 — new question types (true_false / fill_blank / numeric).
# Only widens `question_type` (max_length 4 -> 12) and adds the new choices;
# no data change, existing rows stay valid. `options` / `correct_answer` are
# JSON fields, so option images need no schema change.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0002_testseries_announced_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("text", "Text / Subjective"),
                    ("mcq", "Multiple Choice (single)"),
                    ("msq", "Multiple Select"),
                    ("list", "List-based (match / order)"),
                    ("true_false", "True / False"),
                    ("fill_blank", "Fill in the blank"),
                    ("numeric", "Numeric answer"),
                ],
                db_index=True,
                max_length=12,
            ),
        ),
    ]
