"""
Hardened Django admin (task 14): TOTP two-factor + strong staff passwords + login lockout.

The copyright moderators (L1/L2/L3 groups) do ALL their work in this admin
(copyrights/admin.py), so protecting the admin login protects every takedown,
strike and account-termination action.

Behaviour is controlled by ONE setting, ``ADMIN_REQUIRE_2FA`` (settings.py):
  True  -> login needs password + a 6-digit TOTP code (or a one-time backup code) and a
           staff user WITHOUT a confirmed device simply cannot get in. Enrol people with
           ``python manage.py setup_staff_2fa <username>``.
  False -> plain admin login, but the strong-password rule and the lockout still apply.
Default: True in production (DEBUG=False), False for local dev and the test runner.
"""
import hashlib
import logging
import math
import time

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django_otp.admin import OTPAdminAuthenticationForm, OTPAdminSite

from .password_policy import is_staff_like, staff_min_length

logger = logging.getLogger("security.admin")


def _enforce_2fa() -> bool:
    return bool(getattr(settings, "ADMIN_REQUIRE_2FA", not settings.DEBUG))


def _max_failures() -> int:
    return int(getattr(settings, "ADMIN_LOGIN_MAX_FAILURES", 5))


def _lockout_seconds() -> int:
    return int(getattr(settings, "ADMIN_LOGIN_LOCKOUT_SECONDS", 900))


def _key(kind: str, value: str) -> str:
    return f"adminlogin:{kind}:" + hashlib.sha256((value or "").strip().lower().encode()).hexdigest()[:32]


def _client_ip(request) -> str:
    """P-B8: the lockout counted REMOTE_ADDR, which behind nginx / a load balancer is the PROXY - one bad actor locked
    everybody out. Same trusted-proxy rule as the IP allow-list (REMOTE_ADDR only while no proxy count is set)."""
    if request is None:
        return ""
    from . import admin_ip_allowlist as ipl

    return ipl.client_ip(getattr(request, "META", {}) or {}, ipl.settings_num_proxies(settings))


def _wait_text(seconds: float) -> str:
    seconds = max(1, int(math.ceil(seconds)))
    if seconds < 90:
        return f"{seconds} seconds"
    return f"{math.ceil(seconds / 60)} minutes"


def _lock_remaining(*keys) -> float:
    """Seconds left on the lock for whichever of these counters is locked (L1 gap 20).
    Falls back to the full lockout if the lock-end time is unknown (counter set before this existed)."""
    now = time.time()
    left = 0.0
    for k in keys:
        until = cache.get(k + ":until")
        left = max(left, (until - now) if until else float(_lockout_seconds()))
    return left


def _audit(event, request, *, outcome, username, user=None, reason=""):
    """L1 gap 19 -- also write the admin login outcome to the AuthAuditLog table (the
    logger lines below stay). Imported lazily; login.audit.record() never raises."""
    try:
        from login import audit

        audit.record(
            getattr(audit.Event, event), request, outcome=getattr(audit, outcome), user=user,
            identifier=username, kind=audit.KIND_USERNAME, reason=reason,
        )
    except Exception:  # auditing must never break the admin login
        logger.exception("admin login audit failed")


