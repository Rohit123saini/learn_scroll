"""
user_profile/wallet_settings.py - S9: Wallet / payments settings.

Kya hai: user ke coin kharch par apna control. Teen cheezein:
  * confirm_purchases   - app har coin-spend se pehle "N coins kharch karein?" puchhe (default ON).
  * daily_spend_limit / monthly_spend_limit - user apni limit set kare (NULL = koi limit nahi).
  * minors             - 18 se kam (DOB) ke liye server-side cap: apni limit sirf cap se KAM ho sakti hai, hat nahi
                         sakti, confirm_purchases band nahi hota. Top-up (paise) ki bhi mahine ki cap hai.

Enforcement `CoinLedger.objects.record_transaction` se hota hai (SPEND / GIFT_SENT / TESTSERIES_PURCHASE) isliye koi bhi
call site (campus, doubts, streak freeze, test series ...) isse bypass nahi kar sakta. tuitionclass ka purana
`CoinTransaction` path alag hai - wahan `check_spend_allowed` seedha gift flow me lagaya gaya hai.

Rules:
  * "Din" / "mahina" user ke LOCAL time (NotificationPreference.timezone) me hain; pata na ho to server timezone.
  * Sirf user-initiated spends gine jaate hain; withdrawal, refund, fee payment (enforce_spend_limit=False) nahi.
  * Padhne wale functions kabhi raise nahi karte (settings screen kabhi nahi tootni chahiye); `check_spend_allowed`
    sirf `SpendLimitExceeded` raise karta hai.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

#: In transaction types ko "kharch" gina jaata hai (user ne apni marzi se coins diye).
LIMITED_TYPES = frozenset({"spend", "gift_sent", "testseries_purchase"})

MAX_LIMIT = 1_000_000  # sanity bound (coins)


# --------------------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------------------
def _int_setting(name: str, default: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def minor_caps() -> dict:
    """Minor ke liye server cap: coins/day, coins/month, aur top-up INR/month."""
    return {
        "daily": _int_setting("WALLET_MINOR_DAILY_SPEND_LIMIT", 200),
        "monthly": _int_setting("WALLET_MINOR_MONTHLY_SPEND_LIMIT", 1000),
        "topup_inr_monthly": _int_setting("WALLET_MINOR_MONTHLY_TOPUP_INR", 1000),
    }


def is_minor(user_id) -> bool:
    """privacy.is_minor_user ka wrapper - age check fail ho to adult maana jaata hai (privacy.py jaisa)."""
    try:
        from .privacy import is_minor_user

        return bool(is_minor_user(int(user_id)))
    except Exception:  # noqa: BLE001 - settings / spend kabhi age-check ki wajah se nahi tootne chahiye
        return False


# --------------------------------------------------------------------------------------------------
# row access
# --------------------------------------------------------------------------------------------------
def get_row(user_id, create: bool = False):
    """WalletSettings row (ya None). create=True par bana deta hai."""
    from .models import WalletSettings

    if create:
        row, _ = WalletSettings.objects.get_or_create(user_id=user_id)
        return row
    return WalletSettings.objects.filter(user_id=user_id).first()


# --------------------------------------------------------------------------------------------------
# time windows (user ke local time me)
# --------------------------------------------------------------------------------------------------
def user_timezone(user_id):
    try:
        from .learning_prefs import user_tz

        tz = user_tz(user_id)
    except Exception:  # noqa: BLE001
        tz = None
    return tz or timezone.get_current_timezone()


def day_start(user_id, now: Optional[datetime.datetime] = None) -> datetime.datetime:
    now = now or timezone.now()
    local = now.astimezone(user_timezone(user_id))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(user_id, now: Optional[datetime.datetime] = None) -> datetime.datetime:
    now = now or timezone.now()
    local = now.astimezone(user_timezone(user_id))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------------------------------
# usage + limits
# --------------------------------------------------------------------------------------------------
def spent_since(user_id, since: datetime.datetime) -> int:
    """`since` ke baad ke limited spends ka total (coins, positive number)."""
    from .models import CoinLedger

    total = (
        CoinLedger.objects.filter(user_id=user_id, amount__lt=0, transaction_type__in=LIMITED_TYPES, created_at__gte=since)
        .aggregate(t=Sum("amount"))["t"]
    )
    return -int(total or 0)


def effective_limits(user_id, row=None, minor: Optional[bool] = None) -> dict:
    """User ki asli (lagu) limits: apni limit aur minor cap me se jo KAM ho. None = koi limit nahi."""
    row = row if row is not None else get_row(user_id)
    minor = is_minor(user_id) if minor is None else minor
    own_daily = getattr(row, "daily_spend_limit", None)
    own_monthly = getattr(row, "monthly_spend_limit", None)
    caps = minor_caps() if minor else {}

    def _pick(own, cap):
        values = [v for v in (own, cap) if v]
        return min(values) if values else None

    return {
        "daily": _pick(own_daily, caps.get("daily")),
        "monthly": _pick(own_monthly, caps.get("monthly")),
        "minor": minor,
        "daily_cap": caps.get("daily"),
        "monthly_cap": caps.get("monthly"),
        "topup_inr_monthly_cap": caps.get("topup_inr_monthly"),
    }


def check_spend_allowed(user_id, coins: int, transaction_type: str = "spend") -> None:
    """Raise SpendLimitExceeded agar `coins` kharch karne se daily / monthly limit paar hoti hai.
    Sirf LIMITED_TYPES par lagta hai; koi limit na ho to sasta (ek query)."""
    from .models import SpendLimitExceeded

    if str(transaction_type) not in LIMITED_TYPES or coins <= 0:
        return
    limits = effective_limits(user_id)
    daily, monthly = limits["daily"], limits["monthly"]
    if not daily and not monthly:
        return
    who = "Minor safety limit" if limits["minor"] else "Aapki spend limit"
    if daily:
        spent = spent_since(user_id, day_start(user_id))
        if spent + coins > daily:
            left = max(daily - spent, 0)
            raise SpendLimitExceeded(
                f"{who}: aaj ke {daily} coins ki limit paar ho jaayegi ({left} coins bache hain).",
                code="daily", limit=daily, spent=spent,
            )
    if monthly:
        spent = spent_since(user_id, month_start(user_id))
        if spent + coins > monthly:
            left = max(monthly - spent, 0)
            raise SpendLimitExceeded(
                f"{who}: is mahine ke {monthly} coins ki limit paar ho jaayegi ({left} coins bache hain).",
                code="monthly", limit=monthly, spent=spent,
            )


def usage(user_id) -> dict:
    return {
        "spent_today": spent_since(user_id, day_start(user_id)),
        "spent_month": spent_since(user_id, month_start(user_id)),
    }


# --------------------------------------------------------------------------------------------------
# top-up cap (minor, paise)
# --------------------------------------------------------------------------------------------------
def topup_month_inr(user_id) -> Decimal:
    """Is mahine (local) SUCCESS top-ups ka total INR."""
    from .models import CoinPurchaseRequest

    total = CoinPurchaseRequest.objects.filter(
        user_id=user_id, status=CoinPurchaseRequest.Status.SUCCESS, created_at__gte=month_start(user_id),
    ).aggregate(t=Sum("amount"))["t"]
    return Decimal(total or 0)


def topup_block_reason(user_id, amount_inr) -> Optional[str]:
    """Minor ke liye mahine ki top-up cap. None = theek hai. Adult ke liye hamesha None."""
    if not is_minor(user_id):
        return None
    cap = minor_caps()["topup_inr_monthly"]
    used = topup_month_inr(user_id)
    if used + Decimal(str(amount_inr)) > cap:
        left = max(Decimal(cap) - used, Decimal(0))
        return f"Minor safety limit: is mahine sirf Rs {cap} tak ke coins khareed sakte ho (Rs {left:.0f} bache hain)."
    return None


# --------------------------------------------------------------------------------------------------
# validation (PATCH) + serialisation
# --------------------------------------------------------------------------------------------------
def _clean_limit(raw, label: str) -> Optional[int]:
    """None / "" / "none" -> None (limit hatao); warna 1..MAX_LIMIT int. ValueError message user ko dikhta hai."""
    if raw is None or raw == "" or (isinstance(raw, str) and raw.strip().lower() in {"none", "null", "off"}):
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{label}: number chahiye.")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: poora number chahiye.") from None
    if value < 1:
        raise ValueError(f"{label}: kam se kam 1 coin ya limit hata do.")
    if value > MAX_LIMIT:
        raise ValueError(f"{label}: bahut zyada ({MAX_LIMIT} se kam rakho).")
    return value


def validate_patch(user_id, data: dict, row=None) -> dict:
    """PATCH body -> {field: value} jo row par likhna hai. Galat par ValueError (view 400 banata hai).
    Minor: limit cap se upar / hataana mana, confirm_purchases band karna mana."""
    row = row if row is not None else get_row(user_id)
    minor = is_minor(user_id)
    caps = minor_caps() if minor else {}
    out: dict = {}

    if "confirm_purchases" in data:
        value = data["confirm_purchases"]
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:  # form-encoded body
            value = value.strip().lower() == "true"
        if not isinstance(value, bool):
            raise ValueError("confirm_purchases: true ya false chahiye.")
        if minor and not value:
            raise ValueError("Minor account par purchase confirmation band nahi ho sakta.")
        out["confirm_purchases"] = value

    for key, label, cap_key in (
        ("daily_spend_limit", "Daily limit", "daily"),
        ("monthly_spend_limit", "Monthly limit", "monthly"),
    ):
        if key not in data:
            continue
        value = _clean_limit(data[key], label)
        if minor:
            cap = caps[cap_key]
            if value is None:
                raise ValueError(f"{label}: minor account par limit hata nahi sakte (max {cap}).")
            if value > cap:
                raise ValueError(f"{label}: minor account par max {cap} coins rakh sakte ho.")
        out[key] = value

    daily = out.get("daily_spend_limit", getattr(row, "daily_spend_limit", None))
    monthly = out.get("monthly_spend_limit", getattr(row, "monthly_spend_limit", None))
    if daily and monthly and daily > monthly:
        raise ValueError("Daily limit monthly limit se zyada nahi ho sakti.")
    return out


def apply_patch(user, data: dict):
    """Validate + save. Naya / purana row return karta hai."""
    row = get_row(user.pk, create=True)
    changes = validate_patch(user.pk, data, row)
    for field, value in changes.items():
        setattr(row, field, value)
    if changes:
        row.save(update_fields=[*changes.keys(), "updated_at"])
    return row


def serialize(user_id) -> dict:
    row = get_row(user_id)
    limits = effective_limits(user_id, row)
    use = usage(user_id)
    minor = limits["minor"]
    data = {
        "confirm_purchases": True if minor else bool(getattr(row, "confirm_purchases", True)),
        "daily_spend_limit": getattr(row, "daily_spend_limit", None),
        "monthly_spend_limit": getattr(row, "monthly_spend_limit", None),
        "effective_daily_limit": limits["daily"],
        "effective_monthly_limit": limits["monthly"],
        "is_minor": minor,
        "limits_locked": minor,  # minor: limit hata nahi sakte, sirf cap ke andar kam kar sakte
        "daily_cap": limits["daily_cap"],
        "monthly_cap": limits["monthly_cap"],
        "spent_today": use["spent_today"],
        "spent_month": use["spent_month"],
        "updated_at": row.updated_at.isoformat() if row is not None else None,
    }
    if minor:
        data["topup_inr_monthly_cap"] = limits["topup_inr_monthly_cap"]
        data["topup_inr_month"] = str(topup_month_inr(user_id))
    return data
