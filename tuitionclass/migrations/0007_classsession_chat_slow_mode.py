# Live-room tools: per-session chat slow mode (0 = off).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tuitionclass', '0006_classsession_live_presence'),
    ]

    operations = [
        migrations.AddField(
            model_name='classsession',
            name='chat_slow_mode_seconds',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
