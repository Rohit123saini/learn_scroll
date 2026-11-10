# TASK T2.6 — optional public Q&A page for an answered classroom doubt.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0002_usernote_conversationparticipant_request_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='doubtquestion',
            name='is_public',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='doubtquestion',
            name='public_slug',
            field=models.CharField(blank=True, max_length=24, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='doubtquestion',
            name='public_published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
