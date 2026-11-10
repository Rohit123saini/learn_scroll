# S4b: Post.allow_reshare / Post.allow_save (per-post switches; the account-level default comes from
# user_profile.PrivacySettings). Hand-written (nothing was run): `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("post", "0039_muted_account_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="allow_reshare",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="post",
            name="allow_save",
            field=models.BooleanField(default=True),
        ),
    ]
