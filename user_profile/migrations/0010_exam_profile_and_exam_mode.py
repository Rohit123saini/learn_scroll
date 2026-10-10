# Exam profile + Exam Mode fields on UserPreference (additive, safe defaults).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user_profile', '0009_streak_freeze_and_daily_goal'),
    ]

    operations = [
        migrations.AddField(
            model_name='userpreference',
            name='class_level',
            field=models.CharField(blank=True, choices=[('6', 'Class 6'), ('7', 'Class 7'), ('8', 'Class 8'), ('9', 'Class 9'), ('10', 'Class 10'), ('11', 'Class 11'), ('12', 'Class 12'), ('dropper', 'Dropper'), ('graduate', 'Graduate'), ('other', 'Other')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='userpreference',
            name='exam_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userpreference',
            name='exam_mode',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='userpreference',
            name='exam_target',
            field=models.CharField(blank=True, choices=[('jee', 'JEE'), ('neet', 'NEET'), ('board', 'Board exams'), ('upsc', 'UPSC'), ('other', 'Other')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='userpreference',
            name='focus_subjects',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
