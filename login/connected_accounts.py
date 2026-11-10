# login/connected_accounts.py
"""S10 -- Connected accounts & devices (all the rules in one place; views are in connected_views.py).

What lives here
---------------
1. GOOGLE LINK / UNLINK     ``LinkedIdentity`` (one row per user). Sign-in with Google now looks the account up by the
                            Google ``sub`` first and by e-mail second (old behaviour). Unlinking keeps the row with
                            ``revoked_at`` set, so the e-mail fallback can not silently re-attach Google afterwards.
2. RE-AUTH                  Linking / unlinking Google and changing e-mail / phone are the doors an account thief uses,
                            so a live session alone is not enough: password (if the account has one) + 2FA code (if on);
                            an account with neither proves itself with a code sent to its CURRENT e-mail / phone.
3. E-MAIL / PHONE CHANGE    Code goes to the NEW address; the old e-mail is told afterwards. Phone can also be removed
                            (phone is optional) but only while another way to sign in is left.
4. QR LOGIN                 Computer asks for a QR, phone scans + approves (and types the 4-digit match code shown on the
                            computer), computer collects a normal session. Sessions made this way are the "Linked apps".

Nothing here raises HTTP errors; it raises ``ConnectionError_`` (code, message, status) and the views turn that into
the usual login error envelope.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from . import google_verify, two_factor
from .device_info import extract_device_info
from .disposable import new_identifier_problem
from .identifiers import KIND_EMAIL, KIND_PHONE, normalize_identifier
from .models import AuthToken, LinkedIdentity, QRLoginRequest, User
from .token_issuance import issue_tokens_for_user

logger = logging.getLogger(__name__)

PROVIDER_GOOGLE = LinkedIdentity.PROVIDER_GOOGLE

# re-auth requirement names (also sent to the app so it knows which fields to show)
REAUTH_PASSWORD = "password"
REAUTH_TWO_FACTOR = "two_factor"
REAUTH_OTP = "otp"


class ConnectionError_(Exception):
    """A refusal with a machine code the app translates. (Trailing underscore: not the builtin ConnectionError.)"""

    def __init__(self, code: str, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra


def _cfg(name, default):
    return getattr(settings, name, default)


# ====================================================================================== sign-in methods
def active_google_identity(user) -> LinkedIdentity | None:
    return LinkedIdentity.objects.filter(user=user, provider=PROVIDER_GOOGLE, revoked_at__isnull=True).first()


def sign_in_methods(user) -> dict:
    """Ways this account can be signed into, ignoring Google. (E-mail and phone count because a one-time code to them
    signs the owner in -- see VerifyOTPView.)"""
    return {
        "password": bool(user.has_usable_password()),
        "email_code": bool(user.email),
        "phone_code": bool(user.phone),
    }


def has_other_way_in(user, *, without_phone: bool = False, without_google: bool = False) -> bool:
    m = sign_in_methods(user)
    if without_phone:
        m["phone_code"] = False
    ways = [m["password"], m["email_code"], m["phone_code"]]
    if not without_google:
        ways.append(active_google_identity(user) is not None)
    return any(ways)


# ====================================================================================== re-auth
def reauth_requirements(user) -> list[str]:
    """What the caller must send with a sensitive change. Order = how the app shows the fields."""
    need: list[str] = []
    if user.has_usable_password():
        need.append(REAUTH_PASSWORD)
    if two_factor.is_enabled(user):
        need.append(REAUTH_TWO_FACTOR)
    if not need and (user.email or user.phone):
        need.append(REAUTH_OTP)
    return need


def reauth_otp_target(user) -> tuple[str, bool] | None:
    """Where the re-auth code goes: the account's own e-mail, else its phone. ``(target, is_email)``."""
    if user.email:
        return user.email, True
    if user.phone:
        return user.phone, False
    return None


_REAUTH_SALT = "s10-connections-reauth"


def reauth_ttl() -> int:
    return int(_cfg("CONNECTIONS_REAUTH_TTL_SECONDS", 600))


