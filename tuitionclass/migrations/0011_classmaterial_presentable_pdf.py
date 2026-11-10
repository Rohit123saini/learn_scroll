# Live room: cached PDF of a ppt/doc material so it can be presented (see office_convert.py).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tuitionclass', '0010_live_stage_only_and_guest_cohost'),
    ]

    operations = [
        migrations.AddField(
            model_name='classmaterial',
            name='presentable_pdf',
            field=models.FileField(blank=True, null=True, upload_to='class_materials/presentable/'),
        ),
        migrations.AddField(
            model_name='classmaterial',
            name='presentable_source_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
