"""
core/notification_receipts.py - R9 / N6b: payment receipts as notifications (coin top-up, pass purchase) + the ONE copy
format every receipt uses (campus fee receipt included, so the three read the same).

  Type      NotifType.PURCHASE_RECEIPT ("purchase_receipt"), group "payments", LOCKED (money moved: the user must always
            be able to see it - Notification.LOCKED_TYPE_NAMES), route -> wallet (receipts / PDF live there, S9).
  Cap       NOT capped: a receipt is the answer to something the user just did, not a nudge. notification_reminders only
            gates REMINDER types, and this type is not one of them. (Quiet hours / DND / push toggle still apply: a
            receipt at 2 am becomes a bell row, the push waits - it is not a QUIET_BYPASS type.)
  Once      one row per (user, ref): `data.ref` ("coin_purchase_request:12" / "pass_purchase:34") is looked up before
            creating, so a retried webhook / a Celery redelivery / a double call never produces a second receipt.
  Never     raises into the caller: a receipt problem must never fail a payment or a pass accept. Emitters call
            `queue_receipt()`, which enqueues a Celery task after the surrounding transaction commits.

Copy format (all kinds): title  "Receipt: <what happened>"      body  "<details>. Receipt <number>."
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.cache import cache

logger = logging.getLogger(__name__)

KIND_COINS = "coins"
KIND_PASS = "pass"
KIND_TESTSERIES = "testseries"  # P-G2: shown in the receipts list / PDF only (no bell row)
KIND_FEE = "fee"  # campus fee: the row type stays CAMPUS_FEE_PAID (campus/fee_paid_push.py) - only the copy is shared

_REF_PREFIX = {KIND_COINS: "coin_purchase_request", KIND_PASS: "pass_purchase", KIND_FEE: "campus_fee_payment",
               KIND_TESTSERIES: "testseries_purchase"}
_NO_PREFIX = {KIND_PASS: "LS-P", KIND_FEE: "LS-F", KIND_TESTSERIES: "LS-T"}  # coins reuse user_profile.wallet_receipt_pdf.purchase_receipt_no (LS-R)
_MARK_KEY = "n6b:receipt:{ref}"
_MARK_TTL_SECONDS = 14 * 24 * 60 * 60


# ---------------------------------------------------------------------------------------------------------------------
# pure helpers (no DB) - unit-testable
# ---------------------------------------------------------------------------------------------------------------------
def ref_for(kind: str, pk) -> str:
    return f"{_REF_PREFIX[kind]}:{pk}"


def fmt_amount(value) -> str:
    """12 -> "12", Decimal("499.50") -> "499.50", Decimal("500.00") -> "500"."""
    try:
        text = f"{Decimal(str(value)):.2f}"
    except Exception:  # noqa: BLE001 - cosmetic only
        return str(value)
    return text[:-3] if text.endswith(".00") else text


def receipt_no(kind: str, pk, when) -> str:
    """Coins keep the S9 number (the PDF shows the same one); pass / fee use the same shape with their own letter."""
    try:
        if kind == KIND_COINS:
            from user_profile.wallet_receipt_pdf import purchase_receipt_no

            return purchase_receipt_no(int(pk), when)
        return f"{_NO_PREFIX[kind]}-{when:%Y%m}-{int(pk):06d}"
    except Exception:  # noqa: BLE001
        return f"{_NO_PREFIX.get(kind, 'LS-R')}-{pk}"


def receipt_copy(kind: str, *, number: str, amount=None, coins=None, what: str = "", who: str = "",
                 valid_until=None, balance=None) -> tuple[str, str]:
    """(title, body) for one receipt. `balance`: fee only - remaining amount (0 / None = fully paid)."""
    number = str(number or "").strip()
    tail = f" Receipt {number}." if number else ""
    if kind == KIND_COINS:
        title = f"Receipt: {coins} coins added" if coins else "Receipt: coins added"
        body = f"Paid Rs. {fmt_amount(amount)}. The coins are in your wallet.{tail}"
    elif kind == KIND_PASS:
        title = "Receipt: pass purchased"
        body = f"{coins} coin(s) for your pass to {what}"
        body += f", valid until {valid_until:%d %b %Y}." if valid_until else "."
        body += tail
    elif kind == KIND_FEE:
        title = "Receipt: fee payment"
        body = f"Rs. {fmt_amount(amount)} received towards '{what}'"
        body += f" for {who}." if who else "."
        try:
            if balance is not None and Decimal(str(balance)) > 0:
                body += f" Balance: Rs. {fmt_amount(balance)}."
            else:
                body += " Fully paid."
        except Exception:  # noqa: BLE001
            logger.exception("receipt balance text failed; sending the receipt without it")
        body += tail
    else:
        raise ValueError(f"unknown receipt kind {kind!r}")
    return title[:140], body[:240]


# ---------------------------------------------------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------------------------------------------------
def _already_sent(recipient_id, ref) -> bool:
    from .models import Notification

    return Notification.objects.filter(
        recipient_id=recipient_id, notif_type=Notification.NotifType.PURCHASE_RECEIPT, data__ref=ref,
    ).exists()


def _send(recipient_id, kind, pk, when, title, body, extra) -> bool:
    """Create the bell row + push (preference aware) once. True = nothing left to retry (sent or already there)."""
    from .models import Notification
    from .tasks import create_notification_rows

    ref = ref_for(kind, pk)
    mark = _MARK_KEY.format(ref=ref)
    if not cache.add(mark, 1, timeout=_MARK_TTL_SECONDS):
        return True  # a concurrent / earlier call owns it
    try:
        if _already_sent(recipient_id, ref):
            return True
        data = {"type": Notification.NotifType.PURCHASE_RECEIPT.value, "kind": kind, "ref": ref, **extra}
        failed = create_notification_rows(
            [recipient_id], Notification.NotifType.PURCHASE_RECEIPT.value, title, body, data, push=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("receipt send failed (%s)", ref)
        cache.delete(mark)
        return False
    if failed:
        cache.delete(mark)  # let the task retry
        return False
    return True


def notify_coin_purchase(purchase_id) -> bool:
    """Receipt for a SUCCESS CoinPurchaseRequest. Returns False only when a retry is worthwhile."""
    try:
        from user_profile.models import CoinPurchaseRequest

        p = CoinPurchaseRequest.objects.filter(
            pk=purchase_id, status=CoinPurchaseRequest.Status.SUCCESS,
        ).first()
        if p is None:
            return True
        number = receipt_no(KIND_COINS, p.pk, p.created_at)
        title, body = receipt_copy(KIND_COINS, number=number, amount=p.amount, coins=p.coins)
        return _send(p.user_id, KIND_COINS, p.pk, p.created_at, title, body, {
            "receipt_no": number, "coins": p.coins, "amount": fmt_amount(p.amount),
            "purchase_id": str(p.pk),
        })
    except Exception:  # noqa: BLE001
        logger.exception("notify_coin_purchase failed (purchase=%s)", purchase_id)
        return False


def notify_pass_purchase(purchase_id) -> bool:
    """Receipt for a paid PassPurchase (coins_spent > 0; a free pass has nothing to receipt)."""
    try:
        from tuitionclass.models import PassPurchase

        p = (
            PassPurchase.objects.select_related("class_pass__classroom")
            .filter(pk=purchase_id, status=PassPurchase.Status.SUCCESS).first()
        )
        if p is None or not p.coins_spent:
            return True
        classroom = p.class_pass.classroom
        number = receipt_no(KIND_PASS, p.pk, p.purchased_at)
        title, body = receipt_copy(
            KIND_PASS, number=number, coins=p.coins_spent, what=classroom.title, valid_until=p.expires_at,
        )
        return _send(p.student_id, KIND_PASS, p.pk, p.purchased_at, title, body, {
            "receipt_no": number, "coins": p.coins_spent, "classroom_id": str(classroom.pk),
            "pass_purchase_id": str(p.pk),
        })
    except Exception:  # noqa: BLE001
        logger.exception("notify_pass_purchase failed (purchase=%s)", purchase_id)
        return False


_LOADERS = {KIND_COINS: notify_coin_purchase, KIND_PASS: notify_pass_purchase}


def deliver(kind: str, purchase_id) -> bool:
    """Body of the Celery task. Unknown kind -> True (nothing to retry)."""
    loader = _LOADERS.get(kind)
    return True if loader is None else bool(loader(purchase_id))


def queue_receipt(kind: str, purchase_id) -> None:
    """Emitters call this INSIDE their payment transaction: the task is enqueued after commit (nothing for a rolled-back
    payment). Never raises."""
    try:
        from .async_utils import dispatch_after_commit
        from .tasks import send_purchase_receipt

        dispatch_after_commit(send_purchase_receipt, kind, purchase_id)
        if kind == KIND_COINS:  # P-G2: money moved -> also e-mail the PDF receipt (own task, own retry)
            from user_profile.tasks import send_coin_receipt_email

            dispatch_after_commit(send_coin_receipt_email, purchase_id)
    except Exception:  # noqa: BLE001
        logger.exception("could not queue %s receipt (purchase=%s)", kind, purchase_id)
