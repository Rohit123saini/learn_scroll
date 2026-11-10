"""
user_profile/receipts_views.py - P-G2: ONE receipts list for everything the user paid for + PDFs for the coin-spend kinds.

  GET /profile/wallet/receipts/?kind=coins|pass|testseries|fee&limit=&offset=
        -> {"status": true, "data": {"count": N, "results": [ {kind, id, receipt_no, title, amount_text, date, status,
                                                              pdf_path}, ... ]}}
  GET /profile/wallet/receipts/pass/<id>/pdf/         PDF (only the buyer, only a paid + successful pass)
  GET /profile/wallet/receipts/testseries/<id>/pdf/   PDF (only the buyer, only coins_spent > 0)

Sources (each query is bounded by offset+limit, then merged by date - no unbounded scans):
  coins       CoinPurchaseRequest SUCCESS      (INR)   PDF: existing /profile/wallet/payments/<id>/receipt/
  pass        PassPurchase SUCCESS, coins > 0  (coins) PDF: new
  testseries  TestSeriesPurchase coins > 0     (coins) PDF: new
  fee         campus FeePayment SUCCESS paid_by=me (INR) PDF: existing campus /campus/fee-payments/<id>/receipt-pdf/
Privacy: strictly the caller's own rows. Nothing here changes money. `count` = rows across all shown kinds.
"""
from __future__ import annotations

import logging

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CoinPurchaseRequest
from .wallet_receipt_pdf import (
    PdfUnavailable,
    purchase_receipt_no,
    render_spend_receipt,
    spend_receipt_no,
)

logger = logging.getLogger(__name__)

KINDS = ("coins", "pass", "testseries", "fee")


def _int(value, default, lo=0, hi=100):
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _err(message, code=http.HTTP_400_BAD_REQUEST):
    return Response({"status": False, "message": message}, status=code)


def _user_name(user) -> str:
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()


def _money(value) -> str:
    try:
        return f"Rs. {float(value):.2f}".replace(".00", "")
    except (TypeError, ValueError):
        return f"Rs. {value}"


def _row(kind, pk, receipt_no, title, amount_text, when, state, pdf_path, **extra):
    return {
        "kind": kind, "id": str(pk), "receipt_no": receipt_no, "title": title, "amount_text": amount_text,
        "date": when.isoformat() if when else None, "status": state, "pdf_path": pdf_path, **extra,
    }


# ----------------------------------------------------------------------------------------------- collectors
def _coins(user, n):
    for p in CoinPurchaseRequest.objects.filter(user=user, status=CoinPurchaseRequest.Status.SUCCESS).order_by("-updated_at", "-pk")[:n]:
        yield _row(
            "coins", p.pk, purchase_receipt_no(p.pk, p.created_at), f"{p.coins} coins added", _money(p.amount),
            p.updated_at, "refunded" if p.refund_status == "processed" else "paid",
            f"/profile/wallet/payments/{p.pk}/receipt/",
        )


def _passes(user, n):
    from tuitionclass.models import PassPurchase

    qs = (PassPurchase.objects.select_related("class_pass__classroom")
          .filter(student=user, status=PassPurchase.Status.SUCCESS, coins_spent__gt=0).order_by("-purchased_at", "-pk")[:n])
    for p in qs:
        yield _row(
            "pass", p.pk, spend_receipt_no("LS-P", p.pk, p.purchased_at), f"Pass: {p.class_pass.classroom.title}"[:140],
            f"{p.coins_spent} coins", p.purchased_at, "paid", f"/profile/wallet/receipts/pass/{p.pk}/pdf/",
        )


def _testseries(user, n):
    from testseries.models import TestSeriesPurchase

    qs = (TestSeriesPurchase.objects.select_related("series")
          .filter(buyer=user, coins_spent__gt=0).order_by("-created_at", "-pk")[:n])
    for p in qs:
        yield _row(
            "testseries", p.pk, spend_receipt_no("LS-T", p.pk, p.created_at), f"Test series: {p.series.title}"[:140],
            f"{p.coins_spent} coins", p.created_at, "refunded" if p.status == "refunded" else "paid",
            f"/profile/wallet/receipts/testseries/{p.pk}/pdf/",
        )


