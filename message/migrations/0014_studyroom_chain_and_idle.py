# Hand-written (makemigrations / migrate NAHI chalaya). 0013 (S8) ke baad. Do chhote leftovers:
#   * S2: `StudyRoomParticipant.chain_started_at` — C13 study-room token-refresh chain ka start, ab cache ki jagah DB me
#     (null=True => purani rows pe data-migration nahi; refresh par None mile to chain ab se shuru, jaise cache-evict pe hota tha).
#   * S5: `StudyRoomPresence.activity_tracked / active_seconds / last_active_at` — idle time (purani rows `activity_tracked=False`
#     => poora connected time gina jata hai, kisi ki purani minutes nahi badalti).
#   * S4: `StudyRoomReport.reviewed_by / reviewed_at / review_note` — staff review audit trail.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('message', '0013_group_join_rules_safety'),
    ]

    operations = [
        migrations.AddField(
            model_name='studyroomparticipant',
            name='chain_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroompresence',
            name='activity_tracked',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='studyroompresence',
            name='active_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='studyroompresence',
            name='last_active_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroomreport',
            name='reviewed_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='study_room_reports_reviewed', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='studyroomreport',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studyroomreport',
            name='review_note',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
    ]
