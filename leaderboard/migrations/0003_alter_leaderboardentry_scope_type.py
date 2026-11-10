# S5 — hand-written (makemigrations / migrate NAHI chalaya). Sirf `scope_type` ke choices me naya "study_time"
# (study room presence minutes ka leaderboard). Koi table / column nahi badla. `makemigrations --check --dry-run` se confirm karo.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leaderboard', '0002_alter_leaderboardentry_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='leaderboardentry',
            name='scope_type',
            field=models.CharField(choices=[('test_series', 'Test Series'), ('campus_section', 'Campus Section'), ('engagement', 'App-wide Engagement'), ('study_time', 'Study Room Time')], db_index=True, max_length=20),
        ),
    ]
