from django.db import migrations, models


class Migration(migrations.Migration):
    """P7 -- edit profile polish: `show_birthday` (day + month visible on the profile, default off). (hand-written)"""

    dependencies = [
        ("login", "0011_profile_header"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="show_birthday",
            field=models.BooleanField(default=False),
        ),
    ]
