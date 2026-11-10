# login/otp_service.py
"""
Everything about SENDING an OTP/reset code, in one place (L1 gap 3).

SendOTPView and ForgotPasswordView used to carry two copies of "generate ->
hash -> store -> deliver", with no resend control at all: the 30 second resend
timer lived only in the Flutter app, and the only server limit was 5 requests a
minute per IP (not per number). Anyone calling the API directly could mail/SMS
one person unlimited codes, and every new code reset the 5-try counter.

Rules enforced here, server side, per target (email or phone):
    * RESEND COOLDOWN   one code per OTP_RESEND_COOLDOWN_SECONDS      (30s)
    * HOURLY CAP        at most OTP_MAX_SENDS_PER_HOUR codes / hour    (3)
    * IP CAP            at most OTP_IP_MAX_SENDS_PER_HOUR codes / hour (20)
                        across all numbers (SMS-pumping / bombing guard)
    * NO REPEAT         a new code is never equal to the previous one
    * A failed delivery (SMS/email provider down) is REFUNDED: it neither
      starts a cooldown nor eats one of the 3 hourly sends.
Because a fresh code resets the wrong-guess counter, the hourly cap also bounds
guessing to 3 x 5 = 15 tries per target per hour.

Counters live in the shared cache (Redis in production), same pattern as
login_guard.py / common/admin_site.py.
"""
import logging
import math
import secrets
import time

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from django.core.cache import cache

from .login_guard import _hash, incr_with_ttl
from .models import OTPVerification
from .sms_service import SMSDeliveryError, send_otp_sms

logger = logging.getLogger("security.otp")

PURPOSE_VERIFY = "verify"
PURPOSE_RESET = "reset"

_HOUR = 3600


def _cfg(name, default):
    return int(getattr(settings, name, default))


class OTPRateLimited(Exception):
    """Too many codes requested. ``code`` is machine-matchable, ``retry_after``
    is seconds until a new code may be requested."""

    def __init__(self, code, retry_after, message):
        super().__init__(message)
        self.code = code
        self.retry_after = int(retry_after)
        self.message = message


class OTPDeliveryError(Exception):
    """The code could not be handed to the email/SMS provider."""


