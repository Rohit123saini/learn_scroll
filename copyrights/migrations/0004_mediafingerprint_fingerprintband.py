# Hand-written (Django was not available when this was authored). Run
#   python manage.py makemigrations --check
# once; if it reports a difference, delete this file and run makemigrations.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copyrights', '0003_copyrightappeal'),
    ]

    operations = [
        migrations.CreateModel(
            name='MediaFingerprint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(db_index=True, max_length=10)),
                ('algo', models.CharField(db_index=True, max_length=12)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('source_sha256', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('source_name', models.CharField(blank=True, default='', max_length=300)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claim', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='copyrights.copyrightclaim')),
                ('legal_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='copyrights.legalorder')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='FingerprintBand',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(db_index=True, max_length=32)),
                ('fingerprint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bands', to='copyrights.mediafingerprint')),
            ],
        ),
        migrations.AddIndex(
            model_name='fingerprintband',
            index=models.Index(fields=['key', 'fingerprint'], name='cr_fpband_key_idx'),
        ),
    ]
