# campus/fee_gateway.py
"""
FEE-5 — pay a campus fee ONLINE (UPI / card / net-banking) through Razorpay.

Fees already had two payment paths (coin wallet `pay`, office `record`). This is the third,
for real money. It re-uses the Razorpay plumbing of the coin top-up
(`user_profile/payments.py`: order create, `to_paise`, webhook-signature check) — no new
package, no second set of keys.

Flow
  1. App:     POST /campus/fee-payments/online-order/   {"invoice": "<id>", "amount": "5000.00"?}
     Server:  checks who may pay, the fee module, the balance; PRICES IT ITSELF (amount can
              only be <= what is still owed); creates a Razorpay Order (server key secret);
              stores a PENDING `FeePayment(payment_mode=online, gateway_reference=<order_id>)`;
              returns {order_id, key_id, amount_paise, currency, methods, ...} for Checkout.
  2. App:     opens Razorpay Checkout (UPI / card) with that order_id.
  3. App:     on success Checkout returns {razorpay_order_id, razorpay_payment_id,
              razorpay_signature} -> POST /campus/fee-payments/online-verify/ with them.
     Server:  HMAC-SHA256("order_id|payment_id", key_secret) must match -> the payment is
              settled (invoice recomputed, receipt e-mail queued). Instant UI answer.
  4. Razorpay: POST /campus/fee-payments/webhook/razorpay/  (signed, backup for a closed app /
              dropped network): `payment.captured` / `order.paid` settle the same row,
              `refund.processed` marks a fully refunded payment REFUNDED.
  5. Receipt: GET /campus/fee-payments/{id}/receipt-pdf/ (already existed) — now also prints
              the transaction id + UPI / card.

Why it is safe
  * The client NEVER says "it is paid": only a valid signature (verify) or the signed webhook
    does. The amount is the one WE put on the order (an order's amount cannot be changed by
    the client); the webhook re-checks amount + currency against our row.
  * Settling is row-locked and idempotent: verify + `payment.captured` + `order.paid` for one
    payment credit the invoice exactly once and queue exactly one receipt.
  * `payment.failed` is IGNORED (a Razorpay order allows several attempts; failing the row on
    the first bad attempt would make the later good payment un-settleable). Abandoned orders
    stay PENDING (and are capped per invoice).
  * Overpayment (two parallel full-amount orders both paid) is accepted — the money IS
    received — and logged at ERROR so a human refunds the extra from the Razorpay dashboard.

Settings (all optional): CAMPUS_FEE_ONLINE_ENABLED (default True; needs RAZORPAY_KEY_ID/SECRET),
CAMPUS_FEE_ONLINE_METHODS (default "upi,card"), CAMPUS_FEE_ONLINE_MAX_PENDING (default 5 per
invoice per payer per day), CAMPUS_FEE_ONLINE_MIN_AMOUNT (default 1 = Rs 1, Razorpay's minimum).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.http.request import RawPostDataException
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.async_utils import dispatch_after_commit
from user_profile.payments import GatewayError, create_razorpay_order, to_paise

from . import tasks as campus_tasks
from .models import FeeInvoice, FeePayment

logger = logging.getLogger(__name__)

GATEWAY = "razorpay"
SUCCESS_EVENTS = {"payment.captured", "order.paid"}
REFUND_EVENTS = {"refund.processed"}

# Outcomes of `settle_online_payment`
PAID = "paid"                # this call moved PENDING -> SUCCESS
ALREADY = "already"          # was already SUCCESS (retry / second event) — nothing to do
UNKNOWN = "unknown"          # no such order of ours
MISMATCH = "mismatch"        # paid amount / currency != what we asked for -> NOT settled
NOT_SETTLABLE = "not_settlable"  # row is REFUNDED / FAILED


# --------------------------------------------------------------------------- config
def online_enabled() -> bool:
    if not getattr(settings, "CAMPUS_FEE_ONLINE_ENABLED", True):
        return False
    return bool(getattr(settings, "RAZORPAY_KEY_ID", "") and getattr(settings, "RAZORPAY_KEY_SECRET", ""))


def allowed_methods() -> list[str]:
    raw = getattr(settings, "CAMPUS_FEE_ONLINE_METHODS", "upi,card")
    if isinstance(raw, str):
        raw = raw.split(",")
    return [m.strip().lower() for m in raw if m and m.strip()]


def _max_pending() -> int:
    return int(getattr(settings, "CAMPUS_FEE_ONLINE_MAX_PENDING", 5))


def _min_amount() -> Decimal:
    return Decimal(str(getattr(settings, "CAMPUS_FEE_ONLINE_MIN_AMOUNT", "1")))


# --------------------------------------------------------------------------- money
def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def balance_of(invoice: FeeInvoice) -> Decimal:
    """What is still owed on an invoice (never negative)."""
    return max(money(invoice.amount_due) - money(invoice.amount_paid), Decimal("0.00"))


def _err(detail: str, http: int, **extra) -> Response:
    return Response({"detail": detail, **extra}, status=http)


# --------------------------------------------------------------------------- 1. order
def start_online_payment(*, invoice: FeeInvoice, user, payer_role: str, raw_amount=None) -> Response:
    """Create the Razorpay order + PENDING FeePayment. Permission / fee-module checks are the
    caller's (`FeePaymentViewSet._get_invoice_and_check`)."""
    if not online_enabled():
        return _err("Online fee payment is not available right now.", status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="online_disabled")
    if invoice.status == FeeInvoice.Status.WAIVED:
        return _err("This fee has been waived.", status.HTTP_400_BAD_REQUEST, code="waived")

    from . import finance  # local import: finance -> models only

    finance.refresh_fine(invoice)  # FINANCE: the parent pays today's balance incl. today's late fine
    owed = balance_of(invoice)
    if owed <= 0:
        return _err("This fee is already paid.", status.HTTP_400_BAD_REQUEST, code="already_paid")

    if raw_amount in (None, ""):
        amount = owed
    else:
        try:
            amount = money(raw_amount)
        except (InvalidOperation, TypeError, ValueError):
            return _err("amount must be a valid number.", status.HTTP_400_BAD_REQUEST, code="bad_amount")
    if amount <= 0:
        return _err("amount must be greater than zero.", status.HTTP_400_BAD_REQUEST, code="bad_amount")
    if amount > owed:
        return _err(f"You can pay at most Rs. {owed} on this fee.", status.HTTP_400_BAD_REQUEST,
                    code="amount_too_high", balance=str(owed))
    # A small last instalment is fine; a tiny first one is not (Razorpay minimum + fee noise).
    if amount < _min_amount() and amount != owed:
        return _err(f"The minimum online payment is Rs. {_min_amount()}.", status.HTTP_400_BAD_REQUEST,
                    code="amount_too_low")

    since = timezone.now() - timedelta(days=1)
    pending = FeePayment.objects.filter(
        invoice=invoice, paid_by=user, payment_mode=FeePayment.Mode.ONLINE,
        status=FeePayment.Status.PENDING, created_at__gte=since,
    ).count()
    if pending >= _max_pending():
        return _err("Too many unfinished payments on this fee. Finish one or try again later.",
                    status.HTTP_429_TOO_MANY_REQUESTS, code="too_many_pending")

    amount_paise = to_paise(amount)
    try:
        order_id = create_razorpay_order(
            amount_paise=amount_paise,
            receipt=f"fee_{uuid.uuid4().hex[:30]}",
            notes={"kind": "campus_fee", "invoice_id": str(invoice.id), "payer_id": str(user.id)},
        )
    except GatewayError:
        logger.exception("campus fee order: gateway error (invoice=%s)", invoice.id)
        return _err("Payment service is unavailable. Try again shortly.", status.HTTP_502_BAD_GATEWAY,
                    code="gateway_unavailable")

    payment = FeePayment.objects.create(
        invoice=invoice, amount=amount, paid_by=user, payer_role=payer_role,
        payment_mode=FeePayment.Mode.ONLINE, status=FeePayment.Status.PENDING,
        gateway_reference=order_id,
    )
    return Response({
        "payment_id": str(payment.id),
        "invoice": str(invoice.id),
        "order_id": order_id,
        "key_id": settings.RAZORPAY_KEY_ID,  # public key id — safe for the app
        "amount": str(amount),
        "amount_paise": amount_paise,
        "currency": "INR",
        "methods": allowed_methods(),
        "title": invoice.fee_structure.title,
        "balance_after": str(owed - amount),
    }, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- 2. settle
def settle_online_payment(*, order_id: str, payment_id: str = "", method: str = "",
                          amount_paise=None, currency: str = "INR") -> tuple[str, FeePayment | None]:
    """Idempotently settle the PENDING online payment of `order_id` as SUCCESS.

    `amount_paise=None` skips the amount comparison — only for the signed client verify, where
    the signature already binds the payment to OUR order (whose amount the client cannot change).
    The webhook always passes the paid amount, which must equal ours.
    """
    with transaction.atomic():
        payment = (
            FeePayment.objects.select_for_update()
            .filter(gateway_reference=order_id, payment_mode=FeePayment.Mode.ONLINE)
            .first()
        )
        if payment is None:
            return UNKNOWN, None
        if payment.status == FeePayment.Status.SUCCESS:
            return ALREADY, payment
        if payment.status != FeePayment.Status.PENDING:
            return NOT_SETTLABLE, payment

        if amount_paise is not None:
            try:
                ok = int(amount_paise) == to_paise(payment.amount) and (currency or "").upper() == "INR"
            except (TypeError, ValueError, InvalidOperation):
                ok = False
            if not ok:
                # Money moved but the numbers do not line up: do NOT settle and do NOT fail the
                # row — a human must look. ERROR so Sentry alerts.
                logger.error(
                    "campus fee webhook AMOUNT MISMATCH order=%s payment=%s paid=%s %s expected=%s",
                    order_id, payment_id, amount_paise, currency, to_paise(payment.amount),
                )
                return MISMATCH, payment

        # Serialise concurrent settles of the SAME invoice (two payments landing together).
        invoice = FeeInvoice.objects.select_for_update().get(pk=payment.invoice_id)
        payment.invoice = invoice
        payment.gateway_payment_id = (payment_id or "")[:100]
        payment.gateway_method = (method or "")[:20]
        payment.save(update_fields=["gateway_payment_id", "gateway_method"])
        payment.mark_success()  # status + paid_at + invoice.recompute_status()

        if money(invoice.amount_paid) > money(invoice.amount_due):
            logger.error(
                "campus fee OVERPAYMENT invoice=%s due=%s paid=%s (latest payment=%s order=%s) — refund the "
                "extra from the Razorpay dashboard.",
                invoice.id, invoice.amount_due, invoice.amount_paid, payment.id, order_id,
            )
        dispatch_after_commit(campus_tasks.send_fee_receipt_email, payment.id)
        dispatch_after_commit(campus_tasks.send_fee_paid_notification, payment.id)
    return PAID, payment


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Razorpay Checkout success signature: HMAC-SHA256(key_secret, "order_id|payment_id")."""
    secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if not (secret and order_id and payment_id and signature):
        return False
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature))


def verify_online_payment(*, user, order_id: str, payment_id: str, signature: str) -> Response:
    """Client says "Checkout succeeded" — believe it only if the signature proves it."""
    from .serializers import FeePaymentSerializer

    payment = FeePayment.objects.filter(
        gateway_reference=order_id, payment_mode=FeePayment.Mode.ONLINE
    ).first()
    # Same answer for "no such order" and "not your order" — no probing of other people's orders.
    if payment is None or payment.paid_by_id != user.id:
        return _err("Payment not found.", status.HTTP_404_NOT_FOUND, code="not_found")
    if not verify_checkout_signature(order_id, payment_id, signature):
        logger.warning("campus fee verify: bad signature order=%s user=%s", order_id, user.id)
        return _err("Payment signature could not be verified.", status.HTTP_400_BAD_REQUEST,
                    code="bad_signature")

    outcome, payment = settle_online_payment(order_id=order_id, payment_id=payment_id)
    if outcome in (PAID, ALREADY):
        payment.refresh_from_db()
        return Response(
            FeePaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED if outcome == PAID else status.HTTP_200_OK,
        )
    return _err("This payment can no longer be completed. If money was deducted it will be refunded.",
                status.HTTP_409_CONFLICT, code=outcome)


# --------------------------------------------------------------------------- 3. webhook
def _entities(event: dict):
    payload = event.get("payload") or {}
    pay = (payload.get("payment") or {}).get("entity") or {}
    order = (payload.get("order") or {}).get("entity") or {}
    refund = (payload.get("refund") or {}).get("entity") or {}
    return pay, order, refund


def _ok(msg: str, **extra) -> Response:
    return Response({"status": True, "message": msg, **extra}, status=status.HTTP_200_OK)


class FeeRazorpayWebhookView(APIView):
    """POST /campus/fee-payments/webhook/razorpay/ — add THIS URL as a (second) webhook in the
    Razorpay dashboard with events payment.captured, order.paid, refund.processed; use the same
    webhook secret (RAZORPAY_WEBHOOK_SECRET). Orders that are not fee orders (coin top-ups) are
    answered 200 and ignored, exactly as the coin webhook ignores fee orders.
    Signature = HMAC-SHA256(raw body, webhook secret)."""

    permission_classes = [AllowAny]
    authentication_classes: list = []  # the signature is the only credential
    throttle_classes: list = []        # gateway retries must never be rate-limited away

    def post(self, request):
        from user_profile.views import (  # local: avoids import cycles at load time
            GatewayWebhookSignatureError,
            _verify_gateway_webhook_signature,
        )

        try:
            raw = request.body
        except RawPostDataException:
            return Response({"status": False, "message": "Could not read the request body."}, status=400)

        try:
            _verify_gateway_webhook_signature(request, GATEWAY, raw_body=raw)
        except GatewayWebhookSignatureError as exc:
            if exc.is_misconfiguration:
                logger.error("campus fee webhook: %s", exc)
                return Response({"status": False, "message": "Webhook not configured."}, status=503)
            return Response({"status": False, "message": "Webhook signature verification failed."}, status=401)

        try:
            event = json.loads(raw.decode("utf-8"))
            name = event.get("event", "")
        except (ValueError, AttributeError):
            return Response({"status": False, "message": "Malformed payload."}, status=400)

        pay, order, refund = _entities(event)

        if name in SUCCESS_EVENTS:
            order_id = pay.get("order_id") or order.get("id")
            if not order_id:
                return _ok("No order in event; ignored.", event=name)
            if name == "payment.captured" and pay.get("status") and pay.get("status") != "captured":
                return _ok("Payment not captured; ignored.")
            amount = pay.get("amount") if pay.get("amount") is not None else order.get("amount_paid")
            currency = pay.get("currency") or order.get("currency") or ""
            outcome, _ = settle_online_payment(
                order_id=order_id, payment_id=pay.get("id", ""), method=pay.get("method", ""),
                amount_paise=amount, currency=currency,
            )
            if outcome == PAID:
                return _ok("Fee payment settled.", settled=True)
            if outcome == MISMATCH:
                return _ok("Amount mismatch; not settled (flagged for review).", settled=False)
            if outcome == NOT_SETTLABLE:
                logger.error("campus fee webhook: order %s paid but row is not PENDING (flag for review).", order_id)
                return _ok("Payment row is not pending; not settled (flagged for review).", settled=False)
            # ALREADY (duplicate delivery) / UNKNOWN (not a fee order) -> stop retries
            return _ok("Already settled." if outcome == ALREADY else "Unknown order; ignored.")

        if name in REFUND_EVENTS:
            return self._refund(pay, refund)

        # payment.failed / authorized / refund.created ... — see module docstring.
        return _ok("Event ignored.", event=name)

    @staticmethod
    def _refund(pay: dict, refund: dict) -> Response:
        payment_id = refund.get("payment_id") or pay.get("id")
        if not payment_id:
            return _ok("No payment in event; ignored.")
        with transaction.atomic():
            payment = (
                FeePayment.objects.select_for_update()
                .filter(gateway_payment_id=payment_id, payment_mode=FeePayment.Mode.ONLINE)
                .first()
            )
            if payment is None:
                return _ok("Unknown payment; ignored.")
            if payment.status == FeePayment.Status.REFUNDED:
                return _ok("Already refunded.")
            if payment.status != FeePayment.Status.SUCCESS:
                return _ok("Payment was not successful; ignored.")
            try:
                full = int(refund.get("amount")) >= to_paise(payment.amount)
            except (TypeError, ValueError, InvalidOperation):
                full = False
            if not full:
                # A partial refund cannot be expressed by one status flip — leave it for a human.
                logger.error("campus fee webhook: PARTIAL refund %s on payment %s (flag for review).",
                             refund.get("id"), payment.id)
                return _ok("Partial refund; flagged for review.")
            payment.status = FeePayment.Status.REFUNDED
            payment.save(update_fields=["status"])
            payment.invoice.recompute_status()
        return _ok("Payment marked refunded.")
