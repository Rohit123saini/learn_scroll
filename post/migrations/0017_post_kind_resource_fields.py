# P1 (partial) — Post.kind + resource fields, PostMedia.alt_text, extended visibility choices.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0016_postmedia_processing_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='kind',
            field=models.CharField(
                choices=[('post', 'Post'), ('reel', 'Reel'), ('doubt', 'Doubt'),
                         ('poll', 'Poll'), ('resource', 'Notes / Resource')],
                db_index=True, default='post', max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='post',
            name='subject',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='post',
            name='level',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='post',
            name='topics',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='post',
            name='download_allowed',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='post',
            name='visibility',
            field=models.CharField(
                choices=[('public', 'Public'), ('connections', 'Followers'),
                         ('close_friends', 'Close friends'), ('class_campus', 'My class / campus'),
                         ('private', 'Private')],
                default='public', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='postmedia',
            name='alt_text',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
