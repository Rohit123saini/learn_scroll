from django.db import migrations, models


class Migration(migrations.Migration):
    """P1 -- profile header: cover_photo, location, username_changed_at, verified_reason. (hand-written)

    MAX_PROFILE_LINKS 3 -> 5 needs no migration: the field validator is a function that reads the constant.
    """

    dependencies = [
        ("login", "0010_connected_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="cover_photo",
            field=models.ImageField(blank=True, null=True, upload_to="profile_covers/"),
        ),
        migrations.AddField(
            model_name="user",
            name="location",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="user",
            name="username_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="verified_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("identity", "Verified account"),
                    ("teacher", "Verified teacher"),
                    ("campus", "Verified by campus"),
                    ("creator", "Verified creator"),
                    ("official", "Official account"),
                ],
                default="",
                max_length=12,
            ),
        ),
    ]
