# P-F8: adds the `spaced_repetition` key to FeatureFlag.Key (state-only AlterField - choices only, no DB change; same convention
# as 0041). Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run` -> "No changes detected".

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0041_featureflag_social_learning_key'),
    ]

    operations = [
        migrations.AlterField(
            model_name='featureflag',
            name='key',
            field=models.CharField(choices=[('live', 'Live classes / live streams'), ('reels_upload', 'Reels upload'), ('coin_purchase', 'Coin purchase (top-up)'), ('ai_features', 'AI features'), ('cashout', 'Coin cash-out (needs env COIN_CASHOUT_ENABLED=true too)'), ('social_learning', 'Social learning (study-room Pomodoro, study partner, seasons)'), ('spaced_repetition', 'Spaced repetition (Revision deck SM-2 review)')], max_length=32),
        ),
    ]