class OTPInvalid(Exception):
    """A submitted code was wrong / expired / locked. ``message`` is user-facing.

    ``code`` is machine-matchable (otp_invalid / otp_expired / otp_locked /
    otp_not_found) and ``attempts_left`` (wrong-code failures only) lets the app
    show "3 attempts left" instead of a bare "Invalid OTP" (L1 gap 14).
    """

    def __init__(self, message, status_code=400, code="otp_invalid", attempts_left=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.attempts_left = attempts_left


class SendTicket:
    """Remembers which counters one send consumed, so a failed delivery can
    give them back."""

    def __init__(self, cooldown_key, hourly_key, ip_key):
        self.cooldown_key = cooldown_key
        self.hourly_key = hourly_key
        self.ip_key = ip_key


def _wait_text(seconds):
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minutes"


def reserve_send(target, ip):
    """Check every limit and, if allowed, count this send. Call BEFORE issuing.

    Raises OTPRateLimited. Returns a SendTicket for ``refund_send``.
    """
    now = time.time()
    target_h = _hash(target)
    cooldown = _cfg("OTP_RESEND_COOLDOWN_SECONDS", 30)
    max_hourly = _cfg("OTP_MAX_SENDS_PER_HOUR", 3)
    max_ip = _cfg("OTP_IP_MAX_SENDS_PER_HOUR", 20)

    cooldown_key = f"otp:cd:{target_h}"
    hourly_key = f"otp:hr:{target_h}"
    ip_key = f"otp:ip:{_hash(ip)}"

    # 1) cooldown -- cache.add is atomic: only the first caller gets True.
    if cooldown > 0 and not cache.add(cooldown_key, now, cooldown):
        last = cache.get(cooldown_key) or now
        wait = max(1, math.ceil(cooldown - (now - last)))
        raise OTPRateLimited(
            "otp_cooldown", wait,
            f"Please wait {_wait_text(wait)} before requesting another code.",
        )

    # 2) per-target hourly cap (fixed window starting at the first send)
    start_key = hourly_key + ":start"
    cache.add(start_key, now, _HOUR)
    sent = incr_with_ttl(hourly_key, _HOUR)
    if sent > max_hourly:
        start = cache.get(start_key) or now
        wait = max(1, math.ceil(start + _HOUR - now))
        cache.delete(cooldown_key)
        logger.warning("otp hourly cap hit: target=%s", target_h[:8])
        raise OTPRateLimited(
            "otp_hourly_limit", wait,
            f"Too many codes requested. Please try again in {_wait_text(wait)}.",
        )

    # 3) per-IP hourly cap across all targets
    ip_sent = incr_with_ttl(ip_key, _HOUR)
    if ip_sent > max_ip:
        refund_send(SendTicket(cooldown_key, hourly_key, None))
        logger.warning("otp ip cap hit: ip=%s", _hash(ip)[:8])
        raise OTPRateLimited(
            "otp_ip_limit", 15 * 60,
            "Too many codes requested from this network. Please try again later.",
        )

    return SendTicket(cooldown_key, hourly_key, ip_key)


def refund_send(ticket):
    """Give back the counters a send consumed (delivery failed)."""
    if ticket.cooldown_key:
        cache.delete(ticket.cooldown_key)
    for key in (ticket.hourly_key, ticket.ip_key):
        if not key:
            continue
        try:
            cache.decr(key)
        except ValueError:  # already expired
            pass


def issue_code(target):
    """Create + store (hashed) a fresh 6-digit code for ``target``; return it.

    Never equal to the previous code for this target. Re-issuing also clears
    ``is_verified`` so an old verified row can't carry over onto a new code.
    """
    existing = OTPVerification.objects.filter(target=target).first()
    code = None
    for _ in range(5):
        candidate = str(secrets.randbelow(900000) + 100000)
        if existing is None or not existing.check_otp(candidate):
            code = candidate
            break
    if code is None:  # 5 collisions in a row is ~impossible; fail safe anyway
        raise OTPDeliveryError("Could not generate a fresh code.")

    otp_obj, _ = OTPVerification.objects.update_or_create(
        target=target,
        defaults={"created_at": timezone.now(), "is_verified": False},
    )
    otp_obj.set_otp(code)
    otp_obj.save(update_fields=["otp_hash", "attempts"])
    return code


def deliver_code(target, is_email, code, purpose=PURPOSE_VERIFY):
    """Email or SMS the code. Raises OTPDeliveryError on any failure."""
    if is_email:
        if purpose == PURPOSE_RESET:
            subject = "Your password reset code"
            message = (
                f"Your password reset code is {code}. "
                f"It expires in {OTPVerification.EXPIRY_MINUTES} minutes. "
                "If you didn't request this, you can safely ignore this "
                "email \u2014 your password will not be changed."
            )
        else:
            subject = "Your verification code"
            message = (
                f"Your verification code is {code}. "
                f"It expires in {OTPVerification.EXPIRY_MINUTES} minutes. "
                "Do not share this code with anyone."
            )
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target],
                fail_silently=False,
            )
        except Exception:
            logger.exception("OTP email delivery failed")
            raise OTPDeliveryError("Email delivery failed.") from None
        return

    try:
        send_otp_sms(target, code)
    except SMSDeliveryError:
        raise OTPDeliveryError("SMS delivery failed.") from None


def wrong_code_message(noun, attempts_left):
    """\"Invalid OTP. 3 attempts left.\" -- one wording for every OTP check."""
    if attempts_left > 0:
        word = "attempt" if attempts_left == 1 else "attempts"
        return f"Invalid {noun}. {attempts_left} {word} left."
    return f"Invalid {noun}. No attempts left \u2014 please request a new code."


def check_code(target, raw_code):
    """Spend a code for ``target`` in one step (used where there is no separate
    "verify" call, e.g. adding a phone to an already signed-in account).

    Same rules as VerifyOTPView: expiry, 5-try lock, hashed compare. On success
    the row is deleted so the code can't be replayed. Raises ``OTPInvalid``.
    """
    otp_obj = OTPVerification.objects.filter(target=target).first()
    if otp_obj is None:
        raise OTPInvalid("No OTP request found for this identifier.", code="otp_not_found")
    if otp_obj.is_expired():
        otp_obj.delete()
        raise OTPInvalid("OTP has expired.", code="otp_expired")
    if otp_obj.is_locked():
        otp_obj.delete()
        raise OTPInvalid(
            "Too many incorrect attempts. Please request a new OTP.", 429, code="otp_locked", attempts_left=0
        )
    if not otp_obj.check_otp(raw_code):
        otp_obj.register_failed_attempt()
        left = otp_obj.attempts_left()
        raise OTPInvalid(wrong_code_message("OTP", left), code="otp_invalid", attempts_left=left)
    otp_obj.delete()