def _fees(user, n):
    from campus.models import FeePayment

    qs = (FeePayment.objects.select_related("invoice__fee_structure")
          .filter(paid_by=user, status=FeePayment.Status.SUCCESS).order_by("-paid_at", "-created_at")[:n])
    for p in qs:
        yield _row(
            "fee", p.pk, f"RCPT-{p.pk}", f"Fee: {p.invoice.fee_structure.title}"[:140], _money(p.amount),
            p.paid_at or p.created_at, "paid", f"/campus/fee-payments/{p.pk}/receipt-pdf/",
        )


_COLLECTORS = {"coins": _coins, "pass": _passes, "testseries": _testseries, "fee": _fees}


def _count(user, kind) -> int:
    if kind == "coins":
        return CoinPurchaseRequest.objects.filter(user=user, status=CoinPurchaseRequest.Status.SUCCESS).count()
    if kind == "pass":
        from tuitionclass.models import PassPurchase
        return PassPurchase.objects.filter(student=user, status=PassPurchase.Status.SUCCESS, coins_spent__gt=0).count()
    if kind == "testseries":
        from testseries.models import TestSeriesPurchase
        return TestSeriesPurchase.objects.filter(buyer=user, coins_spent__gt=0).count()
    from campus.models import FeePayment
    return FeePayment.objects.filter(paid_by=user, status=FeePayment.Status.SUCCESS).count()


class WalletReceiptsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wanted = (request.GET.get("kind") or "").lower()
        kinds = [wanted] if wanted in KINDS else list(KINDS)
        limit = _int(request.GET.get("limit"), 30, 1, 100)
        offset = _int(request.GET.get("offset"), 0, 0, 5000)
        need = offset + limit
        rows, total = [], 0
        for kind in kinds:
            try:
                rows.extend(_COLLECTORS[kind](request.user, need))
                total += _count(request.user, kind)
            except Exception:  # noqa: BLE001 - one broken source (e.g. app not installed) must not hide the others
                logger.exception("receipts list: source %s failed", kind)
        rows.sort(key=lambda r: r["date"] or "", reverse=True)
        return Response({"status": True, "message": "Receipts fetched.",
                         "data": {"count": total, "results": rows[offset:offset + limit]}})


def _pdf_response(pdf: bytes, filename: str) -> HttpResponse:
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


class WalletPassReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from tuitionclass.models import PassPurchase

        p = PassPurchase.objects.select_related("class_pass__classroom").filter(pk=pk, student=request.user).first()
        if p is None:
            return _err("Receipt nahi mili.", http.HTTP_404_NOT_FOUND)
        if p.status != PassPurchase.Status.SUCCESS or not p.coins_spent:
            return _err("Receipt sirf paid pass ki milti hai.", http.HTTP_409_CONFLICT)
        no = spend_receipt_no("LS-P", p.pk, p.purchased_at)
        try:
            pdf = render_spend_receipt(
                doc_no=no, title="CLASS PASS RECEIPT", user_name=_user_name(request.user), when=p.purchased_at,
                item=f"Pass: {p.class_pass.classroom.title}",
                detail_rows=[("Valid until", f"{p.expires_at:%d %B %Y}")], coins=p.coins_spent, status="paid",
            )
        except PdfUnavailable as exc:
            return _err(str(exc), http.HTTP_501_NOT_IMPLEMENTED)
        return _pdf_response(pdf, f"{no}.pdf")


class WalletTestSeriesReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from testseries.models import TestSeriesPurchase

        p = TestSeriesPurchase.objects.select_related("series").filter(pk=pk, buyer=request.user).first()
        if p is None:
            return _err("Receipt nahi mili.", http.HTTP_404_NOT_FOUND)
        if not p.coins_spent:
            return _err("Receipt sirf paid test series ki milti hai.", http.HTTP_409_CONFLICT)
        no = spend_receipt_no("LS-T", p.pk, p.created_at)
        try:
            pdf = render_spend_receipt(
                doc_no=no, title="TEST SERIES RECEIPT", user_name=_user_name(request.user), when=p.created_at,
                item=f"Test series: {p.series.title}", detail_rows=[], coins=p.coins_spent,
                status="refunded" if p.status == "refunded" else "paid",
            )
        except PdfUnavailable as exc:
            return _err(str(exc), http.HTTP_501_NOT_IMPLEMENTED)
        return _pdf_response(pdf, f"{no}.pdf")
