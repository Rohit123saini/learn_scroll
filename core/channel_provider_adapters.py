"""
core/channel_provider_adapters.py - N8: provider payload -> the ONE normalised event shape of `channel_policy.handle_provider_event`.

Why: the webhook view used to accept only our own shape ({"event", "address", "bounce_type"}) and said "write a tiny adapter in
front of it". Real providers send their own JSON. This module is that adapter - PURE functions (no DB, no network, no
Django), so every format is unit-testable and a new provider is one more function + one line in `ADAPTERS`.

Normalised event (a dict):   {"event": "bounce"|"complaint"|"stop"|"invalid", "address": str, "bounce_type": "hard"|"soft", "detail": str}

Supported `format` values (webhook `?format=`):
    generic   our own shape (default, unchanged)
    ses       Amazon SES notification delivered through SNS (e-mail): Bounce / Complaint
    sendgrid  SendGrid Event Webhook (e-mail): bounce / blocked / dropped / spamreport
    msg91     MSG91 SMS delivery report + inbound reply (sms): failed / invalid number / STOP
    meta      WhatsApp Cloud API webhook (whatsapp): inbound STOP + undeliverable status
Unknown / malformed input never raises - it yields no events.
"""
import json

STOP_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "बंद", "रोको"})

# SendGrid `dropped` reasons that mean "this address is dead / does not want mail" (hard), not a one-off problem.
_SENDGRID_DEAD_REASONS = ("bounced address", "spam reporting address", "unsubscribed address", "invalid")
# MSG91 / DLT status text that means the NUMBER itself is unusable (not a temporary network issue).
_SMS_INVALID_HINTS = ("invalid", "not exist", "unallocated", "barred", "number not", "absent subscriber")


def _ev(event, address, bounce_type="", detail=""):
    out = {"event": event, "address": str(address or "").strip(), "detail": str(detail or "")[:200]}
    if bounce_type:
        out["bounce_type"] = bounce_type
    return out


def is_stop_text(text) -> bool:
    """Whole message is a stop keyword (case-insensitive, surrounding spaces / punctuation ignored). 'please do not stop' is NOT one."""
    t = str(text or "").strip().strip(".!").strip()
    return bool(t) and t.upper() in STOP_WORDS


