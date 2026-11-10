# campus/fee_paid_push.py
"""
Push: "fee payment received" after an ONLINE (UPI / card) fee payment settles.

Audience: the student, their real linked parents (normal bell + push pipeline,
honours NotificationPreference) and loginless Parent Mode devices
(``bridge.push_to_parent_devices``) — same audience as results_push.

Rules:
  * One push per payment (atomic ``cache.add`` marker) — the signed client
    verify AND the Razorpay webhook both settle the same payment; whichever
    runs second must not buzz again. The marker is rolled back if nothing could
    be sent so the next attempt retries.
  * Only SUCCESS payments. Offline/cash entries recorded by the fee office do
    not use this push (the payer is standing at the counter).
  * Never raises — a notification problem must never affect the settle.
"""
import logging

from django.core.cache import cache

from . import bridge
from .bridge import NotifTypes
from .models import CampusParentLink, FeePayment

logger = logging.getLogger(__name__)

_KEY = "campus:fee-paid-push:{payment_id}"
_TTL_SECONDS = 7 * 24 * 60 * 60
_SHADOW_PARENT_PREFIX = "parent_shadow_"


def _display_name(user):
    full = getattr(user, "get_full_name", lambda: "")() or ""
    return full.strip() or user.username


def _fmt_amount(value) -> str:
    text = f"{value:.2f}"
    return text[:-3] if text.endswith(".00") else text


def notify_fee_paid(payment_id) -> int:
    """Returns the number of people (users) notified; 0 when skipped."""
    try:
        payment = (
            FeePayment.objects.select_related(
                "invoice__enrollment__student",
                "invoice__enrollment__section__school_class",
                "invoice__fee_structure",
            )
            .filter(pk=payment_id, status=FeePayment.Status.SUCCESS)
            .first()
        )
        if payment is None:
            return 0

        key = _KEY.format(payment_id=payment.pk)
        if not cache.add(key, 1, timeout=_TTL_SECONDS):
            return 0  # already pushed for this payment

        invoice = payment.invoice
        enrollment = invoice.enrollment
        student = enrollment.student
        campus_id = enrollment.section.school_class.campus_id

        parent_ids = [
            pid
            for pid, uname in CampusParentLink.objects.filter(
                campus_id=campus_id, student_id=student.pk
            ).values_list("parent_id", "parent__username")
            if not (uname or "").startswith(_SHADOW_PARENT_PREFIX)
        ]
        recipients = [student.pk] + parent_ids

        name = _display_name(student)
        # R9 / N6b - same "Receipt: ..." copy format as the coin / pass receipts (core.notification_receipts).
        from core import notification_receipts as receipts

        try:
            balance = invoice.amount_due - invoice.amount_paid
        except Exception:  # pragma: no cover - cosmetic only
            balance = None
        number = f"RCPT-{payment.pk}"  # same number the campus fee-receipt PDF prints (campus/tasks.py)
        title, body = receipts.receipt_copy(
            receipts.KIND_FEE, number=number, amount=payment.amount, what=invoice.fee_structure.title, who=name,
            balance=balance,
        )

        data = {
            "type": NotifTypes.CAMPUS_FEE_PAID,
            "invoice_id": str(invoice.pk),
            "payment_id": str(payment.pk),
            "student_id": str(student.pk),  # a parent tap opens THIS child's fees
            "campus_id": str(campus_id),
            "receipt_no": number,
        }
        try:
            bridge.notify(
                users=recipients,
                notif_type=NotifTypes.CAMPUS_FEE_PAID,
                title=title,
                body=body[:240],
                data=data,
                push=True,
            )
            bridge.push_to_parent_devices(student=student, title=title, body=body[:240], data=data)
        except Exception:
            logger.exception("campus fee-paid push failed (payment=%s).", payment_id)
            cache.delete(key)
            return 0
        return len(recipients)
    except Exception:
        logger.exception("campus.fee_paid_push.notify_fee_paid failed.")
        return 0
