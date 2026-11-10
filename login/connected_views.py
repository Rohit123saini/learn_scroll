# login/connected_views.py
"""S10 -- Connected accounts & devices endpoints (all under /login/auth/).

    GET    connections/                         one screen's worth: Google, e-mail, phone, password, 2FA, linked apps
    POST   connections/reauth/send-code/        accounts with no password / 2FA: code to the CURRENT e-mail / phone
    POST   connections/reauth/                  {password?, two_factor_code?, reauth_code?} -> {reauth_token}
    POST   connections/google/link/             {id_token, <reauth>}
    POST   connections/google/unlink/           {<reauth>}
    POST   connections/contact/send-code/       {kind: email|phone, value}            code goes to the NEW address
    POST   connections/contact/confirm/         {kind, value, otp, <reauth>}          add / replace e-mail or phone
    POST   connections/phone/remove/            {<reauth>}                            phone is optional
    GET    connections/apps/                    "Linked apps": sessions that were created by a QR sign-in
    DELETE connections/apps/<id>/               sign one of them out

    POST   qr/create/                           (no auth)  computer asks for a QR       -> {code, secret, match_code, ...}
    POST   qr/poll/                             (no auth)  {code, secret}               -> {state, [user, token]}
    POST   qr/lookup/                           phone: {code}                           -> who is asking
    POST   qr/answer/                           phone: {code, approve, match_code}

``<reauth>`` = ``reauth_token`` (from connections/reauth/) OR the raw fields it is made of. The overview says which
fields this account needs (``reauth.required``).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import audit, connected_accounts as ca, google_verify, login_alerts, two_factor
from .disposable import phone_lookup_problem
from .errors import UnifiedErrorsMixin, api_error, retry_error
from .identifiers import KIND_EMAIL, KIND_PHONE
from .login_guard import client_ip
from .sensitive import SensitiveWriteThrottle  # X6 -- writes-only throttle that audits a refusal
from .models import AuthToken, LinkedIdentity, OTPVerification, QRLoginRequest
from .otp_service import (
    OTPDeliveryError,
    OTPInvalid,
    OTPRateLimited,
    check_code,
    deliver_code,
    issue_code,
    refund_send,
    reserve_send,
)
from .session_views import _current_session_jti

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ small helpers
def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _truthy(value):
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _fail(exc: ca.ConnectionError_):
    extra = dict(exc.extra)
    retry_after = extra.pop("retry_after", None)
    if retry_after is not None and exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
        return retry_error(exc.message, exc.code, retry_after)
    return api_error(exc.message, exc.code, exc.status, **extra)


def _mask_email(email):
    return audit.mask_identifier(email, audit.KIND_EMAIL) if email else ""


def _mask_phone(phone):
    return audit.mask_identifier(phone, audit.KIND_PHONE) if phone else ""


def _email_proven(user) -> bool:
    try:
        from core.channel_policy import email_verified

        return bool(user.email) and bool(email_verified(user))
    except Exception:  # noqa: BLE001
        return False


def _reauth_or_error(request, user, body):
    """``None`` when re-auth is satisfied, else the error response (and an audit row)."""
    try:
        ca.check_reauth(user, body, _current_session_jti(request))
    except ca.ConnectionError_ as exc:
        audit.record(
            audit.Event.CONNECTION_REAUTH_FAILED, request,
            # X6 -- a lock (too many wrong passwords / codes) is a "blocked" row, a plain wrong value is a "failure"
            outcome=audit.BLOCKED if exc.status == 429 else audit.FAILURE, user=user, reason=exc.code,
        )
        return _fail(exc)
    return None


def _session_row(session, current_jti):
    return {
        "id": session.id,
        "device_name": session.device_name or "Unknown device",
        "platform": session.platform or "other",
        "ip_address": session.ip_address,
        "created_at": session.created_at,
        "last_used_at": session.last_used_at,
        "is_current": bool(current_jti) and session.jti == current_jti,
    }


def build_overview(user, request=None) -> dict:
    google = ca.active_google_identity(user)
    methods = ca.sign_in_methods(user)
    current_jti = _current_session_jti(request) if request is not None else None
    apps = list(ca.linked_app_sessions(user)[:20])
    return {
        "status": True,
        "google": {
            "configured": google_verify.is_configured(),
            "linked": google is not None,
            "email": google.email if google else "",
            "email_masked": _mask_email(google.email) if google else "",
            "display_name": google.display_name if google else "",
            "linked_at": google.linked_at if google else None,
            "last_used_at": google.last_used_at if google else None,
            "can_unlink": bool(google) and ca.has_other_way_in(user, without_google=True),
        },
        "email": {
            "value": user.email or "",
            "masked": _mask_email(user.email),
            "verified": _email_proven(user) or bool(google and google.email == (user.email or "")),
            "can_change": True,
        },
        "phone": {
            "value": user.phone or "",
            "masked": _mask_phone(user.phone),
            "verified": bool(user.phone and user.phone_verified),
            "can_remove": bool(user.phone) and ca.has_other_way_in(user, without_phone=True),
        },
        "has_password": methods["password"],
        "two_factor_enabled": two_factor.is_enabled(user),
        "reauth": {
            "required": ca.reauth_requirements(user),
            "otp_target_masked": (
                _mask_email(user.email) if user.email else _mask_phone(user.phone)
            ) if ca.REAUTH_OTP in ca.reauth_requirements(user) else "",
            "ttl_seconds": ca.reauth_ttl(),
        },
        "linked_apps": [_session_row(s, current_jti) for s in apps],
        "qr_login": {"enabled": ca.qr_enabled(), "ttl_seconds": ca.qr_ttl()},
    }


class _ConnBase(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "connections"


class _ConnWriteBase(_ConnBase):
    # X6 -- e-mail / phone / Google link changes: counted on writes only, and a refusal leaves an audit row.
    throttle_classes = [SensitiveWriteThrottle]
    throttle_scope = "connections_write"


# ------------------------------------------------------------------ overview
class ConnectionsOverviewView(_ConnBase):
    def get(self, request):
        return Response(build_overview(request.user, request))


# ------------------------------------------------------------------ re-auth
class ReauthSendCodeView(_ConnWriteBase):
    """Only for accounts whose re-auth is "a code to my own e-mail / phone" (no password, no 2FA)."""

    def post(self, request):
        user = request.user
        if ca.REAUTH_OTP not in ca.reauth_requirements(user):
            return api_error("This account does not use an e-mailed code.", "reauth_not_otp", status.HTTP_400_BAD_REQUEST)
        target = ca.reauth_otp_target(user)
        if target is None:
            return api_error("No e-mail or phone on this account.", "no_contact", status.HTTP_400_BAD_REQUEST)
        value, is_email = target
        return _send_code(request, value, is_email, purpose="reauth", user=user)


class ReauthView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        body = _body(request)
        # a token must not vouch for itself here: only the raw fields count
        raw = {k: v for k, v in body.items() if k != "reauth_token"}
        err = _reauth_or_error(request, user, raw)
        if err is not None:
            return err
        return Response(
            {
                "status": True,
                "reauth_token": ca.make_reauth_token(user, _current_session_jti(request)),
                "expires_in": ca.reauth_ttl(),
            }
        )


# ------------------------------------------------------------------ Google
class GoogleLinkView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        body = _body(request)
        err = _reauth_or_error(request, user, body)
        if err is not None:
            return err
        try:
            profile = ca.verify_google_id_token(body.get("id_token"))
            identity = ca.link_google(user, profile)
        except ca.ConnectionError_ as exc:
            audit.record(
                audit.Event.GOOGLE_FAILED, request, outcome=audit.FAILURE, user=user, reason=exc.code, purpose="link",
            )
            return _fail(exc)
        audit.record(audit.Event.GOOGLE_LINKED, request, user=user, identifier=identity.email, method="google")
        payload = build_overview(user, request)
        payload["message"] = "Google account linked."
        return Response(payload)


class GoogleUnlinkView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        err = _reauth_or_error(request, user, _body(request))
        if err is not None:
            return err
        try:
            identity = ca.unlink_google(user)
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        audit.record(audit.Event.GOOGLE_UNLINKED, request, user=user, identifier=identity.email, method="google")
        payload = build_overview(user, request)
        payload["message"] = "Google account unlinked."
        return Response(payload)


# ------------------------------------------------------------------ e-mail / phone
def _send_code(request, target, is_email, *, purpose, user):
    """The one place a connections code is rate-limited, created, delivered and audited."""
    kind = KIND_EMAIL if is_email else KIND_PHONE
    try:
        ticket = reserve_send(target, client_ip(request))
    except OTPRateLimited as exc:
        audit.record(
            audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, user=user, identifier=target, kind=kind,
            reason=exc.code, purpose=purpose, retry_after=exc.retry_after,
        )
        return retry_error(exc.message, exc.code, exc.retry_after)
    try:
        code = issue_code(target)
        deliver_code(target, is_email, code)
    except OTPDeliveryError:
        refund_send(ticket)
        audit.record(
            audit.Event.OTP_SEND_FAILED, request, outcome=audit.FAILURE, user=user, identifier=target, kind=kind,
            reason="otp_delivery_failed", purpose=purpose,
        )
        return api_error(
            "Could not send the code right now. Please try again.", "otp_delivery_failed",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    audit.record(audit.Event.OTP_SENT, request, user=user, identifier=target, kind=kind, purpose=purpose)
    return Response(
        {
            "status": True,
            "message": "Code sent.",
            "sent_to": _mask_email(target) if is_email else _mask_phone(target),
            "resend_after": int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)),
            "max_attempts": OTPVerification.MAX_ATTEMPTS,
            "expires_in": OTPVerification.EXPIRY_MINUTES * 60,
        }
    )


class ContactSendCodeView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        body = _body(request)
        kind = str(body.get("kind") or "").strip().lower()
        try:
            value = ca.validate_new_contact(user, kind, body.get("value"))
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        if kind == KIND_PHONE:
            problem = phone_lookup_problem(value)  # paid carrier lookup (optional, cached, fail-open)
            if problem:
                audit.record(
                    audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, user=user, identifier=value,
                    kind=kind, reason=problem[0], purpose="change_contact",
                )
                return api_error(problem[1], problem[0], status.HTTP_400_BAD_REQUEST)
        return _send_code(request, value, kind == KIND_EMAIL, purpose="change_contact", user=user)


class ContactConfirmView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        body = _body(request)
        kind = str(body.get("kind") or "").strip().lower()
        try:
            value = ca.validate_new_contact(user, kind, body.get("value"))
        except ca.ConnectionError_ as exc:
            return _fail(exc)

        # re-auth first so a wrong password never burns the one-time code that was e-mailed / texted
        err = _reauth_or_error(request, user, body)
        if err is not None:
            return err

        otp = str(body.get("otp") or "").strip()
        if not otp:
            return api_error("Enter the code we sent you.", "otp_required", status.HTTP_400_BAD_REQUEST)
        try:
            check_code(value, otp)
        except OTPInvalid as exc:
            audit.record(
                audit.Event.OTP_FAILED, request,
                outcome=audit.BLOCKED if exc.code == "otp_locked" else audit.FAILURE,
                user=user, identifier=value, kind=kind, reason=exc.code, purpose="change_contact",
                attempts_left=exc.attempts_left,
            )
            return api_error(exc.message, exc.code, exc.status_code, attempts_left=exc.attempts_left)

        had_before = bool(user.email if kind == KIND_EMAIL else user.phone)
        try:
            ca.apply_contact_change(user, kind, value)
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        audit.record(
            audit.Event.CONTACT_CHANGED, request, user=user, identifier=value, kind=kind, replaced=had_before,
        )
        if kind == KIND_PHONE:
            audit.record(audit.Event.PHONE_VERIFIED, request, user=user, identifier=value, kind=KIND_PHONE)
        payload = build_overview(user, request)
        payload["message"] = (
            ("E-mail updated." if had_before else "E-mail added.")
            if kind == KIND_EMAIL
            else ("Phone number updated." if had_before else "Phone number added.")
        )
        return Response(payload)


class PhoneRemoveView(_ConnWriteBase):
    def post(self, request):
        user = request.user
        err = _reauth_or_error(request, user, _body(request))
        if err is not None:
            return err
        old = user.phone
        try:
            ca.remove_phone(user)
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        audit.record(audit.Event.CONTACT_REMOVED, request, user=user, identifier=old, kind=KIND_PHONE)
        payload = build_overview(user, request)
        payload["message"] = "Phone number removed."
        return Response(payload)


# ------------------------------------------------------------------ linked apps
class LinkedAppsView(_ConnBase):
    def get(self, request):
        current = _current_session_jti(request)
        rows = [_session_row(s, current) for s in ca.linked_app_sessions(request.user)[:50]]
        return Response({"status": True, "apps": rows})


class LinkedAppDetailView(_ConnWriteBase):
    def delete(self, request, pk):
        session = ca.linked_app_sessions(request.user).filter(pk=pk).first()
        if session is None:
            return api_error("That app is already signed out.", "linked_app_not_found", status.HTTP_404_NOT_FOUND)
        name, platform = session.device_name, session.platform
        session.delete()
        audit.record(
            audit.Event.LINKED_APP_REVOKED, request, user=request.user, revoked_device=name, revoked_platform=platform,
        )
        return Response({"status": True, "message": "App signed out."})


# ------------------------------------------------------------------ QR sign-in
class QRCreateView(UnifiedErrorsMixin, APIView):
    """Computer (no session yet) asks for a QR. Keyed by IP."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "qr_login"

    def post(self, request):
        try:
            data = ca.create_qr_request(request)
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        data.pop("id", None)
        return Response({"status": True, **data}, status=status.HTTP_201_CREATED)


