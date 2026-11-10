# Lectures (round 4): VideoChapter, VideoCaptionTrack, VideoProgress, VideoSeries, VideoSeriesItem + PostAttachment.for_media.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check` before applying.
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import post.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0029_doubt_post_upgrade'),
    ]

    operations = [
        migrations.AddField(
            model_name='postattachment',
            name='for_media',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='notes', to='post.postmedia'),
        ),
        migrations.CreateModel(
            name='VideoChapter',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('start_seconds', models.PositiveIntegerField()),
                ('title', models.CharField(max_length=120)),
                ('media', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='chapters', to='post.postmedia')),
            ],
            options={'db_table': 'post_video_chapters', 'ordering': ['start_seconds']},
        ),
        migrations.AddConstraint(
            model_name='videochapter',
            constraint=models.UniqueConstraint(fields=('media', 'start_seconds'), name='post_video_chapter_uniq'),
        ),
        migrations.CreateModel(
            name='VideoCaptionTrack',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('language', models.CharField(choices=[('en', 'English'), ('hi', 'Hindi')], max_length=5)),
                ('source', models.CharField(
                    choices=[('auto', 'Automatic'), ('upload', 'Uploaded')], default='upload', max_length=8)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'),
                             ('failed', 'Failed')],
                    db_index=True, default='ready', max_length=12)),
                ('file', models.FileField(
                    blank=True, max_length=255, null=True, upload_to=post.models._caption_upload_to)),
                ('job_name', models.CharField(blank=True, default='', max_length=120)),
                ('error', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('media', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='caption_tracks', to='post.postmedia')),
            ],
            options={'db_table': 'post_video_captions'},
        ),
        migrations.AddConstraint(
            model_name='videocaptiontrack',
            constraint=models.UniqueConstraint(fields=('media', 'language'), name='post_video_caption_uniq'),
        ),
        migrations.CreateModel(
            name='VideoProgress',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('position_seconds', models.PositiveIntegerField(default=0)),
                ('duration_seconds', models.PositiveIntegerField(blank=True, null=True)),
                ('completed', models.BooleanField(default=False)),
                ('last_watched_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('media', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='progress_rows', to='post.postmedia')),
                ('post', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='video_progress', to='post.post')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='video_progress',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'post_video_resume', 'ordering': ['-last_watched_at']},
        ),
        migrations.AddConstraint(
            model_name='videoprogress',
            constraint=models.UniqueConstraint(fields=('user', 'media'), name='post_video_resume_uniq'),
        ),
        migrations.AddIndex(
            model_name='videoprogress',
            index=models.Index(fields=['user', '-last_watched_at'], name='post_video_resume_recent_idx'),
        ),
        migrations.CreateModel(
            name='VideoSeries',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True, default='')),
                ('cover', models.ImageField(blank=True, null=True, upload_to='posts/series_covers/%Y/%m/')),
                ('kind', models.CharField(
                    choices=[('creator', 'Creator collection'), ('classroom', 'Classroom course')],
                    default='creator', max_length=10)),
                ('classroom_id', models.CharField(blank=True, db_index=True, default='', max_length=40)),
                ('is_public', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='video_series',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'post_video_series', 'ordering': ['-updated_at']},
        ),
        migrations.CreateModel(
            name='VideoSeriesItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('position', models.PositiveIntegerField(default=0)),
                ('section', models.CharField(blank=True, default='', max_length=80)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='series_items', to='post.post')),
                ('series', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='items', to='post.videoseries')),
            ],
            options={'db_table': 'post_video_series_items', 'ordering': ['position', 'added_at']},
        ),
        migrations.AddConstraint(
            model_name='videoseriesitem',
            constraint=models.UniqueConstraint(fields=('series', 'post'), name='post_video_series_item_uniq'),
        ),
        migrations.AddIndex(
            model_name='videoseriesitem',
            index=models.Index(fields=['series', 'position'], name='post_vseries_pos_idx'),
        ),
    ]
