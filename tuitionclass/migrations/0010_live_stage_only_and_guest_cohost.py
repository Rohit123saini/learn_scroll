# Live room: stage-only (listener) mode, guest co-host flag, quiz fields on poll templates.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tuitionclass', '0009_live_quiz_and_presenting'),
    ]

    operations = [
        migrations.AddField(
            model_name='classsession',
            name='stage_only',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sessionparticipant',
            name='guest_cohost',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='polltemplate',
            name='correct_option_index',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='polltemplate',
            name='explanation',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
    ]
