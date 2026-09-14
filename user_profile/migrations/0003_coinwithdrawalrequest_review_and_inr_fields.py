# Task 38 — add MIN_WITHDRAWAL_COINS floor (a plain class constant,
# schema-invisible — no migration needed for that part), reviewed_by,
# reviewed_at, and an INR-conversion snapshot (amount_inr) to
# CoinWithdrawalRequest, closing the gap Task 4's own docstring in
# user_profile/models.py deliberately deferred until liveclass.
# CoinWithdrawal (the reference model) was actually reviewed.
#
# amount_inr is added as a required field (no null=True on the model),
# so existing PENDING/PROCESSING/SUCCESS/REJECTED rows from before this
# migration need a value backfilled, not left NULL. Two-step: add it
# nullable with no default, backfill every existing row from its own
# `coins` value at the CURRENT COIN_TO_INR_RATE (the only rate that's
# ever been in force so far — this table has no historical rate change
# to account for), then tighten to NOT NULL. Safer than a single
# AddField(default=0) pass, which would leave pre-existing rows sitting
# at a wrong, constraint-violating amount_inr=0 forever (this migration
# also adds a CHECK amount_inr > 0 further down).
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_amount_inr(apps, schema_editor):
    CoinWithdrawalRequest = apps.get_model("user_profile", "CoinWithdrawalRequest")
    # COIN_TO_INR_RATE lives on the real model class, not the historical
    # migration model apps.get_model() hands back, so it's inlined here
    # as the same literal value defined on CoinWithdrawalRequest in
    # models.py as of this migration. If that rate is ever changed
    # going forward, existing rows are NOT expected to be rewritten
    # (see CoinWithdrawalRequest's Task 38 docstring — amount_inr is a
    # point-in-time snapshot, not a live conversion), so hardcoding the
    # rate that was in force for every row created before this
    # migration is correct, not a shortcut.
    coin_to_inr_rate = 1
    CoinWithdrawalRequest.objects.update(amount_inr=models.F("coins") * coin_to_inr_rate)


def noop_reverse(apps, schema_editor):
    # No reverse backfill needed — RemoveField below drops the column
    # (and its data) outright on unmigrate.
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0002_coinpurchaserequest_gateway_verification_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="coinwithdrawalrequest",
            name="amount_inr",
            field=models.DecimalField(max_digits=10, decimal_places=2, null=True),
        ),
        migrations.AddField(
            model_name="coinwithdrawalrequest",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="coin_withdrawal_requests_reviewed",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="coinwithdrawalrequest",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_amount_inr, noop_reverse),
        migrations.AlterField(
            model_name="coinwithdrawalrequest",
            name="amount_inr",
            field=models.DecimalField(max_digits=10, decimal_places=2),
        ),
        migrations.AddConstraint(
            model_name="coinwithdrawalrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_inr__gt", 0)),
                name="coinwithdrawalrequest_amount_inr_positive",
            ),
        ),
    ]