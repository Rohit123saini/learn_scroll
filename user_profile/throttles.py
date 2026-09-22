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
