# "Recording ready" / "new note" push claims (at-most-once, same idea as ClassResource.notified_at).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0014_recorded_classes_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="campuslivesession",
            name="recording_notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="classnote",
            name="notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
