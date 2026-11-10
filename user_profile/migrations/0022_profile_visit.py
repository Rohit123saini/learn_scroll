# P9: ProfileVisit (per profile / viewer / day visit counter for the owner's "Profile insights"). Purely additive - a new
# table, nothing existing changes.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0021_profile_featured"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileVisit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day", models.DateField()),
                ("visits", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="profile_visits_received",
                    to=settings.AUTH_USER_MODEL)),
                ("viewer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="profile_visits_made",
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="profilevisit",
            constraint=models.UniqueConstraint(
                fields=("profile", "viewer", "day"), name="uniq_profilevisit_profile_viewer_day"),
        ),
        migrations.AddIndex(
            model_name="profilevisit",
            index=models.Index(fields=["profile", "day"], name="profilevisit_profile_day_idx"),
        ),
        migrations.AddIndex(
            model_name="profilevisit",
            index=models.Index(fields=["day"], name="profilevisit_day_idx"),
        ),
    ]
