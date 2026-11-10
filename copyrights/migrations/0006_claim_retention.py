# Hand-written (Django was not available when this was authored). Run
#   python manage.py makemigrations --check
# once; if it reports a difference, delete this file and run makemigrations.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copyrights', '0005_trustedclaimant_tier'),
    ]

    operations = [
        migrations.AddField(
            model_name='copyrightclaim',
            name='retention_hold',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='copyrightclaim',
            name='pii_erased_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
