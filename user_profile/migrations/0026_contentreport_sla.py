# P-F15: ContentReport review deadline (sla_due_at) + one-time breach stamp (sla_breached_at). Purely additive, both
# columns nullable, so no data migration is needed (old reports simply have no deadline; the sweep ignores them).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0025_follower_loss"),
    ]

    operations = [
        migrations.AddField(
            model_name="contentreport",
            name="sla_due_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="contentreport",
            name="sla_breached_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
