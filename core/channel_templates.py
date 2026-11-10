"""
core/channel_templates.py - N8: Hindi + English wording for e-mail / SMS / WhatsApp.

Language = UserPreference.language ("hi*" -> Hindi, everything else English), same rule as the study digest.
PURE rendering: no sending, no DB writes (language_for does one tiny read, failure -> "en").

SMS note (India / DLT): operators only deliver text that matches a REGISTERED template. `render_sms` therefore keeps
the registered body untouched and only appends the short STOP line; if your DLT template already contains the opt-out
wording set NOTIF_SMS_APPEND_STOP = False. Hindi SMS is unicode (70 chars / segment) - the body is cut at
NOTIF_SMS_MAX_SEGMENTS segments to keep the cost predictable.
"""
from __future__ import annotations

from html import escape

from django.conf import settings

EN, HI = "en", "hi"


def language_for(user) -> str:
    from .content_digest import _language

    return _language(user)


def _pick(lang: str) -> str:
    return HI if (lang or "").lower().startswith("hi") else EN


APP_NAME = "LearnScroll"

_T = {
    EN: {
        "verify_subject": "Your LearnScroll e-mail verification code",
        "verify_body": "Your verification code is {code}.\nIt works for {minutes} minutes. If you did not ask for it, ignore this mail.",
        "verify_html_intro": "Your verification code is",
        "verify_html_foot": "It works for {minutes} minutes. If you did not ask for it, ignore this mail.",
        "footer_unsub": "You get this because e-mail notifications are on in your LearnScroll settings.",
        "unsub_link": "Unsubscribe",
        "manage": "Manage notifications in the app: Settings > Notifications.",
        "digest_subject": "Your {freq} LearnScroll digest ({n} update{s})",
        "digest_intro": "Here's what you missed:",
        "freq_daily": "daily", "freq_weekly": "weekly",
        "sms_stop": "Reply STOP to opt out.",
    },
    HI: {
        "verify_subject": "आपका LearnScroll ईमेल वेरिफ़िकेशन कोड",
        "verify_body": "आपका वेरिफ़िकेशन कोड {code} है।\nयह {minutes} मिनट तक चलेगा। अगर आपने नहीं माँगा तो इस मेल को अनदेखा करें।",
        "verify_html_intro": "आपका वेरिफ़िकेशन कोड",
        "verify_html_foot": "यह {minutes} मिनट तक चलेगा। अगर आपने नहीं माँगा तो इस मेल को अनदेखा करें।",
        "footer_unsub": "आपको यह मेल इसलिए मिला क्योंकि LearnScroll सेटिंग्स में ईमेल नोटिफ़िकेशन चालू हैं।",
        "unsub_link": "अनसब्सक्राइब करें",
        "manage": "ऐप में बदलें: सेटिंग्स > नोटिफ़िकेशन।",
        "digest_subject": "आपका {freq} LearnScroll डाइजेस्ट ({n} अपडेट)",
        "digest_intro": "जो आपसे छूट गया:",
        "freq_daily": "दैनिक", "freq_weekly": "साप्ताहिक",
        "sms_stop": "बंद करने के लिए STOP भेजें।",
    },
}


def t(lang: str, key: str, **kw) -> str:
    text = _T[_pick(lang)][key]
    return text.format(**kw) if kw else text


# ------------------------------------------------------------------------------------------------ e-mail
def _html_shell(lang: str, inner_html: str, unsub_url: str) -> str:
    unsub = (f'<a href="{escape(unsub_url, quote=True)}">{escape(t(lang, "unsub_link"))}</a> · ' if unsub_url else "")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;padding:16px;color:#1c1b1f">'
        f'<h2 style="margin:0 0 12px;color:#4b3fd1">{APP_NAME}</h2>{inner_html}'
        '<hr style="border:none;border-top:1px solid #e3e1ec;margin:20px 0 8px">'
        f'<p style="font-size:12px;color:#6b6975;margin:0">{escape(t(lang, "footer_unsub"))}<br>{unsub}{escape(t(lang, "manage"))}</p>'
        '</div>'
    )


