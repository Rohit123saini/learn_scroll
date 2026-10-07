from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0004_sync_block_tables"),
    ]

    operations = [
        migrations.AddField(
            model_name="blockuser",
            name="block_new_accounts",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ContentReport",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_type", models.CharField(choices=[("user", "Account"), ("post", "Post"), ("comment", "Comment"), ("story", "Story")], max_length=10)),
                ("target_id", models.CharField(max_length=64)),
                ("reason", models.CharField(choices=[("spam", "Spam"), ("harassment", "Harassment or bullying"), ("hate", "Hate speech"), ("nudity", "Nudity or sexual content"), ("violence", "Violence or dangerous content"), ("self_harm", "Self-harm"), ("scam", "Scam or fraud"), ("impersonation", "Pretending to be someone else"), ("other", "Something else")], max_length=20)),
                ("details", models.TextField(blank=True, default="", max_length=1000)),
                ("status", models.CharField(choices=[("open", "Open"), ("reviewed", "Reviewed"), ("actioned", "Action taken"), ("dismissed", "Dismissed")], db_index=True, default="open", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reported_user", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="reports_received", to=settings.AUTH_USER_MODEL)),
                ("reporter", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="reports_filed", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="contentreport",
            constraint=models.UniqueConstraint(fields=("reporter", "target_type", "target_id"), name="unique_content_report"),
        ),
        migrations.AddIndex(
            model_name="contentreport",
            index=models.Index(fields=["reported_user", "status"], name="report_user_status_idx"),
        ),
    ]
