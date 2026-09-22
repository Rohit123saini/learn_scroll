# Issue #12 — a gateway's transaction id is only unique WITHIN that gateway.
#
# `CoinPurchaseRequest.gateway_reference` was globally unique, so the day a
# second gateway (e.g. Stripe next to Razorpay) issued the same string as an
# existing purchase, the second user's purchase would fail. The identity is
# now the PAIR (gateway, gateway_reference).
#
# Safe on existing data: every current row already has a globally unique
# reference, so no pair can collide when the new constraint is created.
# `gateway_reference` keeps its own (non-unique) index — webhooks still look
# a purchase up by reference first.
#
# Deliberately NOT included: the `id` AutoField -> BigAutoField alterations
# `makemigrations` also proposes for every model here. That is a separate,
# pre-existing DEFAULT_AUTO_FIELD mismatch (a heavy table rewrite on big
# tables) that should be planned on its own, not slipped into this change.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0003_coinwithdrawalrequest_review_and_inr_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="coinpurchaserequest",
            name="gateway_reference",
            field=models.CharField(db_index=True, max_length=150),
        ),
        migrations.AddConstraint(
            model_name="coinpurchaserequest",
            constraint=models.UniqueConstraint(
                fields=("gateway", "gateway_reference"),
                name="unique_gateway_reference_per_gateway",
            ),
        ),
    ]
