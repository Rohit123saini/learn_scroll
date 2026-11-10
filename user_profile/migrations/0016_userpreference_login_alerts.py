# S3: UserPreference.login_alerts (e-mail on sign-in from a new device). Hand-written (nothing was run):
# check with `python manage.py makemigrations --check`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0015_coinledger_doubt_bounty"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreference",
            name="login_alerts",
            field=models.BooleanField(default=True),
        ),
    ]
