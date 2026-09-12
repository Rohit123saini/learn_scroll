# login/sms_service.py
"""
Phone-OTP delivery via MSG91.

WHY MSG91 and not Twilio: the project already has an MSG91 account wired
up for liveclass notifications (see `liveclass/notifications.py`
`_send_sms` / `_send_whatsapp`, and `MSG91_AUTH_KEY` /
`MSG91_SMS_SENDER_ID` in settings.py). Standing up a second SMS vendor
(Twilio) just for login OTP would mean two vendor accounts, two sets of
credentials, and two billing relationships for the exact same job — this
reuses the existing MSG91 account instead.

WHY the OTP endpoint specifically (not the generic MSG91 "send SMS" /
flow API that `_send_sms` uses): MSG91's `POST /api/v5/otp` endpoint is
purpose-built for OTP delivery and — critically — accepts an `otp`
query param so *we* still generate and hash the OTP ourselves (see
`OTPVerification.set_otp` in models.py, using `secrets`, never MSG91's
own auto-generated code). MSG91 is used purely as the delivery channel;
the code, its hash, expiry and attempt-lock all continue to live in our
own DB exactly as they do for the email path. This also means
`VerifyOTPView` doesn't need any branching for phone vs email — both
paths verify the same way, against our own `otp_hash`.

Needs a DLT-registered OTP template on the MSG91 dashboard containing a
`##OTP##` variable (India's TRAI/DLT regulations require this for any
transactional SMS) — its ID goes in `MSG91_OTP_TEMPLATE_ID` (settings.py).
`MSG91_AUTH_KEY` is already shared with the liveclass notifications.

Fails LOUD, on purpose: `_send_sms` in liveclass is a best-effort
notification (it no-ops on missing config, since a missed "class
starting soon" ping isn't blocking). An OTP that silently fails to send
is a broken signup/login with no path forward for the user — so every
failure here raises `SMSDeliveryError`, and `SendOTPView` (views.py)
catches it and returns 503, same shape as the existing email failure
path.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MSG91_OTP_URL = "https://api.msg91.com/api/v5/otp"
REQUEST_TIMEOUT_SECONDS = 8


class SMSDeliveryError(Exception):
    """Raised whenever the OTP could not be handed off to MSG91 for
    delivery — missing config, network failure, or a non-success
    response from MSG91 itself. Callers should treat this the same as
    the existing `send_mail` failure path (503, don't leak internals)."""


def send_otp_sms(phone: str, otp_code: str) -> None:
    """Send `otp_code` to `phone` via MSG91's OTP API.

    `phone` is expected in the same international format already
    enforced by `login.models.phone_validator` (e.g. +919876543210).
    MSG91 wants the number without a leading '+', so that's stripped
    here rather than pushing this MSG91-specific quirk up into the view.

    Raises `SMSDeliveryError` on any failure. Returns None on success.
    """
    if not (settings.MSG91_AUTH_KEY and settings.MSG91_OTP_TEMPLATE_ID):
        # Fail loud (unlike liveclass's best-effort notifications) —
        # see module docstring. An unconfigured SMS provider must not
        # look like a successful send to the caller.
        logger.error(
            "Phone OTP requested but MSG91_AUTH_KEY / MSG91_OTP_TEMPLATE_ID "
            "is not configured."
        )
        raise SMSDeliveryError("SMS provider is not configured.")

    mobile = phone.lstrip("+")

    try:
        response = requests.post(
            MSG91_OTP_URL,
            params={
                "template_id": settings.MSG91_OTP_TEMPLATE_ID,
                "mobile": mobile,
                "otp": otp_code,
                "authkey": settings.MSG91_AUTH_KEY,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.exception("MSG91 OTP request failed for phone=%s", mobile)
        raise SMSDeliveryError("Could not reach SMS provider.") from None

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    # MSG91 returns HTTP 200 with `{"type": "success", ...}` on success
    # and `{"type": "error", "message": "..."}` on failure (bad
    # template id, unroutable number, insufficient balance, etc.) — a
    # 200 status code alone does NOT mean delivery was accepted.
    if response.status_code != 200 or payload.get("type") != "success":
        logger.error(
            "MSG91 OTP send failed for phone=%s status=%s body=%s",
            mobile,
            response.status_code,
            payload or response.text[:500],
        )
        raise SMSDeliveryError("SMS provider rejected the request.")