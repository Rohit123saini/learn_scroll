# P-J2 - remote feature flags (kill switches + staged rollout). Hand-written (nothing was run); `makemigrations --check` must say "No changes".
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0038_funnel_onboarding_step'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FeatureFlag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(choices=[('live', 'Live classes / live streams'), ('reels_upload', 'Reels upload'), ('coin_purchase', 'Coin purchase (top-up)'), ('ai_features', 'AI features'), ('cashout', 'Coin cash-out (needs env COIN_CASHOUT_ENABLED=true too)')], max_length=32)),
                ('platform', models.CharField(choices=[('all', 'All platforms'), ('android', 'Android'), ('ios', 'iOS')], default='all', max_length=10)),
                ('enabled', models.BooleanField(default=True, help_text='OFF = kill switch: the app hides the feature AND the server refuses it (403 feature_disabled) for everyone.')),
                ('rollout_percent', models.PositiveSmallIntegerField(default=100, help_text='0-100. Staged rollout: share of devices that SEE the feature (stable per device). 100 = everyone. Only the app hides it for the rest; the server refuses only when the switch above is OFF.')),
                ('min_app_version', models.CharField(blank=True, default='', help_text='Optional, e.g. 1.5.0. Older builds do not get the feature (the app hides it).', max_length=20)),
                ('message', models.CharField(blank=True, default='', help_text='Shown instead of the feature when it is off (English).', max_length=300)),
                ('message_hi', models.CharField(blank=True, default='', help_text='Same, Hindi.', max_length=300)),
                ('note', models.CharField(blank=True, default='', help_text='Internal: why it was changed (incident / ticket).', max_length=300)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Feature flag',
                'verbose_name_plural': 'Feature flags',
                'db_table': 'core_feature_flag',
                'ordering': ['key', 'platform'],
            },
        ),
        migrations.AddConstraint(
            model_name='featureflag',
            constraint=models.UniqueConstraint(fields=('key', 'platform'), name='uniq_feature_flag_key_platform'),
        ),
    ]
