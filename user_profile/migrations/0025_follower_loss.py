# P9b: FollowerLoss (per profile / day counter of followers who left, for "Profile insights"). Purely additive - a new
# table, nothing existing changes.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0024_privacysettings_content_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="FollowerLoss",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day", models.DateField()),
                ("lost", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="follower_losses",
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="followerloss",
            constraint=models.UniqueConstraint(fields=("profile", "day"), name="uniq_followerloss_profile_day"),
        ),
        migrations.AddIndex(
            model_name="followerloss",
            index=models.Index(fields=["day"], name="followerloss_day_idx"),
        ),
    ]
