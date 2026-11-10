# Hand-written (Django was not available when this was authored). Run
#   python manage.py makemigrations --check
# once; if it reports a difference, delete this file and run makemigrations.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

REPORT_TARGETS = [
    ('user', 'Account'), ('post', 'Post'), ('comment', 'Comment'), ('story', 'Story'),
    ('message', 'Direct / group message'), ('class_chat', 'Live-class chat message'),
    ('live_session', 'Live class session'), ('profile_photo', 'Profile photo'),
]
REPORT_REASONS = [
    ('spam', 'Spam'), ('harassment', 'Harassment or bullying'), ('hate', 'Hate speech'),
    ('nudity', 'Nudity or sexual content'), ('violence', 'Violence or dangerous content'),
    ('self_harm', 'Self-harm'), ('scam', 'Scam or fraud'),
    ('impersonation', 'Pretending to be someone else'),
    ('copyright', 'Copyright / intellectual property'),
    ('child_safety', 'Child safety (a child is being harmed / exploited)'),
    ('other', 'Something else'),
]
FLAG_TARGETS = [
    ('post', 'Post'), ('comment', 'Comment'), ('story', 'Story'), ('bio', 'Profile bio'),
    ('message', 'Direct message'), ('feature', 'Feature request'), ('user', 'Account'),
    ('class_chat', 'Live-class chat message'), ('live_session', 'Live class session'),
    ('profile_photo', 'Profile photo'),
]


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user_profile', '0012_alter_automoderationflag_source_and_more'),
    ]

    operations = [
        # ---- ContentReport -------------------------------------------------
        migrations.AlterField(
            model_name='contentreport', name='target_type',
            field=models.CharField(choices=REPORT_TARGETS, max_length=16),
        ),
        migrations.AlterField(
            model_name='contentreport', name='reason',
            field=models.CharField(choices=REPORT_REASONS, max_length=20),
        ),
        migrations.AddField(
            model_name='contentreport', name='priority',
            field=models.CharField(choices=[('normal', 'Normal'), ('urgent', 'Urgent')], db_index=True, default='normal', max_length=6),
        ),
        migrations.AddField(
            model_name='contentreport', name='evidence',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='contentreport', name='brigading_suspected',
            field=models.BooleanField(default=False),
        ),
        migrations.AddIndex(
            model_name='contentreport',
            index=models.Index(fields=['priority', 'status', '-created_at'], name='report_queue_idx'),
        ),
        migrations.AddIndex(
            model_name='contentreport',
            index=models.Index(fields=['target_type', 'target_id'], name='report_target_idx'),
        ),
        migrations.AlterModelOptions(
            name='contentreport',
            options={
                'ordering': ['-created_at'],
                'permissions': [('handle_child_safety', 'Can work the child-safety queue and file reports to the authorities')],
            },
        ),
        migrations.AlterField(
            model_name='automoderationflag', name='target_type',
            field=models.CharField(choices=FLAG_TARGETS, max_length=16),
        ),
        migrations.CreateModel(
            name='ChildSafetyReport',
            fields=[],
            options={
                'verbose_name': 'child-safety report',
                'verbose_name_plural': 'CHILD SAFETY queue (urgent)',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('user_profile.contentreport',),
        ),
        # ---- audit trail ---------------------------------------------------
        migrations.CreateModel(
            name='ModerationAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[
                    ('auto_hold', 'Auto-hold (report threshold)'),
                    ('auto_hold_child', 'Auto-hold (child safety)'),
                    ('brigading_review', 'Sent to review - brigading suspected'),
                    ('hide', 'Staff: content hidden'),
                    ('dismiss', 'Staff: reports dismissed'),
                    ('status_change', 'Staff: report status changed'),
                    ('restore', 'Content restored'),
                    ('appeal_filed', 'Owner appealed'),
                    ('appeal_accepted', 'Appeal accepted'),
                    ('appeal_rejected', 'Appeal rejected'),
                    ('child_case_opened', 'Child-safety case opened'),
                    ('child_reported_authority', 'Child-safety: reported to authorities'),
                    ('child_case_closed', 'Child-safety case closed'),
                    ('photo_removed', 'Profile photo removed'),
                ], db_index=True, max_length=30)),
                ('actor_label', models.CharField(default='system', max_length=40)),
                ('target_type', models.CharField(max_length=16)),
                ('target_id', models.CharField(max_length=64)),
                ('reason', models.CharField(blank=True, default='', max_length=20)),
                ('report_count', models.PositiveIntegerField(default=0)),
                ('note', models.CharField(blank=True, default='', max_length=500)),
                ('owner_visible', models.BooleanField(default=False)),
                ('can_appeal', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('target_owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='moderation_actions_received', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='moderationaction',
            index=models.Index(fields=['target_type', 'target_id'], name='modaction_target_idx'),
        ),
        migrations.AddIndex(
            model_name='moderationaction',
            index=models.Index(fields=['target_owner', '-created_at'], name='modaction_owner_idx'),
        ),
        migrations.CreateModel(
            name='ModerationAppeal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(max_length=16)),
                ('target_id', models.CharField(max_length=64)),
                ('message', models.TextField(max_length=1000)),
                ('status', models.CharField(choices=[('open', 'Waiting for review'), ('accepted', 'Accepted - content restored'), ('rejected', 'Rejected - decision stands')], db_index=True, default='open', max_length=10)),
                ('decision_note', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('action', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='appeals', to='user_profile.moderationaction')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='moderation_appeals', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='moderationappeal',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'open')), fields=('user', 'target_type', 'target_id'), name='one_open_moderation_appeal'),
        ),
        migrations.CreateModel(
            name='ChildSafetyCase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(max_length=16)),
                ('target_id', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('new', 'New - needs review'), ('under_review', 'Under review'), ('reported', 'Reported to the authorities'), ('closed', 'Closed - not a child-safety issue')], db_index=True, default='new', max_length=14)),
                ('snapshot', models.JSONField(blank=True, default=dict)),
                ('first_report_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('due_at', models.DateTimeField(db_index=True)),
                ('sla_warned_at', models.DateTimeField(blank=True, null=True)),
                ('authority_name', models.CharField(blank=True, default='', max_length=200)),
                ('authority_reference', models.CharField(blank=True, default='', max_length=120)),
                ('reported_to_authority_at', models.DateTimeField(blank=True, null=True)),
                ('staff_note', models.TextField(blank=True, default='')),
                ('preserve_until', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('reported_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('reported_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='child_safety_cases', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['due_at']},
        ),
        migrations.AddConstraint(
            model_name='childsafetycase',
            constraint=models.UniqueConstraint(fields=('target_type', 'target_id'), name='one_child_safety_case_per_target'),
        ),
    ]