def make_reauth_token(user, session_jti) -> str:
    """Short-lived proof "this person just re-entered their password / code", bound to this user AND this session, so
    the app asks once and then does link / unlink / change without asking again for a few minutes."""
    return signing.dumps({"u": user.pk, "j": session_jti or ""}, salt=_REAUTH_SALT)


def reauth_token_ok(user, session_jti, token) -> bool:
    if not isinstance(token, str) or not token:
        return False
    try:
        data = signing.loads(token, salt=_REAUTH_SALT, max_age=reauth_ttl())
    except signing.BadSignature:
        return False
    return data.get("u") == user.pk and data.get("j") == (session_jti or "")


def check_reauth(user, body: dict, session_jti=None) -> None:
    """Raise ``ConnectionError_`` unless ``body`` carries a valid ``reauth_token`` or meets every requirement from
    :func:`reauth_requirements` directly (``password``, ``two_factor_code``, ``reauth_code``)."""
    from .otp_service import OTPInvalid, check_code  # local: otp_service imports models/sms

    if reauth_token_ok(user, session_jti, body.get("reauth_token")):
        return
    for need in reauth_requirements(user):
        if need == REAUTH_PASSWORD:
            # X6 -- same wrong-current-password lock as change-password: an unlocked stolen phone must not be able
            # to guess the password at throttle speed just because the target action is "change e-mail" instead.
            from .sensitive import ReauthGuard  # local: sensitive imports audit -> models

            guard = ReauthGuard(user, "connections")
            wait = guard.retry_after()
            if wait:
                raise ConnectionError_(
                    "too_many_attempts", "Too many wrong passwords. Try again later.", 429, retry_after=wait
                )
            pw = body.get("password")
            if not isinstance(pw, str) or not pw or not user.check_password(pw):
                locked_for, _left = guard.record_failure()
                if locked_for:
                    raise ConnectionError_(
                        "too_many_attempts", "Too many wrong passwords. Try again later.", 429,
                        retry_after=locked_for,
                    )
                raise ConnectionError_("invalid_password", "Current password is wrong.", 400, reauth="password")
            guard.clear()
        elif need == REAUTH_TWO_FACTOR:
            seconds = two_factor.seconds_locked(user)
            if seconds:
                raise ConnectionError_(
                    "two_factor_locked", "Too many wrong codes. Try again later.", 429, retry_after=seconds
                )
            code = body.get("two_factor_code")
            if not isinstance(code, str) or not code.strip() or two_factor.check_code(user, code) is None:
                raise ConnectionError_(
                    "two_factor_invalid", "That two-step code is wrong.", 400, reauth="two_factor"
                )
        elif need == REAUTH_OTP:
            target = reauth_otp_target(user)
            if target is None:
                continue
            code = body.get("reauth_code")
            if not isinstance(code, str) or not code.strip():
                raise ConnectionError_("reauth_code_required", "Enter the code we sent you.", 400, reauth="otp")
            try:
                check_code(target[0], code.strip())
            except OTPInvalid as exc:
                raise ConnectionError_(
                    exc.code, exc.message, exc.status_code, attempts_left=exc.attempts_left, reauth="otp"
                ) from None


# ====================================================================================== Google
def verify_google_id_token(raw) -> dict:
    """Verified Google profile (``sub``, lower-cased ``email``, ``name``) or ``ConnectionError_``."""
    # T1 - same shared helper as GoogleAuthView (timeout, certs cache, multi client-id, 503 google_unreachable).
    try:
        idinfo = google_verify.verify_google_token(raw)
    except google_verify.GoogleVerifyError as exc:
        raise ConnectionError_(exc.code, exc.message, exc.status) from None
    sub = str(idinfo.get("sub") or "").strip()
    email = str(idinfo.get("email") or "").strip().lower()
    if not sub:
        raise ConnectionError_("google_token_invalid", "Invalid Google token", 401)
    if not email:
        raise ConnectionError_("google_no_email", "Google account has no email", 400)
    if not idinfo.get("email_verified", False):
        raise ConnectionError_("google_email_unverified", "This Google account's email is not verified.", 400)
    return {"sub": sub, "email": email, "name": str(idinfo.get("name") or "")[:120]}


