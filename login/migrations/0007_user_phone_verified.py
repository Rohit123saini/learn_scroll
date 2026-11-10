from django.db import migrations, models


class Migration(migrations.Migration):
    """L1 gap 4 -- track whether a phone number was proven with an OTP.

    Existing rows default to False (their phone was never verified). New phones
    are only ever saved together with phone_verified=True.
    """

    dependencies = [
        ("login", "0006_authtoken_device_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="phone_verified",
            field=models.BooleanField(default=False),
        ),
    ]
