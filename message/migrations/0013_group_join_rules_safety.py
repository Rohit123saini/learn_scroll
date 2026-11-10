# S8 — hand-written (makemigrations / migrate NAHI chalaya). Discovery -> room safety:
#   * Group.join_approval_required / room_rules / room_rules_version
#   * naya table GroupRulesAcceptance (kisne kaunsa rules-version accept kiya)
# 0012 (S5) ke baad ki hai. Purane groups: approval OFF, rules blank (=> default rules), version 1.
# `makemigrations --check --dry-run` se confirm karo.
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0012_studyroom_presence'),
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='join_approval_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='group',
            name='room_rules',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='group',
            name='room_rules_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name='GroupRulesAcceptance',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('version', models.PositiveIntegerField(default=1)),
                ('accepted_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rules_acceptances', to='message.group')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_rules_acceptances', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['group', 'version'], name='msg_gra_group_version_idx'),
                ],
                'unique_together': {('group', 'user')},
            },
        ),
    ]