def _as_list(value):
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _loads(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------------- generic
def from_generic(payload):
    items = payload.get("events") if isinstance(payload, dict) and isinstance(payload.get("events"), list) else [payload]
    out = []
    for ev in items:
        if isinstance(ev, dict) and ev.get("event") and ev.get("address"):
            out.append(_ev(str(ev["event"]).lower(), ev["address"], str(ev.get("bounce_type", "")), ev.get("detail", "")))
    return out


# ---------------------------------------------------------------------------------------------------------------- SES (via SNS)
def from_ses(payload):
    """SNS envelope {"Type": "Notification", "Message": "<json string>"} (a raw SES notification dict is accepted too).
    Bounce: Permanent = hard, Transient / Undetermined = soft. Complaint -> complaint. Everything else (Delivery, Open ...) ignored."""
    if not isinstance(payload, dict):
        return []
    msg = payload
    if "Message" in payload:
        msg = _loads(payload.get("Message"))
        if not isinstance(msg, dict):
            return []
    kind = str(msg.get("notificationType") or msg.get("eventType") or "").lower()
    out = []
    if kind == "bounce":
        bounce = msg.get("bounce") or {}
        hard = str(bounce.get("bounceType", "")).lower() == "permanent"
        detail = bounce.get("bounceSubType", "")
        for r in _as_list(bounce.get("bouncedRecipients")):
            out.append(_ev("bounce", r.get("emailAddress"), "hard" if hard else "soft", detail))
    elif kind == "complaint":
        complaint = msg.get("complaint") or {}
        for r in _as_list(complaint.get("complainedRecipients")):
            out.append(_ev("complaint", r.get("emailAddress"), "", complaint.get("complaintFeedbackType", "")))
    return [e for e in out if e["address"]]


def sns_subscribe_url(payload):
    """SNS sends a `SubscriptionConfirmation` once; the owner has to open `SubscribeURL`. We never fetch it ourselves
    (server-side fetch of a URL taken from a request body = SSRF) - the view just returns it so the operator can confirm."""
    if isinstance(payload, dict) and payload.get("Type") == "SubscriptionConfirmation":
        url = payload.get("SubscribeURL")
        if isinstance(url, str) and url.startswith("https://") and ".amazonaws.com" in url.split("/")[2]:
            return url
    return None


# ---------------------------------------------------------------------------------------------------------------- SendGrid
def from_sendgrid(payload):
    """SendGrid Event Webhook = a JSON ARRAY of events. bounce (type 'blocked' = soft, else hard), dropped (dead address = hard),
    spamreport -> complaint. delivered / open / click / deferred ... ignored."""
    out = []
    for e in _as_list(payload):
        if not isinstance(e, dict):
            continue
        kind, addr = str(e.get("event", "")).lower(), e.get("email")
        if kind == "bounce":
            soft = str(e.get("type", "")).lower() == "blocked"
            out.append(_ev("bounce", addr, "soft" if soft else "hard", e.get("reason", "")))
        elif kind == "dropped":
            reason = str(e.get("reason", ""))
            if any(h in reason.lower() for h in _SENDGRID_DEAD_REASONS):
                out.append(_ev("bounce", addr, "hard", reason))
        elif kind == "spamreport":
            out.append(_ev("complaint", addr))
    return [e for e in out if e["address"]]


# ---------------------------------------------------------------------------------------------------------------- MSG91 (SMS)
def from_msg91(payload):
    """MSG91 posts delivery reports and inbound replies as one object or a list; field names vary by product, so the lookup is
    tolerant: number = mobile / mobiles / number / sender, text = text / message / keyword, status = status / report / description.
    Inbound STOP word -> stop; a failed report whose text says the number is unusable -> invalid. Plain 'failed' (network) is ignored."""
    out = []
    for e in _as_list(payload):
        if not isinstance(e, dict):
            continue
        number = e.get("mobile") or e.get("mobiles") or e.get("number") or e.get("sender") or e.get("from")
        text = e.get("text") or e.get("message") or e.get("keyword")
        if text and is_stop_text(text):
            out.append(_ev("stop", number, detail="sms reply"))
            continue
        status = " ".join(str(e.get(k, "")) for k in ("status", "report", "description", "reason")).lower()
        if "fail" in status or "reject" in status or "undeliver" in status:
            if any(h in status for h in _SMS_INVALID_HINTS):
                out.append(_ev("invalid", number, detail=status.strip()))
    return [e for e in out if e["address"]]


# ---------------------------------------------------------------------------------------------------------------- WhatsApp Cloud API
_META_UNDELIVERABLE_CODES = {131026}  # "Message undeliverable": number is not on WhatsApp / blocked the business


def from_meta(payload):
    """WhatsApp Cloud API: entry[].changes[].value.{messages[], statuses[]}. Inbound text STOP -> stop; a status 'failed' with
    code 131026 (undeliverable) -> invalid."""
    out = []
    if not isinstance(payload, dict):
        return out
    for entry in _as_list(payload.get("entry")):
        for change in _as_list((entry or {}).get("changes")):
            value = (change or {}).get("value") or {}
            for m in _as_list(value.get("messages")):
                body = ((m or {}).get("text") or {}).get("body") or ((m or {}).get("button") or {}).get("text")
                if is_stop_text(body):
                    out.append(_ev("stop", (m or {}).get("from"), detail="whatsapp reply"))
            for s in _as_list(value.get("statuses")):
                if str((s or {}).get("status", "")).lower() != "failed":
                    continue
                for err in _as_list((s or {}).get("errors")):
                    if (err or {}).get("code") in _META_UNDELIVERABLE_CODES:
                        out.append(_ev("invalid", (s or {}).get("recipient_id"), detail=str(err.get("title", ""))))
                        break
    return [e for e in out if e["address"]]


ADAPTERS = {
    "generic": from_generic,
    "ses": from_ses,
    "sendgrid": from_sendgrid,
    "msg91": from_msg91,
    "meta": from_meta,
}

# which channel(s) each format may feed - a SendGrid payload posted to /channel-webhooks/sms/ is a mistake, not an event.
FORMAT_CHANNELS = {
    "generic": ("email", "sms", "whatsapp"),
    "ses": ("email",),
    "sendgrid": ("email",),
    "msg91": ("sms",),
    "meta": ("whatsapp",),
}


def normalise(fmt, payload):
    """-> list of normalised events; never raises."""
    fn = ADAPTERS.get(fmt)
    if fn is None:
        return []
    try:
        return fn(payload)
    except Exception:  # noqa: BLE001 - a weird provider payload must not 500 the webhook
        return []
