# Doubt post upgrade: PostDoubt (subject / class / exam / chapter, urgent, OCR text, coin bounty, solved_at),
# PostAnswerStep (step-wise solution) and PostAnswerAttachment (image / video in an answer).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check`.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import post.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('post', '0028_poll_quiz_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='PostDoubt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(blank=True, db_index=True, default='', max_length=80)),
                ('class_level', models.CharField(blank=True, default='', max_length=40)),
                ('exam', models.CharField(blank=True, default='', max_length=60)),
                ('chapter', models.CharField(blank=True, default='', max_length=120)),
                ('is_urgent', models.BooleanField(db_index=True, default=False)),
                ('ocr_text', models.TextField(blank=True, default='')),
                ('bounty_coins', models.PositiveIntegerField(default=0)),
                ('bounty_status', models.CharField(
                    choices=[('none', 'No bounty'), ('held', 'Held until a best answer is chosen'),
                             ('paid', 'Paid to the best answer'), ('refunded', 'Refunded to the asker')],
                    db_index=True, default='none', max_length=10)),
                ('solved_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('teachers_notified_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('bounty_paid_to', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='doubt_bounties_won', to=settings.AUTH_USER_MODEL)),
                ('post', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name='doubt_info', to='post.post')),
            ],
            options={'db_table': 'post_doubt_info'},
        ),
        migrations.AddIndex(
            model_name='postdoubt',
            index=models.Index(fields=['subject', '-created_at'], name='post_doubt__subject_5c1a0e_idx'),
        ),
        migrations.AddIndex(
            model_name='postdoubt',
            index=models.Index(fields=['is_urgent', '-created_at'], name='post_doubt__is_urge_7d2b4f_idx'),
        ),
        migrations.CreateModel(
            name='PostAnswerStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('text', models.TextField(blank=True, default='')),
                ('image', models.ImageField(
                    blank=True, max_length=255, null=True, upload_to=post.models._answer_step_upload_to)),
                ('answer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='post.postanswer')),
            ],
            options={'db_table': 'post_answer_steps', 'ordering': ['position']},
        ),
        migrations.AddConstraint(
            model_name='postanswerstep',
            constraint=models.UniqueConstraint(fields=('answer', 'position'), name='uniq_answer_step_position'),
        ),
        migrations.CreateModel(
            name='PostAnswerAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('image', 'Image'), ('video', 'Video')], max_length=5)),
                ('file', models.FileField(max_length=255, upload_to=post.models._answer_media_upload_to)),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('file_size_bytes', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('answer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='post.postanswer')),
            ],
            options={'db_table': 'post_answer_attachments', 'ordering': ['position']},
        ),
    ]
