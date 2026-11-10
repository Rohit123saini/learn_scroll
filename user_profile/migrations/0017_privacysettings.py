# S4: PrivacySettings (who can message / call / tag / mention / add me, online status, followers list, search).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0016_userpreference_login_alerts"),
    ]

    operations = [
        migrations.CreateModel(
            name="PrivacySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("call_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("tag_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("mention_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("group_add_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("online_status_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("followers_list_from", models.CharField(
                    choices=[("everyone", "Everyone"), ("connections", "People I follow and my followers"), ("nobody", "Nobody")],
                    default="everyone", max_length=12)),
                ("hide_from_search", models.BooleanField(default=False)),
                ("find_by_phone", models.BooleanField(default=True)),
                ("find_by_email", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="privacy",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "privacy settings",
                "verbose_name_plural": "privacy settings",
            },
        ),
    ]
