"""
copyrights/legal_orders.py - court orders and government / authority notices.

A private copyright claim (services.py) can be disputed, expires, and costs the
user a strike. A legal order is none of that: it stays down until a level-3 admin
reverses it. So it has its own models, its own post state ('legal_removed') and
its own rules:

  register_order   L2+  record the order (what, who issued it, deadline = received + 36 h)
  start_review     L2+  mark "with legal"
  comply           L3    remove every listed item, tell the owners (unless notify_owner=False)
  reject_order     L3    invalid / over-broad / being challenged - nothing is removed
  reverse_order    L3    order was set aside - content comes back
  warn_due_orders  automation (hourly): warn L3 staff 12 h before the deadline and once again if it is missed

Deadline: settings.LEGAL_ORDER_SLA_HOURS (default 36).  Warning window: LEGAL_ORDER_WARN_HOURS (12).
Have a lawyer confirm the deadline and the procedure for your situation.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import permissions as perms
from . import targets
from .models import CopyrightAuditLog, LegalOrder, LegalOrderItem
from .services import ClaimError, _cfg, _notify, _notify_staff

logger = logging.getLogger(__name__)

LEGAL_POST = targets.LEGAL
O = LegalOrder
I = LegalOrderItem


def _olog(order, action, *, actor=None, label=None, note="", user=None):
    try:
        CopyrightAuditLog.objects.create(
            legal_order=order, user=user, actor=actor if getattr(actor, "pk", None) else None,
            actor_label=label or (getattr(actor, "username", None) or "system")[:40],
            action=action, note=(note or "")[:500],
        )
    except Exception:
        logger.exception("legal order audit log failed (%s)", action)


# --------------------------------------------------------------------------
# workflow
# --------------------------------------------------------------------------
@transaction.atomic
def register_order(actor, data, target_list):
    """data: reference, order_type, authority, summary, received_via?, received_at?, notify_owner?.
    target_list: [(target_type, target_id), ...]. Returns the order."""
    perms.require_level(actor, perms.L2, "registering a legal order")
    for key in ("reference", "authority", "summary"):
        if not (data.get(key) or "").strip():
            raise ClaimError("incomplete", f"'{key}' is required.")
    if not target_list:
        raise ClaimError("incomplete", "List at least one piece of content.")
    received_at = data.get("received_at") or timezone.now()
    order = O.objects.create(
        reference=data["reference"].strip()[:120], order_type=data.get("order_type") or O.OrderType.COURT,
        authority=data["authority"].strip()[:200], received_via=(data.get("received_via") or "")[:60],
        summary=data["summary"].strip()[:2000], received_at=received_at,
        due_at=received_at + timedelta(hours=_cfg("LEGAL_ORDER_SLA_HOURS", 36)),
        notify_owner=bool(data.get("notify_owner", True)), received_by=actor,
    )
    add_items(order, target_list)
    _olog(order, "order_registered", actor=actor, note=f"{order.reference}, due {order.due_at:%Y-%m-%d %H:%M}")
    _notify_staff(perms.L3, "Legal order received", f"{order.authority}: {order.reference} - due within "
                  f"{_cfg('LEGAL_ORDER_SLA_HOURS', 36)}h", data={"role": "staff", "legal_order": order.id})
    return order


def _resolve(target_type, target_id):
    """(obj, owner). Content we are ALREADY hiding (for another claim) can still be named in an order."""
    spec = targets.spec_for(target_type)
    if spec is None:
        return None, None
    obj = spec.load(str(target_id))
    if obj is None and targets.is_held(target_type, str(target_id)):
        obj = spec.load_any(str(target_id))
    return (obj, spec.owner(obj)) if obj is not None else (None, None)


def add_items(order, target_list):
    for target_type, target_id in target_list:
        _, owner = _resolve(target_type, str(target_id))
        I.objects.get_or_create(
            order=order, target_type=target_type, target_id=str(target_id),
            defaults={"owner": owner, "state": I.State.PENDING if owner is not None else I.State.MISSING},
        )


@transaction.atomic
def start_review(order, actor):
    perms.require_level(actor, perms.L2, "starting a legal review")
    order = O.objects.select_for_update().get(pk=order.pk)
    if order.status != O.Status.RECEIVED:
        raise ClaimError("bad_state", "Order is not waiting for review.")
    order.status = O.Status.IN_REVIEW
    order.save(update_fields=["status"])
    _olog(order, "order_in_review", actor=actor)
    return order


@transaction.atomic
def comply(order, actor, note=""):
    perms.require_level(actor, perms.L3, "complying with a legal order")
    order = O.objects.select_for_update().get(pk=order.pk)
    if not order.is_open:
        raise ClaimError("bad_state", "This order was already decided.")
    owners = {}
    removed = 0
    touched = set()
    for item in order.items.select_for_update():
        if item.state != I.State.PENDING:
            continue
        spec = targets.spec_for(item.target_type)
        if spec is not None and spec.load_any(item.target_id) is not None:
            item.state = I.State.REMOVED
            removed += 1
            touched.add((item.target_type, item.target_id))
            if item.owner_id:
                owners[item.owner_id] = item.owner
        else:
            item.state = I.State.MISSING
        item.save(update_fields=["state"])
    for target_type, target_id in touched:  # hide / quarantine - strongest active level wins
        targets.refresh(target_type, target_id)
    order.status = O.Status.COMPLIED
    order.decided_by, order.decided_at, order.decision_note = actor, timezone.now(), (note or "")[:2000]
    order.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    _olog(order, "order_complied", actor=actor, note=f"{removed} item(s) removed. {note}")
    if order.notify_owner:
        for owner in owners.values():
            _notify(owner, "Your content was removed",
                    "We removed it after a legal order from an authority. This is not a copyright strike.",
                    data={"role": "owner", "legal_order": order.id})
    return order


@transaction.atomic
def reject_order(order, actor, note):
    perms.require_level(actor, perms.L3, "rejecting a legal order")
    note = (note or "").strip()
    if not note:
        raise ClaimError("incomplete", "A reason is required.")
    order = O.objects.select_for_update().get(pk=order.pk)
    if not order.is_open:
        raise ClaimError("bad_state", "This order was already decided.")
    order.status = O.Status.REJECTED
    order.decided_by, order.decided_at, order.decision_note = actor, timezone.now(), note[:2000]
    order.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    _olog(order, "order_rejected", actor=actor, note=note)
    return order


@transaction.atomic
def reverse_order(order, actor, note):
    perms.require_level(actor, perms.L3, "reversing a legal order")
    note = (note or "").strip()
    if not note:
        raise ClaimError("incomplete", "A reason (for example the court's set-aside order) is required.")
    order = O.objects.select_for_update().get(pk=order.pk)
    if order.status != O.Status.COMPLIED:
        raise ClaimError("bad_state", "Only a complied order can be reversed.")
    owners = {}
    touched = set()
    for item in order.items.select_for_update().filter(state=I.State.REMOVED):
        item.state = I.State.RESTORED
        item.save(update_fields=["state"])
        touched.add((item.target_type, item.target_id))
        if item.owner_id:
            owners[item.owner_id] = item.owner
    for target_type, target_id in touched:  # restores only if no other claim / order still holds it
        targets.refresh(target_type, target_id)
    order.status = O.Status.REVERSED
    order.decision_note = (order.decision_note + f"\n[reversed] {note}")[:2000]
    order.save(update_fields=["status", "decision_note"])
    _olog(order, "order_reversed", actor=actor, note=note)
    if order.notify_owner:
        for owner in owners.values():
            _notify(owner, "Your content is back", "The legal order was withdrawn.",
                    data={"role": "owner", "legal_order": order.id})
    return order


# --------------------------------------------------------------------------
# automation
# --------------------------------------------------------------------------
def warn_due_orders(now=None):
    """Hourly. One warning when the deadline is near, one more when it is missed."""
    now = now or timezone.now()
    warn_before = timedelta(hours=_cfg("LEGAL_ORDER_WARN_HOURS", 12))
    n = 0
    for order in O.objects.filter(status__in=(O.Status.RECEIVED, O.Status.IN_REVIEW)):
        if now > order.due_at:
            if not CopyrightAuditLog.objects.filter(legal_order=order, action="order_overdue").exists():
                _olog(order, "order_overdue", label="automation", note="deadline passed")
                _notify_staff(perms.L3, "LEGAL ORDER OVERDUE", f"{order.authority} {order.reference} missed its deadline.",
                              data={"role": "staff", "legal_order": order.id})
                n += 1
        elif order.sla_warned_at is None and order.due_at - now <= warn_before:
            order.sla_warned_at = now
            order.save(update_fields=["sla_warned_at"])
            hours = max(int((order.due_at - now).total_seconds() // 3600), 0)
            _olog(order, "order_due_soon", label="automation", note=f"{hours}h left")
            _notify_staff(perms.L3, "Legal order due soon", f"{order.reference}: about {hours}h left.",
                          data={"role": "staff", "legal_order": order.id})
            n += 1
    return n
