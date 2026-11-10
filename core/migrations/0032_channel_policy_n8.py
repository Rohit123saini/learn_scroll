# N8 e-mail / SMS / WhatsApp channel setup: verified e-mail proof on NotificationPreference + ChannelConsent (DPDP consent
# ledger) + ChannelSuppression (bounce / complaint / unsubscribe / STOP) + ChannelSendLog (daily caps).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check`.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_testseries_due_soon_notif_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='email_verified_address',
            field=models.CharField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='notificationpreference',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ChannelConsent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(choices=[('email', 'E-mail'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=10)),
                ('granted', models.BooleanField()),
                ('address', models.CharField(blank=True, default='', max_length=254)),
                ('text_version', models.CharField(blank=True, default='', max_length=20)),
                ('source', models.CharField(blank=True, default='app', max_length=20)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channel_consents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'core_channel_consent',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ChannelSuppression',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(max_length=10)),
                ('address', models.CharField(max_length=254)),
                ('reason', models.CharField(choices=[('hard_bounce', 'Hard bounce'), ('soft_bounce_limit', 'Repeated soft bounces'), ('complaint', 'Spam complaint'), ('unsubscribe', 'Unsubscribed'), ('stop', 'STOP reply'), ('invalid', 'Invalid address / number')], max_length=20)),
                ('detail', models.CharField(blank=True, default='', max_length=200)),
                ('soft_bounces', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'core_channel_suppression',
                'constraints': [models.UniqueConstraint(fields=('channel', 'address'), name='uniq_channel_suppression')],
            },
        ),
        migrations.CreateModel(
            name='ChannelSendLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(max_length=10)),
                ('notif_type', models.CharField(blank=True, default='', max_length=40)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channel_send_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'core_channel_send_log',
            },
        ),
        migrations.AddIndex(
            model_name='channelconsent',
            index=models.Index(fields=['user', 'channel', '-created_at'], name='chconsent_user_ch_idx'),
        ),
        migrations.AddIndex(
            model_name='channelsendlog',
            index=models.Index(fields=['user', 'channel', '-created_at'], name='chsendlog_user_ch_idx'),
        ),
    ]