def render_event_email(title: str, message: str, lang: str = EN, unsub_url: str = ""):
    """-> (subject, text, html). `title` becomes the subject; every dynamic string is HTML-escaped."""
    lang = _pick(lang)
    text = f"{title}\n\n{message}".strip() if message else title
    text += f"\n\n--\n{t(lang, 'footer_unsub')}\n" + (f"{t(lang, 'unsub_link')}: {unsub_url}\n" if unsub_url else "") + t(lang, "manage")
    inner = f'<h3 style="margin:0 0 8px">{escape(title)}</h3>' + (f'<p style="margin:0">{escape(message)}</p>' if message else "")
    return title[:200], text, _html_shell(lang, inner, unsub_url)


def render_digest_email(notifs, freq: str, lang: str = EN, unsub_url: str = ""):
    """`notifs`: iterable of objects with .title / .message. -> (subject, text, html)."""
    lang = _pick(lang)
    items = list(notifs)
    n = len(items)
    freq_word = t(lang, "freq_weekly" if freq == "weekly" else "freq_daily")
    subject = t(lang, "digest_subject", freq=freq_word, n=n, s="" if n == 1 else "s")
    lines = [f"- {i.title}" + (f": {i.message}" if getattr(i, "message", "") else "") for i in items]
    text = t(lang, "digest_intro") + "\n\n" + "\n".join(lines)
    text += f"\n\n--\n{t(lang, 'footer_unsub')}\n" + (f"{t(lang, 'unsub_link')}: {unsub_url}\n" if unsub_url else "") + t(lang, "manage")
    li = "".join(
        f"<li><b>{escape(i.title)}</b>" + (f" - {escape(i.message)}" if getattr(i, "message", "") else "") + "</li>" for i in items
    )
    inner = f'<p style="margin:0 0 8px">{escape(t(lang, "digest_intro"))}</p><ul style="padding-left:18px">{li}</ul>'
    return subject, text, _html_shell(lang, inner, unsub_url)


def render_verification_email(code: str, minutes: int, lang: str = EN):
    """-> (subject, text, html). Transactional: no unsubscribe footer."""
    lang = _pick(lang)
    text = t(lang, "verify_body", code=code, minutes=minutes)
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;padding:16px">'
        f'<h2 style="margin:0 0 12px;color:#4b3fd1">{APP_NAME}</h2>'
        f'<p>{escape(t(lang, "verify_html_intro"))}</p>'
        f'<p style="font-size:30px;letter-spacing:6px;font-weight:bold;margin:8px 0">{escape(code)}</p>'
        f'<p style="font-size:13px;color:#6b6975">{escape(t(lang, "verify_html_foot", minutes=minutes))}</p></div>'
    )
    return t(lang, "verify_subject"), text, html


# ------------------------------------------------------------------------------------------------ SMS / WhatsApp
def _sms_segment_len(text: str) -> int:
    return 70 if any(ord(c) > 127 for c in text) else 160


def render_sms(title: str, message: str, lang: str = EN) -> str:
    """One compact text: 'title: message' + STOP line, cut to NOTIF_SMS_MAX_SEGMENTS segments."""
    lang = _pick(lang)
    body = (f"{title}: {message}" if title and message else (message or title or "")).strip()
    stop = t(lang, "sms_stop") if getattr(settings, "NOTIF_SMS_APPEND_STOP", True) else ""
    tail = f" {stop}" if stop else ""
    limit = _sms_segment_len(body + tail) * int(getattr(settings, "NOTIF_SMS_MAX_SEGMENTS", 2))
    room = limit - len(tail)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…"
    return body + tail


def whatsapp_template(lang: str = EN):
    """-> (template_name, language_code). Per-language template names: MSG91_WHATSAPP_TEMPLATE_NAME_HI / _EN, falling back
    to the single MSG91_WHATSAPP_TEMPLATE_NAME (English code) so an existing deployment keeps working."""
    lang = _pick(lang)
    default = getattr(settings, "MSG91_WHATSAPP_TEMPLATE_NAME", "") or ""
    name = getattr(settings, f"MSG91_WHATSAPP_TEMPLATE_NAME_{lang.upper()}", "") or ""
    if name:
        return name, lang
    return default, "en"


def render_whatsapp_value(title: str, message: str) -> str:
    """The single body variable of the approved template. WhatsApp forbids newlines / 4+ spaces in a variable."""
    body = (f"{title}: {message}" if title and message else (message or title or "")).replace("\n", " ").replace("\t", " ")
    while "    " in body:
        body = body.replace("    ", "   ")
    return body.strip()[:1000]
