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

[TASK 37 — RESOLVED] The three earn-rate-limit constants below
(`EARN_RATE_LIMIT_WINDOW` / `_MAX_TRANSACTIONS` / `_MAX_COINS`) used
to be hardcoded directly in this file. Now sourced from Django
settings (`LearnScroll/settings.py`, env-overridable, same shape as
`REFERRAL_BONUS_COINS` etc.) so ops can retune them without a deploy
— see that block in settings.py and the comment just above these
three lines. Values are unchanged; this was a relocation, not a
retune.
"""
from datetime import timedelta

from django.conf import settings
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
def eligible_source_types():
    """THE single definition of "which credit types are withdrawal-eligible".
    `get_withdrawal_eligible_balance()` below, the `withdrawal_eligible`
    metadata flag `record_transaction()` stamps on each row, and the admin
    "withdrawal eligible" filter ALL read this, so they cannot disagree."""
    from .models import CoinLedger

    return {
        CoinLedger.TransactionType.PURCHASE,
        CoinLedger.TransactionType.GIFT_RECEIVED,
        CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
    }


_eligible_source_types = eligible_source_types  # old private name, kept for callers/tests


def get_withdrawal_eligible_balance(user):
    """
    How many of `user`'s current coins are withdrawal-eligible (i.e.
    traceable back to a purchase or a received gift), as of right now.

    The ledger is REPLAYED IN ORDER into two buckets:

      eligible  — coins that came from money / another user
                  (PURCHASE, GIFT_RECEIVED, WITHDRAWAL_REJECTED)
      other     — everything else that is a credit (EARN, CAMPUS_REWARD,
                  REFUND, ADMIN_ADJUSTMENT, TESTSERIES_*, ...)

    and each row moves coins like this:

      credit, eligible type   -> eligible += amount
      credit, any other type  -> other    += amount
      WITHDRAWAL_REQUESTED    -> comes out of `eligible` (only ever
                                 requested against that pool); if it is
                                 somehow short, the rest comes out of `other`
      any other debit (SPEND, GIFT_SENT, ...)
                              -> comes out of `other` FIRST, and only the
                                 overflow eats into `eligible`

    "Earned coins are spent first" is the user-friendly rule (their real-
    money coins survive as long as possible), and replaying in time order
    is what makes it hold AT THE MOMENT OF EACH SPEND.

    WHY NOT THE OLD FORMULA — the previous version added up the final total
    of every bucket and subtracted `min(0, other_net)`. That ignores order,
    so it could be gamed: buy 100, spend 100 (eligible -> 0), then EARN 100
    (other_net back to 0) and the "overflow" vanished — 100 EARNED coins
    became withdrawable. Replaying can't be fooled that way.

    REFUNDS: a REFUND credit goes to `other`, NOT back to `eligible`. The
    ledger doesn't record which bucket the refunded spend originally came
    from, and a cash-out path must fail CLOSED — guessing "it came from the
    purchased coins" would let earned coins be laundered by spending and
    refunding them. Cost: someone who spent purchased coins and was later
    refunded can't withdraw those coins until ops re-issues them as an
    explicit GIFT_RECEIVED / PURCHASE entry (same rule this module already
    states for ADMIN_ADJUSTMENT). If refunds ever need to restore
    eligibility automatically, the refund row must carry a link to the
    original spend — a schema change, deliberately not guessed at here.

    Cost: one ordered pass over this user's ledger rows (streamed, two
    columns). It runs per withdrawal request / eligibility check, never on
    a hot path.

    Never negative. `User.coin` should equal eligible + other; this
    function only reports the eligible part.
    """
    from .models import CoinLedger

    eligible_types = _eligible_source_types()
    withdrawal_debit = CoinLedger.TransactionType.WITHDRAWAL_REQUESTED

    eligible = 0
    other = 0
    rows = (
        CoinLedger.objects.filter(user=user)
        .order_by("created_at", "id")
        .values_list("transaction_type", "amount")
        .iterator(chunk_size=2000)
    )
    for ttype, amount in rows:
        if amount > 0:
            if ttype in eligible_types:
                eligible += amount
            else:
                other += amount
            continue

        debit = -amount
        if ttype == withdrawal_debit:
            from_eligible = min(eligible, debit)
            eligible -= from_eligible
            other = max(other - (debit - from_eligible), 0)
        else:
            from_other = min(other, debit)
            other -= from_other
            eligible = max(eligible - (debit - from_other), 0)

    return max(eligible, 0)


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

# [MOVED — Task 37] Was hardcoded here directly, with a comment saying
# to move these to Django settings "once there's real production signal
# to tune against" — moved now (LearnScroll/settings.py, TASK 37 block),
# same env-overridable-constant shape every other coin-economy number in
# that file already uses (REFERRAL_BONUS_COINS,
# CAMPUS_ATTENDANCE_STREAK_BONUS_COINS, ...). Read from `settings` at
# import time here (not inside `check_earn_rate_limit()` per call) —
# same "module-level constant, not settings.X re-read on every call"
# shape this module already had before this pass; ops retuning these now
# still only needs an env var change + restart, not a code deploy,
# which was the actual point of moving them.
#
# Values themselves are UNCHANGED from the previous hardcoded ones — see
# settings.py's TASK 37 comment for why (relocation, not a retune).
# getattr() fallbacks (same defaults as settings.py) so a settings module
# missing these keys can't make the app fail to boot at import time.
EARN_RATE_LIMIT_WINDOW = timedelta(minutes=getattr(settings, "EARN_RATE_LIMIT_WINDOW_MINUTES", 60))
EARN_RATE_LIMIT_MAX_TRANSACTIONS = getattr(settings, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 20)
EARN_RATE_LIMIT_MAX_COINS = getattr(settings, "EARN_RATE_LIMIT_MAX_COINS", 500)


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