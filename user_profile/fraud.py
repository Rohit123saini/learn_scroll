# user_profile/fraud.py
"""
TASK 5 — fraud / anti-abuse layer for the coin economy.

Two independent rules live here, both enforced at the `CoinLedger`
write path (`CoinLedgerManager.record_transaction()` in models.py)
rather than only in a view, so no call site — this app's own views,
campus's tasks, referral bonuses, or anything written later — can
bypass them by going around a particular view:

1. Withdrawal eligibility — only coins that came from real money
   (`TransactionType.PURCHASE`) or from another user
   (`TransactionType.GIFT_RECEIVED`) may ever be cashed out.
   `TransactionType.EARN` / `CAMPUS_REWARD` coins can be spent inside
   the product but never withdrawn. `is_withdrawal_eligible()` is the
   single function `CoinWithdrawalRequestView` (views.py) calls before
   accepting a withdrawal request.

2. Earn-rate limiting — `EARN`/`CAMPUS_REWARD` credits for a single
   user are capped within a rolling time window, so a script (or a
   user replaying the same "task complete" call) can't burst-farm
   coins. `check_earn_rate_limit()` is called from inside
   `record_transaction()` itself for exactly those two transaction
   types; every other transaction_type is untouched by this rule.

Both functions take a plain `user` object (not a user id) and never
mutate anything — this module reads the ledger, it never writes to
it. The only writer stays `CoinLedgerManager.record_transaction()`.
"""
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone


class EarnRateLimitExceeded(Exception):
    """
    Raised by `CoinLedgerManager.record_transaction()` (models.py) when
    an EARN/CAMPUS_REWARD credit would push a user past the burst-farm
    limit. Deliberately NOT a `ValueError` — `record_transaction()`
    already uses plain `ValueError` for "this request is malformed /
    can't be satisfied" (zero amount, insufficient balance), which
    campus tasks and the referral-bonus flow may already be catching
    generically. A distinct exception class lets a caller that *does*
    want to tell "you're farming too fast" apart from "you're broke"
    catch this specifically (e.g. to log it, or to back off and retry
    later) without also swallowing unrelated ValueErrors — while a
    caller that only wants "something went wrong, don't credit" can
    still catch `Exception` (or `(ValueError, EarnRateLimitExceeded)`)
    the same way it always could.
    """


# --- Withdrawal eligibility ------------------------------------------------

# Credits that count toward the withdrawal-eligible pool. Deliberately
# just these two "money actually entered the platform" types, per the
# rule as specified — NOT `TESTSERIES_PAYOUT` (a creator payout is
# revenue-shaped but isn't literally a purchase or a gift), NOT
# `REFUND` (a generic refund's original source isn't known here), and
# NOT `ADMIN_ADJUSTMENT` (an ops-issued adjustment isn't "real paisa"
# either — if a specific adjustment SHOULD be withdrawable, it should
# be issued as an explicit `GIFT_RECEIVED`/`PURCHASE` entry instead of
# widening this set).
#
# `WITHDRAWAL_REJECTED` is included too, but not because a rejected
# withdrawal is itself a new source of money — it's the refund of a
# withdrawal that could only have been *requested* by draining this
# same eligible pool in the first place (see `is_withdrawal_eligible`
# / `CoinWithdrawalRequestManager.request_withdrawal` in models.py).
# Refunding a rejected withdrawal and NOT crediting it back to the
# eligible pool would silently strand a user's own purchased/gifted
# coins as un-withdrawable forever, which is a bug, not a fraud
# control — no acceptance test covers this edge case, but leaving it
# out would be wrong on inspection.
def _eligible_source_types():
    from .models import CoinLedger

    return {
        CoinLedger.TransactionType.PURCHASE,
        CoinLedger.TransactionType.GIFT_RECEIVED,
        CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
    }


