# Resources upgrade: research-paper metadata (ResourcePaper), file versions (PostAttachmentVersion, PostAttachment.version),
# searchable text (PostAttachment.text_pages / search_text / text_source) and Post.watermark_mode.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check`.
import django.db.models.deletion
import django.utils.timezone
import post.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0026_publish_fingerprint'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='watermark_mode',
            field=models.CharField(choices=[('none', 'None'), ('author', 'Author name'), ('viewer', 'Viewer name')], default='none', max_length=8),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='version',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='replaced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='version_note',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='text_pages',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='search_text',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='postattachment',
            name='text_source',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pdf_text', 'PDF text layer'), ('office', 'Office text'), ('client_ocr', 'On-device OCR'), ('server_ocr', 'Server OCR')], default='', max_length=12),
        ),
        migrations.CreateModel(
            name='PostAttachmentVersion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('version', models.PositiveSmallIntegerField()),
                ('file', models.FileField(max_length=255, upload_to=post.models._resource_upload_to)),
                ('original_name', models.CharField(max_length=255)),
                ('mime', models.CharField(max_length=100)),
                ('size', models.BigIntegerField()),
                ('pages', models.PositiveIntegerField(blank=True, null=True)),
                ('thumbnail', models.ImageField(blank=True, max_length=255, null=True, upload_to=post.models._resource_thumb_upload_to)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('archived_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('attachment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='post.postattachment')),
                ('replaced_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'post_attachment_versions', 'ordering': ['-version']},
        ),
        migrations.AddConstraint(
            model_name='postattachmentversion',
            constraint=models.UniqueConstraint(fields=('attachment', 'version'), name='post_attach_version_uniq'),
        ),
        migrations.CreateModel(
            name='ResourcePaper',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('authors', models.JSONField(blank=True, default=list)),
                ('authors_text', models.TextField(blank=True, default='')),
                ('abstract', models.TextField(blank=True, default='')),
                ('doi', models.CharField(blank=True, db_index=True, default='', max_length=150)),
                ('link', models.URLField(blank=True, default='', max_length=500)),
                ('year', models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ('venue', models.CharField(blank=True, default='', max_length=200)),
                ('post', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='paper', to='post.post')),
            ],
            options={'db_table': 'post_resource_paper'},
        ),
    ]
