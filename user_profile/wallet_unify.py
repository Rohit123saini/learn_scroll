"""
T3.10 -- ONE wallet history over the two coin ledgers that still coexist.

  * user_profile.CoinLedger        -- the sanctioned ledger (top-ups, withdrawals, campus, test series, ...)
  * tuitionclass.CoinTransaction   -- legacy ledger still written by the class-pass money paths
                                      (pass purchase / renewal / class earning / gifts / referral / refunds)

`User.coin` is the single balance; both tables are only HISTORY of it. This module is READ-ONLY on
purpose: it never changes a balance and never feeds the fraud rules (earn rate-limit, withdrawal-eligible
replay), so merging the two histories for display cannot alter any money behaviour.
Moving the remaining legacy WRITE paths onto CoinLedger.record_transaction() is a separate, riskier step
-- see user_profile/WALLET_CONSOLIDATION.md for the checklist.
"""
from .models import CoinLedger

# legacy Reason -> closest CoinLedger.TransactionType (display only)
LEGACY_REASON_TO_TYPE = {
    "pass_purchase": CoinLedger.TransactionType.SPEND,
    "pass_auto_renewed": CoinLedger.TransactionType.SPEND,
    "gift_sent": CoinLedger.TransactionType.GIFT_SENT,
    "class_earning": CoinLedger.TransactionType.EARN,
    "referral_bonus": CoinLedger.TransactionType.EARN,
    "class_referral_join_bonus": CoinLedger.TransactionType.EARN,
    "class_referral_commission": CoinLedger.TransactionType.REFERRAL_COMMISSION,
    "refund": CoinLedger.TransactionType.REFUND,
    "gift_cancelled_refund": CoinLedger.TransactionType.REFUND,
    "admin_adjustment": CoinLedger.TransactionType.ADMIN_ADJUSTMENT,
    # topup / withdrawal / withdrawal_reversed were migrated into CoinLedger by
    # `migrate_tuitionclass_coin_models`; they only show up here if that command was never run.
    "topup": CoinLedger.TransactionType.PURCHASE,
    "withdrawal": CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
    "withdrawal_reversed": CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
}
MAX_WINDOW = 600  # offset + limit; keeps the in-memory merge small


def _legacy_model():
    from tuitionclass.models import CoinTransaction  # local import: user_profile must not import tuitionclass at load

    return CoinTransaction


