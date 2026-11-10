# S9: WalletSettings (confirm purchases + daily/monthly spend limit) and CoinPurchaseRequest payment-method / refund
# display fields. All additive with safe defaults - existing rows change nothing.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0018_userpreference_learning_prefs"),
    ]

    operations = [
        migrations.CreateModel(
            name="WalletSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("confirm_purchases", models.BooleanField(default=True)),
                ("daily_spend_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("monthly_spend_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="wallet_settings",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Wallet settings",
                "verbose_name_plural": "Wallet settings",
            },
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="payment_method",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="payment_method_detail",
            field=models.CharField(blank=True, max_length=60),
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="refund_status",
            field=models.CharField(blank=True, max_length=12),
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="refund_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="refund_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="refunded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
