"""
user_profile/throttles.py

BuyCoinView creates one PENDING CoinPurchaseRequest row per new
`gateway_reference`, and a client picks that reference itself — so without a
limit a script can insert rows as fast as the server answers (DB bloat).
Two per-user limits (rates live in settings.DEFAULT_THROTTLE_RATES):

  - burst: profile_coin_purchase        (10/min)
  - daily: profile_coin_purchase_daily  (100/day)

On top of these, BuyCoinView caps how many PENDING rows one user may hold
at once (settings.COIN_PURCHASE_MAX_PENDING_PER_USER).
"""
from rest_framework.throttling import UserRateThrottle


class CoinPurchaseBurstThrottle(UserRateThrottle):
    scope = "profile_coin_purchase"


class CoinPurchaseDailyThrottle(UserRateThrottle):
    scope = "profile_coin_purchase_daily"


# ----------------------------------------------------------------------
# Block / unblock / report spam guards.
#
# Block, unblock and "undo" are cheap for the user and expensive for us (each
# one fans out: follow cleanup, chat-table mirror, live socket events, call
# teardown). A script — or a person flipping the button — must not be able to
# trigger that in a loop, and block/unblock-cycling is also a harassment
# pattern. Both views share ONE bucket per user (same scope on purpose), so
# alternating block/unblock can't dodge the limit. Reading the blocked list is
# never throttled by these. Rates live in settings.DEFAULT_THROTTLE_RATES.
# ----------------------------------------------------------------------
from rest_framework.permissions import SAFE_METHODS


class _WritesOnlyUserThrottle(UserRateThrottle):
    """Counts POST/DELETE/PUT/PATCH only; GET/HEAD/OPTIONS pass untouched."""

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class BlockBurstThrottle(_WritesOnlyUserThrottle):
    scope = "profile_block_burst"


class BlockDailyThrottle(_WritesOnlyUserThrottle):
    scope = "profile_block_daily"


class ReportBurstThrottle(_WritesOnlyUserThrottle):
    scope = "profile_report_burst"
