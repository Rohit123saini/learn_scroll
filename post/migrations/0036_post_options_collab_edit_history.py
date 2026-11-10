"""Round 5 (post options): Post.comments_limit / edited_at / scheduled_timezone + PostCollaborator + PostEditVersion.

These model changes existed in post/models.py without a migration (a fresh DB / deploy would have crashed with
"no such column"). Hand-written to match the models exactly - run `python manage.py makemigrations --check --dry-run`
afterwards: it must print "No changes detected".
"""
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0035_reel_sounds'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='comments_limit',
            field=models.CharField(
                choices=[('everyone', 'Everyone'), ('followers', 'Followers only')],
                default='everyone', max_length=12),
        ),
        migrations.AddField(
            model_name='post',
            name='edited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='post',
            name='scheduled_timezone',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.CreateModel(
            name='PostCollaborator',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')],
                    db_index=True, default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('invited_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='post_collab_invites_sent', to=settings.AUTH_USER_MODEL)),
                ('post', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='collaborators', to='post.post')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='post_collaborations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_collaborators',
                'ordering': ['created_at'],
                'unique_together': {('post', 'user')},
            },
        ),
        migrations.CreateModel(
            name='PostEditVersion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('version', models.PositiveIntegerField()),
                ('title', models.CharField(blank=True, default='', max_length=300)),
                ('content', models.TextField(blank=True, default='')),
                ('hashtags', models.JSONField(blank=True, default=list)),
                ('visibility', models.CharField(default='public', max_length=20)),
                ('changed_fields', models.JSONField(blank=True, default=list)),
                ('saved_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('edited_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('post', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='edit_versions', to='post.post')),
            ],
            options={
                'db_table': 'post_edit_versions',
                'ordering': ['-version'],
                'unique_together': {('post', 'version')},
            },
        ),
    ]
