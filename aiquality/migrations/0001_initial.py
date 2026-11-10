import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AiCallLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("surface", models.CharField(max_length=24)),
                ("feature", models.CharField(max_length=32)),
                ("model", models.CharField(blank=True, default="", max_length=60)),
                ("prompt_version", models.CharField(blank=True, default="", max_length=24)),
                ("prompt_hash", models.CharField(blank=True, default="", max_length=12)),
                ("ok", models.BooleanField(default=True)),
                ("error_kind", models.CharField(blank=True, default="", max_length=40)),
                ("has_image", models.BooleanField(default=False)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("tokens_in", models.PositiveIntegerField(default=0)),
                ("tokens_out", models.PositiveIntegerField(default=0)),
                ("tokens_estimated", models.BooleanField(default=False)),
                ("cost_micro_usd", models.PositiveBigIntegerField(default=0)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="aicalllog",
            index=models.Index(fields=["surface", "feature", "created_at"], name="aiq_call_feat_idx"),
        ),
        migrations.AddIndex(
            model_name="aicalllog",
            index=models.Index(fields=["user", "created_at"], name="aiq_call_user_idx"),
        ),
        migrations.CreateModel(
            name="AiFeedback",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("surface", models.CharField(max_length=24)),
                ("feature", models.CharField(max_length=32)),
                ("ref_id", models.CharField(blank=True, default="", max_length=80)),
                ("prompt_version", models.CharField(blank=True, default="", max_length=24)),
                ("model", models.CharField(blank=True, default="", max_length=60)),
                ("rating", models.SmallIntegerField(default=0)),
                ("reason", models.CharField(blank=True, choices=[("wrong_answer", "Wrong answer"), ("unclear", "Confusing / unclear"), ("off_topic", "Not about my question"), ("wrong_language", "Wrong language"), ("unsafe", "Unsafe / inappropriate"), ("too_long", "Too long / too short"), ("other", "Something else")], default="", max_length=16)),
                ("comment", models.CharField(blank=True, default="", max_length=500)),
                ("reported_wrong", models.BooleanField(db_index=True, default=False)),
                ("excerpt", models.CharField(blank=True, default="", max_length=800)),
                ("dedupe_key", models.CharField(max_length=40)),
                ("status", models.CharField(choices=[("new", "New"), ("confirmed_wrong", "Confirmed wrong"), ("fixed", "Fixed (prompt / data)"), ("dismissed", "Dismissed (answer was fine)")], db_index=True, default="new", max_length=16)),
                ("staff_note", models.CharField(blank=True, default="", max_length=500)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("call", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="feedback", to="aiquality.aicalllog")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_feedback", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="aifeedback",
            constraint=models.UniqueConstraint(fields=["user", "dedupe_key"], name="aiq_feedback_one_per_output"),
        ),
        migrations.AddIndex(
            model_name="aifeedback",
            index=models.Index(fields=["status", "-created_at"], name="aiq_fb_status_idx"),
        ),
        migrations.AddIndex(
            model_name="aifeedback",
            index=models.Index(fields=["surface", "feature", "prompt_version"], name="aiq_fb_prompt_idx"),
        ),
    ]
