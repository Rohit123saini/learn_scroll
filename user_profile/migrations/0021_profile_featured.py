# P6: ProfileFeatured (the single "Featured" slot of a profile). Purely additive - a new table, nothing existing changes.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0020_role_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileFeatured",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(
                    choices=[("test", "Test series"), ("class", "Class"), ("resource", "Resource")], max_length=10)),
                ("target_id", models.CharField(max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name="profile_featured",
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
