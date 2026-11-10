"""
core/channel_policy.py - N8: ONE gate every e-mail / SMS / WhatsApp send goes through.

Why: before N8 `_send_email` mailed whatever address was on the account, `_send_sms` texted whatever number it found, and
nothing remembered a bounce, a complaint or an unsubscribe. This module answers one question - "may we contact this user on
this channel RIGHT NOW, and if not, why?" - and keeps the records behind the answer:

    e-mail     master toggle on . address is VERIFIED (6-digit code) . not suppressed (bounce / complaint) . under the daily cap
    sms        master toggle on . phone verified (User.phone_verified) . consent granted for THIS number (ChannelConsent)
    whatsapp   . not suppressed (STOP / invalid number) . under the per-user cap AND the global cost guard . not held back by
               quiet hours / focus mode (same rules as push)

PURE python + ORM. Never raises into a send path: decide() returns a Decision, the caller logs `reason` and skips.
Reasons (stable strings, the Flutter settings screen maps them to text): see REASONS.

Settings (all optional, env-driven in LearnScroll/settings.py):
    NOTIF_EMAIL_DAILY_CAP / NOTIF_SMS_DAILY_CAP / NOTIF_WHATSAPP_DAILY_CAP        per user, rolling 24 h   (10 / 3 / 3)
    NOTIF_SMS_GLOBAL_DAILY_CAP / NOTIF_WHATSAPP_GLOBAL_DAILY_CAP                  whole platform cost guard (0 = off)
    NOTIF_EMAIL_SOFT_BOUNCE_LIMIT                                                 soft bounces in 7 days before suppress (3)
    NOTIF_CONSENT_TEXT_VERSION                                                    version label of the consent wording
    NOTIF_EMAIL_CODE_TTL_SECONDS / _RESEND_SECONDS / _MAX_ATTEMPTS / _MAX_SENDS_PER_DAY
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CHANNELS = ("email", "sms", "whatsapp")
PHONE_CHANNELS = ("sms", "whatsapp")

# Decision.reason values.
REASONS = (
    "ok",
    "master_off",          # the channel switch in Notification settings is off
    "type_off",            # muted type / per-type override (N1)
    "no_address",          # no e-mail / phone on the account
    "email_unverified",    # e-mail not verified (or changed after verification)
    "phone_unverified",    # User.phone_verified is False
    "no_consent",          # sms / whatsapp: no granted ChannelConsent for the current number
    "suppressed",          # bounce / complaint / STOP / invalid number
    "daily_cap",           # per-user cap reached
    "global_cap",          # platform cost guard reached
    "quiet",               # quiet hours / DND / focus / roll-up (sms + whatsapp only)
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = "ok"
    address: str = ""

    def __bool__(self):  # `if policy.decide(...)`
        return self.allowed


# ------------------------------------------------------------------------------------------------ settings helpers
def _cfg(name, default):
    return getattr(settings, name, default)


def daily_cap(channel: str) -> int:
    return int({"email": _cfg("NOTIF_EMAIL_DAILY_CAP", 10), "sms": _cfg("NOTIF_SMS_DAILY_CAP", 3),
                "whatsapp": _cfg("NOTIF_WHATSAPP_DAILY_CAP", 3)}.get(channel, 0))


def global_cap(channel: str) -> int:
    return int({"sms": _cfg("NOTIF_SMS_GLOBAL_DAILY_CAP", 0), "whatsapp": _cfg("NOTIF_WHATSAPP_GLOBAL_DAILY_CAP", 0)}.get(channel, 0))


def consent_text_version() -> str:
    return str(_cfg("NOTIF_CONSENT_TEXT_VERSION", "2026-10"))


# ------------------------------------------------------------------------------------------------ addresses
def normalize_address(channel: str, address) -> str:
    """Canonical form used as the suppression / consent key (lower-case e-mail; digits-only phone with leading +)."""
    a = (address or "").strip()
    if channel == "email":
        return a.lower()
    digits = "".join(ch for ch in a if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:  # bare Indian mobile
        digits = "91" + digits
    return "+" + digits


def email_for(user) -> str:
    return normalize_address("email", getattr(user, "email", "") or "")


def phone_for(user) -> str:
    """The number SMS / WhatsApp use. `User.phone` is the real field; phone_number / mobile are legacy fallbacks."""
    raw = getattr(user, "phone", None) or getattr(user, "phone_number", None) or getattr(user, "mobile", None) or ""
    return normalize_address("sms", raw)


def address_for(user, channel: str) -> str:
    return email_for(user) if channel == "email" else phone_for(user)


def mask_address(channel: str, address: str) -> str:
    if not address:
        return ""
    if channel == "email":
        local, _, domain = address.partition("@")
        return f"{local[:1]}***@{domain}" if domain else address[:1] + "***"
    return f"{address[:3]}{'•' * max(0, len(address) - 7)}{address[-4:]}" if len(address) > 7 else address


# ------------------------------------------------------------------------------------------------ verification state
def email_verified(user, pref=None) -> bool:
    """True only while the CURRENT account e-mail is the one that was verified (a changed address un-verifies)."""
    pref = pref or _pref(user)
    addr = email_for(user)
    return bool(addr and pref.email_verified_at and normalize_address("email", pref.email_verified_address) == addr)


def phone_verified(user) -> bool:
    return bool(phone_for(user) and getattr(user, "phone_verified", False))


def _pref(user):
    from .models import NotificationPreference

    return NotificationPreference.for_user(user)


# ------------------------------------------------------------------------------------------------ consent ledger
def latest_consent(user, channel: str):
    from .models import ChannelConsent

    return ChannelConsent.objects.filter(user=user, channel=channel).order_by("-created_at", "-id").first()


def has_valid_consent(user, channel: str) -> bool:
    """Newest ledger row is a grant AND was given for the number / address the account has now."""
    row = latest_consent(user, channel)
    return bool(row and row.granted and normalize_address(channel, row.address) == address_for(user, channel))


def record_consent(user, channel: str, granted: bool, *, source: str = "app", text_version: str = "", ip=None):
    from .models import ChannelConsent

    return ChannelConsent.objects.create(
        user=user, channel=channel, granted=bool(granted), address=address_for(user, channel),
        text_version=(text_version or consent_text_version())[:20], source=source[:20], ip_address=ip or None,
    )


# ------------------------------------------------------------------------------------------------ suppression
def is_suppressed(channel: str, address: str) -> bool:
    from .models import ChannelSuppression

    addr = normalize_address(channel, address)
    return bool(addr) and ChannelSuppression.objects.filter(channel=channel, address=addr).exists()


def deliverable_emails(addresses) -> list:
    """N8 - for TRANSACTIONAL mail that legitimately bypasses `decide()` (fee receipts, legal / security notices - the user cannot
    opt out of those): drop only the addresses the provider told us are dead (hard bounce / complaint / soft-bounce limit).
    Mailing a dead address again only hurts the sender reputation of every other mail. Order kept, duplicates removed, never raises
    (on any error the list is returned unchanged - a receipt must not be lost because the check failed)."""
    try:
        out, seen = [], set()
        for a in addresses or ():
            key = normalize_address("email", a)
            if not key or key in seen:
                continue
            seen.add(key)
            if not is_suppressed("email", key):
                out.append(a)
        return out
    except Exception:
        return [a for a in (addresses or ()) if a]


def suppress(channel: str, address: str, reason: str, detail: str = "", soft_bounces: int = 0) -> int:
    """Stop contacting `address` on `channel`, switch the master toggle off for every account using it and write a
    withdrawn-consent row for each (source = the reason). Returns how many accounts were switched off."""
    from login.models import User

    from .models import ChannelSuppression

    addr = normalize_address(channel, address)
    if not addr:
        return 0
    ChannelSuppression.objects.update_or_create(
        channel=channel, address=addr,
        defaults={"reason": reason, "detail": (detail or "")[:200], "soft_bounces": soft_bounces},
    )
    field = "email" if channel == "email" else "phone"
    switched = 0
    for user in User.objects.filter(**{f"{field}__iexact": addr}):
        pref = _pref(user)
        flag = {"email": "email_enabled", "sms": "sms_enabled", "whatsapp": "whatsapp_enabled"}[channel]
        if getattr(pref, flag):
            setattr(pref, flag, False)
            pref.save(update_fields=[flag, "updated_at"])
            switched += 1
        record_consent(user, channel, False, source=reason)
    return switched


def clear_suppression(channel: str, address: str) -> int:
    from .models import ChannelSuppression

    return ChannelSuppression.objects.filter(channel=channel, address=normalize_address(channel, address)).delete()[0]


# ------------------------------------------------------------------------------------------------ caps + send log
def sent_last_24h(user, channel: str, now=None) -> int:
    from .models import ChannelSendLog

    now = now or timezone.now()
    return ChannelSendLog.objects.filter(user=user, channel=channel, created_at__gte=now - timedelta(hours=24)).count()


def sent_today_global(channel: str, now=None) -> int:
    from .models import ChannelSendLog

    now = now or timezone.now()
    return ChannelSendLog.objects.filter(channel=channel, created_at__gte=now - timedelta(hours=24)).count()


def record_send(user, channel: str, notif_type: str = ""):
    from .models import ChannelSendLog

    try:
        return ChannelSendLog.objects.create(user=user, channel=channel, notif_type=(notif_type or "")[:40])
    except Exception:  # noqa: BLE001 - bookkeeping must never fail a send that already went out
        logger.exception("could not write ChannelSendLog (user=%s channel=%s)", getattr(user, "id", None), channel)
        return None


def prune_send_log(days: int = 35) -> int:
    from .models import ChannelSendLog

    return ChannelSendLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()[0]


# ------------------------------------------------------------------------------------------------ THE gate
def decide(user, channel: str, notif_type: str | None = None, *, now=None, respect_master: bool = True,
           apply_caps: bool = True) -> Decision:
    """May we send `channel` to `user` now? `notif_type` (a Notification.NotifType value) enables per-type switches, the
    locked-type cap bypass and the quiet-hours rule for sms / whatsapp. `respect_master=False` is for the e-mail digest,
    which is its own opt-in (digest_frequency) independent of the per-event e-mail switch."""
    if channel not in CHANNELS:
        return Decision(False, "master_off")
    now = now or timezone.now()
    pref = _pref(user)
    master = {"email": pref.email_enabled, "sms": pref.sms_enabled, "whatsapp": pref.whatsapp_enabled}[channel]
    if respect_master and not master:
        return Decision(False, "master_off")
    if notif_type and respect_master and not pref.channel_allowed(notif_type, channel):
        return Decision(False, "type_off")

    addr = address_for(user, channel)
    if not addr:
        return Decision(False, "no_address")
    if is_suppressed(channel, addr):
        return Decision(False, "suppressed", addr)
    if channel == "email":
        if not email_verified(user, pref):
            return Decision(False, "email_unverified", addr)
    else:
        if not phone_verified(user):
            return Decision(False, "phone_unverified", addr)
        if not has_valid_consent(user, channel):
            return Decision(False, "no_consent", addr)
        if notif_type and pref.push_block_reason(notif_type, now):  # same hold-back rules as push
            return Decision(False, "quiet", addr)

    if apply_caps:
        locked = bool(notif_type) and notif_type in pref.NEVER_MUTE_TYPES
        cap = daily_cap(channel)
        if not locked and cap and sent_last_24h(user, channel, now) >= cap:
            return Decision(False, "daily_cap", addr)
        gcap = global_cap(channel)
        if gcap and sent_today_global(channel, now) >= gcap:
            return Decision(False, "global_cap", addr)
    return Decision(True, "ok", addr)


# ------------------------------------------------------------------------------------------------ enabling a channel
def enable_blockers(user, channel: str) -> list[str]:
    """Why the master switch for `channel` cannot be turned ON yet (empty list = it can).
    sms / whatsapp need a verified phone AND a granted consent for it; e-mail only needs an address + no suppression
    (verification is checked at send time, so the switch itself may be on while the code is still pending)."""
    out = []
    addr = address_for(user, channel)
    if not addr:
        out.append("no_address")
        return out
    if is_suppressed(channel, addr):
        out.append("suppressed")
    if channel in PHONE_CHANNELS:
        if not phone_verified(user):
            out.append("phone_unverified")
        if not has_valid_consent(user, channel):
            out.append("no_consent")
    return out


def set_channel_enabled(user, channel: str, enabled: bool, *, source="app", text_version="", ip=None) -> list[str]:
    """Consent-aware switch used by the consent endpoint. Turning ON writes the grant row itself (the user tapped the
    consent checkbox); returns blockers if it could not be turned on."""
    pref = _pref(user)
    flag = {"email": "email_enabled", "sms": "sms_enabled", "whatsapp": "whatsapp_enabled"}[channel]
    if not enabled:
        if getattr(pref, flag):
            setattr(pref, flag, False)
            pref.save(update_fields=[flag, "updated_at"])
        if channel in PHONE_CHANNELS or latest_consent(user, channel) is not None:
            record_consent(user, channel, False, source=source, text_version=text_version, ip=ip)
        return []
    addr = address_for(user, channel)
    blockers = []
    if not addr:
        blockers.append("no_address")
    elif is_suppressed(channel, addr):
        blockers.append("suppressed")
    if channel in PHONE_CHANNELS and addr and not phone_verified(user):
        blockers.append("phone_unverified")
    if blockers:
        return blockers
    record_consent(user, channel, True, source=source, text_version=text_version, ip=ip)
    setattr(pref, flag, True)
    pref.save(update_fields=[flag, "updated_at"])
    return []


# ------------------------------------------------------------------------------------------------ e-mail verification
def _code_key(user_id):
    return f"n8:emailcode:{user_id}"


def _digest(code: str, user_id, address: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{code}:{user_id}:{address}".encode(), hashlib.sha256).hexdigest()


def request_email_code(user, now=None) -> dict:
    """Create + e-mail a 6-digit code to the account's current address. Returns {ok, reason?, retry_after?}."""
    addr = email_for(user)
    if not addr:
        return {"ok": False, "reason": "no_address"}
    if is_suppressed("email", addr):
        return {"ok": False, "reason": "suppressed"}
    if email_verified(user):
        return {"ok": True, "reason": "already_verified"}
    cooldown = int(_cfg("NOTIF_EMAIL_CODE_RESEND_SECONDS", 60))
    cd_key = f"n8:emailcode:cd:{user.id}"
    if cooldown and cache.get(cd_key):
        return {"ok": False, "reason": "too_soon", "retry_after": cooldown}
    day_key = f"n8:emailcode:day:{user.id}:{timezone.now():%Y%m%d}"
    if int(cache.get(day_key) or 0) >= int(_cfg("NOTIF_EMAIL_CODE_MAX_SENDS_PER_DAY", 5)):
        return {"ok": False, "reason": "daily_limit"}

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = int(_cfg("NOTIF_EMAIL_CODE_TTL_SECONDS", 600))
    cache.set(_code_key(user.id), {"h": _digest(code, user.id, addr), "a": addr, "n": 0}, ttl)
    from .channel_templates import render_verification_email, language_for

    subject, text, html = render_verification_email(code, ttl // 60, language_for(user))
    try:
        from django.core.mail import EmailMultiAlternatives

        msg = EmailMultiAlternatives(subject, text, getattr(settings, "DEFAULT_FROM_EMAIL", None), [addr])
        msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)
    except Exception:  # noqa: BLE001
        logger.exception("verification e-mail failed for user %s", user.id)
        cache.delete(_code_key(user.id))
        return {"ok": False, "reason": "send_failed"}
    if cooldown:
        cache.set(cd_key, 1, cooldown)
    cache.set(day_key, int(cache.get(day_key) or 0) + 1, 24 * 3600)
    return {"ok": True, "reason": "sent", "ttl_seconds": ttl, "resend_after": cooldown}


