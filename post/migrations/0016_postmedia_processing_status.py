# V1 — video playback pipeline: PostMedia.processing_status.
#
# Default 'ready' on purpose: every row that already exists (images, docs and
# old videos alike) keeps behaving exactly as before. Only videos uploaded
# after this change start as 'pending' (set by post.signals) and are moved to
# 'ready' / 'failed' by post.tasks.process_video_for_streaming. Old videos can
# be converted with `manage.py process_videos --all`.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0015_story_takedown_hidden'),
    ]

    operations = [
        migrations.AddField(
            model_name='postmedia',
            name='processing_status',
            field=models.CharField(
                choices=[
                    ('ready', 'Ready'),
                    ('pending', 'Pending'),
                    ('processing', 'Processing'),
                    ('failed', 'Failed'),
                ],
                default='ready',
                max_length=12,
            ),
        ),
    ]
