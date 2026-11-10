"""
user_profile/wallet_settings_views.py - S9: Wallet / payments settings API.

  GET/PATCH /profile/wallet/settings/              confirm_purchases + daily / monthly spend limit (+ minor caps, usage)
  GET       /profile/wallet/summary/               balance, withdrawable coins, pending withdrawals, this-month in/out
  GET       /profile/wallet/payments/              coin top-up history (method, status, refund status)   ?status=&limit=&offset=
  GET       /profile/wallet/payments/<id>/receipt/ PDF receipt (sirf SUCCESS, sirf apna)
  GET       /profile/wallet/withdrawals/<id>/receipt/ PDF payout slip (sirf SUCCESS, sirf apna)

Ledger filters `wallet/history/` (wallet_views.py) me hain. Response envelope baaki user_profile jaisa:
{"status": bool, "message": str, "data": ...}.
"""
from __future__ import annotations

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from login import audit as _x6_audit  # X6
from login.sensitive import SensitiveWriteThrottle, changed_fields as _x6_changed_fields, record_change as _x6_record_change  # X6

from . import wallet_settings
from common.feature_flags import coin_cashout_enabled  # P-G1
from .models import CoinPurchaseRequest, CoinWithdrawalRequest
from .wallet_receipt_pdf import (
    PdfUnavailable,
    payout_summary,
    purchase_receipt_no,
    render_purchase_receipt,
    render_withdrawal_slip,
    withdrawal_slip_no,
)


def _int(value, default, lo=0, hi=100):
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _ok(message, data=None, code=http.HTTP_200_OK):
    return Response({"status": True, "message": message, "data": data}, status=code)


def _err(message, code=http.HTTP_400_BAD_REQUEST, **extra):
    return Response({"status": False, "message": message, **extra}, status=code)


def method_text(purchase) -> str:
    """"UPI - ab***@okbank" / "Card - Visa XX4242" / "" (gateway ne kuch nahi bheja)."""
    m = (purchase.payment_method or "").strip()
    if not m:
        return ""
    label = {"upi": "UPI", "card": "Card", "netbanking": "Net banking", "wallet": "Wallet", "emi": "EMI"}.get(m.lower(), m.title())
    detail = (purchase.payment_method_detail or "").strip()
    return f"{label} - {detail}" if detail else label


def payment_row(p) -> dict:
    refund = None
    if p.refund_status:
        refund = {
            "status": p.refund_status,
            "amount": str(p.refund_amount) if p.refund_amount is not None else None,
            "refund_id": p.refund_id,
            "at": p.refunded_at.isoformat() if p.refunded_at else None,
        }
    ok = p.status == CoinPurchaseRequest.Status.SUCCESS
    return {
        "id": p.pk,
        "order_id": p.gateway_reference,
        "payment_id": p.gateway_payment_id,
        "gateway": p.gateway,
        "amount": str(p.amount),
        "coins": p.coins,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "method": p.payment_method,
        "method_text": method_text(p),
        "refund": refund,
        "receipt_no": purchase_receipt_no(p.pk, p.created_at) if ok else "",
        "receipt_available": ok,
        "created_at": p.created_at.isoformat(),
    }


def _pdf_response(pdf_bytes: bytes, filename: str) -> HttpResponse:
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


def _user_name(user) -> str:
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()


_X6_WALLET_FIELDS = ("confirm_purchases", "daily_spend_limit", "monthly_spend_limit")


class WalletSettingsView(APIView):
    permission_classes = [IsAuthenticated]
    # X6 -- spend limits / the "confirm purchases" switch protect the wallet: writes capped per hour, refusal audited.
    throttle_classes = [UserRateThrottle, SensitiveWriteThrottle]  # + the project-wide per-user floor
    throttle_scope = "wallet_settings_write"

    def get(self, request):
        return _ok("Wallet settings fetched.", wallet_settings.serialize(request.user.pk))

    def patch(self, request):
        if not hasattr(request.data, "get"):
            return _err("JSON object chahiye.")
        row0 = wallet_settings.get_row(request.user.pk)
        before = {f: getattr(row0, f, None) for f in _X6_WALLET_FIELDS}
        try:
            row = wallet_settings.apply_patch(request.user, request.data)
        except ValueError as exc:
            return _err(str(exc))
        after = {f: getattr(row, f, None) for f in _X6_WALLET_FIELDS}
        changed = _x6_changed_fields(request.data, _X6_WALLET_FIELDS, before, after)
        if changed:  # X6 -- names only: never the limit amounts
            _x6_record_change(_x6_audit.Event.WALLET_SETTINGS_CHANGED, request, request.user, changed)
        return _ok("Wallet settings saved.", wallet_settings.serialize(request.user.pk))

    put = patch


class WalletSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from . import fraud
        from .wallet_unify import WalletFilters, wallet_totals

        user = request.user
        cashout_enabled = coin_cashout_enabled()  # P-G1
        withdrawable = 0
        if cashout_enabled:  # P-G1: cash-out OFF -> nothing is withdrawable, and the fraud lookup is skipped too
            try:
                withdrawable = int(fraud.get_withdrawal_eligible_balance(user))
            except Exception:  # noqa: BLE001 - summary kabhi fraud-calc ki wajah se nahi tootni chahiye
                withdrawable = 0
        pending = CoinWithdrawalRequest.objects.filter(
            user=user, status__in=[CoinWithdrawalRequest.Status.PENDING, CoinWithdrawalRequest.Status.PROCESSING],
        )
        pending_coins = sum(w.coins for w in pending)
        month = wallet_totals(user, WalletFilters(date_from=wallet_settings.month_start(user.pk)))
        data = {
            "balance": int(getattr(user, "coin", 0) or 0),
            "inr_per_coin": str(CoinWithdrawalRequest.get_coin_to_inr_rate()),
            "cashout_enabled": cashout_enabled,  # P-G1: the app hides the withdraw UI ("Coming soon") when False
            "withdrawable": min(withdrawable, int(getattr(user, "coin", 0) or 0)),
            "min_withdrawal": CoinWithdrawalRequest.MIN_WITHDRAWAL_COINS,
            "pending_withdrawals": pending.count(),
            "pending_withdrawal_coins": pending_coins,
            "month_in": month["in"],
            "month_out": month["out"],
            "limits": wallet_settings.serialize(user.pk),
        }
        return _ok("Wallet summary fetched.", data)


class WalletPaymentsView(APIView):
    """Coin top-up history. ?status=success|pending|failed  ?limit=30&offset=0"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = CoinPurchaseRequest.objects.filter(user=request.user)
        wanted = (request.GET.get("status") or "").lower()
        if wanted in {s.value for s in CoinPurchaseRequest.Status}:
            qs = qs.filter(status=wanted)
        if (request.GET.get("refunded") or "") in {"1", "true"}:
            qs = qs.exclude(refund_status="")
        limit = _int(request.GET.get("limit"), 30, 1, 100)
        offset = _int(request.GET.get("offset"), 0, 0, 100000)
        total = qs.count()
        rows = [payment_row(p) for p in qs.order_by("-created_at", "-pk")[offset:offset + limit]]
        return _ok("Payments fetched.", {"count": total, "results": rows})


class WalletPurchaseReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        p = CoinPurchaseRequest.objects.filter(pk=pk, user=request.user).first()
        if p is None:
            return _err("Payment nahi mila.", http.HTTP_404_NOT_FOUND)
        if p.status != CoinPurchaseRequest.Status.SUCCESS:
            return _err("Receipt sirf successful payment ki milti hai.", http.HTTP_409_CONFLICT)
        try:
            pdf = render_purchase_receipt(
                purchase_id=p.pk, user_name=_user_name(request.user), created_at=p.created_at, paid_at=p.updated_at,
                order_id=p.gateway_reference, payment_id=p.gateway_payment_id, gateway=p.gateway,
                method_text=method_text(p), coins=p.coins, amount=p.amount, status=p.status,
                refund_status=p.refund_status, refund_amount=p.refund_amount,
            )
        except PdfUnavailable as exc:
            return _err(str(exc), http.HTTP_501_NOT_IMPLEMENTED)
        return _pdf_response(pdf, f"{purchase_receipt_no(p.pk, p.created_at)}.pdf")


class WalletWithdrawalReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        w = CoinWithdrawalRequest.objects.filter(pk=pk, user=request.user).first()
        if w is None:
            return _err("Withdrawal nahi mila.", http.HTTP_404_NOT_FOUND)
        if w.status != CoinWithdrawalRequest.Status.SUCCESS:
            return _err("Slip sirf poore hue withdrawal ki milti hai.", http.HTTP_409_CONFLICT)
        try:
            pdf = render_withdrawal_slip(
                withdrawal_id=w.pk, user_name=_user_name(request.user), created_at=w.created_at, completed_at=w.updated_at,
                coins=w.coins, amount_inr=w.amount_inr, payout_text=payout_summary(w.payout_method, w.payout_details),
                status=w.status,
            )
        except PdfUnavailable as exc:
            return _err(str(exc), http.HTTP_501_NOT_IMPLEMENTED)
        return _pdf_response(pdf, f"{withdrawal_slip_no(w.pk, w.created_at)}.pdf")