class _GuardedLoginMixin:
    """Lockout after repeated failures + strong-password gate, shared by both login forms."""

    def clean(self):
        username = (self.data.get("username") or "").strip()
        ip = _client_ip(self.request)
        ukey, ikey = _key("user", username), _key("ip", ip)
        limit = _max_failures()
        if cache.get(ukey, 0) >= limit or cache.get(ikey, 0) >= limit * 4:
            logger.warning("admin login locked out: user=%r ip=%s", username, ip)
            _audit("ADMIN_LOGIN_LOCKED", self.request, outcome="BLOCKED", username=username, reason="locked_out")
            locked = [k for k, n in ((ukey, limit), (ikey, limit * 4)) if cache.get(k, 0) >= n]
            raise ValidationError(
                f"Too many failed attempts. Try again in {_wait_text(_lock_remaining(*locked))}.",
                code="locked_out",
            )
        try:
            cleaned = super().clean()
        except ValidationError:
            for k in (ukey, ikey):
                cache.add(k, 0, _lockout_seconds())
                try:
                    cache.incr(k)
                except ValueError:
                    cache.set(k, 1, _lockout_seconds())
            for k, threshold in ((ukey, limit), (ikey, limit * 4)):
                if cache.get(k, 0) >= threshold:
                    # the lock starts NOW: restart the clock and remember when it ends, so the
                    # login page can say how long is left.
                    cache.set(k, threshold, _lockout_seconds())
                    cache.set(k + ":until", time.time() + _lockout_seconds(), _lockout_seconds())
            logger.warning("admin login failed: user=%r ip=%s", username, ip)
            _audit("ADMIN_LOGIN_FAILED", self.request, outcome="FAILURE", username=username, reason="invalid_credentials")
            raise
        user = self.get_user()
        if user is not None and is_staff_like(user):
            try:
                # runs EVERY validator incl. StaffStrongPasswordValidator, against the raw password
                validate_password(self.cleaned_data.get("password") or self.data.get("password", ""), user)
            except ValidationError as exc:
                logger.warning("admin login refused (weak staff password): user=%r", username)
                _audit(
                    "ADMIN_LOGIN_WEAK_PASSWORD", self.request, outcome="FAILURE", username=username, user=user,
                    reason="staff_password_weak",
                )
                raise ValidationError(
                    "Your password does not meet the staff security policy: "
                    + " ".join(exc.messages)
                    + " Ask a superuser to run `manage.py changepassword` for you.",
                    code="staff_password_weak",
                )
        cache.delete(ukey)
        cache.delete(ukey + ":until")
        _audit("ADMIN_LOGIN", self.request, outcome="SUCCESS", username=username, user=user)
        return cleaned


class StaffLoginForm(_GuardedLoginMixin, AdminAuthenticationForm):
    pass


class StaffOTPLoginForm(_GuardedLoginMixin, OTPAdminAuthenticationForm):
    pass


class SecureAdminSite(OTPAdminSite):
    """Drop-in replacement for the default admin site (see common/apps.py)."""

    def __init__(self, name="admin"):  # keep the "admin" URL namespace so reverse("admin:...") still works
        super().__init__(name)

    @property
    def login_form(self):
        return StaffOTPLoginForm if _enforce_2fa() else StaffLoginForm

    @property
    def login_template(self):
        # L1 gap 20: one wrapper template (login/templates/admin/staff_login.html) that adds the
        # password / 2FA hints on top of whichever stock login page applies.
        return "admin/staff_login.html"

    def login(self, request, extra_context=None):
        extra = {
            "login_parent": "otp/admin111/login.html" if _enforce_2fa() else "admin/login.html",
            "staff_min_length": staff_min_length(),
            "require_2fa": _enforce_2fa(),
            "max_failures": _max_failures(),
            "lockout_minutes": max(1, _lockout_seconds() // 60),
        }
        extra.update(extra_context or {})
        return super().login(request, extra)

    def get_urls(self):
        from django.urls import path

        # Public on purpose: a moderator who has no 2FA device yet cannot log in, and is exactly
        # who needs this page. It contains no secrets.
        return [path("2fa-guide/", self.twofa_guide, name="twofa_guide")] + super().get_urls()

    def twofa_guide(self, request):
        from django.shortcuts import render

        user = request.user if request.user.is_authenticated and request.user.is_staff else None
        has_device = False
        if user is not None:
            try:
                from django_otp.plugins.otp_totp.models import TOTPDevice

                has_device = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
            except Exception:
                has_device = False
        return render(request, "admin/staff_2fa_guide.html", {
            "username": user.get_username() if user else "<your-username>",
            "signed_in": user is not None,
            "has_device": has_device,
            "require_2fa": _enforce_2fa(),
        })

    def has_permission(self, request):
        if not (request.user.is_active and request.user.is_staff):
            return False
        if _enforce_2fa():
            is_verified = getattr(request.user, "is_verified", None)
            return bool(is_verified and is_verified())
        return True
