# assigments/migrations/0003_publishing_projects_rubric.py
#
# Publishing (draft / published / archived + private / link / public), a share
# slug per assignment, tags + difficulty for Explore, project support
# (submission types, rubric, link hand-ins) and rubric grading.
#
# Every column added to an existing table is nullable or has a default that
# reproduces the old behaviour ("published", "private", "assignment", empty
# lists) — safe on a table that already has rows, no data backfill needed.
#
# NOTE: hand-written (no Django available where it was authored). Run
# `python manage.py makemigrations --check --dry-run`; if it reports a
# difference, regenerate with `makemigrations assigments` and keep that file.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assigments", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="assigments",
            name="kind",
            field=models.CharField(
                choices=[("assignment", "Assignment"), ("project", "Project")],
                default="assignment",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="assigments",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                db_index=True,
                default="published",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="assigments",
            name="visibility",
            field=models.CharField(
                choices=[("private", "Private"), ("link", "Anyone with the link"), ("public", "Public")],
                db_index=True,
                default="private",
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name="assigments",
            name="public_slug",
            field=models.CharField(blank=True, max_length=24, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="assigments",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assigments",
            name="tags",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="assigments",
            name="difficulty",
            field=models.CharField(
                blank=True,
                choices=[("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")],
                max_length=6,
            ),
        ),
        migrations.AddField(
            model_name="assigments",
            name="submission_types",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="assigments",
            name="rubric",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name="assigments",
            index=models.Index(fields=["status", "visibility", "-published_at"], name="assign_explore_idx"),
        ),
        migrations.AddField(
            model_name="assigmentssubmission",
            name="link_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="assigmentssubmission",
            name="rubric_scores",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