class QRPollView(UnifiedErrorsMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "qr_poll"

    def post(self, request):
        body = _body(request)
        try:
            result = ca.poll_qr_request(request, body.get("code"), body.get("secret"))
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        state = result["state"]
        if state != "approved":
            out = {"status": True, "state": state}
            if "seconds_left" in result:
                out["seconds_left"] = result["seconds_left"]
            return Response(out)

        user = result["user"]
        audit.record(audit.Event.QR_LOGIN, request, user=user, method="qr")
        try:
            login_alerts.check_new_device(request, user, method="qr")
        except Exception:  # noqa: BLE001
            logger.exception("new-device alert failed after QR sign-in")
        return Response(
            {
                "status": True,
                "state": "approved",
                "message": "Login Successful",
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
                "token": result["tokens"],
            }
        )


class QRLookupView(_ConnBase):
    """Phone scanned a code: who is asking? (``match_code`` is never returned -- it is only on the computer.)"""

    def post(self, request):
        try:
            row = ca.lookup_qr_request(_body(request).get("code"))
        except ca.ConnectionError_ as exc:
            return _fail(exc)
        return Response({"status": True, **ca.describe_qr_request(row)})


class QRAnswerView(_ConnWriteBase):
    def post(self, request):
        body = _body(request)
        approve = _truthy(body.get("approve"))
        try:
            row = ca.answer_qr_request(
                request.user, body.get("code"), approve=approve, match_code=str(body.get("match_code") or "")
            )
        except ca.ConnectionError_ as exc:
            if exc.code in ("qr_match_invalid", "qr_match_locked"):
                audit.record(
                    audit.Event.QR_LOGIN_DENIED, request, outcome=audit.FAILURE, user=request.user, reason=exc.code,
                )
            return _fail(exc)
        audit.record(
            audit.Event.QR_LOGIN_APPROVED if approve else audit.Event.QR_LOGIN_DENIED, request, user=request.user,
            requester_platform=row.requester_platform,
        )
        return Response(
            {
                "status": True,
                "approved": approve,
                "message": "Computer signed in." if approve else "Request declined.",
            }
        )
