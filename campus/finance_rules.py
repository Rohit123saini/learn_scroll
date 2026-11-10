# campus/finance_rules.py
"""
FINANCE — pure rules (NO Django imports, so they are unit-testable anywhere).

Everything money-related that is a *calculation* lives here; `campus/finance.py` only
loads rows, calls these functions and writes the result back.

Invariant kept on every FeeInvoice (so older code that only reads `amount_due` —
the gateway, dashboard, reminders, my-summary — keeps working untouched):

    amount_due = max(base_amount - concession_amount, 0) + fine_amount

* base_amount        what the FeeStructure charged when the invoice was made
* concession_amount  discount from active FeeConcessionGrant rows (never more than base)
* fine_amount        late fine accrued so far (0 until the grace period is over)

Late fine (per FeeStructure policy)
    none     no fine
    flat     one fixed amount, once the invoice is late
    per_day  `value` rupees for every day late
    percent  `value` % of what was still unpaid when the grace period ended, once
  * "late" starts the day AFTER due_date + grace_days.
  * The fine STOPS growing the day the principal (base - concession) is fully paid —
    a parent who pays on day 10 owes 10 days of fine even if the invoice is read on day 40.
  * Optional cap; fine never negative.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Optional, Sequence

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

# --- fine policy kinds ------------------------------------------------------------------
FINE_NONE = "none"
FINE_FLAT = "flat"
FINE_PER_DAY = "per_day"
FINE_PERCENT = "percent"
FINE_KINDS = (FINE_NONE, FINE_FLAT, FINE_PER_DAY, FINE_PERCENT)

# --- concession kinds -------------------------------------------------------------------
CONC_PERCENT = "percent"
CONC_FLAT = "flat"
CONCESSION_KINDS = (CONC_PERCENT, CONC_FLAT)


def money(value) -> Decimal:
    """Any number-ish -> Decimal with 2 places (half-up). None/'' -> 0.00."""
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Not a valid amount: {value!r}")


def parse_money(value, *, allow_zero: bool = False) -> Decimal:
    """Strict parse for API input: finite, 2dp, >0 (or >=0). Raises ValueError."""
    d = money(value)
    if not d.is_finite():
        raise ValueError("Amount must be a finite number.")
    if d < 0 or (d == 0 and not allow_zero):
        raise ValueError("Amount must be greater than zero." if not allow_zero else "Amount cannot be negative.")
    if d >= Decimal("100000000"):
        raise ValueError("Amount is too large.")
    return d


# ======================================================================================
# concessions
# ======================================================================================
def concession_value(base: Decimal, kind: str, value) -> Decimal:
    """One concession's rupee value against `base` (clamped to 0..base)."""
    base = money(base)
    val = money(value)
    if base <= 0 or val <= 0:
        return ZERO
    if kind == CONC_PERCENT:
        amount = (base * min(val, Decimal("100")) / Decimal("100")).quantize(_CENT, rounding=ROUND_HALF_UP)
    elif kind == CONC_FLAT:
        amount = val
    else:
        raise ValueError(f"Unknown concession kind: {kind!r}")
    return min(amount, base)


def total_concession(base: Decimal, grants: Iterable[tuple[str, object]]) -> Decimal:
    """Stack concessions ADDITIVELY on the same base (two 10% = 20% of base, not 19%),
    capped at `base` so an invoice can never go negative."""
    base = money(base)
    total = sum((concession_value(base, kind, value) for kind, value in grants), ZERO)
    return min(money(total), base)


def net_principal(base, concession) -> Decimal:
    return max(money(base) - money(concession), ZERO)


def amount_due(base, concession, fine) -> Decimal:
    """The invariant every FeeInvoice keeps (see module docstring)."""
    return net_principal(base, concession) + max(money(fine), ZERO)


# ======================================================================================
# late fine
# ======================================================================================
def _as_date(d) -> date:
    return d.date() if hasattr(d, "date") and callable(d.date) else d


def settled_on(principal, payments: Iterable[tuple[date, object]]) -> Optional[date]:
    """Date the cumulative payments first reached `principal` (None if never)."""
    principal = money(principal)
    if principal <= 0:
        return None
    running = ZERO
    for when, amount in sorted(((_as_date(w), money(a)) for w, a in payments), key=lambda x: x[0]):
        running += amount
        if running >= principal:
            return when
    return None