def migrated_legacy_ids(user):
    """Legacy CoinTransaction ids that `migrate_tuitionclass_coin_models` already copied into CoinLedger."""
    ids = set()
    for meta in CoinLedger.objects.filter(user=user, metadata__has_key="source_coin_transaction_id").values_list("metadata", flat=True):
        try:
            ids.add(int(meta["source_coin_transaction_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return ids


def _ledger_row(entry):
    return {
        "id": entry.pk, "source": "ledger", "transaction_type": entry.transaction_type, "amount": entry.amount,
        "balance_after": entry.balance_after, "reference": entry.reference, "description": entry.description,
        "metadata": entry.metadata, "created_at": entry.created_at,
    }


def _legacy_row(txn):
    signed = txn.amount if txn.txn_type == "credit" else -txn.amount
    return {
        # negative id => can never collide with a CoinLedger id (the app models `id` as an int)
        "id": -txn.pk, "source": "legacy", "transaction_type": str(LEGACY_REASON_TO_TYPE.get(txn.reason, CoinLedger.TransactionType.ADMIN_ADJUSTMENT)),
        "amount": signed, "balance_after": txn.balance_after, "reference": txn.reference_id,
        "description": txn.get_reason_display(), "metadata": {"legacy_reason": txn.reason, "legacy_id": txn.pk},
        "created_at": txn.created_at,
    }


# S9 - ledger filters. Groups are what the app's filter chips send as `?group=`.
TYPE_GROUPS = {
    "topups": ["purchase"],
    "spends": ["spend", "testseries_purchase", "gift_sent"],
    "earnings": ["earn", "campus_reward", "streak_reward", "referral_commission", "doubt_bounty",
                 "testseries_payout", "gift_received"],
    "withdrawals": ["withdrawal_requested", "withdrawal_completed", "withdrawal_rejected"],
    "refunds": ["refund"],
    "other": ["admin_adjustment"],
}


class WalletFilters:
    """direction: "in" | "out" | "" ; group: key of TYPE_GROUPS | "" ; date_from/date_to: aware datetimes ; q: text."""

    def __init__(self, direction="", group="", date_from=None, date_to=None, q=""):
        self.direction = direction if direction in ("in", "out") else ""
        self.group = group if group in TYPE_GROUPS else ""
        self.date_from = date_from
        self.date_to = date_to
        self.q = (q or "").strip()[:60]

    @property
    def active(self):
        return bool(self.direction or self.group or self.date_from or self.date_to or self.q)

    @classmethod
    def from_query(cls, params, tz=None):
        """request.GET se. date_from / date_to = YYYY-MM-DD (user ke local din, date_to inclusive). Kharab date ignore."""
        import datetime as _dt

        from django.utils import timezone as _tzu

        zone = tz or _tzu.get_current_timezone()  # Django 5: zoneinfo

        def _d(raw, end=False):
            try:
                day = _dt.date.fromisoformat((raw or "").strip())
            except ValueError:
                return None
            moment = _dt.datetime.combine(day + _dt.timedelta(days=1 if end else 0), _dt.time.min)
            return moment.replace(tzinfo=zone)  # zoneinfo / datetime.timezone (project pytz use nahi karta)

        return cls(
            direction=(params.get("direction") or "").lower(),
            group=(params.get("group") or "").lower(),
            date_from=_d(params.get("date_from")),
            date_to=_d(params.get("date_to"), end=True),
            q=params.get("q") or "",
        )


def _filtered_querysets(user, flt):
    from django.db.models import Q

    ledger_qs = CoinLedger.objects.filter(user=user)
    legacy_qs = _legacy_model().objects.filter(user=user).exclude(pk__in=migrated_legacy_ids(user))
    if flt is None or not flt.active:
        return ledger_qs, legacy_qs

    if flt.direction == "in":
        ledger_qs = ledger_qs.filter(amount__gt=0)
        legacy_qs = legacy_qs.filter(txn_type="credit")
    elif flt.direction == "out":
        ledger_qs = ledger_qs.filter(amount__lt=0)
        legacy_qs = legacy_qs.filter(txn_type="debit")

    if flt.group:
        wanted = set(TYPE_GROUPS[flt.group])
        ledger_qs = ledger_qs.filter(transaction_type__in=wanted)
        reasons = [r for r, t in LEGACY_REASON_TO_TYPE.items() if str(t) in wanted]
        legacy_qs = legacy_qs.filter(reason__in=reasons)

    if flt.date_from:
        ledger_qs = ledger_qs.filter(created_at__gte=flt.date_from)
        legacy_qs = legacy_qs.filter(created_at__gte=flt.date_from)
    if flt.date_to:
        ledger_qs = ledger_qs.filter(created_at__lt=flt.date_to)
        legacy_qs = legacy_qs.filter(created_at__lt=flt.date_to)

    if flt.q:
        ledger_qs = ledger_qs.filter(Q(description__icontains=flt.q) | Q(reference__icontains=flt.q))
        legacy_qs = legacy_qs.filter(Q(reference_id__icontains=flt.q) | Q(reason__icontains=flt.q.replace(" ", "_")))
    return ledger_qs, legacy_qs


def wallet_history(user, limit=30, offset=0, flt=None):
    """Return (total_count, rows) -- newest first, both ledgers merged, nothing double counted.
    S9: optional `flt` (WalletFilters) narrows BOTH ledgers; total_count is of the filtered set."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    window = min(offset + limit, MAX_WINDOW)

    ledger_qs, legacy_qs = _filtered_querysets(user, flt)

    rows = [_ledger_row(e) for e in ledger_qs.order_by("-created_at", "-pk")[:window]]
    rows += [_legacy_row(t) for t in legacy_qs.order_by("-created_at", "-pk")[:window]]
    rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return ledger_qs.count() + legacy_qs.count(), rows[offset:offset + limit]


def wallet_totals(user, flt=None):
    """S9: {"in": coins credited, "out": coins debited (positive number), "count": rows} for the same filtered set."""
    from django.db.models import Sum

    ledger_qs, legacy_qs = _filtered_querysets(user, flt)
    l_in = ledger_qs.filter(amount__gt=0).aggregate(t=Sum("amount"))["t"] or 0
    l_out = -(ledger_qs.filter(amount__lt=0).aggregate(t=Sum("amount"))["t"] or 0)
    g_in = legacy_qs.filter(txn_type="credit").aggregate(t=Sum("amount"))["t"] or 0
    g_out = legacy_qs.filter(txn_type="debit").aggregate(t=Sum("amount"))["t"] or 0
    return {"in": int(l_in + g_in), "out": int(l_out + g_out), "count": ledger_qs.count() + legacy_qs.count()}
