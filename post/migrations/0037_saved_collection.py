"""P2 - SavedCollection (named, privacy-flagged folders for saved posts).

Hand-written to match post/models.py exactly - NOT run. Afterwards: `python manage.py makemigrations --check --dry-run`
must print "No changes detected".
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0036_post_options_collab_edit_history'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedCollection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=40)),
                ('is_private', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_collections', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'post_saved_collections',
                'ordering': ['created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='savedcollection',
            constraint=models.UniqueConstraint(fields=('user', 'name'), name='uniq_saved_collection_user_name'),
        ),
    ]
