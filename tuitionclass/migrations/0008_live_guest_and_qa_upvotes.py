# Live room: guest/stage state on SessionParticipant + upvotes on ClassQuery.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tuitionclass', '0007_classsession_chat_slow_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessionparticipant',
            name='guest_state',
            field=models.CharField(blank=True, choices=[('', 'None'), ('invited', 'Invited'), ('on_stage', 'On stage')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='sessionparticipant',
            name='guest_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='classquery',
            name='upvote_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='ClassQueryUpvote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('query', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upvotes', to='tuitionclass.classquery')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='query_upvotes', to=settings.AUTH_USER_MODEL)),
            ],
            options={'unique_together': {('query', 'user')}},
        ),
    ]
