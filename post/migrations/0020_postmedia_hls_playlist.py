# HLS rendition of videos (360p / 540p / 720p): storage name of the master playlist. Hand-written.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0019_post_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='postmedia',
            name='hls_playlist',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
