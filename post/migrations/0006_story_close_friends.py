# Stories upgrade, Part 1 (Close Friends): Story.audience + the CloseFriend list.
# Existing stories keep behaving exactly as before (audience defaults to 'everyone').
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0005_post_watch_time_stats'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='story',
            name='audience',
            field=models.CharField(
                choices=[('everyone', 'Everyone'), ('close_friends', 'Close friends')],
                default='everyone',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='CloseFriend',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('friend', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='close_friend_of_entries', to=settings.AUTH_USER_MODEL)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='close_friend_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'close_friends',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['owner', '-created_at'], name='close_frien_owner_i_156220_idx')],
                'constraints': [
                    models.UniqueConstraint(fields=('owner', 'friend'), name='unique_close_friend'),
                    models.CheckConstraint(condition=models.Q(('owner', models.F('friend')), _negated=True), name='close_friend_no_self'),
                ],
            },
        ),
    ]
