# S4b: PrivacySettings -- account-level defaults for new posts / stories (audience, comments limit, allow reshare / save).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0023_userpreference_appearance_data_storage"),
    ]

    operations = [
        migrations.AddField(
            model_name="privacysettings",
            name="default_story_audience",
            field=models.CharField(
                choices=[("everyone", "Everyone"), ("followers", "Followers"), ("close_friends", "Close friends")],
                default="everyone", max_length=20),
        ),
        migrations.AddField(
            model_name="privacysettings",
            name="default_post_visibility",
            field=models.CharField(
                choices=[("public", "Public"), ("connections", "Followers"), ("close_friends", "Close friends")],
                default="public", max_length=20),
        ),
        migrations.AddField(
            model_name="privacysettings",
            name="default_comments_limit",
            field=models.CharField(
                choices=[("everyone", "Everyone"), ("followers", "Followers only")],
                default="everyone", max_length=12),
        ),
        migrations.AddField(
            model_name="privacysettings",
            name="default_allow_reshare",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="privacysettings",
            name="default_allow_save",
            field=models.BooleanField(default=True),
        ),
    ]