def confirm_email_code(user, code: str, now=None) -> dict:
    """Check the code; on success store the verified address + time on the preference row."""
    addr = email_for(user)
    state = cache.get(_code_key(user.id))
    if not addr or not state or state.get("a") != addr:
        return {"ok": False, "reason": "code_expired"}
    if int(state.get("n", 0)) >= int(_cfg("NOTIF_EMAIL_CODE_MAX_ATTEMPTS", 5)):
        cache.delete(_code_key(user.id))
        return {"ok": False, "reason": "too_many_attempts"}
    state["n"] = int(state.get("n", 0)) + 1
    if not hmac.compare_digest(state["h"], _digest((code or "").strip(), user.id, addr)):
        cache.set(_code_key(user.id), state, int(_cfg("NOTIF_EMAIL_CODE_TTL_SECONDS", 600)))
        return {"ok": False, "reason": "bad_code", "attempts_left": max(0, int(_cfg("NOTIF_EMAIL_CODE_MAX_ATTEMPTS", 5)) - state["n"])}
    cache.delete(_code_key(user.id))
    pref = _pref(user)
    pref.email_verified_address = addr
    pref.email_verified_at = now or timezone.now()
    pref.save(update_fields=["email_verified_address", "email_verified_at", "updated_at"])
    return {"ok": True, "reason": "verified"}


