# login/security_views.py
"""S3 -- Security centre endpoints (all under /login/auth/).

    GET  security/overview/       one screen's worth: 2FA, backup codes left, e-mail / phone status, alerts, sessions
    GET  security/activity/       recent security events of THIS account (login, new device, password, 2FA, sessions)
    POST 2fa/setup/               {password?}  -> {secret, otpauth_url}   (not enabled yet)
    POST 2fa/enable/              {code}       -> {backup_codes: [...]}   (shown ONCE)
    POST 2fa/disable/             {password?, code}
    POST 2fa/backup-codes/        {password?, code} -> new codes (old ones die)
    POST 2fa/verify/              {challenge, code} -> the same session payload Login returns   (no auth yet)

Sessions (list / sign one device out / sign out others) already exist in session_views.py.
Re-auth: setup / disable / backup-codes need the current password when the account has one (a stolen unlocked phone
must not be able to switch 2FA off or enrol the thief's authenticator). Google-only accounts have no password; they
are protected by the TOTP code itself (disable / regenerate) and setup needs a fresh session.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import audit, login_alerts, two_factor
from .errors import UnifiedErrorsMixin, api_error, retry_error
from .models import AuthAuditLog, AuthToken, User
from .token_issuance import issue_tokens_for_user, revoke_user_sessions

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ gate used by Login / Google / OTP-login
def two_factor_gate(request, user, method: str):
    """``None`` when the user has no 2FA (caller issues tokens as before); otherwise the 200 response that asks for
    the second step. NO tokens are minted here."""
    if not two_factor.is_enabled(user):
        return None
    audit.record(audit.Event.TWO_FACTOR_CHALLENGE, request, user=user, method=method)
    return Response(
        {
            "status": True,
            "two_factor_required": True,
            "message": "Enter the 6-digit code from your authenticator app.",
            "challenge": two_factor.make_challenge(user, method),
            "methods": ["totp", "backup"],
            "expires_in": two_factor.CHALLENGE_MAX_AGE,
        },
        status=status.HTTP_200_OK,
    )


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _reauth_error(request):
    """None if OK, else an error response. Accounts with a password must send it."""
    user = request.user
    if not user.has_usable_password():
        return None
    pw = _body(request).get("password")
    if not isinstance(pw, str) or not pw or not user.check_password(pw):
        audit.record(audit.Event.TWO_FACTOR_FAILED, request, outcome=audit.FAILURE, user=user, reason="bad_password")
        return api_error("Current password is wrong.", "invalid_password", status.HTTP_400_BAD_REQUEST)
    return None


def _locked_response(user):
    return retry_error("Too many wrong codes. Try again later.", "two_factor_locked", two_factor.seconds_locked(user))


def _mask_email(email):
    return audit.mask_identifier(email, audit.KIND_EMAIL) if email else ""


def _mask_phone(phone):
    return audit.mask_identifier(phone, audit.KIND_PHONE) if phone else ""


# ------------------------------------------------------------------ overview
class SecurityOverviewView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "session_manage"

    def get(self, request):
        user = request.user
        try:
            from core.channel_policy import email_verified

            email_ok = bool(user.email) and bool(email_verified(user))
        except Exception:
            email_ok = False
        try:
            from user_profile.models import UserPreference

            login_alerts_on = bool(UserPreference.for_user(user).login_alerts)
        except Exception:
            login_alerts_on = True

        cutoff = timezone.now() - AuthToken.SLIDING_EXPIRY
        sessions = AuthToken.objects.filter(user=user, last_used_at__gt=cutoff).count()
        last_pw = (
            AuthAuditLog.objects.filter(
                user=user, outcome=audit.SUCCESS,
                event__in=(audit.Event.PASSWORD_CHANGED, audit.Event.RESET_SUCCESS),
            )
            .values_list("created_at", flat=True)
            .first()
        )
        enabled = two_factor.is_enabled(user)
        return Response(
            {
                "status": True,
                "two_factor": {
                    "enabled": enabled,
                    "backup_codes_remaining": two_factor.backup_codes_remaining(user) if enabled else 0,
                },
                "has_password": user.has_usable_password(),
                "last_password_change": last_pw,
                "login_alerts": login_alerts_on,
                "sessions_count": sessions,
                "recovery": {
                    "email": _mask_email(user.email),
                    "email_verified": email_ok,
                    "phone": _mask_phone(user.phone),
                    "phone_verified": bool(user.phone and user.phone_verified),
                    # recovery works if at least ONE of them is proven
                    "ready": email_ok or bool(user.phone and user.phone_verified),
                },
            }
        )


# ------------------------------------------------------------------ activity feed
_ACTIVITY_LABELS = {
    audit.Event.LOGIN_SUCCESS: "Signed in",
    audit.Event.GOOGLE_LOGIN: "Signed in with Google",
    audit.Event.OTP_LOGIN: "Signed in with a one-time code",
    audit.Event.LOGIN_FAILED: "Wrong password",
    audit.Event.LOGIN_LOCKED: "Sign-in locked after too many tries",
    audit.Event.NEW_DEVICE_LOGIN: "New device signed in",
    audit.Event.PASSWORD_CHANGED: "Password changed",
    audit.Event.PASSWORD_CHANGE_FAILED: "Wrong current password while changing password",
    audit.Event.RESET_SUCCESS: "Password reset",
    audit.Event.TWO_FACTOR_ENABLED: "Two-step verification turned on",
    audit.Event.TWO_FACTOR_DISABLED: "Two-step verification turned off",
    audit.Event.TWO_FACTOR_FAILED: "Wrong two-step code",
    audit.Event.TWO_FACTOR_BACKUP_USED: "Backup code used",
    audit.Event.BACKUP_CODES_REGENERATED: "New backup codes created",
    audit.Event.LOGOUT_ALL: "Signed out of devices",
    audit.Event.LOGIN_ALERTS_CHANGED: "Sign-in alerts changed",
    audit.Event.SESSION_REVOKED: "A device was signed out",
    audit.Event.ACCOUNT_DELETION_SCHEDULED: "Account deletion scheduled",
    audit.Event.ACCOUNT_DELETION_CANCELLED: "Account deletion cancelled",
    audit.Event.DATA_EXPORT_REQUESTED: "Data export requested",
    audit.Event.DATA_EXPORT_DOWNLOADED: "Data export downloaded",
    audit.Event.CONSENT_CHANGED: "Privacy consent changed",
    # S10 -- connected accounts & devices
    audit.Event.GOOGLE_LINKED: "Google account linked",
    audit.Event.GOOGLE_UNLINKED: "Google account unlinked",
    audit.Event.CONTACT_CHANGED: "E-mail or phone number changed",
    audit.Event.CONTACT_REMOVED: "Phone number removed",
    audit.Event.CONNECTION_REAUTH_FAILED: "Wrong password or code while changing a connected account",
    audit.Event.QR_LOGIN_APPROVED: "Computer approved with QR code",
    audit.Event.QR_LOGIN_DENIED: "QR sign-in declined",
    audit.Event.QR_LOGIN: "Signed in with QR code",
    audit.Event.LINKED_APP_REVOKED: "A linked app was signed out",
    # X6 -- sensitive changes (field names only are stored)
    audit.Event.USERNAME_CHANGED: "Username changed",
    audit.Event.DOB_CHANGED: "Date of birth changed",
    audit.Event.ACCOUNT_PRIVACY_CHANGED: "Private account setting changed",
    audit.Event.PRIVACY_CONTROLS_CHANGED: "Privacy controls changed",
    audit.Event.WALLET_SETTINGS_CHANGED: "Wallet limits changed",
    audit.Event.WITHDRAWAL_REQUESTED: "Coin withdrawal requested",
    audit.Event.WITHDRAWAL_BLOCKED: "Coin withdrawal refused",
}
# Shown to the account owner; noisy anonymous events (captcha, otp-send blocks ...) are left out on purpose.
ACTIVITY_EVENTS = tuple(_ACTIVITY_LABELS)


class SecurityActivityView(UnifiedErrorsMixin, APIView):
    """Newest first. ``?limit=`` (1-50, default 30) and ``?before=<id>`` for the next page."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "session_manage"

    def get(self, request):
        try:
            limit = max(1, min(50, int(request.query_params.get("limit", 30))))
        except (TypeError, ValueError):
            limit = 30
        qs = AuthAuditLog.objects.filter(user=request.user, event__in=ACTIVITY_EVENTS)
        before = request.query_params.get("before")
        if before and str(before).isdigit():
            qs = qs.filter(id__lt=int(before))
        rows = list(qs.order_by("-id")[: limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        items = [
            {
                "id": r.id,
                "event": r.event,
                "label": _ACTIVITY_LABELS.get(r.event, r.event),
                "outcome": r.outcome,
                "created_at": r.created_at,
                "ip_address": r.ip_address,
                "platform": r.platform,
                "device_name": (r.metadata or {}).get("device_name", ""),
            }
            for r in rows
        ]
        return Response(
            {"status": True, "items": items, "next_before": rows[-1].id if (more and rows) else None}
        )


# ------------------------------------------------------------------ 2FA management
class TwoFactorSetupView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        if two_factor.is_enabled(request.user):
            return api_error("Two-step verification is already on.", "two_factor_already_enabled", status.HTTP_409_CONFLICT)
        err = _reauth_error(request)
        if err is not None:
            return err
        info = two_factor.start_setup(request.user)
        audit.record(audit.Event.TWO_FACTOR_SETUP_STARTED, request, user=request.user)
        return Response(
            {
                "status": True,
                "secret": info["secret"],
                "otpauth_url": info["otpauth_url"],
                "account": request.user.email or request.user.username,
            }
        )


class TwoFactorEnableView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        user = request.user
        if two_factor.is_enabled(user):
            return api_error("Two-step verification is already on.", "two_factor_already_enabled", status.HTTP_409_CONFLICT)
        if two_factor.seconds_locked(user):
            return _locked_response(user)
        codes = two_factor.confirm_setup(user, _body(request).get("code"))
        if codes is None:
            two_factor._record_failure(user)
            audit.record(audit.Event.TWO_FACTOR_FAILED, request, outcome=audit.FAILURE, user=user, reason="enable_bad_code")
            return api_error("That code is not right. Check the app and try again.", "two_factor_invalid_code", status.HTTP_400_BAD_REQUEST)
        audit.record(audit.Event.TWO_FACTOR_ENABLED, request, user=user)
        return Response({"status": True, "message": "Two-step verification is on.", "backup_codes": codes})


class TwoFactorDisableView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        user = request.user
        if not two_factor.is_enabled(user):
            return api_error("Two-step verification is not on.", "two_factor_not_enabled", status.HTTP_409_CONFLICT)
        if two_factor.seconds_locked(user):
            return _locked_response(user)
        err = _reauth_error(request)
        if err is not None:
            return err
        if two_factor.check_code(user, _body(request).get("code")) is None:
            audit.record(audit.Event.TWO_FACTOR_FAILED, request, outcome=audit.FAILURE, user=user, reason="disable_bad_code")
            return api_error("That code is not right.", "two_factor_invalid_code", status.HTTP_400_BAD_REQUEST)
        two_factor.disable(user)
        audit.record(audit.Event.TWO_FACTOR_DISABLED, request, user=user)
        return Response({"status": True, "message": "Two-step verification is off."})


class TwoFactorBackupCodesView(UnifiedErrorsMixin, APIView):
    """Make 10 fresh backup codes; the old ones stop working."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        user = request.user
        if not two_factor.is_enabled(user):
            return api_error("Two-step verification is not on.", "two_factor_not_enabled", status.HTTP_409_CONFLICT)
        if two_factor.seconds_locked(user):
            return _locked_response(user)
        err = _reauth_error(request)
        if err is not None:
            return err
        if two_factor.check_code(user, _body(request).get("code")) is None:
            audit.record(audit.Event.TWO_FACTOR_FAILED, request, outcome=audit.FAILURE, user=user, reason="regen_bad_code")
            return api_error("That code is not right.", "two_factor_invalid_code", status.HTTP_400_BAD_REQUEST)
        codes = two_factor.regenerate_backup_codes(user)
        audit.record(audit.Event.BACKUP_CODES_REGENERATED, request, user=user)
        return Response({"status": True, "backup_codes": codes})


# ------------------------------------------------------------------ login step 2
class TwoFactorVerifyView(UnifiedErrorsMixin, APIView):
    """Second step of a login. No auth header: the signed ``challenge`` from the first step is the proof."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def post(self, request):
        body = _body(request)
        data = two_factor.read_challenge(body.get("challenge"))
        if data is None:
            return api_error("Sign in again.", "two_factor_challenge_expired", status.HTTP_401_UNAUTHORIZED)
        user = User.objects.filter(pk=data["u"], is_active=True).first()
        if user is None or not two_factor.is_enabled(user):
            return api_error("Sign in again.", "two_factor_challenge_expired", status.HTTP_401_UNAUTHORIZED)
        if two_factor.seconds_locked(user):
            return _locked_response(user)

        method = two_factor.check_code(user, body.get("code"))
        if method is None:
            audit.record(audit.Event.TWO_FACTOR_FAILED, request, outcome=audit.FAILURE, user=user, reason="login_bad_code")
            if two_factor.seconds_locked(user):
                return _locked_response(user)
            return api_error("That code is not right.", "two_factor_invalid_code", status.HTTP_400_BAD_REQUEST)

        two_factor.burn_challenge(data)
        login_alerts.check_new_device(request, user, method=data.get("m", ""))  # BEFORE this login's audit row
        tokens = issue_tokens_for_user(user, request)
        audit.record(audit.Event.LOGIN_SUCCESS, request, user=user, method=f"{data.get('m', 'password')}+2fa", second_factor=method)
        remaining = None
        if method == "backup":
            remaining = two_factor.backup_codes_remaining(user)
            audit.record(audit.Event.TWO_FACTOR_BACKUP_USED, request, user=user, remaining=remaining)

        # One superset payload: it has every key the password / Google / OTP login responses have, so the app's
        # existing session-saving code works unchanged.
        return Response(
            {
                "status": True,
                "message": "Login Successful",
                "user_exists": True,
                "is_new_user": False,
                "phone_missing": not bool(user.phone),
                "dob_missing": user.date_of_birth is None,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                },
                "token": tokens,
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "backup_codes_remaining": remaining,
            },
            status=status.HTTP_200_OK,
        )
