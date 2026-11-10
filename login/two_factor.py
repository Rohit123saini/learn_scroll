"""S3 -- app-level two-step verification (TOTP authenticator app + one-time backup codes).

Built on ``django-otp`` (already installed: ``otp_totp`` + ``otp_static``), so NO new table / migration.
Two separately NAMED devices per user, so this never mixes with the Django-admin 2FA device staff may have:

    TOTPDevice   name = APP_TOTP_NAME     (confirmed=False while the user is still scanning the QR)
    StaticDevice name = APP_BACKUP_NAME   (10 single-use backup codes)

Login flow (login/security_views.py + views.py):
    password / Google / OTP step passes  ->  if ``is_enabled(user)``: NO tokens, answer
    ``{"two_factor_required": true, "challenge": "<signed, 5 min>"}``  ->  app asks for the 6-digit code (or a
    backup code)  ->  ``POST /login/auth/2fa/verify/`` -> tokens.

Brute force: django-otp's own per-device throttle (exponential delay) + our counter (``MAX_FAILURES`` wrong codes
per ``LOCK_SECONDS`` per user -> locked).
"""
from __future__ import annotations

import base64
import logging
import re
import secrets

from django.core import signing
from django.core.cache import cache

APP_TOTP_NAME = "LearnScroll app"
APP_BACKUP_NAME = "LearnScroll app backup"

CHALLENGE_SALT = "login.2fa.challenge.v1"
CHALLENGE_MAX_AGE = 300  # seconds the user has to type the code after the first step
BACKUP_CODE_COUNT = 10
MAX_FAILURES = 5
LOCK_SECONDS = 900

_DIGITS = re.compile(r"^\d{6}$")


# ---------------------------------------------------------------- devices
logger = logging.getLogger(__name__)

def _totp_model():
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice


def _static_models():
    from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

    return StaticDevice, StaticToken


def confirmed_totp(user):
    return _totp_model().objects.filter(user=user, name=APP_TOTP_NAME, confirmed=True).first()


def is_enabled(user) -> bool:
    return bool(getattr(user, "pk", None)) and confirmed_totp(user) is not None


def backup_codes_remaining(user) -> int:
    StaticDevice, StaticToken = _static_models()
    dev = StaticDevice.objects.filter(user=user, name=APP_BACKUP_NAME).first()
    return dev.token_set.count() if dev else 0


def start_setup(user) -> dict:
    """Create (or replace) the UNCONFIRMED device and return what the app shows: secret + otpauth URL.

    Not enabled until ``confirm_setup`` gets a correct code, so a half-finished setup can never lock anyone out."""
    TOTPDevice = _totp_model()
    TOTPDevice.objects.filter(user=user, name=APP_TOTP_NAME, confirmed=False).delete()
    device = TOTPDevice.objects.create(user=user, name=APP_TOTP_NAME, confirmed=False)
    return {
        "secret": base64.b32encode(device.bin_key).decode().rstrip("="),
        "otpauth_url": device.config_url,
    }


def _normalise_code(raw) -> str:
    return re.sub(r"[\s-]", "", str(raw or "")).lower()


def confirm_setup(user, code) -> list[str] | None:
    """Correct code -> device confirmed + fresh backup codes (returned ONCE). Wrong code -> None."""
    device = _totp_model().objects.filter(user=user, name=APP_TOTP_NAME, confirmed=False).first()
    if device is None:
        return None
    code = _normalise_code(code)
    if not _DIGITS.match(code) or not device.verify_token(code):
        return None
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    return regenerate_backup_codes(user)


def regenerate_backup_codes(user) -> list[str]:
    StaticDevice, StaticToken = _static_models()
    device, _ = StaticDevice.objects.get_or_create(user=user, name=APP_BACKUP_NAME, defaults={"confirmed": True})
    device.token_set.all().delete()
    codes = []
    while len(codes) < BACKUP_CODE_COUNT:
        token = StaticToken.random_token().lower()
        if token not in codes:
            codes.append(token)
    StaticToken.objects.bulk_create([StaticToken(device=device, token=c) for c in codes])
    return codes


def disable(user) -> None:
    _totp_model().objects.filter(user=user, name=APP_TOTP_NAME).delete()
    StaticDevice, _ = _static_models()
    StaticDevice.objects.filter(user=user, name=APP_BACKUP_NAME).delete()


# ---------------------------------------------------------------- code checking
def _fail_key(user) -> str:
    return f"2fa:fail:{user.pk}"


def seconds_locked(user) -> int:
    """>0 while the user is locked out of trying codes."""
    try:
        if int(cache.get(_fail_key(user), 0)) >= MAX_FAILURES:
            return LOCK_SECONDS
    except Exception:
        logger.exception("2FA lockout counter unreadable (cache down) - lockout not enforced")
    return 0


def _record_failure(user) -> int:
    key = _fail_key(user)
    try:
        cache.add(key, 0, LOCK_SECONDS)
        return int(cache.incr(key))
    except Exception:
        return 0


def _reset_failures(user) -> None:
    try:
        cache.delete(_fail_key(user))
    except Exception:
        logger.exception("2FA failure counter reset failed")


def check_code(user, raw_code) -> str | None:
    """Return ``"totp"`` / ``"backup"`` when the code is right, else ``None`` (and count the failure).

    A 6-digit code is tried against the authenticator device; anything else against the backup codes
    (each backup code works once and is deleted by django-otp when used)."""
    if seconds_locked(user):
        return None
    code = _normalise_code(raw_code)
    method = None
    if _DIGITS.match(code):
        device = confirmed_totp(user)
        if device is not None and device.verify_token(code):
            method = "totp"
    elif code:
        StaticDevice, _ = _static_models()
        dev = StaticDevice.objects.filter(user=user, name=APP_BACKUP_NAME).first()
        if dev is not None and dev.verify_token(code):
            method = "backup"
    if method:
        _reset_failures(user)
        return method
    _record_failure(user)
    return None


# ---------------------------------------------------------------- login challenge
def make_challenge(user, method: str) -> str:
    """Signed, short-lived proof that the FIRST step (password / google / otp) was passed. Not a session."""
    return signing.dumps(
        {"u": user.pk, "m": method, "n": secrets.token_hex(8)},
        salt=CHALLENGE_SALT,
        compress=False,
    )


def read_challenge(token) -> dict | None:
    """Payload or ``None`` (bad / expired / already used)."""
    try:
        data = signing.loads(str(token or ""), salt=CHALLENGE_SALT, max_age=CHALLENGE_MAX_AGE)
    except signing.BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict) or "u" not in data or "n" not in data:
        return None
    try:
        if cache.get(f"2fa:used:{data['n']}"):
            return None
    except Exception:
        logger.exception("2FA challenge replay check unreadable (cache down)")
    return data


def burn_challenge(data: dict) -> None:
    """One challenge = one successful login (no replay of a captured challenge + a reused code)."""
    try:
        cache.set(f"2fa:used:{data['n']}", 1, CHALLENGE_MAX_AGE + 60)
    except Exception:
        logger.exception("2FA challenge burn failed (cache down) - replay possible")