def link_google(user, profile: dict) -> LinkedIdentity:
    sub, email = profile["sub"], profile["email"]

    taken = LinkedIdentity.objects.filter(provider=PROVIDER_GOOGLE, subject=sub).exclude(user=user).first()
    if taken is not None and taken.is_active:
        raise ConnectionError_(
            "google_already_linked", "This Google account is already linked to another LearnScroll account.", 409
        )
    if taken is not None:  # the other account unlinked it earlier: the `sub` is free to move
        taken.delete()
    if User.objects.filter(email=email).exclude(pk=user.pk).exists():
        raise ConnectionError_(
            "google_email_other_account",
            "This Google account's e-mail belongs to a different LearnScroll account.",
            409,
        )

    mine = LinkedIdentity.objects.filter(user=user, provider=PROVIDER_GOOGLE).first()
    if mine is not None and mine.is_active:
        if mine.subject == sub:
            return mine  # idempotent
        raise ConnectionError_(
            "google_other_linked", "A different Google account is already linked. Unlink it first.", 409
        )
    now = timezone.now()
    try:
        with transaction.atomic():
            if mine is None:
                return LinkedIdentity.objects.create(
                    user=user, provider=PROVIDER_GOOGLE, subject=sub, email=email,
                    display_name=profile.get("name", ""), linked_at=now,
                )
            mine.subject, mine.email, mine.display_name = sub, email, profile.get("name", "")
            mine.linked_at, mine.revoked_at, mine.last_used_at = now, None, None
            mine.save()
            return mine
    except IntegrityError:
        raise ConnectionError_(
            "google_already_linked", "This Google account is already linked to another LearnScroll account.", 409
        ) from None


def unlink_google(user) -> LinkedIdentity:
    identity = active_google_identity(user)
    if identity is None:
        raise ConnectionError_("google_not_linked", "No Google account is linked.", 404)
    if not has_other_way_in(user, without_google=True):
        raise ConnectionError_(
            "last_sign_in_method",
            "Google is your only way to sign in. Add a password, e-mail or phone number first.",
            409,
        )
    identity.revoked_at = timezone.now()
    identity.save(update_fields=["revoked_at"])
    return identity


def google_login_blocked(user, sub: str | None) -> bool:
    """True when the user unlinked Google: a Google login that only matches by e-mail must not get in again."""
    if not sub:
        return False
    row = LinkedIdentity.objects.filter(user=user, provider=PROVIDER_GOOGLE).first()
    return bool(row is not None and not row.is_active)


def record_google_login(user, idinfo: dict) -> None:
    """Called by GoogleAuthView after a Google login/sign-up. Creates the link row for accounts that predate S10 and
    new Google sign-ups, and stamps ``last_used_at``. Never raises -- a login must not depend on this."""
    try:
        sub = str(idinfo.get("sub") or "").strip()
        if not sub:
            return
        email = str(idinfo.get("email") or "").strip().lower()
        row = LinkedIdentity.objects.filter(user=user, provider=PROVIDER_GOOGLE).first()
        if row is None:
            with transaction.atomic():
                LinkedIdentity.objects.create(
                    user=user, provider=PROVIDER_GOOGLE, subject=sub, email=email,
                    display_name=str(idinfo.get("name") or "")[:120], last_used_at=timezone.now(),
                )
        elif row.is_active and row.subject == sub:
            row.last_used_at = timezone.now()
            row.save(update_fields=["last_used_at"])
    except IntegrityError:
        pass  # another account holds this sub -- the e-mail login that just happened still stands
    except Exception:  # noqa: BLE001
        logger.exception("record_google_login failed for user %s", getattr(user, "pk", None))


