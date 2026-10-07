from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0002_badge_userpreference_daily_limit_minutes_and_more"),
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
                ],
                max_length=20,
            ),
        ),
    ]
