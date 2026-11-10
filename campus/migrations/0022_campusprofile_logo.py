# CAMPUS SETTINGS — school logo on CampusProfile.
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0021_campus_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="campusprofile",
            name="logo",
            field=models.FileField(blank=True, null=True, upload_to="campus/logos/%Y/%m/"),
        ),
    ]
