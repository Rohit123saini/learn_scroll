# Doubt post upgrade: adds CoinLedger.TransactionType.DOUBT_BOUNTY (choices-only AlterField, no data change).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0014_alter_badge_rule_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="coinledger",
            name="transaction_type",
            field=models.CharField(
                choices=[
                    ("earn", "Earned"),
                    ("purchase", "Purchased"),
                    ("spend", "Spent"),
                    ("refund", "Refunded"),
                    ("gift_sent", "Gift Sent"),
                    ("gift_received", "Gift Received"),
                    ("admin_adjustment", "Admin Adjustment"),
                    ("campus_reward", "Campus Reward"),
                    ("testseries_purchase", "Test Series Purchase"),
                    ("testseries_payout", "Test Series Payout"),
                    ("withdrawal_requested", "Withdrawal Requested"),
                    ("withdrawal_completed", "Withdrawal Completed"),
                    ("withdrawal_rejected", "Withdrawal Rejected"),
                    ("streak_reward", "Streak Reward"),
                    ("referral_commission", "Referral Commission"),
                    ("doubt_bounty", "Doubt Bounty"),
                ],
                max_length=20,
            ),
        ),
    ]
