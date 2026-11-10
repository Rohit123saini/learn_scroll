# N9 notification-center extras: per-category sound / vibration choice (NotificationPreference.sound_prefs) and the
# per-class / chat / post mute (NotificationScopeMute). Hand-written (nothing was run): check with
# `python manage.py makemigrations --check --dry-run` - it must report "No changes detected".

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_channel_policy_n8'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='sound_prefs',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='NotificationScopeMute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope_type', models.CharField(choices=[('post', 'Post / reel'), ('class', 'Class'), ('conversation', 'Chat')], max_length=12)),
                ('scope_id', models.CharField(max_length=40)),
                ('muted_until', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('label', models.CharField(blank=True, default='', max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_scope_mutes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'core_notification_scope_mute',
            },
        ),
        migrations.AddConstraint(
            model_name='notificationscopemute',
            constraint=models.UniqueConstraint(fields=('user', 'scope_type', 'scope_id'), name='uniq_notification_scope_mute'),
        ),
    ]
