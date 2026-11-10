# campus/parent_weekly.py
"""
P-F14 — PARENT WEEKLY SUMMARY (campus).

One read-only builder that answers "how was this week for my child?":

  * attendance of the last 7 days (days present / late / absent / leave, % and the absent dates)
  * marks trend (published results only: last exam terms, % per term, up / down / flat vs the term before)
  * fees (balance, overdue invoices, the next due date)

and renders the SAME data three ways, so the numbers can never disagree between channels:

  * JSON for the in-app screen        -> GET /campus/parent-weekly/            (parent_weekly_views.py)
  * WhatsApp-ready text + wa.me link  -> `share_text` / `whatsapp_url` per child and for all children
  * email (subject + text + html)     -> `email` (sent by campus.tasks.send_parent_weekly_summaries)

Rules:
  * Strictly the caller's own `CampusParentLink` rows — a parent never sees a child they are not linked to.
  * Published results only (a DRAFT sheet never reaches a parent).
  * Bulk queries (no per-child query loop for attendance / results / fees).
  * No chat content, no teacher remarks — report-card style aggregates only.
  * Money is a string with 2 decimals (same as the parent dashboard). Language: "en" | "hi" (anything else -> en).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from html import escape
from urllib.parse import quote

from django.conf import settings
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    Attendance,
    CampusParentLink,
    FeeInvoice,
    FeePayment,
    ResultEntry,
    StudentEnrollment,
)

WINDOW_DAYS = 7
TREND_TERMS = 3          # how many latest exam terms are shown as the trend
FLAT_BAND = Decimal("1")  # |delta| below this many percentage points = "flat"

_SEVERITY = {"absent": 4, "late": 3, "leave": 2, "present": 1}


def normalize_lang(lang) -> str:
    return "hi" if str(lang or "").lower().startswith("hi") else "en"


def _name(user) -> str:
    full = getattr(user, "get_full_name", lambda: "")() or ""
    return full.strip() or user.username


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _pct(num, den):
    return round(Decimal(num) * 100 / Decimal(den), 1) if den else None


def _fmt_pct(v) -> str:
    return "" if v is None else f"{Decimal(str(v)).normalize():f}%"


# ---------------------------------------------------------------------------------------------------------- data
def week_window(today=None):
    today = today or timezone.localdate()
    return today - timedelta(days=WINDOW_DAYS - 1), today


def _attendance_by_enrollment(enrollment_ids, start, end) -> dict:
    """{enrollment_id: {"present": n, "late": n, "absent": n, "leave": n, "absent_dates": [date,...]}} counted in
    DAYS (the worst status of a day wins, so a subject-wise register does not count one missed day six times)."""
    per_day: dict[tuple, str] = {}
    for enr_id, day, status in Attendance.objects.filter(
        enrollment_id__in=enrollment_ids, date__gte=start, date__lte=end
    ).values_list("enrollment_id", "date", "status"):
        key = (enr_id, day)
        cur = per_day.get(key)
        if cur is None or _SEVERITY.get(status, 0) > _SEVERITY.get(cur, 0):
            per_day[key] = status
    out: dict[int, dict] = defaultdict(lambda: {"present": 0, "late": 0, "absent": 0, "leave": 0, "absent_dates": []})
    for (enr_id, day), status in sorted(per_day.items(), key=lambda kv: kv[0][1]):
        row = out[enr_id]
        if status in ("present", "late", "absent", "leave"):
            row[status] += 1
        if status == "absent":
            row["absent_dates"].append(day)
    return out


def _marks_by_enrollment(enrollment_ids, start, end) -> dict:
    """{enrollment_id: {"trend": [{"term", "percent"}...oldest->newest], "direction", "delta", "new_results"}}."""
    rows = (
        ResultEntry.objects.filter(enrollment_id__in=enrollment_ids, status=ResultEntry.Status.PUBLISHED)
        .select_related("exam_term")
        .values_list("enrollment_id", "exam_term_id", "exam_term__name", "exam_term__start_date",
                     "marks_obtained", "max_marks", "published_at")
    )
    terms: dict[int, dict] = defaultdict(dict)   # enr -> term_id -> aggregate
    new_results: dict[int, int] = defaultdict(int)
    for enr_id, term_id, term_name, term_start, got, mx, published_at in rows:
        agg = terms[enr_id].setdefault(
            term_id, {"term": term_name, "start": term_start, "got": Decimal("0"), "max": Decimal("0")}
        )
        agg["got"] += Decimal(got)
        agg["max"] += Decimal(mx)
        if published_at and start <= timezone.localtime(published_at).date() <= end:
            new_results[enr_id] += 1

    out = {}
    for enr_id, per_term in terms.items():
        ordered = sorted(per_term.values(), key=lambda a: a["start"])
        trend = [{"term": a["term"], "percent": _pct(a["got"], a["max"])} for a in ordered if a["max"] > 0]
        trend = trend[-TREND_TERMS:]
        direction, delta = "none", None
        if len(trend) >= 2:
            delta = round(Decimal(str(trend[-1]["percent"])) - Decimal(str(trend[-2]["percent"])), 1)
            direction = "flat" if abs(delta) < FLAT_BAND else ("up" if delta > 0 else "down")
        elif len(trend) == 1:
            direction = "first"
        out[enr_id] = {"trend": trend, "direction": direction, "delta": delta, "new_results": new_results.get(enr_id, 0)}
    return out


def _fees_by_enrollment(enrollment_ids, today) -> dict:
    """{enrollment_id: {"due": Decimal, "open": n, "overdue": n, "next_due": date|None}} — open balance only."""
    open_statuses = [FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE]
    rows = (
        FeeInvoice.objects.filter(enrollment_id__in=enrollment_ids, status__in=open_statuses)
        .annotate(
            paid=Coalesce(
                Sum("payments__amount", filter=Q(payments__status=FeePayment.Status.SUCCESS)),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .values("enrollment_id", "amount_due", "status", "paid", "fee_structure__due_date")
    )
    out: dict[int, dict] = defaultdict(lambda: {"due": Decimal("0"), "open": 0, "overdue": 0, "next_due": None})
    for r in rows:
        balance = Decimal(r["amount_due"]) - Decimal(r["paid"])
        if balance <= 0:
            continue
        row = out[r["enrollment_id"]]
        row["due"] += balance
        row["open"] += 1
        due_date = r["fee_structure__due_date"]
        if r["status"] == FeeInvoice.Status.OVERDUE or (due_date and due_date < today):
            row["overdue"] += 1
        if due_date and (row["next_due"] is None or due_date < row["next_due"]):
            row["next_due"] = due_date
    return out


def build_weekly(user, campus_id=None, lang="en", today=None) -> dict:
    """The weekly summary for every child linked to `user` (optionally one campus). See module docstring."""
    lang = normalize_lang(lang)
    start, today = week_window(today)
    base = {
        "lang": lang, "window_days": WINDOW_DAYS, "week_start": start.isoformat(), "week_end": today.isoformat(),
        "label": f"{start:%d %b} \u2013 {today:%d %b}",
    }

    links = CampusParentLink.objects.filter(parent=user).select_related("student", "campus")
    if campus_id:
        links = links.filter(campus_id=campus_id)
    links = list(links.order_by("student__first_name", "student__username", "campus_id"))
    if not links:
        return {**base, "children": [], "totals": _totals([]), "share_text": "", "whatsapp_url": ""}

    student_ids = {l.student_id for l in links}
    campus_ids = {l.campus_id for l in links}
    enrollments: dict[tuple, StudentEnrollment] = {}
    for e in (
        StudentEnrollment.objects.filter(
            student_id__in=student_ids, section__school_class__campus_id__in=campus_ids,
            status=StudentEnrollment.Status.ACTIVE,
        ).select_related("section__school_class").order_by("-session__start_date")
    ):
        enrollments.setdefault((e.section.school_class.campus_id, e.student_id), e)
    enr_ids = [e.pk for e in enrollments.values()]

    att = _attendance_by_enrollment(enr_ids, start, today)
    marks = _marks_by_enrollment(enr_ids, start, today)
    fees = _fees_by_enrollment(enr_ids, today)

    children = []
    for link in links:
        enr = enrollments.get((link.campus_id, link.student_id))
        enr_id = enr.pk if enr else None
        a = att.get(enr_id) if enr_id else None
        marked = (a["present"] + a["late"] + a["absent"]) if a else 0
        m = marks.get(enr_id) if enr_id else None
        f = fees.get(enr_id) if enr_id else None
        child = {
            "link_id": str(link.pk),
            "campus_id": str(link.campus_id),
            "campus_name": link.campus.name,
            "student_id": str(link.student_id),
            "student_name": _name(link.student),
            "section": (f"{enr.section.school_class.name} {enr.section.name}".strip() if enr else ""),
            "attendance": {
                "marked_days": marked,
                "present": a["present"] if a else 0,
                "late": a["late"] if a else 0,
                "absent": a["absent"] if a else 0,
                "leave": a["leave"] if a else 0,
                "percent": _pct((a["present"] + a["late"]) if a else 0, marked),
                "absent_dates": [d.isoformat() for d in a["absent_dates"]] if a else [],
            },
            "marks": {
                "trend": m["trend"] if m else [],
                "direction": m["direction"] if m else "none",
                "delta": m["delta"] if m else None,
                "new_results": m["new_results"] if m else 0,
            },
            "fees": {
                "due": _money(f["due"]) if f else "0.00",
                "open_invoices": f["open"] if f else 0,
                "overdue_invoices": f["overdue"] if f else 0,
                "next_due_date": f["next_due"].isoformat() if f and f["next_due"] else None,
            },
        }
        child["has_news"] = _has_news(child)
        child["share_text"] = render_child_text(child, base["label"], lang)
        children.append(child)

    share_text = render_share_text(children, base["label"], lang)
    return {
        **base,
        "children": children,
        "totals": _totals(children),
        "share_text": share_text,
        "whatsapp_url": whatsapp_url(share_text),
    }


def _has_news(child) -> bool:
    return bool(
        child["attendance"]["marked_days"] or child["marks"]["trend"] or child["fees"]["open_invoices"]
    )


def _totals(children) -> dict:
    return {
        "children": len(children),
        "absent_days": sum(c["attendance"]["absent"] for c in children),
        "fees_due": _money(sum((Decimal(c["fees"]["due"]) for c in children), Decimal("0"))),
        "overdue_invoices": sum(c["fees"]["overdue_invoices"] for c in children),
        "new_results": sum(c["marks"]["new_results"] for c in children),
    }


def has_anything_to_report(summary) -> bool:
    return any(c["has_news"] for c in summary["children"])


# ------------------------------------------------------------------------------------------------------ templates
_T = {
    "en": {
        "head": "{name}'s weekly report ({label})",
        "att": "Attendance: {pct} ({present}/{marked} days present)",
        "att_none": "Attendance: not marked this week",
        "absent_on": "Absent on: {dates}",
        "marks": "Marks: {trend}",
        "marks_up": "up {d} points since {term}",
        "marks_down": "down {d} points since {term}",
        "marks_flat": "steady since {term}",
        "marks_new": "{n} new result(s) published",
        "marks_none": "Marks: no published results yet",
        "fee": "Fees due: Rs. {amount}{overdue}",
        "fee_overdue": " ({n} overdue)",
        "fee_next": "Next due date: {date}",
        "fee_clear": "Fees: nothing due",
        "app_line": "Open the app for details: {url}",
        "mail_subject": "Weekly report: {names} ({label})",
        "mail_hello": "Hello {parent},",
        "mail_intro": "Here is how the week went ({label}).",
        "mail_bye": "- {campus}",
        "mail_footer": "You get this because you are linked as a parent. Turn it off in Notification settings.",
        "push_title": "Weekly report ready",
        "push_body_1": "{name}: {pct} attendance{fees}",
        "push_body_n": "{n} children - tap to see the week",
        "push_fees": ", fees due Rs. {amount}",
    },
    "hi": {
        "head": "{name} की साप्ताहिक रिपोर्ट ({label})",
        "att": "हाज़िरी: {pct} ({present}/{marked} दिन उपस्थित)",
        "att_none": "हाज़िरी: इस हफ़्ते दर्ज नहीं",
        "absent_on": "अनुपस्थित: {dates}",
        "marks": "अंक: {trend}",
        "marks_up": "{term} से {d} अंक ऊपर",
        "marks_down": "{term} से {d} अंक नीचे",
        "marks_flat": "{term} जितने ही",
        "marks_new": "{n} नया रिज़ल्ट आया",
        "marks_none": "अंक: अभी कोई रिज़ल्ट प्रकाशित नहीं",
        "fee": "फ़ीस बाकी: Rs. {amount}{overdue}",
        "fee_overdue": " ({n} की तारीख निकल चुकी)",
        "fee_next": "अगली तारीख: {date}",
        "fee_clear": "फ़ीस: कुछ बाकी नहीं",
        "app_line": "पूरा ब्योरा ऐप में देखें: {url}",
        "mail_subject": "साप्ताहिक रिपोर्ट: {names} ({label})",
        "mail_hello": "नमस्ते {parent},",
        "mail_intro": "यह रहा इस हफ़्ते का हाल ({label})।",
        "mail_bye": "- {campus}",
        "mail_footer": "आपको यह इसलिए मिला क्योंकि आप अभिभावक के रूप में जुड़े हैं। नोटिफ़िकेशन सेटिंग्स में बंद कर सकते हैं।",
        "push_title": "साप्ताहिक रिपोर्ट तैयार है",
        "push_body_1": "{name}: हाज़िरी {pct}{fees}",
        "push_body_n": "{n} बच्चे - हफ़्ता देखने के लिए खोलें",
        "push_fees": ", फ़ीस बाकी Rs. {amount}",
    },
}


def _t(lang, key, **kw) -> str:
    return _T[normalize_lang(lang)][key].format(**kw)


def _short_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def child_lines(child, lang="en") -> list[str]:
    """The plain lines for one child (no heading) — shared by WhatsApp text, email text and email html."""
    lang = normalize_lang(lang)
    a, m, f = child["attendance"], child["marks"], child["fees"]
    lines = []
    if a["marked_days"]:
        lines.append(_t(lang, "att", pct=_fmt_pct(a["percent"]), present=a["present"] + a["late"], marked=a["marked_days"]))
        if a["absent_dates"]:
            lines.append(_t(lang, "absent_on", dates=", ".join(_short_date(d) for d in a["absent_dates"])))
    else:
        lines.append(_t(lang, "att_none"))

    if m["trend"]:
        trend = " \u2192 ".join(f"{p['term']} {_fmt_pct(p['percent'])}" for p in m["trend"])
        extra = ""
        if m["direction"] in ("up", "down", "flat") and len(m["trend"]) >= 2:
            key = {"up": "marks_up", "down": "marks_down", "flat": "marks_flat"}[m["direction"]]
            extra = " (" + _t(lang, key, d=abs(m["delta"]), term=m["trend"][-2]["term"]) + ")"
        lines.append(_t(lang, "marks", trend=trend) + extra)
        if m["new_results"]:
            lines.append(_t(lang, "marks_new", n=m["new_results"]))
    else:
        lines.append(_t(lang, "marks_none"))

    if f["open_invoices"]:
        overdue = _t(lang, "fee_overdue", n=f["overdue_invoices"]) if f["overdue_invoices"] else ""
        lines.append(_t(lang, "fee", amount=f["due"], overdue=overdue))
        if f["next_due_date"]:
            lines.append(_t(lang, "fee_next", date=_short_date(f["next_due_date"])))
    else:
        lines.append(_t(lang, "fee_clear"))
    return lines


def _app_url() -> str:
    return (getattr(settings, "APP_WEB_BASE_URL", "") or "").rstrip("/")


def render_child_text(child, label, lang="en") -> str:
    """WhatsApp-ready text for ONE child (a parent can forward it to the other guardian)."""
    head = "\U0001F4CA " + _t(lang, "head", name=child["student_name"], label=label)
    return "\n".join([head, *child_lines(child, lang)])


def render_share_text(children, label, lang="en") -> str:
    """WhatsApp-ready text for all children (blank line between children, one app link at the end)."""
    if not children:
        return ""
    blocks = [render_child_text(c, label, lang) for c in children]
    url = _app_url()
    if url:
        blocks.append(_t(lang, "app_line", url=url))
    return "\n\n".join(blocks)


def whatsapp_url(text: str) -> str:
    return f"https://wa.me/?text={quote(text, safe='')}" if text else ""


def render_email(summary, parent_name="", campus_name="", lang="en") -> dict:
    """{"subject", "text", "html"} for the weekly email. Everything user-supplied is HTML-escaped."""
    lang = normalize_lang(lang)
    children = summary["children"]
    label = summary["label"]
    names = ", ".join(c["student_name"] for c in children)
    subject = _t(lang, "mail_subject", names=names, label=label)[:200]
    hello = _t(lang, "mail_hello", parent=parent_name or "")
    intro = _t(lang, "mail_intro", label=label)
    bye = _t(lang, "mail_bye", campus=campus_name or "LearnScroll")
    footer = _t(lang, "mail_footer")
    url = _app_url()

    text_parts = [hello, "", intro, ""]
    html_parts = [f"<p>{escape(hello)}</p>", f"<p>{escape(intro)}</p>"]
    for c in children:
        title = _t(lang, "head", name=c["student_name"], label=label)
        lines = child_lines(c, lang)
        text_parts += [title, *[f"  - {ln}" for ln in lines], ""]
        html_parts.append(f"<h3 style=\"margin:16px 0 4px\">{escape(title)}</h3><ul style=\"margin:0;padding-left:18px\">")
        html_parts += [f"<li>{escape(ln)}</li>" for ln in lines]
        html_parts.append("</ul>")
    if url:
        link = _t(lang, "app_line", url=url)
        text_parts += [link, ""]
        html_parts.append(f"<p><a href=\"{escape(url, quote=True)}\">{escape(link)}</a></p>")
    text_parts += [bye, "", footer]
    html_parts.append(f"<p>{escape(bye)}</p><p style=\"color:#777;font-size:12px\">{escape(footer)}</p>")
    return {"subject": subject, "text": "\n".join(text_parts), "html": "".join(html_parts)}


def push_copy(summary, lang="en") -> tuple[str, str]:
    """(title, body) for the weekly push / bell row."""
    lang = normalize_lang(lang)
    children = summary["children"]
    if len(children) == 1:
        c = children[0]
        pct = _fmt_pct(c["attendance"]["percent"]) or "-"
        due = Decimal(c["fees"]["due"])
        fees = _t(lang, "push_fees", amount=c["fees"]["due"]) if due > 0 else ""
        body = _t(lang, "push_body_1", name=c["student_name"], pct=pct, fees=fees)
    else:
        body = _t(lang, "push_body_n", n=len(children))
    return _t(lang, "push_title"), body
