# X12 - auto recording: Classroom.recording_auto, ClassSession recording timing fields,
# SessionChapter, RecordingWatch, RecordingNote, RecordingSave. Hand-written on top of 0011 (nothing was run).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tuitionclass', '0011_classmaterial_presentable_pdf'),
    ]

    operations = [
        migrations.AddField(
            model_name='classroom',
            name='recording_auto',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='classsession',
            name='last_egress_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='classsession',
            name='recording_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='classsession',
            name='auto_record_attempted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='classsession',
            name='recording_duration_seconds',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='SessionChapter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('start', 'Class started'), ('present', 'Presentation'), ('poll', 'Poll'), ('quiz', 'Quiz'), ('manual', 'Added by teacher')], default='manual', max_length=10)),
                ('title', models.CharField(max_length=120)),
                ('offset_seconds', models.PositiveIntegerField(default=0)),
                ('ref_type', models.CharField(blank=True, default='', max_length=12)),
                ('ref_id', models.PositiveIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chapters', to='tuitionclass.classsession')),
            ],
            options={
                'ordering': ['offset_seconds', 'id'],
                'indexes': [models.Index(fields=['session', 'offset_seconds'], name='tc_chapter_sess_off_idx')],
            },
        ),
        migrations.CreateModel(
            name='RecordingWatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position_seconds', models.PositiveIntegerField(default=0)),
                ('watched_seconds', models.PositiveIntegerField(default=0)),
                ('duration_seconds', models.PositiveIntegerField(blank=True, null=True)),
                ('completed', models.BooleanField(default=False)),
                ('first_watched_at', models.DateTimeField(auto_now_add=True)),
                ('last_watched_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_watches', to='tuitionclass.classsession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_watches', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['session', '-last_watched_at'], name='tc_recwatch_sess_last_idx')],
                'unique_together': {('session', 'user')},
            },
        ),
        migrations.CreateModel(
            name='RecordingNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at_seconds', models.PositiveIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('attached_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('material', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_notes', to='tuitionclass.classmaterial')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_notes', to='tuitionclass.classsession')),
            ],
            options={
                'ordering': ['at_seconds', 'id'],
                'unique_together': {('session', 'material')},
            },
        ),
        migrations.CreateModel(
            name='RecordingSave',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('lecture', 'Lecture'), ('reel', 'Reel'), ('post', 'Post')], max_length=10)),
                ('status', models.CharField(choices=[('pending', 'Queued'), ('processing', 'Processing'), ('ready', 'Ready'), ('failed', 'Failed')], db_index=True, default='pending', max_length=12)),
                ('title', models.CharField(max_length=150)),
                ('description', models.TextField(blank=True, default='')),
                ('start_seconds', models.PositiveIntegerField(default=0)),
                ('end_seconds', models.PositiveIntegerField()),
                ('visibility', models.CharField(choices=[('class_only', 'This class only'), ('followers', 'Followers'), ('public', 'Public')], default='class_only', max_length=12)),
                ('include_chapters', models.BooleanField(default=True)),
                ('series_id', models.CharField(blank=True, default='', max_length=40)),
                ('post_id', models.CharField(blank=True, default='', max_length=40)),
                ('error', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_saves', to=settings.AUTH_USER_MODEL)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recording_saves', to='tuitionclass.classsession')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['session', '-created_at'], name='tc_recsave_sess_idx')],
            },
        ),
    ]
