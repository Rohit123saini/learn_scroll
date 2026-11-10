# FEE-5 — online (Razorpay UPI / card) fee payments.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0015_recording_note_push_claims"),
    ]

    operations = [
        migrations.AddField(
            model_name="feepayment",
            name="gateway_payment_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="feepayment",
            name="gateway_method",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="feepayment",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="feepayment",
            name="payment_mode",
            field=models.CharField(
                choices=[
                    ("wallet", "Wallet (Coins)"),
                    ("online", "Online (UPI / Card)"),
                    ("cash", "Cash"),
                    ("cheque", "Cheque"),
                    ("bank_transfer", "Bank Transfer"),
                    ("other", "Other"),
                ],
                max_length=15,
            ),
        ),
    ]
