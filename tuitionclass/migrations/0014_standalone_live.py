# X12 L-C - standalone "Go Live": Classroom.is_personal, ClassSession live_* fields, LiveReminderSub.
# Hand-written on top of 0013 (nothing was run - check with `makemigrations --check --dry-run`).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tuitionclass', '0013_live_safety'),
    ]

    operations = [
        migrations.AddField(
            model_name='classroom',
            name='is_personal',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_standalone',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_go_now',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_audience',
            field=models.CharField(
                choices=[('class', 'Class members only'), ('followers', 'Followers'), ('public', 'Public'), ('pass', 'Pass holders')],
                default='class', max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_title',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_topic',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_thumbnail',
            field=models.ImageField(blank=True, null=True, upload_to='live_thumbnails/'),
        ),
        migrations.AddField(
            model_name='classsession',
            name='live_timezone',
            field=models.CharField(default='Asia/Kolkata', max_length=50),
        ),
        migrations.AddField(
            model_name='classsession',
            name='reminder_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='LiveReminderSub',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='live_reminder_subs', to='tuitionclass.classsession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='live_reminder_subs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['user', '-created_at'], name='tc_livereminder_user_idx')],
                'constraints': [models.UniqueConstraint(fields=('session', 'user'), name='uniq_live_reminder_session_user')],
            },
        ),
    ]
