# Feed card menu "Archive": owner-only, reversible hide. Hand-written.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0018_post_attachment_hashtag'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='is_archived',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='post',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
