# N9b: NotificationScopeMute gets the `section` scope (campus section mute) and `hide_bell` (optional: skip the bell row
# as well as the push). Hand-written (nothing was run): check with
# `python manage.py makemigrations --check --dry-run` - it must report "No changes detected".

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0035_purchase_receipt_notif_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationscopemute',
            name='hide_bell',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='notificationscopemute',
            name='scope_type',
            field=models.CharField(choices=[('post', 'Post / reel'), ('class', 'Class'), ('conversation', 'Chat'), ('section', 'Campus section')], max_length=12),
        ),
    ]
