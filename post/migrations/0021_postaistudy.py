# Feature 1: cached AI summary / quiz / flashcards per Notes-Resource post. Hand-written.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0020_postmedia_hls_playlist'),
    ]

    operations = [
        migrations.CreateModel(
            name='PostAiStudy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('mode', models.CharField(choices=[('summary', 'Summary'), ('quiz', 'Quiz'),
                                                   ('flashcards', 'Flashcards')], max_length=12)),
                ('language', models.CharField(default='en', max_length=10)),
                ('source_hash', models.CharField(max_length=64)),
                ('payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='+', to=settings.AUTH_USER_MODEL)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='ai_study_results', to='post.post')),
            ],
            options={'db_table': 'post_ai_study'},
        ),
        migrations.AddConstraint(
            model_name='postaistudy',
            constraint=models.UniqueConstraint(fields=('post', 'mode', 'language', 'source_hash'),
                                               name='post_ai_study_uniq'),
        ),
    ]
