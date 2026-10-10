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
            name="SupportTicket",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("subject", models.CharField(max_length=120)),
                ("category", models.CharField(choices=[("account", "Account / login"), ("payment", "Coins / payments"), ("class", "Classes"), ("test", "Tests / assignments"), ("safety", "Safety / abuse"), ("other", "Something else")], default="other", max_length=10)),
                ("status", models.CharField(choices=[("open", "Open"), ("answered", "Answered"), ("resolved", "Resolved"), ("closed", "Closed")], db_index=True, default="open", max_length=10)),
                ("has_unread_reply", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_message_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_tickets", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-last_message_at"]},
        ),
        migrations.AddIndex(
            model_name="supportticket",
            index=models.Index(fields=["user", "-last_message_at"], name="support_ticket_user_idx"),
        ),
        migrations.CreateModel(
            name="SupportMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("is_staff", models.BooleanField(default=False)),
                ("body", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sender", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_messages", to=settings.AUTH_USER_MODEL)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="support.supportticket")),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="BugReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=120)),
                ("description", models.TextField(max_length=4000)),
                ("screen", models.CharField(blank=True, default="", max_length=80)),
                ("app_version", models.CharField(blank=True, default="", max_length=40)),
                ("platform", models.CharField(blank=True, default="", max_length=20)),
                ("device_info", models.CharField(blank=True, default="", max_length=120)),
                ("screenshot", models.ImageField(blank=True, null=True, upload_to="support/bugs/%Y/%m/")),
                ("status", models.CharField(choices=[("new", "New"), ("triaged", "Triaged"), ("fixed", "Fixed"), ("wont_fix", "Won't fix")], db_index=True, default="new", max_length=10)),
                ("admin_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bug_reports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="FeatureRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="", max_length=1000)),
                ("status", models.CharField(choices=[("open", "Open for votes"), ("planned", "Planned"), ("in_progress", "In progress"), ("shipped", "Shipped"), ("declined", "Not planned")], db_index=True, default="open", max_length=12)),
                ("votes_count", models.PositiveIntegerField(db_index=True, default=0)),
                ("is_hidden", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="feature_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-votes_count", "-created_at"]},
        ),
        migrations.CreateModel(
            name="FeatureVote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="votes", to="support.featurerequest")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feature_votes", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="featurevote",
            constraint=models.UniqueConstraint(fields=("request", "user"), name="unique_feature_vote"),
        ),
    ]
