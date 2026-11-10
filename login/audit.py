# login/audit.py
"""
L1 gap 19 -- one audit trail for login / OTP / password-reset / session events.

Before this, the only record of "who tried to get into which account" was a
handful of ``logger.warning`` lines (login lockout, OTP hourly cap, admin login):
they scroll away in the console, hold no user / device / IP in a searchable form,
and nothing at all was written for a successful login, a password reset or a
logout. Support could not answer "was this account attacked?" or "which device
reset the password?".

``record()`` writes one ``AuthAuditLog`` row (login/models.py). Rules:

    * NEVER RAISES. A failed audit write is logged and swallowed -- auditing must
      not be able to break a login. The insert runs in its own savepoint so a DB
      error cannot poison the caller's transaction either.
    * NO SECRETS. Passwords, OTP codes, tokens and captcha answers are never
      passed in, and any ``**meta`` key that smells like one is dropped as a
      second line of defence.
    * MINIMAL PII. The identifier the user typed is stored MASKED ("r***@gmail.com",
      "+91********10") plus a keyed hash (HMAC-SHA256 with SECRET_KEY) so support can
      still look up "everything for this email" without the table holding it.
    * NOISY EVENTS ARE DEDUPED. "Blocked" events (captcha asked, OTP rate-limited,
      login locked, refresh refused) can be triggered by anyone, so an attacker
      could otherwise fill the table. The same event from the same IP + identifier
      is written once per AUTH_AUDIT_NOISE_DEDUPE_SECONDS (default 60).

Settings (all optional): AUTH_AUDIT_ENABLED (True), AUTH_AUDIT_NOISE_DEDUPE_SECONDS (60),
AUTH_AUDIT_RETENTION_DAYS (180, purged nightly by login.tasks.purge_auth_audit_logs).
"""
import hashlib
import hmac
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .device_info import extract_device_info
from .models import AuthAuditLog

logger = logging.getLogger("security.audit")

# Outcomes ------------------------------------------------------------------
SUCCESS = AuthAuditLog.OUTCOME_SUCCESS
FAILURE = AuthAuditLog.OUTCOME_FAILURE  # tried and failed (wrong password / code ...)
BLOCKED = AuthAuditLog.OUTCOME_BLOCKED  # refused by a guard (lockout, rate limit, captcha ...)


