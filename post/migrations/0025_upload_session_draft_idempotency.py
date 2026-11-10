# H20 - UploadSession (chunked upload for every media kind), Draft (server draft sync), IdempotencyRecord
# (duplicate-submit guard). Hand-written (nothing was run): check with `python manage.py makemigrations --check`.
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0024_readingprogress_posthelpful'),
    ]

    operations = [
        migrations.CreateModel(
            name='UploadSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('idempotency_key', models.CharField(max_length=64)),
                ('kind', models.CharField(choices=[('post', 'Post'), ('reel', 'Reel'), ('story', 'Story'), ('resource', 'Resource')], max_length=12)),
                ('status', models.CharField(choices=[('uploading', 'Uploading'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='uploading', max_length=12)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('files', models.JSONField(blank=True, default=list)),
                ('chunk_size', models.PositiveIntegerField(default=5242880)),
                ('result_status', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('result_body', models.JSONField(blank=True, null=True)),
                ('result_object_id', models.CharField(blank=True, default='', max_length=64)),
                ('last_error_code', models.CharField(blank=True, default='', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField()),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upload_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_upload_session',
                'constraints': [models.UniqueConstraint(fields=('user', 'idempotency_key'), name='post_upload_session_idem_uniq')],
                'indexes': [
                    models.Index(fields=['user', '-created_at'], name='post_upload_sess_user_idx'),
                    models.Index(fields=['status', 'expires_at'], name='post_upload_sess_exp_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='Draft',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('client_id', models.UUIDField()),
                ('kind', models.CharField(choices=[('post', 'Post'), ('reel', 'Reel'), ('story', 'Story'), ('resource', 'Resource')], max_length=12)),
                ('data', models.JSONField(blank=True, default=dict)),
                ('media', models.JSONField(blank=True, default=list)),
                ('version', models.PositiveIntegerField(default=1)),
                ('client_updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_drafts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_draft',
                'ordering': ['-client_updated_at'],
                'constraints': [models.UniqueConstraint(fields=('user', 'client_id'), name='post_draft_client_uniq')],
                'indexes': [models.Index(fields=['user', 'is_deleted', '-client_updated_at'], name='post_draft_user_idx')],
            },
        ),
        migrations.CreateModel(
            name='IdempotencyRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(max_length=40)),
                ('key', models.CharField(max_length=64)),
                ('state', models.CharField(default='in_progress', max_length=12)),
                ('response_status', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('response_body', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='idempotency_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_idempotency_record',
                'constraints': [models.UniqueConstraint(fields=('user', 'scope', 'key'), name='post_idempotency_uniq')],
                'indexes': [models.Index(fields=['created_at'], name='post_idem_created_idx')],
            },
        ),
    ]
