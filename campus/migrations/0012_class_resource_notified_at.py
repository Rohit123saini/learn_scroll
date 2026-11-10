# Class resources: push bookkeeping (`ClassResource.notified_at`).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0011_class_resources"),
    ]

    operations = [
        migrations.AddField(
            model_name="classresource",
            name="notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