# ====================================================================================== e-mail / phone
def validate_new_contact(user, kind: str, raw) -> str:
    """Normalised new e-mail / phone, or ``ConnectionError_``. Does not touch the DB beyond reads."""
    if kind not in (KIND_EMAIL, KIND_PHONE):
        raise ConnectionError_("invalid_kind", "kind must be 'email' or 'phone'.", 400)
    value, found = normalize_identifier(raw)
    if found != kind:
        code = "email_invalid_format" if kind == KIND_EMAIL else "phone_invalid_format"
        msg = (
            "Enter a valid e-mail address."
            if kind == KIND_EMAIL
            else "Enter a valid phone number in international format, e.g. +919876543210."
        )
        raise ConnectionError_(code, msg, 400)

    if kind == KIND_EMAIL:
        if user.email and user.email.lower() == value:
            raise ConnectionError_("same_as_current", "That is already your e-mail.", 400)
        if User.objects.filter(email=value).exclude(pk=user.pk).exists():
            raise ConnectionError_("email_exists", "This e-mail is already registered.", 409)
    else:
        if user.phone and user.phone == value and user.phone_verified:
            raise ConnectionError_("same_as_current", "That is already your verified phone number.", 400)
        if User.objects.filter(phone=value).exclude(pk=user.pk).exists():
            raise ConnectionError_("phone_exists", "Phone number already exists.", 409)

    problem = new_identifier_problem(value, kind)
    if problem:
        raise ConnectionError_(problem[0], problem[1], 400)
    return value


def _mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _mark_email_verified(user, value: str) -> None:
    """The address was just proven by a code -> also tell the notification-channel layer (it keeps its own flag)."""
    try:
        from core.models import NotificationPreference

        pref = NotificationPreference.for_user(user)
        pref.email_verified_address = value
        pref.email_verified_at = timezone.now()
        pref.save(update_fields=["email_verified_address", "email_verified_at", "updated_at"])
    except Exception:  # noqa: BLE001
        logger.exception("could not mark e-mail verified for user %s", user.pk)


def apply_contact_change(user, kind: str, value: str) -> dict:
    """Store the already-verified new e-mail / phone. Returns ``{"old": <previous value or None>}``.

    SMS / WhatsApp consent is bound to the phone number it was given for (core.channel_policy.has_valid_consent), so a
    new number starts without consent -- that is intended."""
    try:
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if kind == KIND_EMAIL:
                old = locked.email
                locked.email = value
                locked.save(update_fields=["email"])
            else:
                old = locked.phone
                locked.phone = value
                locked.phone_verified = True
                locked.save(update_fields=["phone", "phone_verified"])
    except IntegrityError:
        code = "email_exists" if kind == KIND_EMAIL else "phone_exists"
        msg = "This e-mail is already registered." if kind == KIND_EMAIL else "Phone number already exists."
        raise ConnectionError_(code, msg, 409) from None
    user.refresh_from_db(fields=["email", "phone", "phone_verified"])
    if kind == KIND_EMAIL:
        _mark_email_verified(user, value)
        if old and old != value:
            _tell_old_email(old, value)
    return {"old": old}