class Event:
    """Event names stored in ``AuthAuditLog.event`` (plain strings, max 40 chars)."""

    # password login
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGIN_LOCKED = "login_locked"
    # sign-up / Google
    SIGNUP = "signup"
    GOOGLE_LOGIN = "google_login"
    GOOGLE_SIGNUP = "google_signup"
    GOOGLE_FAILED = "google_failed"
    # OTP
    OTP_SENT = "otp_sent"
    OTP_SEND_BLOCKED = "otp_send_blocked"
    OTP_SEND_FAILED = "otp_send_failed"
    OTP_VERIFIED = "otp_verified"  # signup branch: number/email proven, no account yet
    OTP_LOGIN = "otp_login"  # OTP matched an account -> session issued
    OTP_FAILED = "otp_failed"  # wrong / expired / locked / missing code
    PHONE_VERIFIED = "phone_verified"
    # captcha
    CAPTCHA_REQUIRED = "captcha_required"
    CAPTCHA_FAILED = "captcha_failed"
    # password reset / change
    RESET_REQUESTED = "reset_requested"
    RESET_SUCCESS = "reset_success"
    RESET_FAILED = "reset_failed"
    PASSWORD_CHANGE_FAILED = "password_change_failed"  # S3 -- wrong CURRENT password while changing it
    PASSWORD_CHANGED = "password_changed"
    # sessions
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    SESSION_REVOKED = "session_revoked"
    TOKEN_REFRESH_DENIED = "token_refresh_denied"
    # S3 -- security centre
    TWO_FACTOR_SETUP_STARTED = "2fa_setup_started"
    TWO_FACTOR_ENABLED = "2fa_enabled"
    TWO_FACTOR_DISABLED = "2fa_disabled"
    TWO_FACTOR_CHALLENGE = "2fa_challenge"  # password / google / otp step passed, 2nd step still pending
    TWO_FACTOR_FAILED = "2fa_failed"
    TWO_FACTOR_BACKUP_USED = "2fa_backup_used"
    BACKUP_CODES_REGENERATED = "2fa_backup_regenerated"
    NEW_DEVICE_LOGIN = "new_device_login"
    LOGIN_ALERTS_CHANGED = "login_alerts_changed"
    # account
    ACCOUNT_DELETED = "account_deleted"
    ACCOUNT_DELETE_FAILED = "account_delete_failed"
    # S5 -- data rights (DPDP)
    ACCOUNT_DELETION_SCHEDULED = "account_delete_sched"
    ACCOUNT_DELETION_CANCELLED = "account_delete_cancel"
    DATA_EXPORT_REQUESTED = "data_export_requested"
    DATA_EXPORT_DOWNLOADED = "data_export_downloaded"
    CONSENT_CHANGED = "consent_changed"
    # S10 -- connected accounts
    GOOGLE_LINKED = "google_linked"
    GOOGLE_UNLINKED = "google_unlinked"
    CONTACT_CHANGED = "contact_changed"  # e-mail / phone added or replaced after an OTP to the NEW address
    CONTACT_REMOVED = "contact_removed"
    CONNECTION_REAUTH_FAILED = "connection_reauth_fail"
    QR_LOGIN_APPROVED = "qr_login_approved"
    QR_LOGIN_DENIED = "qr_login_denied"
    QR_LOGIN = "qr_login"  # the computer collected its session
    LINKED_APP_REVOKED = "linked_app_revoked"
    # X6 -- security hardening: every sensitive change leaves a row (field NAMES only, never the values)
    USERNAME_CHANGED = "username_changed"
    DOB_CHANGED = "dob_changed"
    ACCOUNT_PRIVACY_CHANGED = "account_privacy_changed"  # private account switched on / off
    PRIVACY_CONTROLS_CHANGED = "privacy_controls_changed"  # S4 who-can-message / call / tag ... controls
    WALLET_SETTINGS_CHANGED = "wallet_settings_changed"  # S9 spend limits / confirm toggle
    WITHDRAWAL_REQUESTED = "withdrawal_requested"
    WITHDRAWAL_BLOCKED = "withdrawal_blocked"  # fraud / eligibility refusal
    REAUTH_LOCKED = "reauth_locked"  # too many wrong CURRENT passwords while changing a setting
    RATE_LIMITED = "rate_limited"  # a sensitive settings endpoint refused a request (429)
    # Django admin
    ADMIN_LOGIN = "admin_login"
    ADMIN_LOGIN_FAILED = "admin_login_failed"
    ADMIN_LOGIN_LOCKED = "admin_login_locked"
    ADMIN_LOGIN_WEAK_PASSWORD = "admin_login_weak_password"


# Anyone can trigger these (no valid credentials needed) -> written at most once per window.
NOISY_EVENTS = frozenset(
    {
        Event.LOGIN_LOCKED,
        Event.OTP_SEND_BLOCKED,
        Event.CAPTCHA_REQUIRED,
        Event.CAPTCHA_FAILED,
        Event.TOKEN_REFRESH_DENIED,
        Event.TWO_FACTOR_FAILED,
        Event.ADMIN_LOGIN_LOCKED,
        Event.RATE_LIMITED,  # X6 -- anyone hammering a settings endpoint can trigger it
        Event.REAUTH_LOCKED,
    }
)

KIND_EMAIL, KIND_PHONE, KIND_USERNAME = "email", "phone", "username"

_PHONE_NOISE = re.compile(r"[\s\-()]")
_PHONE_LIKE = re.compile(r"^\+?\d{8,15}$")
# meta keys containing any of these words are dropped, whatever their value
_SENSITIVE_WORDS = ("password", "passwd", "otp", "token", "secret", "captcha_answer")


# ---------------------------------------------------------------------------
# identifier helpers
# ---------------------------------------------------------------------------
def kind_of(text):
    """'email' | 'phone' | 'username' for something a user typed."""
    text = (text or "").strip()
    if "@" in text:
        return KIND_EMAIL
    if _PHONE_LIKE.match(_PHONE_NOISE.sub("", text)):
        return KIND_PHONE
    return KIND_USERNAME


def mask_identifier(text, kind=None):
    """Mask an email / phone / username for storage.

    r***@gmail.com   +91********10   ab***   (never the full value)
    """
    text = (text or "").strip()
    if not text:
        return ""
    kind = kind or kind_of(text)
    if kind == KIND_EMAIL and "@" in text:
        local, _, domain = text.lower().rpartition("@")
        return f"{local[:1]}***@{domain}"[:120]
    if kind == KIND_PHONE:
        digits = _PHONE_NOISE.sub("", text)
        if len(digits) <= 5:
            return "*" * len(digits)
        return f"{digits[:3]}{'*' * (len(digits) - 5)}{digits[-2:]}"[:120]
    return f"{text[:2]}***"


