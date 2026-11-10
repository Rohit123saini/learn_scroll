from django.db import migrations, models


class Migration(migrations.Migration):
    """P10b -- avatar placeholder: `User.profile_photo_blurhash` (BlurHash string, "" = none). Purely additive. (hand-written)

    Check with `python manage.py makemigrations --check --dry-run`; old accounts get a hash from
    `python manage.py backfill_profile_blurhash`.
    """

    dependencies = [
        ("login", "0012_user_show_birthday"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_photo_blurhash",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
