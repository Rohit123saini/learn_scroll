# Hand-written (Django was not available when this was authored). Run
#   python manage.py makemigrations --check
# once; if it reports a difference, delete this file and run makemigrations.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copyrights', '0004_mediafingerprint_fingerprintband'),
    ]

    operations = [
        migrations.AddField(
            model_name='trustedclaimant',
            name='tier',
            field=models.CharField(
                choices=[('verified', 'Verified (held at once)'), ('publisher', 'Verified publisher (fast track)')],
                default='verified', max_length=10),
        ),
        migrations.AddField(
            model_name='trustedclaimant',
            name='valid_until',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Empty = no expiry. After this moment the account is an ordinary claimant again (re-vet it).'),
        ),
    ]
