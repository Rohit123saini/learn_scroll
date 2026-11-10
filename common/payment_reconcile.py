"""
common/payment_reconcile.py - P-G2: failed / pending payment reconcile (gateway order status vs our DB).

Why: a Razorpay webhook can be lost (our server down, 5xx for >24 h, wrong URL in the dashboard). Then the customer is charged
but the row stays PENDING forever (coins never credited / fee never marked paid). This job asks Razorpay "what happened to
order X?" for every PENDING online row that is old enough and settles it through the SAME idempotent code the webhook uses:

  coin top-up   user_profile.models.CoinPurchaseRequest.objects.confirm_success()    (row lock + ledger reference)
  campus fee    campus.fee_gateway.settle_online_payment()                           (row lock + amount check)

Rules (all env-tunable, see settings.PAYMENT_RECONCILE_*):
  * only rows older than MIN_AGE_MINUTES (the webhook normally wins; we never race the happy path) and younger than
    MAX_AGE_DAYS (older rows are listed in the report, never auto-touched);
  * a captured payment whose amount / currency does NOT match our row is NEVER credited - logged at ERROR (Sentry) and
    reported as "mismatch" for a human;
  * a PENDING row whose order has NO payment attempt at all and is older than EXPIRE_HOURS is marked FAILED("expired...");
  * a PENDING row whose order shows only failed attempts is left PENDING until EXPIRE_HOURS (Razorpay lets the customer
    retry on the same order - failing early would make a later successful retry un-creditable, see payments.py docstring);
  * gateway errors are per-row: one bad order never stops the run; `GatewayError` for all rows -> summary["gateway_errors"].

`fetch` is injectable so tests never touch the network. `dry_run=True` changes nothing (reports what it would do).
Never raises to the caller (Celery) except for programming errors in the summary itself.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

GATEWAY = "razorpay"


class ReconcileGatewayError(Exception):
    """Razorpay could not be reached / answered something unusable for one order."""


# ------------------------------------------------------------------------------ settings ---
def _int_setting(name: str, default: int) -> int:
    try:
        return max(0, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default


def min_age_minutes() -> int:
    return _int_setting("PAYMENT_RECONCILE_MIN_AGE_MINUTES", 10)


def max_age_days() -> int:
    return _int_setting("PAYMENT_RECONCILE_MAX_AGE_DAYS", 7)


def expire_hours() -> int:
    return _int_setting("PAYMENT_RECONCILE_EXPIRE_HOURS", 168)  # = 7 days: the same window as MAX_AGE


def batch_size() -> int:
    return max(1, _int_setting("PAYMENT_RECONCILE_BATCH", 200))


# ------------------------------------------------------------------------------ gateway ---
def fetch_order_payments(order_id: str) -> list[dict]:
    """GET {RAZORPAY_API_BASE}/orders/<id>/payments -> list of payment entities (stdlib only, like payments.py)."""
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise ReconcileGatewayError("Razorpay keys are not configured on the server.")
    base = getattr(settings, "RAZORPAY_API_BASE", "https://api.razorpay.com/v1").rstrip("/")
    req = urlrequest.Request(
        f"{base}/orders/{urlparse.quote(str(order_id), safe='')}/payments", method="GET",
        headers={"Authorization": "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()},
    )
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:  # noqa: S310 (fixed https base)
            data = json.loads(resp.read().decode())
    except (urlerror.URLError, TimeoutError, ValueError) as exc:
        raise ReconcileGatewayError(f"Razorpay order lookup failed: {exc}") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ReconcileGatewayError("Razorpay returned an unexpected payments list.")
    return [i for i in items if isinstance(i, dict)]


def classify(payments: list[dict]) -> tuple[str, Optional[dict]]:
    """-> ("captured", payment) | ("attempted", None)  (only failed / created / authorized) | ("none", None)."""
    for p in payments:
        if str(p.get("status") or "").lower() == "captured":
            return "captured", p
    return ("attempted", None) if payments else ("none", None)


def _new_summary(kind: str, dry_run: bool) -> dict:
    return {
        "kind": kind, "dry_run": dry_run, "checked": 0, "settled": 0, "already": 0, "mismatch": 0,
        "expired": 0, "still_pending": 0, "gateway_errors": 0, "too_old": 0, "details": [],
    }


def _note(summary: dict, **row) -> None:
    if len(summary["details"]) < 50:  # keep the Celery result small
        summary["details"].append(row)


def _window(now):
    return now - timedelta(days=max_age_days()), now - timedelta(minutes=min_age_minutes())


# ------------------------------------------------------------------------------ coin top-ups ---
def reconcile_coin_purchases(*, now=None, fetch: Callable[[str], list] = fetch_order_payments, dry_run: bool = False) -> dict:
    from user_profile.models import CoinLedgerBusy, CoinPurchaseRequest
    from user_profile.payments import _method_from_event, price_for_coins, to_paise

    now = now or timezone.now()
    oldest, newest = _window(now)
    summary = _new_summary("coin_purchase", dry_run)
    summary["too_old"] = CoinPurchaseRequest.objects.filter(
        gateway=GATEWAY, status=CoinPurchaseRequest.Status.PENDING, created_at__lt=oldest,
    ).count()
    if summary["too_old"]:
        logger.warning("payment reconcile: %s coin purchase(s) are PENDING for > %s days - check by hand",
                       summary["too_old"], max_age_days())

    rows = list(
        CoinPurchaseRequest.objects.filter(
            gateway=GATEWAY, status=CoinPurchaseRequest.Status.PENDING,
            created_at__gte=oldest, created_at__lte=newest,
        ).order_by("created_at")[:batch_size()]
    )
    for purchase in rows:
        summary["checked"] += 1
        order_id = purchase.gateway_reference
        try:
            state, pay = classify(fetch(order_id))
        except ReconcileGatewayError:
            summary["gateway_errors"] += 1
            logger.warning("payment reconcile: coin order %s lookup failed", order_id, exc_info=True)
            continue

        if state == "captured":
            try:
                paid_ok = (
                    int(pay.get("amount")) == to_paise(purchase.amount)
                    and str(pay.get("currency") or "").upper() == "INR"
                    and purchase.amount == price_for_coins(purchase.coins)
                )
            except (TypeError, ValueError, InvalidOperation):
                paid_ok = False
            if not paid_ok:
                summary["mismatch"] += 1
                logger.error(
                    "payment reconcile COIN AMOUNT MISMATCH order=%s payment=%s paid=%s %s expected=%s coins=%s",
                    order_id, pay.get("id"), pay.get("amount"), pay.get("currency"), to_paise(purchase.amount), purchase.coins,
                )
                _note(summary, id=purchase.pk, order=order_id, result="mismatch")
                continue
            if dry_run:
                summary["settled"] += 1
                _note(summary, id=purchase.pk, order=order_id, result="would_settle")
                continue
            try:
                done = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=order_id, gateway_payment_id=str(pay.get("id") or ""),
                    gateway_signature="reconcile", gateway=GATEWAY,
                )
            except CoinLedgerBusy:
                summary["still_pending"] += 1  # next run
                continue
            except ValueError:
                # row was FAILED meanwhile; money moved -> a human must look
                summary["mismatch"] += 1
                logger.error("payment reconcile: coin order %s captured but purchase is FAILED", order_id)
                _note(summary, id=purchase.pk, order=order_id, result="captured_but_failed")
                continue
            try:  # display only, same as the webhook
                method, detail = _method_from_event({"payload": {"payment": {"entity": pay}}})
                if method and not done.payment_method:
                    done.payment_method, done.payment_method_detail = method, detail
                    done.save(update_fields=["payment_method", "payment_method_detail", "updated_at"])
            except Exception:  # noqa: BLE001
                logger.warning("payment reconcile: could not store payment method", exc_info=True)
            summary["settled"] += 1
            logger.warning("payment reconcile: SETTLED lost-webhook coin purchase pk=%s order=%s", purchase.pk, order_id)
            _note(summary, id=purchase.pk, order=order_id, result="settled")
            continue

        # no captured payment
        expired = now - purchase.created_at >= timedelta(hours=expire_hours())
        if state == "none" and expired:
            if dry_run:
                summary["expired"] += 1
                _note(summary, id=purchase.pk, order=order_id, result="would_expire")
                continue
            try:
                CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=order_id, reason="expired_by_reconcile: no payment attempt", gateway=GATEWAY,
                )
                summary["expired"] += 1
                _note(summary, id=purchase.pk, order=order_id, result="expired")
            except ValueError:
                summary["already"] += 1  # became SUCCESS between our read and the update
        else:
            summary["still_pending"] += 1
    return summary


# ------------------------------------------------------------------------------ campus fees ---
def reconcile_fee_payments(*, now=None, fetch: Callable[[str], list] = fetch_order_payments, dry_run: bool = False) -> dict:
    from campus import fee_gateway
    from campus.models import FeePayment

    now = now or timezone.now()
    oldest, newest = _window(now)
    online = FeePayment.objects.filter(payment_mode=FeePayment.Mode.ONLINE, status=FeePayment.Status.PENDING)
    summary = _new_summary("campus_fee", dry_run)
    summary["too_old"] = online.filter(created_at__lt=oldest).count()
    if summary["too_old"]:
        logger.warning("payment reconcile: %s campus fee payment(s) are PENDING for > %s days - check by hand",
                       summary["too_old"], max_age_days())

    for payment in list(online.filter(created_at__gte=oldest, created_at__lte=newest).order_by("created_at")[:batch_size()]):
        summary["checked"] += 1
        order_id = payment.gateway_reference
        if not order_id:
            continue
        try:
            state, pay = classify(fetch(order_id))
        except ReconcileGatewayError:
            summary["gateway_errors"] += 1
            logger.warning("payment reconcile: fee order %s lookup failed", order_id, exc_info=True)
            continue

        if state == "captured":
            if dry_run:
                summary["settled"] += 1
                _note(summary, id=str(payment.pk), order=order_id, result="would_settle")
                continue
            outcome, _ = fee_gateway.settle_online_payment(
                order_id=order_id, payment_id=str(pay.get("id") or ""), method=str(pay.get("method") or ""),
                amount_paise=pay.get("amount"), currency=str(pay.get("currency") or ""),
            )
            if outcome == fee_gateway.PAID:
                summary["settled"] += 1
                logger.warning("payment reconcile: SETTLED lost-webhook fee payment=%s order=%s", payment.pk, order_id)
            elif outcome == fee_gateway.ALREADY:
                summary["already"] += 1
            else:  # MISMATCH / NOT_SETTLABLE / UNKNOWN: settle_online_payment already logged at ERROR where it matters
                summary["mismatch"] += 1
            _note(summary, id=str(payment.pk), order=order_id, result=str(outcome))
            continue

        expired = now - payment.created_at >= timedelta(hours=expire_hours())
        if state == "none" and expired:
            if dry_run:
                summary["expired"] += 1
                _note(summary, id=str(payment.pk), order=order_id, result="would_expire")
                continue
            updated = FeePayment.objects.filter(pk=payment.pk, status=FeePayment.Status.PENDING).update(
                status=FeePayment.Status.FAILED,
            )
            summary["expired" if updated else "already"] += 1
            _note(summary, id=str(payment.pk), order=order_id, result="expired" if updated else "already")
        else:
            summary["still_pending"] += 1
    return summary


def run_all(*, now=None, fetch: Callable[[str], list] = fetch_order_payments, dry_run: bool = False) -> dict:
    """Both reconcilers; one failing never blocks the other."""
    out = {}
    for name, fn in (("coin_purchase", reconcile_coin_purchases), ("campus_fee", reconcile_fee_payments)):
        try:
            out[name] = fn(now=now, fetch=fetch, dry_run=dry_run)
        except Exception:  # noqa: BLE001
            logger.exception("payment reconcile: %s run crashed", name)
            out[name] = {"kind": name, "error": "crashed (see logs)"}
    bad = sum(v.get("mismatch", 0) for v in out.values())
    if bad:
        logger.error("payment reconcile: %s payment(s) need manual review (amount mismatch / captured-but-failed)", bad)
    return out
