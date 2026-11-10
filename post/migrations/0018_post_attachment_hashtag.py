# P1 — PostAttachment (resource files) + Hashtag (normalised tags with counts). Hand-written.
import django.db.models.deletion
import django.utils.timezone
import post.models
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0017_post_kind_resource_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='Hashtag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('posts_count', models.PositiveIntegerField(db_index=True, default=0)),
                ('last_used_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={'db_table': 'post_hashtags', 'ordering': ['-posts_count', 'name']},
        ),
        migrations.CreateModel(
            name='PostAttachment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('pdf', 'PDF'), ('doc', 'Document'), ('image', 'Image'), ('video', 'Video')], max_length=10)),
                ('file', models.FileField(max_length=255, upload_to=post.models._resource_upload_to)),
                ('original_name', models.CharField(max_length=255)),
                ('mime', models.CharField(max_length=100)),
                ('size', models.BigIntegerField()),
                ('pages', models.PositiveIntegerField(blank=True, null=True)),
                ('thumbnail', models.ImageField(blank=True, max_length=255, null=True, upload_to=post.models._resource_thumb_upload_to)),
                ('alt_text', models.CharField(blank=True, default='', max_length=500)),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='post.post')),
            ],
            options={'db_table': 'post_attachments', 'ordering': ['order', 'created_at']},
        ),
        migrations.AddIndex(
            model_name='postattachment',
            index=models.Index(fields=['post', 'order'], name='post_attach_post_id_order_idx'),
        ),
    ]
