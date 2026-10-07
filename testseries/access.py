# testseries/access.py
"""
Who may SEE and ATTEMPT a test series?

The problem this fixes
    `TestSeriesViewSet` used to list every published series to every
    logged-in user, and `start()` let anyone attempt any of them — including
    a *campus* series that belongs to one school's section, or a tuition-class
    series sold to a classroom's students. Series of `source=individual`
    are public by design (that is the marketplace); the other two are not.

The rule
    individual    published -> everyone.            draft -> creator only.
    campus        published -> members of that section, or active staff of
                  that campus.                      draft -> creator only.
    tuitionclass     published -> that classroom's teacher / staff / pass
                  holders.                          draft -> creator only.
    the creator and platform staff (`is_staff`) can always access.

How membership is resolved WITHOUT breaking the golden rule
    (`testseries` never imports `campus` / `tuitionclass`)
    `settings.TESTSERIES_CONTEXT_ACCESS` maps a `context_type` to a dotted path
    of a function owned by the other app:

        {"section":   "campus.bridge.user_accessible_testseries_context_ids",
         "classroom": "tuitionclass.bridge.user_accessible_testseries_context_ids"}

    signature  fn(*, user, context_type) -> Iterable[UUID]  (the context ids the
    user may access). Set a value to `"public"` to make that source
    world-readable (e.g. a tuition-class marketplace); set
    `TESTSERIES_ENFORCE_CONTEXT_ACCESS = False` to switch the whole check off.

    Fail-CLOSED: if a resolver can't be imported or raises, the user gets
    no access to that source (logged), never accidental access.
"""
from __future__ import annotations

import importlib
import logging
from django.conf import settings
from django.db.models import Q

from .models import TestSeries

logger = logging.getLogger(__name__)

DEFAULT_RESOLVERS = {
    "section": "campus.bridge.user_accessible_testseries_context_ids",
    "classroom": "tuitionclass.bridge.user_accessible_testseries_context_ids",
}
PUBLIC = "public"

# source -> the `context_type` string its bridge writes onto the series.
_SOURCE_CONTEXT = (
    (TestSeries.Source.CAMPUS, "section"),
    (TestSeries.Source.TUITIONCLASS, "classroom"),
)


def enforcement_enabled() -> bool:
    return bool(getattr(settings, "TESTSERIES_ENFORCE_CONTEXT_ACCESS", True))


def _resolver_setting(context_type: str):
    mapping = getattr(settings, "TESTSERIES_CONTEXT_ACCESS", None) or {}
    return mapping.get(context_type, DEFAULT_RESOLVERS.get(context_type))


def _load(path: str):
    module_path, _, func_name = path.rpartition(".")
    return getattr(importlib.import_module(module_path), func_name)


def accessible_context_ids(user, context_type: str):
    """`set` of context ids the user may access, the string `"public"` when the
    source is configured world-readable, or an empty set (fail-closed)."""
    target = _resolver_setting(context_type)
    if target == PUBLIC:
        return PUBLIC
    if not target:
        return set()
    try:
        return set(_load(target)(user=user, context_type=context_type))
    except Exception:  # noqa: BLE001 — fail closed, but never silently
        logger.exception("TESTSERIES_CONTEXT_ACCESS resolver %s failed; denying access.", target)
        return set()


def visible_series_q(user) -> Q:
    """Q() for "series this user may see": their own (any status) OR published
    series they are allowed to reach. Compose with `.filter(...)`."""
    if not enforcement_enabled():
        return Q(status=TestSeries.Status.PUBLISHED) | Q(creator=user)
    if getattr(user, "is_staff", False):
        return Q()  # platform staff: everything

    q = Q(creator=user) | Q(status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL)
    for source, context_type in _SOURCE_CONTEXT:
        ids = accessible_context_ids(user, context_type)
        if ids == PUBLIC:
            q |= Q(status=TestSeries.Status.PUBLISHED, source=source)
        elif ids:
            q |= Q(status=TestSeries.Status.PUBLISHED, source=source, context_type=context_type, context_id__in=ids)
    return q


def user_can_access_series(user, series: TestSeries) -> bool:
    """Single-object version of `visible_series_q` (used by start / questions)."""
    if series.creator_id == getattr(user, "id", None):
        return True
    if getattr(user, "is_staff", False):
        return True
    if series.status != TestSeries.Status.PUBLISHED:
        return False
    if not enforcement_enabled() or series.source == TestSeries.Source.INDIVIDUAL:
        return True
    context_type = "section" if series.source == TestSeries.Source.CAMPUS else "classroom"
    ids = accessible_context_ids(user, context_type)
    if ids == PUBLIC:
        return True
    return series.context_id in ids