def _normalise_for_hash(text):
    text = (text or "").strip().lower()
    if "@" not in text:
        text = _PHONE_NOISE.sub("", text)
    return text


def identifier_hash(text):
    """Keyed hash of an identifier (hex, 64 chars). Same input -> same output, so support
    can search the log by a full email / phone; without SECRET_KEY it can't be reversed
    by guessing phone numbers. Rotating SECRET_KEY just stops old rows matching."""
    value = _normalise_for_hash(text)
    if not value:
        return ""
    key = str(settings.SECRET_KEY).encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _user_id(user):
    if user is None:
        return None
    pk = getattr(user, "pk", user)  # a User, an AnonymousUser (pk None) or a bare id
    try:
        return int(pk) if pk is not None else None
    except (TypeError, ValueError):
        return None


def _clean_meta(meta):
    out = {}
    for key, value in meta.items():
        if value is None:
            continue
        name = str(key)[:40]
        lowered = name.lower()
        if any(word in lowered for word in _SENSITIVE_WORDS):
            continue
        if isinstance(value, (bool, int, float)):
            out[name] = value
        elif isinstance(value, str):
            out[name] = value[:200]
    return out


def _request_fields(request):
    if request is None:
        return {"ip_address": None, "user_agent": "", "device_id": "", "platform": ""}
    info = extract_device_info(request)  # never raises
    return {
        "ip_address": info.get("ip_address"),
        "user_agent": info.get("user_agent", ""),
        "device_id": info.get("device_id", ""),
        "platform": info.get("platform", ""),
    }


def _is_duplicate_noise(event, fields):
    """True when this noisy event was already written inside the dedupe window."""
    if event not in NOISY_EVENTS:
        return False
    window = int(getattr(settings, "AUTH_AUDIT_NOISE_DEDUPE_SECONDS", 60))
    if window <= 0:
        return False
    who = fields["identifier_hash"][:16] or str(fields["user_id"] or "")
    key = f"authaudit:dd:{event}:{fields['ip_address'] or '-'}:{who}"
    try:
        return not cache.add(key, 1, window)  # add() is atomic: only the first caller gets True
    except Exception:  # cache down -> better a duplicate row than a lost one
        return False


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def record(
    event,
    request=None,
    *,
    outcome=SUCCESS,
    user=None,
    identifier=None,
    kind=None,
    reason="",
    **meta,
):
    """Write one audit row. Returns it, or ``None`` (disabled / deduped / write failed).

    event       an ``Event.*`` name
    request     the DRF / Django request: IP, User-Agent and X-Device-* headers are read from it
    outcome     SUCCESS | FAILURE | BLOCKED
    user        the account involved (User or id), if known
    identifier  what the user typed / what a code was sent to (stored masked + hashed)
    reason      stable machine code (``invalid_credentials``, ``otp_locked`` ...)
    **meta      small extras (attempts_left=2, retry_after=30 ...). Only str/int/float/bool kept.
    """
    if not getattr(settings, "AUTH_AUDIT_ENABLED", True):
        return None
    try:
        ident = (identifier or "").strip()
        if kind not in (KIND_EMAIL, KIND_PHONE, KIND_USERNAME):
            kind = kind_of(ident) if ident else ""
        fields = {
            "event": str(event)[:40],
            "outcome": outcome,
            "user_id": _user_id(user),
            "identifier_kind": kind or "",
            "identifier_masked": mask_identifier(ident, kind) if ident else "",
            "identifier_hash": identifier_hash(ident) if ident else "",
            "reason": str(reason or "")[:64],
            "metadata": _clean_meta(meta),
        }
        fields.update(_request_fields(request))

        if _is_duplicate_noise(event, fields):
            return None

        with transaction.atomic():  # own savepoint: a failed insert must not break the caller's transaction
            row = AuthAuditLog.objects.create(**fields)
    except Exception:
        logger.exception("auth audit write failed: event=%s", event)
        return None

    log = logger.debug if outcome == SUCCESS else logger.info
    log(
        "auth_audit event=%s outcome=%s user_id=%s reason=%s ip=%s",
        row.event, row.outcome, row.user_id, row.reason, row.ip_address,
    )
    return row