# ------------------------------------------------------------------------------------------------ unsubscribe links
_UNSUB_SALT = "n8-unsubscribe"
UNSUB_SCOPES = ("channel", "digest")  # channel = every e-mail of this channel; digest = only the summary mail


def make_unsubscribe_token(user, channel: str = "email", scope: str = "channel") -> str:
    return signing.dumps({"u": user.id, "c": channel, "s": scope}, salt=_UNSUB_SALT, compress=True)


def read_unsubscribe_token(token: str):
    """-> dict or None. No max_age on purpose: an unsubscribe link in a 2-year-old mail must still work."""
    try:
        data = signing.loads(token, salt=_UNSUB_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("c") not in CHANNELS or data.get("s") not in UNSUB_SCOPES:
        return None
    return data


def public_base_url() -> str:
    return (_cfg("SITE_URL", "") or _cfg("PUBLIC_BASE_URL", "") or "").rstrip("/")


def unsubscribe_url(user, channel: str = "email", scope: str = "channel") -> str:
    base = public_base_url()
    if not base:
        logger.warning("SITE_URL is not set - e-mails go out WITHOUT an unsubscribe link (set SITE_URL).")
        return ""
    return f"{base}/core/notifications/unsubscribe/{make_unsubscribe_token(user, channel, scope)}/"


def email_headers(url: str) -> dict:
    """RFC 2369 + RFC 8058 one-click headers (Gmail / Yahoo bulk-sender rules)."""
    if not url:
        return {}
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def apply_unsubscribe(token: str, *, ip=None) -> dict:
    """Used by the public unsubscribe endpoint. Idempotent. -> {ok, channel, scope}."""
    from login.models import User

    data = read_unsubscribe_token(token)
    if data is None:
        return {"ok": False, "reason": "bad_token"}
    user = User.objects.filter(pk=data["u"]).first()
    if user is None:
        return {"ok": False, "reason": "bad_token"}
    pref = _pref(user)
    if data["s"] == "digest":
        pref.digest_frequency = pref.DigestFrequency.OFF
        pref.save(update_fields=["digest_frequency", "updated_at"])
    else:
        set_channel_enabled(user, data["c"], False, source="unsubscribe_link", ip=ip)
        if data["c"] == "email":  # e-mail: also stop the summary mail, "unsubscribe" means unsubscribe
            pref.refresh_from_db()
            pref.digest_frequency = pref.DigestFrequency.OFF
            pref.save(update_fields=["digest_frequency", "updated_at"])
    return {"ok": True, "channel": data["c"], "scope": data["s"]}


# ------------------------------------------------------------------------------------------------ provider events
def handle_provider_event(channel: str, event: str, address: str, *, bounce_type: str = "", detail: str = "") -> dict:
    """Normalised bounce / complaint / STOP / invalid-number handling (called by the webhook view).
        email: bounce (bounce_type hard|soft) | complaint
        sms / whatsapp: stop | invalid
    hard bounce / complaint / stop / invalid -> suppress at once; soft bounces count up and suppress at the limit."""
    from .models import ChannelSuppression

    event = (event or "").lower()
    addr = normalize_address(channel, address)
    if channel not in CHANNELS or not addr:
        return {"ok": False, "reason": "bad_event"}
    if channel == "email" and event == "bounce" and (bounce_type or "hard").lower() == "soft":
        key = f"n8:softbounce:{addr}"
        count = int(cache.get(key) or 0) + 1
        cache.set(key, count, 7 * 24 * 3600)
        if count >= int(_cfg("NOTIF_EMAIL_SOFT_BOUNCE_LIMIT", 3)):
            n = suppress("email", addr, ChannelSuppression.Reason.SOFT_BOUNCE_LIMIT, detail, soft_bounces=count)
            return {"ok": True, "action": "suppressed", "accounts": n}
        return {"ok": True, "action": "counted", "soft_bounces": count}
    reason = {
        ("email", "bounce"): ChannelSuppression.Reason.HARD_BOUNCE,
        ("email", "complaint"): ChannelSuppression.Reason.COMPLAINT,
        ("sms", "stop"): ChannelSuppression.Reason.STOP,
        ("whatsapp", "stop"): ChannelSuppression.Reason.STOP,
        ("sms", "invalid"): ChannelSuppression.Reason.INVALID,
        ("whatsapp", "invalid"): ChannelSuppression.Reason.INVALID,
    }.get((channel, event))
    if reason is None:
        return {"ok": False, "reason": "unknown_event"}
    n = suppress(channel, addr, reason, detail)
    if channel in PHONE_CHANNELS and event == "stop":  # STOP on either phone channel = a wish about the number: both off
        other = "whatsapp" if channel == "sms" else "sms"
        n += suppress(other, addr, reason, detail)
    return {"ok": True, "action": "suppressed", "accounts": n}


# ------------------------------------------------------------------------------------------------ status for the app
def channel_status(user) -> dict:
    """What the Settings screen shows for each channel."""
    pref = _pref(user)
    out = {}
    for ch in CHANNELS:
        addr = address_for(user, ch)
        enabled = {"email": pref.email_enabled, "sms": pref.sms_enabled, "whatsapp": pref.whatsapp_enabled}[ch]
        verified = email_verified(user, pref) if ch == "email" else phone_verified(user)
        consent = has_valid_consent(user, ch)
        blockers = enable_blockers(user, ch)
        suppressed = bool(addr) and is_suppressed(ch, addr)
        row = latest_consent(user, ch)
        out[ch] = {
            "enabled": bool(enabled),
            "address_masked": mask_address(ch, addr),
            "has_address": bool(addr),
            "verified": bool(verified),
            "consent": bool(consent),
            "consent_at": row.created_at.isoformat() if row and row.granted else None,
            "suppressed": suppressed,
            "can_enable": not blockers,
            "blockers": blockers,
            "sending_active": bool(decide(user, ch, apply_caps=False).allowed) if enabled else False,
            "daily_cap": daily_cap(ch),
            "sent_24h": sent_last_24h(user, ch),
        }
    out["consent_text_version"] = consent_text_version()
    out["digest_frequency"] = pref.digest_frequency
    return out