def paid_up_to(payments: Iterable[tuple[date, object]], cutoff: date) -> Decimal:
    return sum((money(a) for w, a in payments if _as_date(w) <= cutoff), ZERO)


def late_days(due_date: Optional[date], grace_days: int, end: date) -> int:
    """Days late counted from the day after (due_date + grace) up to `end` inclusive."""
    if due_date is None:
        return 0
    cutoff = due_date + timedelta(days=max(int(grace_days or 0), 0))
    return max((end - cutoff).days, 0)


def late_fine(
    *,
    kind: str,
    value,
    grace_days: int = 0,
    cap=None,
    principal,
    due_date: Optional[date],
    payments: Sequence[tuple[date, object]] = (),
    as_of: date,
) -> Decimal:
    """Fine accrued on one invoice as of `as_of`. `payments` = SUCCESS payments only
    as (date, amount). Pure + deterministic — same inputs, same fine."""
    if kind in (None, "", FINE_NONE):
        return ZERO
    if kind not in FINE_KINDS:
        raise ValueError(f"Unknown fine kind: {kind!r}")
    principal = money(principal)
    value = money(value)
    if principal <= 0 or value <= 0 or due_date is None:
        return ZERO

    payments = [(_as_date(w), money(a)) for w, a in payments]
    done_on = settled_on(principal, payments)
    end = min(done_on, as_of) if done_on else as_of
    days = late_days(due_date, grace_days, end)
    if days <= 0:
        return ZERO

    if kind == FINE_FLAT:
        fine = value
    elif kind == FINE_PER_DAY:
        fine = value * days
    else:  # FINE_PERCENT — on what was still unpaid when the grace period ended
        cutoff = due_date + timedelta(days=max(int(grace_days or 0), 0))
        still_unpaid = max(principal - paid_up_to(payments, cutoff), ZERO)
        fine = (still_unpaid * min(value, Decimal("100")) / Decimal("100")).quantize(_CENT, rounding=ROUND_HALF_UP)

    fine = money(fine)
    cap_d = money(cap) if cap not in (None, "") else None
    if cap_d is not None and cap_d > 0:
        fine = min(fine, cap_d)
    return max(fine, ZERO)


# ======================================================================================
# installment plans
# ======================================================================================
def split_amount(total, parts: int) -> list[Decimal]:
    """Split `total` into `parts` instalments that add up EXACTLY (paise-level); any
    leftover paise go to the first instalments, one each."""
    total = money(total)
    if parts < 1:
        raise ValueError("parts must be at least 1.")
    if total <= 0:
        raise ValueError("total must be greater than zero.")
    cents = int((total * 100).to_integral_value())
    base, rem = divmod(cents, parts)
    out = [Decimal(base + (1 if i < rem else 0)) / Decimal(100) for i in range(parts)]
    return [money(x) for x in out]


def add_months(d: date, months: int) -> date:
    """Calendar month add, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last))


def installment_dates(first_due: date, parts: int, every_months: int) -> list[date]:
    if parts < 1:
        raise ValueError("parts must be at least 1.")
    if every_months < 1:
        raise ValueError("every_months must be at least 1.")
    return [add_months(first_due, every_months * i) for i in range(parts)]


def installment_titles(title: str, parts: int) -> list[str]:
    title = (title or "").strip()
    if parts == 1:
        return [title]
    return [f"{title} — Instalment {i}/{parts}"[:150] for i in range(1, parts + 1)]


# ======================================================================================
# invoice numbering
# ======================================================================================
def session_label(start: date, end: date) -> str:
    """2026-04-01 .. 2027-03-31 -> '2026-27'."""
    if start.year == end.year:
        return str(start.year)
    return f"{start.year}-{str(end.year)[-2:]}"


def format_invoice_no(label: str, seq: int, *, prefix: str = "INV") -> str:
    if seq < 1:
        raise ValueError("seq must be >= 1")
    return f"{prefix}/{label}/{seq:06d}"
