import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Per-campus parent / child privacy switches. One new table, nothing existing is touched."""

    dependencies = [
        ("campus", "0026_soft_delete_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CampusPrivacyPolicy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("enforce_guardian_consent", models.BooleanField(default=False)),
                ("unknown_dob_is_minor", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="privacy_policy", to="campus.campus")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
