# Hand-written (makemigrations / migrate NAHI chalaya). 0014 ke baad. N3 — push reliability + token hygiene:
#   `DeviceToken` par device_id / device_name / app_version / os_version / session_jti / last_seen_at / failure_count /
#   last_failure_at / last_failure_code + (user, last_seen_at) index.
#   Sab naye columns default/blank/null wale hain => purani rows ke liye data-migration nahi chahiye.
#   `last_seen_at` ka default `timezone.now` (callable): migration ke waqt purani rows "abhi dekhi gayi" maani jaati hain,
#   isliye stale-cleanup pehle din kisi ko nahi hatata — wo tab hatata hai jab token STALE_DAYS tak dobara register hi na ho.
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0014_studyroom_chain_and_idle'),
    ]

    operations = [
        migrations.AddField(
            model_name='devicetoken',
            name='device_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='device_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='app_version',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='os_version',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='session_jti',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='last_seen_at',
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='failure_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='last_failure_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='devicetoken',
            name='last_failure_code',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddIndex(
            model_name='devicetoken',
            index=models.Index(fields=['user', 'last_seen_at'], name='msg_devtok_user_seen_idx'),
        ),
    ]