def _tell_old_email(old: str, new: str) -> None:
    try:
        send_mail(
            subject="Your LearnScroll e-mail was changed",
            message=(
                f"The e-mail on your LearnScroll account was changed to {_mask_email(new)}.\n\n"
                "If this was you, nothing more to do. If it was not, reset your password straight away and "
                "sign out of all devices from Settings > Security."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[old],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not notify old e-mail")


def remove_phone(user) -> None:
    if not user.phone:
        raise ConnectionError_("phone_not_set", "No phone number is saved.", 404)
    if not has_other_way_in(user, without_phone=True):
        raise ConnectionError_(
            "last_sign_in_method",
            "This number is your only way to sign in. Add a password or e-mail first.",
            409,
        )
    user.phone = None
    user.phone_verified = False
    user.save(update_fields=["phone", "phone_verified"])


# ====================================================================================== QR login
def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def qr_ttl() -> int:
    return int(_cfg("QR_LOGIN_TTL_SECONDS", 120))


def qr_enabled() -> bool:
    return bool(_cfg("QR_LOGIN_ENABLED", True))


def qr_payload(code: str) -> str:
    base = str(_cfg("PUBLIC_WEB_BASE_URL", "https://learnscroll.app")).rstrip("/")
    return f"{base}/qr-login?c={code}"


def create_qr_request(request) -> dict:
    """Computer side, step 1. The ``secret`` is returned ONCE and never stored in clear."""
    if not qr_enabled():
        raise ConnectionError_("qr_disabled", "QR sign-in is turned off.", 403)
    info = extract_device_info(request)
    code = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    match = f"{secrets.randbelow(10000):04d}"
    row = QRLoginRequest.objects.create(
        code_hash=_h(code),
        secret_hash=_h(secret),
        match_code=match,
        expires_at=timezone.now() + timedelta(seconds=qr_ttl()),
        requester_ip=info.get("ip_address"),
        requester_device_name=(info.get("device_name") or "")[:120],
        requester_platform=(info.get("platform") or "")[:20],
        requester_user_agent=(info.get("user_agent") or "")[:255],
        requester_device_id=(info.get("device_id") or "")[:64],
    )
    return {
        "code": code,
        "secret": secret,
        "match_code": match,
        "qr_payload": qr_payload(code),
        "expires_in": qr_ttl(),
        "poll_interval": int(_cfg("QR_LOGIN_POLL_SECONDS", 2)),
        "id": row.pk,
    }


def _find_by_code(code) -> QRLoginRequest | None:
    if not isinstance(code, str) or not (8 <= len(code) <= 64):
        return None
    return QRLoginRequest.objects.filter(code_hash=_h(code)).first()


def _ip_hint(ip) -> str:
    """203.0.113.x -- enough for "is that my network?" without showing the whole address to the scanner."""
    if not ip:
        return ""
    if ":" in ip:
        return ip.split(":")[0] + ":…"
    parts = ip.split(".")
    return ".".join(parts[:3] + ["x"]) if len(parts) == 4 else ""


def describe_qr_request(row: QRLoginRequest) -> dict:
    """What the phone shows before the user approves. NEVER includes ``match_code``."""
    left = max(0, int((row.expires_at - timezone.now()).total_seconds()))
    return {
        "device_name": row.requester_device_name or "Unknown device",
        "platform": row.requester_platform or "other",
        "ip_hint": _ip_hint(row.requester_ip),
        "created_at": row.created_at,
        "seconds_left": left,
    }


def lookup_qr_request(code) -> QRLoginRequest:
    row = _find_by_code(code)
    if row is None:
        raise ConnectionError_("qr_not_found", "This QR code is not valid.", 404)
    if row.status != QRLoginRequest.STATUS_PENDING:
        raise ConnectionError_("qr_used", "This QR code was already used.", 409)
    if row.is_expired():
        raise ConnectionError_("qr_expired", "This QR code expired. Show a new one on the computer.", 410)
    return row


def answer_qr_request(user, code, *, approve: bool, match_code: str = "") -> QRLoginRequest:
    """Phone side. Approve needs the 4 digits shown on the computer; 3 wrong tries kill the request."""
    row = lookup_qr_request(code)
    if not approve:
        updated = QRLoginRequest.objects.filter(pk=row.pk, status=QRLoginRequest.STATUS_PENDING).update(
            status=QRLoginRequest.STATUS_DENIED, user=user
        )
        if not updated:
            raise ConnectionError_("qr_used", "This QR code was already used.", 409)
        row.status = QRLoginRequest.STATUS_DENIED
        return row

    if not hmac.compare_digest(str(match_code or "").strip(), row.match_code):
        QRLoginRequest.objects.filter(pk=row.pk).update(wrong_match_attempts=row.wrong_match_attempts + 1)
        left = max(0, 3 - (row.wrong_match_attempts + 1))
        if left == 0:
            QRLoginRequest.objects.filter(pk=row.pk, status=QRLoginRequest.STATUS_PENDING).update(
                status=QRLoginRequest.STATUS_DENIED
            )
            raise ConnectionError_(
                "qr_match_locked", "Too many wrong codes. Show a new QR code on the computer.", 429, attempts_left=0
            )
        raise ConnectionError_(
            "qr_match_invalid", "That code does not match the one on the computer.", 400, attempts_left=left
        )

    updated = QRLoginRequest.objects.filter(pk=row.pk, status=QRLoginRequest.STATUS_PENDING).update(
        status=QRLoginRequest.STATUS_APPROVED, user=user, approved_at=timezone.now()
    )
    if not updated:
        raise ConnectionError_("qr_used", "This QR code was already used.", 409)
    row.status, row.user = QRLoginRequest.STATUS_APPROVED, user
    return row


def poll_qr_request(request, code, secret) -> dict:
    """Computer side, step 2 (repeat). Returns ``{"state": ..., ...}``; on ``approved`` it also carries the tokens,
    exactly once."""
    row = _find_by_code(code)
    if row is None or not hmac.compare_digest(row.secret_hash, _h(secret if isinstance(secret, str) else "")):
        raise ConnectionError_("qr_not_found", "This QR code is not valid.", 404)

    if row.status == QRLoginRequest.STATUS_DENIED:
        return {"state": "denied"}
    if row.status == QRLoginRequest.STATUS_CONSUMED:
        return {"state": "consumed"}
    if row.status == QRLoginRequest.STATUS_PENDING:
        return {"state": "expired" if row.is_expired() else "pending",
                "seconds_left": max(0, int((row.expires_at - timezone.now()).total_seconds()))}

    # approved -> hand the session over once. The approval itself must still be fresh.
    if timezone.now() > row.approved_at + timedelta(seconds=int(_cfg("QR_LOGIN_COLLECT_SECONDS", 120))):
        QRLoginRequest.objects.filter(pk=row.pk, status=QRLoginRequest.STATUS_APPROVED).update(
            status=QRLoginRequest.STATUS_DENIED
        )
        return {"state": "expired"}
    user = row.user
    if user is None or not user.is_active:
        return {"state": "denied"}
    claimed = QRLoginRequest.objects.filter(pk=row.pk, status=QRLoginRequest.STATUS_APPROVED).update(
        status=QRLoginRequest.STATUS_CONSUMED, consumed_at=timezone.now()
    )
    if not claimed:
        return {"state": "consumed"}
    tokens = issue_tokens_for_user(user, request)
    try:
        jti = str(RefreshToken(tokens["refresh"])["jti"])
        QRLoginRequest.objects.filter(pk=row.pk).update(session_jti=jti)
    except Exception:  # noqa: BLE001
        logger.exception("could not store QR session jti")
    return {"state": "approved", "user": user, "tokens": tokens}


# ====================================================================================== linked apps
def linked_app_sessions(user):
    """Live sessions that were created by a QR sign-in, newest first."""
    jtis = list(
        QRLoginRequest.objects.filter(user=user, status=QRLoginRequest.STATUS_CONSUMED)
        .exclude(session_jti="")
        .values_list("session_jti", flat=True)
    )
    if not jtis:
        return AuthToken.objects.none()
    return AuthToken.objects.filter(user=user, jti__in=jtis).order_by("-last_used_at", "-id")


def purge_old_qr_requests(hours: int = 24) -> int:
    """Rows that can no longer do anything. Consumed rows stay while their session is alive (Linked apps list)."""
    cutoff = timezone.now() - timedelta(hours=hours)
    stale = QRLoginRequest.objects.filter(created_at__lt=cutoff).exclude(status=QRLoginRequest.STATUS_CONSUMED)
    n, _ = stale.delete()
    dead = QRLoginRequest.objects.filter(status=QRLoginRequest.STATUS_CONSUMED, created_at__lt=cutoff).exclude(
        session_jti__in=AuthToken.objects.values("jti")
    )
    m, _ = dead.delete()
    return n + m
