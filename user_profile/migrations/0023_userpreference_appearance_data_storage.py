# S2 / R14: UserPreference.appearance + UserPreference.data_storage (JSON, default {}). Purely additive - two new
# columns with a default, nothing existing changes, no data migration needed.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0022_profile_visit"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreference",
            name="appearance",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="userpreference",
            name="data_storage",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
