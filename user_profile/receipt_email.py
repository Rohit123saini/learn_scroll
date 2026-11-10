"""
user_profile/receipt_email.py - P-G2: coin top-up receipt by e-mail (PDF attached), once per purchase.

Transactional (no opt-out) but never to an address the provider reported dead (core.channel_policy.deliverable_emails, N8).
Best effort: returns True when there is nothing left to retry (sent / already sent / no address / PDF lib missing / switched
off) and False only for a transient SMTP failure, so the Celery task retries just those. A mail problem never touches the
payment: the coins are credited long before this runs.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_MARK = "pg2:coin_receipt_email:{pk}"
_MARK_TTL = 30 * 24 * 60 * 60


def send_coin_receipt(purchase_id) -> bool:
    if not getattr(settings, "COIN_RECEIPT_EMAIL_ENABLED", True):
        return True
    from django.core.mail import EmailMessage

    from .models import CoinPurchaseRequest
    from .wallet_receipt_pdf import PdfUnavailable, purchase_receipt_no, render_purchase_receipt

    p = CoinPurchaseRequest.objects.select_related("user").filter(
        pk=purchase_id, status=CoinPurchaseRequest.Status.SUCCESS,
    ).first()
    if p is None:
        return True
    to = (getattr(p.user, "email", "") or "").strip()
    if not to:
        return True
    try:
        from core.channel_policy import deliverable_emails

        if not deliverable_emails([to]):
            return True
    except Exception:  # noqa: BLE001 - policy helper missing must not block a receipt
        logger.warning("coin receipt email: channel policy check failed", exc_info=True)

    mark = _MARK.format(pk=p.pk)
    if not cache.add(mark, 1, timeout=_MARK_TTL):
        return True  # another worker / an earlier run owns it
    number = purchase_receipt_no(p.pk, p.created_at)
    try:
        from .wallet_settings_views import _user_name, method_text

        pdf = render_purchase_receipt(
            purchase_id=p.pk, user_name=_user_name(p.user), created_at=p.created_at, paid_at=p.updated_at,
            order_id=p.gateway_reference, payment_id=p.gateway_payment_id, gateway=p.gateway,
            method_text=method_text(p), coins=p.coins, amount=p.amount, status=p.status,
            refund_status=p.refund_status, refund_amount=p.refund_amount,
        )
    except PdfUnavailable:
        logger.info("coin receipt email: reportlab missing; skipping the e-mail (purchase %s)", p.pk)
        return True
    except Exception:  # noqa: BLE001
        cache.delete(mark)
        logger.exception("coin receipt email: could not build the PDF (purchase %s)", p.pk)
        return False
    try:
        msg = EmailMessage(
            subject=f"LearnScroll receipt {number} - {p.coins} coins",
            body=(
                f"Hi,\n\nWe received Rs. {p.amount:.2f} and added {p.coins} coins to your LearnScroll wallet.\n"
                f"Receipt {number} is attached as a PDF. You can also find it in the app: Wallet > Receipts.\n\n"
                "Refunds follow our refund policy: see Settings > Refund policy in the app.\n\n- LearnScroll"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
            to=[to],
        )
        msg.attach(f"{number}.pdf", pdf, "application/pdf")
        msg.send(fail_silently=False)
    except Exception:  # noqa: BLE001
        cache.delete(mark)
        logger.exception("coin receipt email: send failed (purchase %s)", p.pk)
        return False
    return True
