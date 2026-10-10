from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0005_block_new_accounts_contentreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutoModerationFlag",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_type", models.CharField(choices=[("post", "Post"), ("comment", "Comment"), ("story", "Story"), ("bio", "Profile bio"), ("message", "Direct message")], max_length=10)),
                ("target_id", models.CharField(max_length=64)),
                ("reason", models.CharField(max_length=40)),
                ("source", models.CharField(choices=[("wordlist", "Word list / pattern"), ("ai", "AI model")], default="wordlist", max_length=10)),
                ("severity", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], db_index=True, default="medium", max_length=6)),
                ("snippet", models.CharField(blank=True, default="", max_length=200)),
                ("text_hash", models.CharField(max_length=40)),
                ("status", models.CharField(choices=[("open", "Open"), ("reviewed", "Reviewed"), ("actioned", "Action taken"), ("dismissed", "Dismissed (false positive)")], db_index=True, default="open", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="automod_flags", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="automoderationflag",
            index=models.Index(fields=["status", "-created_at"], name="automod_status_idx"),
        ),
        migrations.AddIndex(
            model_name="automoderationflag",
            index=models.Index(fields=["user", "status"], name="automod_user_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="automoderationflag",
            constraint=models.UniqueConstraint(fields=("target_type", "target_id", "text_hash"), name="unique_automod_flag"),
        ),
    ]
