# Issue #21 — ops/admin dashboards filter the append-only CoinLedger by
# transaction_type over a date range (e.g. the "withdrawal eligible" filter);
# without this composite index that is a full-table scan.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0004_coinpurchaserequest_unique_gateway_reference_per_gateway"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="coinledger",
            index=models.Index(
                fields=["transaction_type", "-created_at"],
                name="coinledger_type_created_idx",
            ),
        ),
    ]
