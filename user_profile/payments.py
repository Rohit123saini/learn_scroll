"""
T0.4 (B4) — real payment-gateway wiring for coin top-ups (Razorpay).

Flow
  1. App:      POST /profile/buy-coin/order/            {"coins": 500}
     Server:   validates the pack, PRICES IT ITSELF, creates a Razorpay Order
               (server key secret — the app can never do this), stores a PENDING
               CoinPurchaseRequest(gateway="razorpay", gateway_reference=<order_id>) and
               returns {order_id, key_id, amount_paise, currency, ...} for Checkout.
  2. App:      opens Razorpay Checkout with that order_id. It NEVER calls any "confirm".
  3. Razorpay: POST /profile/buy-coin/webhook/razorpay/   (signed with the webhook secret)
     Server:   verifies the signature -> finds the order -> checks the paid amount/currency
               against what WE stored -> `confirm_success()` (row-locked + ledger-reference
               idempotent) => wallet credited exactly once, however many times / via
               however many events (payment.captured AND order.paid) Razorpay delivers it.
  4. App:      polls GET /profile/ (balance) — `WalletService.refreshAfterPurchase()`.

Why the old `buy-coin/confirm/` webhook couldn't work with Razorpay: it expected a custom
`{gateway_reference, status}` body, whereas Razorpay posts `{event, payload.payment.entity
...}`; and it never compared the amount paid with the amount owed.

Deliberate choices
  * `payment.failed` is IGNORED (logged). A Razorpay order accepts several payment attempts;
    marking the purchase FAILED on the first failed attempt would make the later successful
    payment un-creditable (`confirm_success` refuses FAILED rows) — customer charged, no coins.
    Abandoned orders just stay PENDING (and are capped per user).
  * Unknown orders / ignored events answer 200 so Razorpay stops retrying; only a bad
    signature (401), our own misconfiguration / a busy wallet (503) ask for a retry.
  * Requires auto-capture on the Razorpay account (we act on payment.captured / order.paid).
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib import error as urlerror
from urllib import request as urlrequest

from django.conf import settings
from django.http.request import RawPostDataException
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import wallet_settings
from .models import CoinLedgerBusy, CoinPurchaseRequest
from .wallet_receipt_pdf import mask_upi
from .throttles import CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle

logger = logging.getLogger(__name__)

GATEWAY = "razorpay"
SUCCESS_EVENTS = {"payment.captured", "order.paid"}


# ------------------------------------------------------------------ pricing --------------
def inr_per_coin() -> Decimal:
    return Decimal(str(getattr(settings, "COIN_PURCHASE_INR_PER_COIN", "1")))


def allowed_packs() -> list[int]:
    raw = getattr(settings, "COIN_PURCHASE_PACKS", "100,250,500,1000,2500")
    if isinstance(raw, str):
        raw = [p for p in raw.split(",") if p.strip()]
    return sorted({int(p) for p in raw})


def price_for_coins(coins: int) -> Decimal:
    return (Decimal(coins) * inr_per_coin()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_paise(amount) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ gateway call ---------
class GatewayError(Exception):
    pass


def create_razorpay_order(*, amount_paise: int, receipt: str, notes: dict) -> str:
    """POST {RAZORPAY_API_BASE}/orders (Basic auth key_id:key_secret). Returns the order id.
    stdlib only — no SDK dependency. Isolated so tests patch just this function."""
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise GatewayError("Razorpay keys are not configured on the server.")
    base = getattr(settings, "RAZORPAY_API_BASE", "https://api.razorpay.com/v1").rstrip("/")
    body = json.dumps({
        "amount": amount_paise, "currency": "INR", "receipt": receipt[:40], "notes": notes,
    }).encode()
    req = urlrequest.Request(f"{base}/orders", data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode()).decode(),
    })
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:  # noqa: S310 (fixed https base)
            data = json.loads(resp.read().decode())
    except (urlerror.URLError, TimeoutError, ValueError) as exc:
        raise GatewayError(f"Razorpay order create failed: {exc}") from exc
    order_id = data.get("id")
    if not order_id or data.get("amount") != amount_paise:
        raise GatewayError("Razorpay returned an unexpected order.")
    return order_id


# ------------------------------------------------------------------ views ----------------
class CreateCoinOrderView(APIView):
    """POST /profile/buy-coin/order/  {"coins": 500}"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle]

    def post(self, request):
        try:
            coins = int(request.data.get("coins"))
        except (TypeError, ValueError):
            return Response({"status": False, "message": "coins must be a whole number."}, status=400)
        packs = allowed_packs()
        if coins not in packs:
            return Response({
                "status": False, "message": "Choose one of the available coin packs.", "packs": packs,
            }, status=400)

        max_pending = getattr(settings, "COIN_PURCHASE_MAX_PENDING_PER_USER", 20)
        if CoinPurchaseRequest.objects.filter(
            user=request.user, status=CoinPurchaseRequest.Status.PENDING
        ).count() >= max_pending:
            return Response({
                "status": False, "message": "Too many pending purchases. Complete or wait for the existing ones.",
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)

        amount = price_for_coins(coins)
        # S9: minors have a monthly top-up (INR) cap; adults are never blocked here.
        blocked = wallet_settings.topup_block_reason(request.user.pk, amount)
        if blocked:
            return Response({"status": False, "message": blocked, "code": "minor_topup_cap"}, status=status.HTTP_403_FORBIDDEN)
        amount_paise = to_paise(amount)
        try:
            order_id = create_razorpay_order(
                amount_paise=amount_paise,
                receipt=f"coins_{request.user.id}_{uuid.uuid4().hex[:10]}",
                notes={"user_id": str(request.user.id), "coins": str(coins)},
            )
        except GatewayError:
            logger.exception("coin order: gateway error")
            return Response({"status": False, "message": "Payment service is unavailable. Try again shortly."},
                            status=status.HTTP_502_BAD_GATEWAY)

        purchase, _ = CoinPurchaseRequest.objects.start_purchase(
            user=request.user, gateway_reference=order_id, amount=amount, coins=coins, gateway=GATEWAY,
        )
        return Response({
            "status": True,
            "data": {
                "purchase_id": purchase.pk,
                "order_id": order_id,
                "key_id": settings.RAZORPAY_KEY_ID,  # public key id — safe for the app
                "amount": str(amount),
                "amount_paise": amount_paise,
                "currency": "INR",
                "coins": coins,
            },
        }, status=status.HTTP_201_CREATED)


def _extract(event: dict):
    """-> (order_id, payment_id, amount_paise, currency, payment_status)"""
    p = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    o = ((event.get("payload") or {}).get("order") or {}).get("entity") or {}
    order_id = p.get("order_id") or o.get("id")
    amount = p.get("amount") if p.get("amount") is not None else o.get("amount_paid")
    return order_id, p.get("id", ""), amount, (p.get("currency") or o.get("currency") or ""), p.get("status", "")


def _method_from_event(event: dict):
    """S9 -> (method, masked_detail) from the Razorpay payment entity. Never returns a full card / UPI id."""
    p = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    method = str(p.get("method") or "").lower()[:20]
    detail = ""
    if method == "upi":
        detail = mask_upi(str(p.get("vpa") or ""))
    elif method == "card":
        card = p.get("card") or {}
        last4 = str(card.get("last4") or "")[-4:]
        network = str(card.get("network") or "").strip()
        detail = f"{network} XX{last4}".strip() if last4 else network
    elif method == "netbanking":
        detail = str(p.get("bank") or "")
    elif method == "wallet":
        detail = str(p.get("wallet") or "")
    return method, detail[:60]


def _record_method(purchase, event: dict) -> None:
    """Best effort, display only: a failure here must never fail the webhook (coins are already credited)."""
    try:
        method, detail = _method_from_event(event)
        if method and not purchase.payment_method:
            purchase.payment_method = method
            purchase.payment_method_detail = detail
            purchase.save(update_fields=["payment_method", "payment_method_detail", "updated_at"])
    except Exception:  # noqa: BLE001
        logger.warning("razorpay webhook: could not store payment method", exc_info=True)


REFUND_EVENTS = {"refund.created": "initiated", "refund.processed": "processed", "refund.failed": "failed"}
_REFUND_RANK = {"": 0, "initiated": 1, "failed": 2, "processed": 3}


def _handle_refund(event: dict, name: str):
    """S9: record refund status on the purchase (display only). Coins are NOT touched here - a refunded payment is
    reviewed by ops (logged at WARNING so it is visible); status never moves backwards (processed stays processed)."""
    from django.utils import timezone

    new_status = REFUND_EVENTS.get(name)
    if new_status is None:
        return _ok("Event ignored.", event=name)
    r = ((event.get("payload") or {}).get("refund") or {}).get("entity") or {}
    payment_id = r.get("payment_id") or ""
    purchase = CoinPurchaseRequest.objects.filter(gateway=GATEWAY, gateway_payment_id=payment_id).first() if payment_id else None
    if purchase is None:
        return _ok("Unknown payment; ignored.")
    if _REFUND_RANK.get(new_status, 0) < _REFUND_RANK.get(purchase.refund_status, 0):
        return _ok("Older refund event; ignored.")
    purchase.refund_status = new_status
    purchase.refund_id = str(r.get("id") or purchase.refund_id)[:100]
    try:
        if r.get("amount") is not None:
            purchase.refund_amount = (Decimal(int(r["amount"])) / Decimal(100)).quantize(Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        logger.warning("refund amount from gateway not parseable", exc_info=True)
    if new_status == "processed":
        purchase.refunded_at = timezone.now()
    purchase.save(update_fields=["refund_status", "refund_id", "refund_amount", "refunded_at", "updated_at"])
    logger.warning("razorpay refund %s purchase=%s payment=%s (coins NOT reversed automatically)", new_status, purchase.pk, payment_id)
    return _ok("Refund status recorded.")


def _ok(msg, **extra):
    return Response({"status": True, "message": msg, **extra}, status=status.HTTP_200_OK)


class RazorpayWebhookView(APIView):
    """POST /profile/buy-coin/webhook/razorpay/ — configure THIS URL in the Razorpay dashboard
    (events: payment.captured, order.paid). Signature = HMAC-SHA256(raw body, webhook secret)."""

    permission_classes = [AllowAny]
    authentication_classes: list = []  # signature is the only credential
    throttle_classes: list = []        # gateway retries must never be rate-limited away

    def post(self, request):
        from .views import (  # local: views imports lots; avoid import cycles at load time
            GatewayWebhookSignatureError,
            _verify_gateway_webhook_signature,
            _wallet_busy_response,
        )

        try:
            raw = request.body
        except RawPostDataException:
            return Response({"status": False, "message": "Could not read the request body."}, status=400)

        try:
            _verify_gateway_webhook_signature(request, GATEWAY, raw_body=raw)
        except GatewayWebhookSignatureError as exc:
            if exc.is_misconfiguration:
                logger.error("razorpay webhook: %s", exc)
                return Response({"status": False, "message": "Webhook not configured."}, status=503)
            return Response({"status": False, "message": "Webhook signature verification failed."}, status=401)

        try:
            event = json.loads(raw.decode("utf-8"))
            name = event.get("event", "")
        except (ValueError, AttributeError):
            return Response({"status": False, "message": "Malformed payload."}, status=400)

        if name.startswith("refund."):
            return _handle_refund(event, name)  # S9: status display only
        if name not in SUCCESS_EVENTS:
            # payment.failed / authorized ... — see module docstring.
            return _ok("Event ignored.", event=name)

        order_id, payment_id, amount_paise, currency, pay_status = _extract(event)
        if not order_id:
            return _ok("No order in event; ignored.", event=name)
        if name == "payment.captured" and pay_status and pay_status != "captured":
            return _ok("Payment not captured; ignored.")

        purchase = CoinPurchaseRequest.objects.filter(gateway=GATEWAY, gateway_reference=order_id).first()
        if purchase is None:
            # Signed by Razorpay but not ours (another product on the same account) -> stop retries.
            return _ok("Unknown order; ignored.")

        # --- the money check: what was PAID must equal what we OWE, at OUR price --------------
        try:
            paid_ok = (
                int(amount_paise) == to_paise(purchase.amount)
                and currency.upper() == "INR"
                and purchase.amount == price_for_coins(purchase.coins)
            )
        except (TypeError, ValueError, InvalidOperation):
            paid_ok = False
        if not paid_ok:
            # Money moved but the numbers don't line up (tampered start, wrong rate, partial
            # payment). Do NOT credit and do NOT fail the row — a human must look. Logged at
            # ERROR so Sentry alerts.
            logger.error(
                "razorpay webhook AMOUNT MISMATCH order=%s payment=%s paid=%s %s expected=%s coins=%s",
                order_id, payment_id, amount_paise, currency, to_paise(purchase.amount), purchase.coins,
            )
            return _ok("Amount mismatch; not credited (flagged for review).", credited=False)

        try:
            purchase = CoinPurchaseRequest.objects.confirm_success(
                gateway_reference=order_id,
                gateway_payment_id=payment_id,
                gateway_signature=request.headers.get("X-Razorpay-Signature", ""),
                gateway=GATEWAY,
            )
        except CoinLedgerBusy:
            return _wallet_busy_response()  # 503 -> Razorpay retries
        except ValueError as exc:
            logger.error("razorpay webhook: cannot confirm %s: %s", order_id, exc)
            return _ok("Purchase already failed; not credited (flagged for review).", credited=False)

        _record_method(purchase, event)  # S9: masked payment method for the history screen
        return _ok("Wallet credited.", credited=purchase.status == CoinPurchaseRequest.Status.SUCCESS)
