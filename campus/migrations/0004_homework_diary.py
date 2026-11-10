# Homework Diary (daily, class-wise) — hand-written to match
# campus.models.HomeworkDiaryEntry exactly. Index names are explicit in the
# model so `makemigrations --check` reports no drift.

import common.attachment_validators
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campus', '0003_t4_visibility_modes_doubts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeworkDiaryEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('diary_date', models.DateField(default=django.utils.timezone.localdate)),
                ('due_date', models.DateField()),
                ('attachment', models.FileField(blank=True, null=True, upload_to='campus/homework_diary/%Y/%m/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp']), common.attachment_validators.validate_attachment_size])),
                ('is_active', models.BooleanField(default=True)),
                ('due_reminder_sent_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('campus', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='homework_diary_entries', to='campus.campus')),
                ('posted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='campus_homework_diary_posted', to=settings.AUTH_USER_MODEL)),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='homework_diary_entries', to='campus.section')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='homework_diary_entries', to='campus.academicsession')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='homework_diary_entries', to='campus.subject')),
            ],
            options={
                'ordering': ['-diary_date', '-created_at'],
                'indexes': [
                    models.Index(fields=['section', '-diary_date'], name='hw_diary_section_date_idx'),
                    models.Index(fields=['section', 'due_date'], name='hw_diary_section_due_idx'),
                    models.Index(fields=['campus', 'session', '-diary_date'], name='hw_diary_campus_sess_idx'),
                    models.Index(fields=['due_date', 'due_reminder_sent_at'], name='hw_diary_due_reminder_idx'),
                ],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('due_date__gte', models.F('diary_date'))), name='homework_diary_due_not_before_date'),
                ],
            },
        ),
    ]
