# Task 30 — add gateway_payment_id/gateway_signature to
# CoinPurchaseRequest, closing the signature-verification gap found by
# diffing against liveclass.CoinPurchase (see that model's class
# docstring in user_profile/models.py for the full diff).
#
# Depends directly on 0001_initial — the screenshot of
# user_profile/migrations/ confirms that's the only migration in this
# app so far (just __init__.py + 0001_initial.py), so this is 0002, no
# placeholder guessing needed.
#
# Both new fields are blank-ok CharFields with an empty-string
# default, so this is a pure additive migration — no backfill needed
# for existing rows.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="gateway_payment_id",
            field=models.CharField(max_length=100, blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="coinpurchaserequest",
            name="gateway_signature",
            field=models.CharField(max_length=255, blank=True, default=""),
            preserve_default=False,
        ),
    ]