# Hand-written for P-F8 (no `makemigrations` was run). Please run `python manage.py makemigrations --check --dry-run`;
# it must report "No changes detected". If it shows a diff, regenerate this file with `makemigrations spaced_repetition`.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("post", "0042_post_title_trgm_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RevisionCard",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("ease", models.FloatField(default=2.5)),
                ("interval_days", models.PositiveIntegerField(default=0)),
                ("repetitions", models.PositiveIntegerField(default=0)),
                ("lapses", models.PositiveIntegerField(default=0)),
                ("relearning", models.BooleanField(default=False)),
                ("next_review_at", models.DateTimeField(db_index=True)),
                ("last_reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("last_grade", models.CharField(blank=True, default="", max_length=5)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("post", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revision_cards", to="post.post")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revision_cards", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "revision_cards"},
        ),
        migrations.CreateModel(
            name="RevisionReview",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("grade", models.CharField(choices=[("again", "Again"), ("hard", "Hard"), ("good", "Good"), ("easy", "Easy")], max_length=5)),
                ("reviewed_at", models.DateTimeField(db_index=True)),
                ("interval_before", models.PositiveIntegerField(default=0)),
                ("interval_after", models.PositiveIntegerField(default=0)),
                ("ease_after", models.FloatField(default=2.5)),
                ("was_new", models.BooleanField(default=False)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("card", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reviews", to="spaced_repetition.revisioncard")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revision_reviews", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "revision_reviews"},
        ),
        migrations.AddConstraint(
            model_name="revisioncard",
            constraint=models.UniqueConstraint(fields=("user", "post"), name="uniq_revision_card_user_post"),
        ),
        migrations.AddIndex(
            model_name="revisioncard",
            index=models.Index(fields=["user", "next_review_at"], name="revcard_user_due_idx"),
        ),
        migrations.AddIndex(
            model_name="revisionreview",
            index=models.Index(fields=["user", "-reviewed_at"], name="revreview_user_time_idx"),
        ),
    ]