def get_withdrawal_eligible_balance(user):
    """
    How many of `user`'s current coins are withdrawal-eligible (i.e.
    traceable back to a purchase or a received gift), as of right now.

    This is NOT `sum(amount for PURCHASE/GIFT_RECEIVED rows)` — a user
    can spend coins on something, and that spend has to come out of
    *some* bucket. The rule applied here: non-eligible coins (EARN,
    CAMPUS_REWARD, ...) are treated as spent first, and only once
    they're exhausted does further spend start eating into the
    eligible (purchased/gifted) pool. This is the generous-to-the-user
    reading (their real-money coins survive as long as possible) and
    is also the one that keeps `User.coin` and this figure mutually
    consistent without needing a second running-balance column: see
    the derivation below.

    Implementation: a single grouped aggregate over this user's
    `CoinLedger` rows (one query, not a row-by-row replay) is enough,
    because the two buckets only interact in one place (debits that
    exceed the non-eligible bucket "overflow" into the eligible one),
    and that overflow amount only depends on the FINAL totals of each
    bucket, not the order the rows happened in — as long as no
    withdrawal was ever approved for more than the eligible balance at
    the time (which `is_withdrawal_eligible` below exists to
    guarantee). Concretely:

      eligible_balance
        = (eligible credits: PURCHASE + GIFT_RECEIVED + WITHDRAWAL_REJECTED)
        - (eligible debits: WITHDRAWAL_REQUESTED, which only ever draws
           from this pool by construction)
        + min(0, non_eligible_net)

      where `non_eligible_net` is the net of every OTHER
      transaction_type (EARN, CAMPUS_REWARD, SPEND, GIFT_SENT, REFUND,
      ADMIN_ADJUSTMENT, TESTSERIES_*, ...) — if that net is negative,
      i.e. more was spent than was ever earned/rewarded, the shortfall
      must have come out of the eligible pool, so it's subtracted from
      it too.

    Clamped to >= 0 defensively; it should never go negative given the
    invariants above, but a negative "eligible balance" is meaningless
    either way.
    """
    from .models import CoinLedger

    eligible_types = _eligible_source_types()
    withdrawal_debit_type = CoinLedger.TransactionType.WITHDRAWAL_REQUESTED

    totals_by_type = dict(
        CoinLedger.objects.filter(user=user)
        .values_list("transaction_type")
        .annotate(total=Sum("amount"))
    )

    eligible_credits = sum(
        totals_by_type.get(t, 0) for t in eligible_types
    )
    eligible_debits = totals_by_type.get(withdrawal_debit_type, 0)  # already negative
    non_eligible_net = sum(
        total
        for ttype, total in totals_by_type.items()
        if ttype not in eligible_types and ttype != withdrawal_debit_type
    )

    eligible_balance = eligible_credits + eligible_debits
    if non_eligible_net < 0:
        eligible_balance += non_eligible_net

    return max(eligible_balance, 0)


def is_withdrawal_eligible(user, coins=None):
    """
    (bool, reason) — whether `user` may withdraw `coins`.

    `coins=None` checks only "does this user have ANY withdrawal-
    eligible balance at all" (useful for e.g. showing/hiding a
    "withdraw" button); `coins=<int>` checks that specific amount, which
    is what `CoinWithdrawalRequestView.post()` (views.py) actually
    calls before accepting a request. On a mixed balance, only the
    purchased/gifted portion is eligible — see
    `get_withdrawal_eligible_balance()` above — not the user's total
    `User.coin` balance.

    Never touches the balance or writes anything; this is a pure
    read-only check, safe to call as many times as a view wants.
    """
    eligible_balance = get_withdrawal_eligible_balance(user)

    if coins is None:
        if eligible_balance <= 0:
            return False, (
                "No withdrawal-eligible balance. Only coins you purchased "
                "or received as a gift can be withdrawn."
            )
        return True, ""

    if coins <= 0:
        return False, "Withdrawal amount must be positive."

    if coins > eligible_balance:
        return False, (
            f"Only {eligible_balance} of your coins are withdrawal-eligible "
            "(purchased or gifted). Earned/reward coins can't be withdrawn."
        )

    return True, ""


# --- Earn-rate limiting -----------------------------------------------------

# Deliberately conservative constants, not config-driven — same
# "cheap moment, no live traffic depends on the exact number yet" call
# this file's sibling models already make for other first-pass
# choices. Tighten/loosen these (or move them to Django settings) once
# there's real farming-attempt data to calibrate against.
EARN_RATE_LIMIT_WINDOW = timedelta(hours=1)
EARN_RATE_LIMIT_MAX_TRANSACTIONS = 20
EARN_RATE_LIMIT_MAX_COINS = 500


def check_earn_rate_limit(user, transaction_type):
    """
    True if `user` may receive another `transaction_type` credit right
    now; False if they've hit the burst-farm limit and the caller
    (`record_transaction()`) should refuse the credit.

    Only ever restricts `EARN`/`CAMPUS_REWARD` — every other
    transaction_type returns True immediately without a query, since
    rate-limiting a purchase or a gift makes no sense (real money and
    another user's coins aren't "farmable" the way a repeatable
    in-app action is).

    Two independent caps within a rolling window (both must pass):
    a count cap (no more than N earn-type credits, regardless of size
    — catches a script hammering a small reward repeatedly) and a
    total-coins cap (no more than M coins total, regardless of how
    many transactions — catches a few large credits instead of many
    small ones). Either one tripping blocks the credit.
    """
    from .models import CoinLedger

    rate_limited_types = (
        CoinLedger.TransactionType.EARN,
        CoinLedger.TransactionType.CAMPUS_REWARD,
    )
    if transaction_type not in rate_limited_types:
        return True

    window_start = timezone.now() - EARN_RATE_LIMIT_WINDOW
    recent = CoinLedger.objects.filter(
        user=user,
        transaction_type__in=rate_limited_types,
        created_at__gte=window_start,
    )

    if recent.count() >= EARN_RATE_LIMIT_MAX_TRANSACTIONS:
        return False

    total_recent_coins = recent.aggregate(total=Sum("amount"))["total"] or 0
    if total_recent_coins >= EARN_RATE_LIMIT_MAX_COINS:
        return False

    return True