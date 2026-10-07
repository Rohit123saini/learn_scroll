from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0011_postmedia_medium_720_postmedia_thumb_320_posttag'),
    ]

    operations = [
        migrations.AddField(
            model_name='highlight',
            name='cover_zoom',
            field=models.FloatField(default=1.0),
        ),
        migrations.AddField(
            model_name='highlight',
            name='cover_x',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='highlight',
            name='cover_y',
            field=models.FloatField(default=0.0),
        ),
    ]
