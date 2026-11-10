# Publish gate - PublishFingerprint (duplicate-content memory). Hand-written (nothing was run):
# check with `python manage.py makemigrations --check`.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0025_upload_session_draft_idempotency'),
    ]

    operations = [
        migrations.CreateModel(
            name='PublishFingerprint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('surface', models.CharField(max_length=12)),
                ('target_id', models.CharField(max_length=64)),
                ('hash_kind', models.CharField(choices=[('text', 'Text'), ('media', 'Media file')], max_length=6)),
                ('digest', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publish_fingerprints', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_publish_fingerprint',
                'indexes': [
                    models.Index(fields=['hash_kind', 'digest', '-created_at'], name='post_pubfp_digest_idx'),
                    models.Index(fields=['user', '-created_at'], name='post_pubfp_user_idx'),
                    models.Index(fields=['created_at'], name='post_pubfp_created_idx'),
                ],
            },
        ),
    ]