# ---------------------------------------------------------------------------
# TASK 12 — Refer & Earn hooks (individual PAID series only)
#
# Golden rule: `testseries` never imports `tuitionclass`. The referral tables
# live in tuitionclass, so — exactly like TESTSERIES_CONTEXT_ACCESS above —
# they are reached through dotted paths in settings.TESTSERIES_REFERRAL_HOOKS.
# FAIL-SAFE direction here is the opposite of access control: if a hook is
# missing or raises, NO commission is paid and the creator keeps 100% (a
# referral glitch must never break buying a test or paying its creator).
# ---------------------------------------------------------------------------
def _referral_hook(name: str):
    target = (getattr(settings, "TESTSERIES_REFERRAL_HOOKS", None) or {}).get(name)
    if not target:
        return None
    try:
        return _load(target)
    except Exception:  # noqa: BLE001
        logger.exception("TESTSERIES_REFERRAL_HOOKS[%s]=%s could not be imported.", name, target)
        return None


def referral_percent():
    """Configured commission %, clamped to [0, TESTSERIES_REFERRAL_MAX_PERCENT]."""
    from decimal import Decimal, InvalidOperation

    try:
        pct = Decimal(str(getattr(settings, "TESTSERIES_REFERRAL_COMMISSION_PERCENT", "0")))
        cap = Decimal(str(getattr(settings, "TESTSERIES_REFERRAL_MAX_PERCENT", "50")))
    except InvalidOperation:
        return Decimal("0")
    return max(Decimal("0"), min(pct, cap, Decimal("100")))


def series_is_referable(series: TestSeries) -> bool:
    """Only individually-sold, paid series earn referral commission. Campus
    series are never paid; class series are covered by the class referral
    program (Classroom.referral_commission_percent), so counting them here
    too would double-pay."""
    return (
        series.source == TestSeries.Source.INDIVIDUAL
        and bool(series.is_paid)
        and series.price_coins > 0
        and referral_percent() > 0
    )


def referral_code_for_user(user):
    hook = _referral_hook("code_for_user")
    if hook is None or not getattr(user, "id", None):
        return None
    try:
        return hook(user=user)
    except Exception:  # noqa: BLE001
        logger.exception("referral code_for_user hook failed")
        return None


def referral_snapshot(*, buyer, series: TestSeries):
    """-> (referrer_id | None, percent Decimal, commission_coins int), taken
    at purchase time. (None, 0, 0) means "no commission on this purchase"."""
    from decimal import Decimal, ROUND_FLOOR

    none = (None, Decimal("0"), 0)
    if not series_is_referable(series) or buyer.id == series.creator_id:
        return none
    hook = _referral_hook("resolve_referrer")
    if hook is None:
        return none
    try:
        referrer_id = hook(buyer=buyer)
    except Exception:  # noqa: BLE001
        logger.exception("referral resolve_referrer hook failed")
        return none
    # The creator can't earn a "commission" on their own sale.
    if not referrer_id or referrer_id == series.creator_id or referrer_id == buyer.id:
        return none
    pct = referral_percent()
    coins = int((Decimal(series.price_coins) * pct / 100).to_integral_value(rounding=ROUND_FLOOR))
    if coins <= 0:
        return none
    return referrer_id, pct, coins


def pay_referral_commission(purchase) -> int:
    """Pay `purchase.referral_commission_coins` to its referrer. Returns the
    coins actually paid (0 if blocked or on any failure). Idempotent per
    purchase. Runs in its own savepoint so a failure can't poison the
    caller's transaction."""
    from django.db import transaction

    hook = _referral_hook("pay_commission")
    if hook is None:
        return 0
    try:
        with transaction.atomic():
            paid = hook(
                referrer_id=purchase.referred_by_id,
                referee_id=purchase.buyer_id,
                purchase_id=str(purchase.id),
                series_id=str(purchase.series_id),
                gross_coins=purchase.coins_spent,
                percent=purchase.referral_commission_percent,
                coins=purchase.referral_commission_coins,
            )
        return max(0, min(int(paid or 0), purchase.coins_spent - 1))
    except Exception:  # noqa: BLE001
        logger.exception("referral pay_commission hook failed for purchase %s", purchase.id)
        return 0
