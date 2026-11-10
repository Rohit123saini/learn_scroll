# T1.1 (doubt photo attachment) + T1.3 (multi-answer / best answer /
# teacher-verified) + T1.4 (list-query index).
#
#   * DoubtQuestion.image                         (T1.1)
#   * DoubtQuestion.text -> blank=True            (T1.1, photo-only doubt)
#   * DoubtQuestion.answers_count / has_best_answer / has_verified_answer (T1.3)
#   * index doubt_group_votes_idx                 (T1.4)
#   * new model DoubtAnswer (+ one-best-per-doubt partial unique constraint)
#   * data migration: every already-answered doubt gets one DoubtAnswer row
#     (teacher answer, verified, best) so old data shows up in the new UI.

import django.db.models.deletion
import message.models
import uuid
from django.conf import settings
from django.db import migrations, models


def backfill_answers(apps, schema_editor):
    DoubtQuestion = apps.get_model('message', 'DoubtQuestion')
    DoubtAnswer = apps.get_model('message', 'DoubtAnswer')

    qs = DoubtQuestion.objects.filter(is_answered=True).exclude(answer_text='')
    batch = []
    for d in qs.iterator(chunk_size=500):
        batch.append(DoubtAnswer(
            doubt_id=d.pk,
            author_id=d.answered_by_id,
            text=d.answer_text,
            # Pehle sirf teacher (admin/mod) ya testseries-teacher hi answer
            # de sakta tha -> teacher answer, auto-verified, aur resolve
            # karne wala answer hone ki wajah se best bhi.
            is_teacher_answer=True,
            is_best=True,
            is_teacher_verified=True,
            verified_by_id=d.answered_by_id,
            verified_at=d.answered_at,
        ))
        if len(batch) >= 500:
            DoubtAnswer.objects.bulk_create(batch)
            batch = []
    if batch:
        DoubtAnswer.objects.bulk_create(batch)

    # Denormalized summary flags (single UPDATE, answers_count = 1 for those).
    answered_ids = DoubtAnswer.objects.values_list('doubt_id', flat=True)
    DoubtQuestion.objects.filter(pk__in=answered_ids).update(
        answers_count=1, has_best_answer=True, has_verified_answer=True,
    )


def noop_reverse(apps, schema_editor):
    # DoubtAnswer table reverse step me drop ho jaati hai; legacy
    # `answer_text` columns waise bhi kabhi touch nahi hue.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0004_doubtquestion_doubt_text_trgm_gin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='doubtquestion',
            name='image',
            field=models.ImageField(blank=True, max_length=255, null=True, upload_to=message.models.doubt_image_upload_path),
        ),
        migrations.AlterField(
            model_name='doubtquestion',
            name='text',
            field=models.TextField(blank=True, default='', max_length=2000),
        ),
        migrations.AddField(
            model_name='doubtquestion',
            name='answers_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='doubtquestion',
            name='has_best_answer',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='doubtquestion',
            name='has_verified_answer',
            field=models.BooleanField(default=False),
        ),
        migrations.AddIndex(
            model_name='doubtquestion',
            index=models.Index(fields=['group', '-upvotes_count', '-created_at'], name='doubt_group_votes_idx'),
        ),
        migrations.CreateModel(
            name='DoubtAnswer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('text', models.TextField(max_length=4000)),
                ('is_teacher_answer', models.BooleanField(default=False)),
                ('is_best', models.BooleanField(default=False)),
                ('is_teacher_verified', models.BooleanField(default=False)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='doubt_answers', to=settings.AUTH_USER_MODEL)),
                ('best_marked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('doubt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='message.doubtquestion')),
                ('verified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='doubt_answers_verified', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-is_best', '-is_teacher_verified', '-is_teacher_answer', 'created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='doubtanswer',
            index=models.Index(fields=['doubt', 'is_best'], name='doubtanswer_doubt_best_idx'),
        ),
        migrations.AddIndex(
            model_name='doubtanswer',
            index=models.Index(fields=['doubt', 'author'], name='doubtanswer_doubt_author_idx'),
        ),
        migrations.AddConstraint(
            model_name='doubtanswer',
            constraint=models.UniqueConstraint(condition=models.Q(('is_best', True)), fields=('doubt',), name='doubtanswer_one_best_per_doubt'),
        ),
        migrations.RunPython(backfill_answers, noop_reverse),
    ]
