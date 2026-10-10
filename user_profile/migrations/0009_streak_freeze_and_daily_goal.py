# Streak freeze tokens + daily goal fields (additive, safe defaults).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user_profile', '0008_automoderationflag_feature_target'),
    ]

    operations = [
        migrations.AddField(
            model_name='streak',
            name='daily_goal_minutes',
            field=models.PositiveSmallIntegerField(default=10),
        ),
        migrations.AddField(
            model_name='streak',
            name='freeze_tokens',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='streak',
            name='freezes_used_total',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='streak',
            name='goal_completed_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='streak',
            name='goals_completed_total',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
