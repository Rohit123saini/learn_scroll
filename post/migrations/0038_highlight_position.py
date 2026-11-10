# P6: Highlight.position (owner's own order of the highlights row on the profile) + new default ordering.
# Purely additive: every existing row gets 0, so the old "most recently edited first" order is unchanged until
# the owner reorders. Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0037_saved_collection'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='highlight',
            options={'ordering': ['position', '-updated_at']},
        ),
        migrations.AddField(
            model_name='highlight',
            name='position',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
