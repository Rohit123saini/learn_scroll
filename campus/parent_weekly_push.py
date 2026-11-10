# campus/parent_weekly_push.py
"""
P-F14 — send the WEEKLY SUMMARY to every parent with a linked child (run by
`campus.tasks.send_parent_weekly_summaries`, Sunday evening).

Per parent (real `User` accounts only — loginless "shadow" parents have no email / bell):
  1. build the summary (campus/parent_weekly.py — the same numbers the in-app screen shows);
  2. nothing to report (no attendance marked, no published result, no open fee) -> skip, no empty mail;
  3. bell row + push (`bridge.notify`, honours push toggle / muted types / quiet hours);
  4. e-mail (HTML + text) through `core.channel_policy.decide` — verified address only, bounce / complaint
     suppression, daily cap, per-type switch, unsubscribe link + one-click headers.

WhatsApp is NOT auto-sent: it is opt-in + needs an approved template. The ready-made text / `wa.me` link /
single-variable template value come from the in-app endpoint (`/campus/parent-weekly/`) so the parent shares it
themselves, and the same text can be wired to a WhatsApp template later without touching the numbers.

Safety rules (each deliberate):
  * ONE send per (parent, ISO week): an atomic `cache.add` marker; removed again if nothing could be delivered so a
    later re-run retries. A Celery re-run / double beat never double-sends.
  * Never raises out of the loop: one bad parent (bad data, SMTP error) is logged and skipped.
  * Report-card style aggregates only — no chat content, no remarks.
"""
from __future__ import annotations

import logging
from html import escape

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from . import bridge, parent_weekly
from .bridge import NotifTypes
from .models import CampusParentLink

logger = logging.getLogger(__name__)

_MARKER_KEY = "campus:parent-weekly:{parent_id}:{week}"
_MARKER_TTL_SECONDS = 6 * 24 * 60 * 60   # a bit under one week: a late re-run on day 6 is still deduped
_SHADOW_PARENT_PREFIX = "parent_shadow_"
_BATCH = 200


def _lang_for(user) -> str:
    try:
        from core.channel_templates import language_for

        return parent_weekly.normalize_lang(language_for(user))
    except Exception:  # language is a nicety
        return "en"


def _week_key(today) -> str:
    iso = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _send_email(parent, summary, lang) -> bool:
    from django.core.mail import EmailMultiAlternatives

    from core import channel_policy as policy

    decision = policy.decide(parent, "email", NotifTypes.CAMPUS_PARENT_WEEKLY)
    if not decision.allowed:
        logger.info("parent weekly e-mail skipped for user %s: %s", parent.pk, decision.reason)
        return False
    campuses = {c["campus_name"] for c in summary["children"]}
    mail = parent_weekly.render_email(
        summary, parent_name=parent_weekly._name(parent),
        campus_name=next(iter(campuses)) if len(campuses) == 1 else "", lang=lang,
    )
    url = policy.unsubscribe_url(parent, "email", "channel")
    text, html = mail["text"], mail["html"]
    if url:
        label = "सदस्यता छोड़ें" if lang == "hi" else "Unsubscribe"
        text += f"\n\n{label}: {url}"
        html += f'<p style="font-size:12px"><a href="{escape(url, quote=True)}">{escape(label)}</a></p>'
    msg = EmailMultiAlternatives(
        subject=mail["subject"], body=text, from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
        to=[decision.address], headers=policy.email_headers(url),
    )
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)
    policy.record_send(parent, "email", NotifTypes.CAMPUS_PARENT_WEEKLY)
    return True


def send_weekly_to_parent(parent, today=None) -> dict:
    """Send one parent's weekly summary. Returns {"sent": bool, "push": bool, "email": bool, "reason": str}."""
    today = today or timezone.localdate()
    key = _MARKER_KEY.format(parent_id=parent.pk, week=_week_key(today))
    if not cache.add(key, 1, timeout=_MARKER_TTL_SECONDS):
        return {"sent": False, "push": False, "email": False, "reason": "already_sent"}

    delivered = {"push": False, "email": False}
    try:
        lang = _lang_for(parent)
        summary = parent_weekly.build_weekly(parent, lang=lang, today=today)
        if not parent_weekly.has_anything_to_report(summary):
            cache.delete(key)
            return {"sent": False, **delivered, "reason": "nothing_to_report"}

        title, body = parent_weekly.push_copy(summary, lang)
        campus_ids = {c["campus_id"] for c in summary["children"]}
        data = {"type": NotifTypes.CAMPUS_PARENT_WEEKLY, "week_end": summary["week_end"]}
        if len(campus_ids) == 1:
            data["campus_id"] = next(iter(campus_ids))
        try:
            bridge.notify(
                users=[parent], notif_type=NotifTypes.CAMPUS_PARENT_WEEKLY, title=title, body=body, data=data, push=True,
            )
            delivered["push"] = True   # the bell row is always written; the push itself honours the user's prefs
        except Exception:
            logger.exception("parent weekly bell/push failed (parent=%s).", parent.pk)

        try:
            delivered["email"] = _send_email(parent, summary, lang)
        except Exception:
            logger.exception("parent weekly e-mail failed (parent=%s).", parent.pk)

        if not any(delivered.values()):
            cache.delete(key)  # nothing went out -> the next run may retry
            return {"sent": False, **delivered, "reason": "delivery_failed"}
        return {"sent": True, **delivered, "reason": "ok"}
    except Exception:
        cache.delete(key)
        logger.exception("parent weekly summary failed (parent=%s).", parent.pk)
        return {"sent": False, **delivered, "reason": "error"}


def send_all(today=None) -> dict:
    """Weekly run over every parent that has a link to a live campus. Returns counts for the beat log."""
    today = today or timezone.localdate()
    User = get_user_model()
    parent_ids = list(
        CampusParentLink.objects.filter(campus__is_deleted=False)
        .values_list("parent_id", flat=True).distinct().order_by("parent_id")
    )
    stats = {"parents": len(parent_ids), "sent": 0, "emailed": 0, "skipped": 0}
    for i in range(0, len(parent_ids), _BATCH):
        chunk = parent_ids[i:i + _BATCH]
        for parent in User.objects.filter(pk__in=chunk, is_active=True):
            if (parent.username or "").startswith(_SHADOW_PARENT_PREFIX):
                stats["skipped"] += 1
                continue
            res = send_weekly_to_parent(parent, today)
            if res["sent"]:
                stats["sent"] += 1
                stats["emailed"] += 1 if res["email"] else 0
            else:
                stats["skipped"] += 1
    return stats
