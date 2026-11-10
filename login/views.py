# login/views.py
import logging
import uuid

from django.contrib.auth import authenticate
from rest_framework.generics import GenericAPIView
from django.utils import timezone
import secrets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from common.open_throttles import GoogleAuthThrottle, LoginThrottle, SignupThrottle  # P-B3
from .token_issuance import issue_tokens_for_user, revoke_user_sessions
from . import login_alerts  # S3 -- new-device alert
from .security_views import two_factor_gate  # S3 -- 2nd step when the account has 2FA
from .two_factor import is_enabled as two_factor_enabled_for  # S3
from .identifiers import (
    KIND_INVALID,
    KIND_PHONE,
    find_user_by_identifier,
    find_user_for_password_login,
    normalize_identifier,
)
from .login_guard import LoginGuard, client_ip
from . import captcha as captcha_guard  # L1 gap 18 -- captcha on abuse-prone endpoints
from .disposable import (  # L1 gap 18 -- disposable email / virtual phone block
    email_problem,
    new_identifier_problem,
    phone_lookup_problem,
)
from .otp_service import (
    PURPOSE_RESET,
    OTPDeliveryError,
    OTPInvalid,
    OTPRateLimited,
    check_code,
    deliver_code,
    issue_code,
    refund_send,
    reserve_send,
    wrong_code_message,
)
from rest_framework.permissions import IsAuthenticated
from .serializers import ChangePasswordSerializer, password_problem_pairs
from .models import LinkedIdentity, OTPVerification
from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
)
from django.contrib.auth import get_user_model
from .serializers import *

from django.conf import settings
from django.core.mail import send_mail
from .sms_service import send_otp_sms, SMSDeliveryError
from .errors import UnifiedErrorsMixin, api_error, retry_error  # L1 gap 16 -- one error envelope
from . import audit  # L1 gap 19 -- audit trail of login / OTP / reset events
from .sensitive import ReauthGuard, SensitiveWriteThrottle  # X6 -- writes-only throttle + wrong-current-password lock
from . import connected_accounts  # S10 -- Google link rules (sub lookup, unlink block)
from . import google_verify  # T1 -- shared Google ID-token verification (timeout, cache, 503/401)

# Google token verification
# NOTE (T1): verification ab `login/google_verify.py` me hai. Ye do imports sirf isliye bache hain ki purane tests
# `login.views.google_id_token.verify_oauth2_token` patch karte hain (wahi module object helper bhi use karta hai).
from google.oauth2 import id_token as google_id_token  # noqa: F401
from google.auth.transport import requests as google_requests  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# L1 gap 16 -- EVERY failure in this file uses the one envelope from
# login/errors.py:  {"detail", "code", "message" (legacy alias), "errors"?, ...extras}.
# `code` is what the app translates; the text is only an English fallback.
# Helpers below keep their old names so call sites stay readable.
# ---------------------------------------------------------------------------
def _retry_response(message, code, retry_after):
    # 429 + body `retry_after` + standard Retry-After header (real countdown in the app).
    return retry_error(message, code, retry_after)


def _otp_limited_response(exc):
    return _retry_response(exc.message, exc.code, exc.retry_after)


def _login_locked_response(retry_after):
    minutes = max(1, -(-int(retry_after) // 60))  # ceil
    unit = "minute" if minutes == 1 else "minutes"
    return _retry_response(
        f"Too many wrong attempts. Please try again in {minutes} {unit}, "
        "or sign in with a code or Google.",
        "login_locked",
        retry_after,
    )


def _otp_error(message, code, http_status=status.HTTP_400_BAD_REQUEST, attempts_left=None):
    """Error body for a bad / expired / locked code (L1 gap 14).

    ``code`` is machine-matchable; ``attempts_left`` lets the app show how many
    wrong guesses remain before the code locks.
    """
    return api_error(
        message,
        code,
        http_status,
        attempts_left=None if attempts_left is None else int(attempts_left),
    )


def _wrong_code_response(otp_obj, noun="OTP"):
    """Count a wrong guess and answer with how many are left."""
    otp_obj.register_failed_attempt()
    left = otp_obj.attempts_left()
    return _otp_error(wrong_code_message(noun, left), "otp_invalid", attempts_left=left)


def _invalid_identifier_response():
    return api_error(
        "Enter a valid email address or phone number.",
        "invalid_identifier",
        status.HTTP_400_BAD_REQUEST,
    )


def _audit_captcha(request, challenge, scope, *, identifier=None, user=None):
    """L1 gap 19 -- a captcha was asked for (or answered wrong). ``challenge`` is the 400 Response."""
    data = getattr(challenge, "data", None)
    code = str(data.get("code") or "") if isinstance(data, dict) else ""
    event = audit.Event.CAPTCHA_FAILED if code == captcha_guard.CODE_INVALID else audit.Event.CAPTCHA_REQUIRED
    audit.record(event, request, outcome=audit.BLOCKED, user=user, identifier=identifier, reason=code, scope=scope)


def _audit_code_failure(
    request, target, kind, reason, *, purpose, event=audit.Event.OTP_FAILED,
    attempts_left=None, user=None, outcome=audit.FAILURE,
):
    """L1 gap 19 -- a wrong / expired / locked / missing code. The owning account (if any) is
    looked up only for the log, so support can see WHO is being guessed at."""
    if user is None:
        try:
            user = find_user_by_identifier(target, kind)
        except Exception:
            user = None
    audit.record(
        event, request, outcome=outcome, user=user, identifier=target, kind=kind,
        reason=reason, purpose=purpose, attempts_left=attempts_left,
    )

#----------------    login   ------------------------------------------
class Login(UnifiedErrorsMixin, GenericAPIView):
    serializer_class = LoginSerializer
    authentication_classes = []
    permission_classes = []
    throttle_classes = [LoginThrottle]  # P-B3 (the per-identifier lockout below stays the real brute-force guard)

    @extend_schema(
        summary="User Login",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Login Successful"),
            401: OpenApiResponse(description="Invalid Credentials"),
            429: OpenApiResponse(description="Too many wrong attempts (login_locked)"),
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        identifier = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        # L1 gap 6 -- brute-force lockout. Checked BEFORE the password so a
        # locked identifier is refused even with the right password.
        guard = LoginGuard(request, identifier)
        locked_for = guard.retry_after()
        if locked_for:
            audit.record(
                audit.Event.LOGIN_LOCKED, request, outcome=audit.BLOCKED, identifier=identifier,
                reason="login_locked", retry_after=locked_for,
            )
            return _login_locked_response(locked_for)

        # L1 gap 18 -- after a few wrong passwords ask for a captcha BEFORE looking at
        # the password (a missing / wrong captcha is not counted as a failed login).
        challenge = captcha_guard.enforce(
            request,
            captcha_guard.SCOPE_LOGIN,
            force=guard.failure_count() >= int(getattr(settings, "CAPTCHA_LOGIN_AFTER_FAILURES", 3)),
        )
        if challenge is not None:
            _audit_captcha(request, challenge, captcha_guard.SCOPE_LOGIN, identifier=identifier)
            return challenge

        # L1 gap 5 -- never `.get(Q(username)|Q(email))` (MultipleObjectsReturned
        # when a username equals someone's email); see login/identifiers.py.
        user_obj = find_user_for_password_login(identifier)
        actual_username = user_obj.username if user_obj else identifier.strip()

        user = authenticate(
            username=actual_username,
            password=password,
        )

        if user is None:
            locked_for, attempts_left = guard.record_failure()
            audit.record(
                audit.Event.LOGIN_FAILED, request, outcome=audit.FAILURE, user=user_obj, identifier=identifier,
                reason="invalid_credentials", attempts_left=attempts_left,
            )
            if locked_for:
                audit.record(
                    audit.Event.LOGIN_LOCKED, request, outcome=audit.BLOCKED, user=user_obj, identifier=identifier,
                    reason="login_locked", retry_after=locked_for, tripped_now=True,
                )
                return _login_locked_response(locked_for)
            return api_error(
                "Invalid Username or Password",
                "invalid_credentials",
                status.HTTP_401_UNAUTHORIZED,
                attempts_left=attempts_left,
            )

        guard.record_success()
        # S3 -- 2FA on: password was right, but NO session yet; the app sends the 6-digit code to /auth/2fa/verify/.
        gate = two_factor_gate(request, user, "password")
        if gate is not None:
            return gate
        login_alerts.check_new_device(request, user, method="password")  # before this login's own audit row
        tokens = issue_tokens_for_user(user, request)
        audit.record(audit.Event.LOGIN_SUCCESS, request, user=user, identifier=identifier, method="password")
        return Response(
            {
                "status": True,
                "message": "Login Successful",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                },
                "token": tokens,
            },
            status=status.HTTP_200_OK,
        )


#--------------------------------- signup ----------------------------
class Signup(UnifiedErrorsMixin, GenericAPIView):

    serializer_class = SignupSerializer

    authentication_classes = []
    permission_classes = []
    throttle_classes = [SignupThrottle]  # P-B3

    def post(self, request):

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # 🔥 FIX — pehle yeh view koi token return nahi karta tha (Login aur
        # GoogleAuthView dono karte hain), matlab naya signed-up user
        # "logged in" state mein nahi aata tha — client ko turant ek alag
        # `Login` call karni padti, jisme dobara password bhejna padta
        # (awkward — signup form ke paas already password hai). Ab
        # consistent hai: signup khud hi refresh+access token de deta hai.
        # signup khud hi refresh+access token de deta hai (aur ab
        # sliding-expiry AuthToken session row bhi — issue_tokens_for_user
        # dono ek saath karta hai, see token_issuance.py).
        tokens = issue_tokens_for_user(user, request)
        audit.record(
            audit.Event.SIGNUP, request, user=user, identifier=user.phone or user.email,
            method="phone" if user.phone else "email",
        )

        return Response(
            {
                "status": True,
                "message": "Account Created Successfully",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                },
                "token": tokens,
            },
            status=status.HTTP_201_CREATED
        )


#--------------------------------- Google login / signup ----------------------------
class GoogleAuthView(UnifiedErrorsMixin, APIView):
    """
    Handles BOTH Google signup and Google login through a single endpoint.
    - Flutter sends the Google `idToken`.
    - We verify it directly with Google using GOOGLE_CLIENT_ID from .env.
    - If the email is new -> account created (signup).
    - If the email already exists -> normal login.
    - `phone` is not provided by Google, so new accounts are created
      with phone empty; the app should then call /complete-profile/.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [GoogleAuthThrottle]  # P-B3
    serializer_class = GoogleLoginSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["id_token"]

        # T1 - verification ek shared helper me: 5 s timeout, certs cache, multi client-id, clean 503 / 401.
        try:
            idinfo = google_verify.verify_google_token(token)
        except google_verify.GoogleVerifyError as exc:
            if exc.code != "google_not_configured":  # server config problem is not a user "login failed" event
                audit.record(
                    audit.Event.GOOGLE_FAILED, request, outcome=audit.FAILURE, reason=exc.code,
                )
            return api_error(exc.message, exc.code, exc.status)

        email = idinfo.get("email")
        first_name = idinfo.get("given_name", "")
        last_name = idinfo.get("family_name", "")

        if not email:
            return api_error("Google account has no email", "google_no_email", status.HTTP_400_BAD_REQUEST)

        # ✅ SECURITY: Google idToken includes "email_verified" — if Google
        # itself hasn't verified this email, don't trust it to log someone
        # into (or create) an account under that address.
        if not idinfo.get("email_verified", False):
            audit.record(
                audit.Event.GOOGLE_FAILED, request, outcome=audit.FAILURE, identifier=email,
                reason="google_email_unverified",
            )
            return api_error(
                "This Google account's email is not verified.",
                "google_email_unverified",
                status.HTTP_400_BAD_REQUEST,
            )

        # ✅ Normalize casing so "User@gmail.com" and "user@gmail.com" always
        # resolve to the same account (matches SignupSerializer's behaviour).
        email = email.strip().lower()

        CustomUser = get_user_model()
        google_sub = str(idinfo.get("sub") or "").strip()

        # S10 -- a Google account that was LINKED to an account signs into THAT account even when the e-mail on the
        # account was changed afterwards; otherwise fall back to the old e-mail match.
        user = None
        created = False
        if google_sub:
            linked_row = (
                LinkedIdentity.objects.select_related("user")
                .filter(provider=LinkedIdentity.PROVIDER_GOOGLE, subject=google_sub, revoked_at__isnull=True)
                .first()
            )
            if linked_row is not None and linked_row.user.is_active:
                user = linked_row.user

        try:
            if user is None:
                user = CustomUser.objects.get(email=email)
                # S10 -- the owner unlinked Google: an e-mail match alone must not let it back in.
                if connected_accounts.google_login_blocked(user, google_sub):
                    audit.record(
                        audit.Event.GOOGLE_FAILED, request, outcome=audit.BLOCKED, user=user, identifier=email,
                        reason="google_unlinked",
                    )
                    return api_error(
                        "Google sign-in is turned off for this account. Sign in with your password or a code, "
                        "then link Google again from Settings > Connected accounts.",
                        "google_unlinked",
                        status.HTTP_403_FORBIDDEN,
                    )
            created = False
        except CustomUser.DoesNotExist:
            # 🔧 FIX (this pass, login_app_reference.md §11 item 3) —
            # the `exists()` pre-check below is a fast-path optimisation
            # only, NOT the actual uniqueness guarantee: under
            # concurrent Google sign-ins, two requests can both pass
            # `exists()` for the same `username` (or even both pass
            # `CustomUser.objects.get(email=email)` raising
            # `DoesNotExist` for the same brand-new `email`) before
            # either has called `.create()` — a classic
            # check-then-act race. The DB's own unique constraint
            # (username, and presumably email) is the only thing that
            # can't be raced, so it's now the actual arbiter:
            # `.create()` runs inside `transaction.atomic()` and a
            # resulting `IntegrityError` is caught and resolved instead
            # of propagating as a 500.
            #
            # Two distinct races can produce that `IntegrityError`:
            #   1. Another concurrent Google sign-in for this SAME email
            #      won the race and already inserted its row — re-fetch
            #      by email; if found, that's the real user, this
            #      request was never actually a signup.
            #   2. A completely unrelated signup (any path, not just
            #      Google) grabbed this exact `username` in the gap
            #      between our `exists()` check and our `.create()` —
            #      bump the suffix and retry.
            # `exists()` pre-check is kept as-is (cheap, avoids paying
            # for an `IntegrityError` + retry in the overwhelmingly
            # common non-racy case); the retry loop below is the
            # correctness backstop for when it's wrong.
            # L1 gap 18 -- never CREATE an account on a throw-away mailbox domain
            # (existing accounts above still sign in).
            problem = email_problem(email)
            if problem:
                audit.record(
                    audit.Event.GOOGLE_FAILED, request, outcome=audit.BLOCKED, identifier=email, reason=problem[0],
                )
                return api_error(problem[1], problem[0], status.HTTP_400_BAD_REQUEST)

            base_username = email.split("@")[0]
            username = base_username
            suffix = 1
            while CustomUser.objects.filter(username=username).exists():
                username = f"{base_username}{suffix}"
                suffix += 1

            user = None
            created = False
            max_attempts = 5
            for _ in range(max_attempts):
                try:
                    with transaction.atomic():
                        user = CustomUser.objects.create(
                            email=email,
                            username=username,
                            first_name=first_name,
                            last_name=last_name,
                            is_verified=True,  # Google ne email verify kar di hai
                        )
                    created = True
                    break
                except IntegrityError:
                    existing = CustomUser.objects.filter(email=email).first()
                    if existing is not None:
                        # Race #1 — someone else already created this
                        # exact account between our DoesNotExist check
                        # and now. Treat this request as a login, not a
                        # signup.
                        user = existing
                        created = False
                        break
                    # Race #2 (or a plain non-unique username with no
                    # email collision) — try the next suffix.
                    username = f"{base_username}{suffix}"
                    suffix += 1
            else:
                # Exhausted every attempt above — pathological, sustained
                # contention on the exact same local-part. Fall back to
                # a suffix that can't realistically collide rather than
                # surfacing a 500 to the user.
                username = f"{base_username}{uuid.uuid4().hex[:8]}"
                user = CustomUser.objects.create(
                    email=email,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    is_verified=True,
                )
                created = True

        if created:
            user.set_unusable_password()
            user.save()

        if not created:
            gate = two_factor_gate(request, user, "google")  # S3
            if gate is not None:
                return gate
            login_alerts.check_new_device(request, user, method="google")
        tokens = issue_tokens_for_user(user, request)
        connected_accounts.record_google_login(user, idinfo)  # S10 -- link row for new / pre-S10 accounts (never raises)
        audit.record(
            audit.Event.GOOGLE_SIGNUP if created else audit.Event.GOOGLE_LOGIN,
            request, user=user, identifier=email, method="google",
        )

        return Response(
            {
                "status": True,
                "message": "Signup Successful" if created else "Login Successful",
                "is_new_user": created,
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
            },
            status=status.HTTP_200_OK,
        )


#--------------------------------- complete profile (phone) ----------------------------
class CompleteProfileView(UnifiedErrorsMixin, APIView):
    """User adds their phone number after Google signup.

    L1 gap 4: the number is only saved after the OTP that was sent to it
    (POST /auth/send-otp/ with the same number) is submitted here as `otp`.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = CompleteProfileSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        try:
            check_code(phone, serializer.validated_data["otp"])
        except OTPInvalid as exc:
            _audit_code_failure(
                request, phone, KIND_PHONE, exc.code, purpose="add_phone", user=request.user,
                attempts_left=exc.attempts_left,
                outcome=audit.BLOCKED if exc.code == "otp_locked" else audit.FAILURE,
            )
            return api_error(exc.message, exc.code, exc.status_code, attempts_left=exc.attempts_left)

        user = request.user
        user.phone = phone
        user.phone_verified = True
        try:
            user.save(update_fields=["phone", "phone_verified"])
        except IntegrityError:  # someone else saved this number a moment ago
            return api_error("Phone number already exists.", "phone_exists", status.HTTP_409_CONFLICT)

        audit.record(audit.Event.PHONE_VERIFIED, request, user=user, identifier=phone, kind=KIND_PHONE)

        return Response(
            {
                "status": True,
                "message": "Profile completed successfully.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                },
            },
            status=status.HTTP_200_OK,
        )


class SetDateOfBirthView(UnifiedErrorsMixin, APIView):
    """
    POST /login/auth/set-dob/   {"date_of_birth": "YYYY-MM-DD"}

    One-time DOB entry for accounts that have none (Google signups, and
    accounts created before the minor-safety feature). Once set it can't be
    changed here — otherwise a minor could simply re-enter an adult DOB;
    corrections go through support/admin. Under-18 -> account is forced
    private.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SetDateOfBirthSerializer

    def post(self, request):
        user = request.user
        if user.date_of_birth is not None:
            return api_error(
                "Date of birth is already set and can't be changed here.",
                "dob_already_set",
                status.HTTP_409_CONFLICT,
            )

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        dob = serializer.validated_data["date_of_birth"]

        user.date_of_birth = dob
        fields = ["date_of_birth"]
        if user.is_minor and not user.is_private:
            user.is_private = True
            fields.append("is_private")
        user.save(update_fields=fields)

        return Response(
            {
                "status": True,
                "message": "Date of birth saved.",
                "is_minor": user.is_minor,
                "is_private": user.is_private,
            },
            status=status.HTTP_200_OK,
        )

#----------------------------    otp verification  -----------------
class SendOTPView(UnifiedErrorsMixin, APIView):
    serializer_class = SendOTPSerializer

    authentication_classes = []
    permission_classes = []

    # Coarse per-IP burst limit (settings.py -> "send_otp": "5/min"). The real
    # per-number limits (30s cooldown, 3/hour, per-IP hourly cap) are enforced
    # server-side in otp_service.reserve_send -- not just by the app's timer.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "send_otp"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)  # -> {"detail","code":"validation_error","errors"}

        target, kind = normalize_identifier(serializer.validated_data["email_or_phone"])
        if kind == KIND_INVALID:
            return _invalid_identifier_response()
        is_email = kind == "email"

        # L1 gap 18 -- a NEW email / phone must be a real one: no throw-away mailbox
        # domains, no fake / virtual numbers. An identifier that already belongs to an
        # account is left alone, so OTP login keeps working for older accounts.
        owner = find_user_by_identifier(target, kind)
        owner_exists = owner is not None
        if not owner_exists:
            problem = new_identifier_problem(target, kind)
            if problem:
                audit.record(
                    audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, identifier=target, kind=kind,
                    reason=problem[0], purpose="verify",
                )
                return api_error(problem[1], problem[0], status.HTTP_400_BAD_REQUEST)

        # L1 gap 18 -- captcha, only when this IP is sending codes at a bot-like rate
        # (or CAPTCHA_MODE=always). Answered before any code is made or SMS paid for.
        challenge = captcha_guard.enforce(request, captcha_guard.SCOPE_SEND_OTP)
        if challenge is not None:
            _audit_captcha(request, challenge, captcha_guard.SCOPE_SEND_OTP, identifier=target, user=owner)
            return challenge

        # L1 gap 18 -- optional paid carrier lookup (VoIP / virtual numbers), only for a
        # new number that got past the captcha. Cached and fail-open.
        if not owner_exists and kind == KIND_PHONE:
            problem = phone_lookup_problem(target)
            if problem:
                audit.record(
                    audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, identifier=target, kind=kind,
                    reason=problem[0], purpose="verify",
                )
                return api_error(problem[1], problem[0], status.HTTP_400_BAD_REQUEST)

        # L1 gap 3: cooldown + hourly cap + IP cap, BEFORE any code is made.
        try:
            ticket = reserve_send(target, client_ip(request))
        except OTPRateLimited as exc:
            audit.record(
                audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, user=owner, identifier=target,
                kind=kind, reason=exc.code, purpose="verify", retry_after=exc.retry_after,
            )
            return _otp_limited_response(exc)

        try:
            otp_code = issue_code(target)
            deliver_code(target, is_email, otp_code)
        except OTPDeliveryError:
            # Provider down / misconfigured: don't leak internals, don't pretend
            # it worked, and don't charge the user a cooldown for OUR failure.
            refund_send(ticket)
            audit.record(
                audit.Event.OTP_SEND_FAILED, request, outcome=audit.FAILURE, user=owner, identifier=target,
                kind=kind, reason="otp_delivery_failed", purpose="verify",
            )
            return api_error(
                "Could not send OTP right now. Please try again.",
                "otp_delivery_failed",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        audit.record(
            audit.Event.OTP_SENT, request, user=owner, identifier=target, kind=kind,
            purpose="verify", existing_account=owner_exists,
        )

        # SECURITY: the OTP is never in the response -- only in the email/SMS.
        # L1 gap 14: also tell the app the rules of this code (how many wrong
        # tries it allows, how long it lives) so the OTP screen can show
        # "5 attempts left" from the very first look.
        return Response({
            "status": "success",
            "message": "OTP sent successfully. Please check your inbox.",
            "resend_after": int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)),
            "max_attempts": OTPVerification.MAX_ATTEMPTS,
            "expires_in": OTPVerification.EXPIRY_MINUTES * 60,
        }, status=status.HTTP_200_OK)


class VerifyOTPView(UnifiedErrorsMixin, APIView):
    serializer_class = VerifyOTPSerializer

    # Pre-login endpoints: caller has no JWT yet. DEFAULT_PERMISSION_CLASSES is
    # IsAuthenticated, so without this signup / OTP login / forgot flow 401s.
    authentication_classes = []
    permission_classes = []

    # ✅ SECURITY: 6-digit OTP has only 1M combinations — without a rate
    # limit + attempt lock, it's brute-forceable. Set
    # REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["verify_otp"] = "10/min" in settings.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_otp"

    def post(self, request):
        from django.contrib.auth import get_user_model
        CustomUser = get_user_model()

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)  # -> {"detail","code":"validation_error","errors"}

        # L1 gap 5: same normalisation as SendOTPView, so the OTP row key and
        # the account lookup below always agree (see login/identifiers.py).
        email_or_phone, kind = normalize_identifier(serializer.validated_data['email_or_phone'])
        is_email = kind == "email"
        user_otp = serializer.validated_data['otp']

        try:
            otp_obj = OTPVerification.objects.get(target=email_or_phone)

            if otp_obj.is_expired():
                otp_obj.delete()
                _audit_code_failure(request, email_or_phone, kind, "otp_expired", purpose="verify")
                return _otp_error("OTP has expired.", "otp_expired")

            if otp_obj.is_locked():
                otp_obj.delete()
                _audit_code_failure(
                    request, email_or_phone, kind, "otp_locked", purpose="verify",
                    attempts_left=0, outcome=audit.BLOCKED,
                )
                return _otp_error(
                    "Too many incorrect attempts. Please request a new OTP.",
                    "otp_locked",
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    attempts_left=0,
                )

            # ✅ SECURITY: hashed compare, plaintext otp field ab exist hi nahi karti
            if not otp_obj.check_otp(user_otp):
                # L1 gap 14: the answer says how many tries are left.
                response = _wrong_code_response(otp_obj, "OTP")
                _audit_code_failure(
                    request, email_or_phone, kind, "otp_invalid", purpose="verify",
                    attempts_left=otp_obj.attempts_left(),
                )
                return response

            # L1 gap 5: match ONLY the account that owns this email/phone --
            # never `username`, which anyone could set to someone else's email.
            matched_user = find_user_by_identifier(email_or_phone, kind)

            if matched_user is not None:
                # Task 16 — CONFIRMED INTENDED: this is passwordless
                # "login via OTP" (a deliberate alternate login method,
                # same family of feature as WhatsApp/Telegram/most
                # consumer social apps — proving you control the
                # email/phone IS the auth factor here, same as it is
                # for the signup branch below and for GoogleAuthView's
                # email-verified check). It is not a silent bypass of a
                # *forgotten* password — the user never has to know or
                # touch their password to use it, by design.
                #
                # The actual risk isn't "should this exist", it's "does
                # the account owner find out an OTP login happened" —
                # unlike a password change, this leaves no trace the
                # owner would otherwise notice. So: if the account has a
                # password set (i.e. it isn't a Google-only account —
                # see GoogleAuthView's set_unusable_password()), email
                # them a heads-up after the fact. Best-effort: a failed
                # notification must never block a legitimate login.
                #
                # (B-7) This is also, deliberately, still NOT the
                # forgot-password flow — it never asks for or sets a new
                # password. That's now ForgotPasswordView/ResetPasswordView
                # further down this file, kept fully separate so a correct
                # OTP here keeps meaning exactly one thing: "log this
                # session in."
                user = matched_user
                # S3 -- 2FA on: the one-time code proved phone / e-mail ownership, but a session still needs the
                # authenticator code. Burn the OTP now (no replay), mark the phone verified, ask for step 2.
                if two_factor_enabled_for(user):
                    otp_obj.delete()
                    if kind == KIND_PHONE and not user.phone_verified:
                        user.phone_verified = True
                        user.save(update_fields=["phone_verified"])
                    gate = two_factor_gate(request, user, "otp")
                    if gate is not None:
                        return gate
                login_alerts.check_new_device(request, user, method="otp")
                tokens = issue_tokens_for_user(user, request)
                # ✅ SECURITY: OTP consume ho gaya, dobara replay use nahi ho sakta
                otp_obj.delete()

                # L1 gap 4: a correct code sent to this phone IS proof the
                # owner controls it -- mark a legacy unverified phone verified.
                phone_newly_verified = kind == KIND_PHONE and not user.phone_verified
                if phone_newly_verified:
                    user.phone_verified = True
                    user.save(update_fields=["phone_verified"])
                audit.record(
                    audit.Event.OTP_LOGIN, request, user=user, identifier=email_or_phone, kind=kind,
                    method="otp", phone_newly_verified=phone_newly_verified,
                )

                if user.has_usable_password() and user.email:
                    try:
                        send_mail(
                            subject="New sign-in to your account",
                            message=(
                                f"Your account was just signed into using a "
                                f"one-time code sent to {email_or_phone}. "
                                "If this wasn't you, change your password "
                                "immediately."
                            ),
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=True,
                        )
                    except Exception:
                        logger.exception(
                            "OTP-login notification email failed for user_id=%s",
                            user.id,
                        )

                return Response({
                    "status": "success",
                    "user_exists": True,
                    "message": "Login Successful!",
                    "access": tokens["access"],
                    "refresh": tokens["refresh"],
                    # L1 gap 12: same nested shape as Login/Signup, so the app's
                    # phone-first screen can store the session the same way.
                    "token": tokens,
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone,
                    },
                }, status=status.HTTP_200_OK)

            else:
                # Task 15 — this is the fact SignupSerializer.validate()
                # now checks server-side (see serializers.py): mark this
                # row verified instead of just leaving it sitting there
                # unconsumed and trusting the frontend to call /signup/
                # next. Still not deleted here — /signup/ is what
                # consumes (deletes) it, same "let it live a little
                # longer for the next step" reasoning as before — it
                # naturally expires via is_expired() either way if
                # signup is never completed.
                otp_obj.is_verified = True
                otp_obj.save(update_fields=["is_verified"])
                audit.record(
                    audit.Event.OTP_VERIFIED, request, identifier=email_or_phone, kind=kind, purpose="signup",
                )
                return Response({
                    "status": "success",
                    "user_exists": False,
                    "message": "OTP Verified Successfully! Please complete your registration.",
                }, status=status.HTTP_200_OK)

        except OTPVerification.DoesNotExist:
            _audit_code_failure(request, email_or_phone, kind, "otp_not_found", purpose="verify")
            return _otp_error("No OTP request found for this identifier.", "otp_not_found")


#------------------------------------  change password   ------------------------------------

class ChangePasswordAPIView(UnifiedErrorsMixin, APIView):

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer
    # S3 -- the current password is guessable by anybody holding an unlocked phone, so the number of tries is capped.
    # X6 -- writes only (+ an audit row when refused); wrong CURRENT passwords are also counted by ReauthGuard below.
    throttle_classes = [SensitiveWriteThrottle]
    throttle_scope = "password_change"

    def post(self, request, *args, **kwargs):

        # L1 gap 15: the user is passed in so Django's validators can reject a
        # password that is too similar to THIS account's username / name / email
        # (and apply the staff policy to moderators).
        serializer = self.serializer_class(data=request.data, context={"user": request.user})

        serializer.is_valid(raise_exception=True)  # -> {"detail","code":"validation_error","errors"}

        user = request.user
        new_password = serializer.validated_data["new_password"]

        # S3 -- re-auth: whoever holds a live session must still know the CURRENT password (a stolen unlocked
        # phone must not be able to lock the owner out). Google-only accounts have no password to check.
        # settings.CHANGE_PASSWORD_REQUIRE_CURRENT = False only for the days before the new app build is out.
        if user.has_usable_password() and getattr(settings, "CHANGE_PASSWORD_REQUIRE_CURRENT", True):
            current = serializer.validated_data.get("current_password") or ""
            if not current:
                return api_error(
                    "Enter your current password.", "current_password_required", status.HTTP_400_BAD_REQUEST
                )
            # X6 -- a stolen unlocked phone must not be able to guess the current password at throttle speed:
            # after REAUTH_MAX_FAILURES wrong tries the action is locked (even for the right password) for a while.
            guard = ReauthGuard(user, "password_change")
            wait = guard.retry_after()
            if wait:
                return retry_error(
                    "Too many wrong passwords. Try again later or use Forgot password.",
                    "too_many_attempts", wait,
                )
            if not user.check_password(current):
                locked_for, attempts_left = guard.record_failure()
                audit.record(
                    audit.Event.PASSWORD_CHANGE_FAILED, request, outcome=audit.FAILURE, user=user,
                    reason="bad_current_password", attempts_left=attempts_left,
                )
                if locked_for:
                    audit.record(
                        audit.Event.REAUTH_LOCKED, request, outcome=audit.BLOCKED, user=user,
                        reason="password_change", retry_after=locked_for,
                    )
                    return retry_error(
                        "Too many wrong passwords. Try again later or use Forgot password.",
                        "too_many_attempts", locked_for,
                    )
                return api_error("Current password is wrong.", "invalid_password", status.HTTP_400_BAD_REQUEST)
            guard.clear()

        if user.check_password(new_password):
            return api_error(
                "New password cannot be same as current password.",
                "password_unchanged",
                status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # L1 gap 1: a password change must end every OTHER session (a stolen or
        # forgotten-on-a-friend's-phone login would otherwise survive it). The
        # session making this request stays signed in.
        current_jti = None
        if request.auth is not None and hasattr(request.auth, "get"):
            current_jti = request.auth.get("session_jti")
        logged_out = revoke_user_sessions(user, keep_jti=current_jti)
        audit.record(audit.Event.PASSWORD_CHANGED, request, user=user, sessions_ended=logged_out)

        return Response(
            {
                "status": True,
                "message": "Password changed successfully.",
                "other_sessions_logged_out": logged_out,
            },
            status=status.HTTP_200_OK,
        )

#------------------------------------  forgot / reset password (B-7)  ------------------------------------
#
# FIX — there was no dedicated "forgot password" flow. The only path that
# looked like one was VerifyOTPView's "user_exists" branch above, which is
# by-design passwordless OTP-login (see Task 16's note right there) — it
# logs the user in on a correct OTP, it never asks for or sets a new
# password. Using it as a password-reset substitute would mean an OTP
# alone both proves identity AND silently hands out a session, with no
# step where the account owner actually sets a new password — not what
# "I forgot my password" should do, and not something to bolt onto an
# endpoint that already has a different, intentional job.
#
# Added instead: a proper two-step flow that never returns a session —
#   1. ForgotPasswordView — request a reset code for a *known* account.
#   2. ResetPasswordView  — spend that code, in the same request as the
#      new password, to actually change it.
# Both reuse the existing OTPVerification model and its hash/expiry/
# lockout machinery (same as SendOTPView/VerifyOTPView above) rather than
# inventing a second OTP mechanism.
class ForgotPasswordView(UnifiedErrorsMixin, APIView):
    """
    Step 1: request a password-reset code for an *existing* account.

    Deliberately NOT the same view as SendOTPView, even though the body
    of this method mirrors it closely. SendOTPView is generic — used by
    signup (where the target is expected to be new) and by OTP-login —
    and sends a code regardless of whether an account exists for that
    target. That's fine there; a nonexistent target simply can't finish
    signup with it. Here it would be an account-enumeration oracle
    ("submit an email, get back whether it has an account" via whether a
    code arrives) — so this view only actually sends a code when an
    account exists, and always returns the same generic response either
    way, so a caller can't tell the difference from the response alone.
    """

    serializer_class = ForgotPasswordSerializer
    authentication_classes = []
    permission_classes = []

    # ✅ SECURITY: same reasoning as SendOTPView's throttle above — set
    # REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["forgot_password"] = "5/min"
    # in settings.py.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "forgot_password"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)  # -> {"detail","code":"validation_error","errors"}

        target, kind = normalize_identifier(serializer.validated_data["email_or_phone"])
        if kind == KIND_INVALID:
            return _invalid_identifier_response()
        is_email = kind == "email"

        # L1 gap 18 -- captcha by request VOLUME only (never by whether an account
        # exists), so it can't be used to find out who is registered.
        challenge = captcha_guard.enforce(request, captcha_guard.SCOPE_FORGOT_PASSWORD)
        if challenge is not None:
            _audit_captcha(request, challenge, captcha_guard.SCOPE_FORGOT_PASSWORD, identifier=target)
            return challenge

        # L1 gap 3: the resend limits apply whether or not an account exists, and
        # BEFORE we look it up -- otherwise "429 vs 200" would leak which
        # emails/phones are registered.
        try:
            ticket = reserve_send(target, client_ip(request))
        except OTPRateLimited as exc:
            audit.record(
                audit.Event.OTP_SEND_BLOCKED, request, outcome=audit.BLOCKED, identifier=target, kind=kind,
                reason=exc.code, purpose="reset", retry_after=exc.retry_after,
            )
            return _otp_limited_response(exc)

        # L1 gap 5: only the account that owns this email/phone (never username).
        owner = find_user_by_identifier(target, kind)
        user_exists = owner is not None

        # Same response whether or not an account exists — see docstring.
        generic_response = Response(
            {
                "status": "success",
                "message": "If an account exists for that email/phone, a reset code has been sent.",
                "resend_after": int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)),
            },
            status=status.HTTP_200_OK,
        )

        if not user_exists:
            # The client can't tell (same response); the audit log can -- a burst of these is
            # someone probing for registered emails / phones.
            audit.record(
                audit.Event.RESET_REQUESTED, request, outcome=audit.FAILURE, identifier=target, kind=kind,
                reason="no_account",
            )
            return generic_response

        try:
            otp_code = issue_code(target)
            deliver_code(target, is_email, otp_code, purpose=PURPOSE_RESET)
        except OTPDeliveryError:
            # Same posture as SendOTPView: don't leak internals, don't pretend
            # it succeeded, and don't charge a cooldown for OUR failure.
            refund_send(ticket)
            audit.record(
                audit.Event.OTP_SEND_FAILED, request, outcome=audit.FAILURE, user=owner, identifier=target,
                kind=kind, reason="otp_delivery_failed", purpose="reset",
            )
            return api_error(
                "Could not send reset code right now. Please try again.",
                "otp_delivery_failed",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        audit.record(audit.Event.RESET_REQUESTED, request, user=owner, identifier=target, kind=kind)
        return generic_response


class ResetPasswordView(UnifiedErrorsMixin, APIView):
    """
    Step 2: verify the code from ForgotPasswordView and set a new
    password, in the same request.

    Deliberately not built on top of VerifyOTPView — see the module note
    above this class. A correct OTP here proves control of the
    email/phone, exactly like everywhere else OTP is used in this app,
    and the *only* thing it authorizes is setting a new password. It
    never issues tokens and never logs the caller in; the user is
    expected to log in normally afterwards with the new password, same
    as after ChangePasswordAPIView.
    """

    serializer_class = ResetPasswordSerializer
    authentication_classes = []
    permission_classes = []

    # ✅ SECURITY: brute-force protection on the OTP guess, same reasoning
    # as VerifyOTPView's throttle above. Set
    # REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["reset_password"] = "10/min"
    # in settings.py.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reset_password"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)  # -> {"detail","code":"validation_error","errors"}

        # L1 gap 5: same normalisation as ForgotPasswordView.
        email_or_phone, kind = normalize_identifier(serializer.validated_data["email_or_phone"])
        is_email = kind == "email"
        user_otp = serializer.validated_data["otp"]
        new_password = serializer.validated_data["new_password"]

        CustomUser = get_user_model()

        try:
            otp_obj = OTPVerification.objects.get(target=email_or_phone)

            if otp_obj.is_expired():
                otp_obj.delete()
                _audit_code_failure(
                    request, email_or_phone, kind, "otp_expired", purpose="reset", event=audit.Event.RESET_FAILED,
                )
                return _otp_error("Reset code has expired.", "otp_expired")

            if otp_obj.is_locked():
                otp_obj.delete()
                _audit_code_failure(
                    request, email_or_phone, kind, "otp_locked", purpose="reset", event=audit.Event.RESET_FAILED,
                    attempts_left=0, outcome=audit.BLOCKED,
                )
                return _otp_error(
                    "Too many incorrect attempts. Please request a new code.",
                    "otp_locked",
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    attempts_left=0,
                )

            # ✅ SECURITY: hashed compare, same as VerifyOTPView.
            if not otp_obj.check_otp(user_otp):
                response = _wrong_code_response(otp_obj, "reset code")
                _audit_code_failure(
                    request, email_or_phone, kind, "otp_invalid", purpose="reset", event=audit.Event.RESET_FAILED,
                    attempts_left=otp_obj.attempts_left(),
                )
                return response

            # L1 gap 5: only the account that owns this email/phone.
            user = find_user_by_identifier(email_or_phone, kind)
            if user is None:
                # Account could have been deleted between the request-code
                # and reset steps. Don't distinguish this from "bad code"
                # in the response — same don't-leak posture as everywhere
                # else in this flow.
                otp_obj.delete()
                audit.record(
                    audit.Event.RESET_FAILED, request, outcome=audit.FAILURE, identifier=email_or_phone, kind=kind,
                    reason="reset_code_invalid", purpose="reset",
                )
                return api_error(
                    "Invalid or expired reset code.",
                    "reset_code_invalid",
                    status.HTTP_400_BAD_REQUEST,
                )

            # L1 gap 15: now that the account is known, run Django's full
            # validators WITH the user (too-similar-to-username/name/email,
            # staff policy, phone number inside the password). The code is
            # NOT spent on a weak password, so the user can just pick another.
            pairs = password_problem_pairs(new_password, user=user)
            problems = [m for m, _c in pairs]
            if problems:
                audit.record(
                    audit.Event.RESET_FAILED, request, outcome=audit.FAILURE, user=user, identifier=email_or_phone,
                    kind=kind, reason="weak_password", purpose="reset",
                )
                return api_error(
                    problems[0],
                    "weak_password",
                    status.HTTP_400_BAD_REQUEST,
                    errors={"new_password": problems},
                    error_codes={"new_password": [c for _m, c in pairs]},
                )

            user.set_password(new_password)
            user.save(update_fields=["password"])

            # L1 gap 1: a reset means "I may have lost control of this account",
            # so EVERY existing session (all devices) is ended -- old access and
            # refresh tokens stop working. The user signs in again with the new
            # password.
            sessions_ended = revoke_user_sessions(user)
            logger.info("password reset: user_id=%s sessions_ended=%s", user.id, sessions_ended)
            audit.record(
                audit.Event.RESET_SUCCESS, request, user=user, identifier=email_or_phone, kind=kind,
                sessions_ended=sessions_ended,
            )

            # L1 gap 6: if they were locked out of password login, the reset
            # is the way back in -- clear that lock for this account.
            LoginGuard.unlock(request, [user.username, user.email, user.phone])

            # ✅ SECURITY: code consumed, can't be replayed for a second reset.
            otp_obj.delete()

            # Best-effort notice — mirrors VerifyOTPView's OTP-login email.
            # A password reset is exactly the kind of event the real
            # account owner should hear about, even though they're
            # presumably the one who triggered it, in case they weren't.
            if user.email:
                try:
                    send_mail(
                        subject="Your password was reset",
                        message=(
                            "Your account password was just reset using a "
                            "one-time code. If this wasn't you, contact "
                            "support immediately."
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception:
                    logger.exception(
                        "Password-reset notification email failed for user_id=%s",
                        user.id,
                    )

            return Response(
                {
                    "status": "success",
                    "message": "Password has been reset successfully. Please log in with your new password.",
                },
                status=status.HTTP_200_OK,
            )

        except OTPVerification.DoesNotExist:
            _audit_code_failure(
                request, email_or_phone, kind, "otp_not_found", purpose="reset", event=audit.Event.RESET_FAILED,
            )
            return _otp_error("No reset code request found for this identifier.", "otp_not_found")


# ----------------------------- T0.8 account deletion -----------------------------


from rest_framework.permissions import IsAuthenticated as _IsAuth  # noqa: E402
from rest_framework.views import APIView as _APIView  # noqa: E402


class DeleteAccountView(UnifiedErrorsMixin, _APIView):
    """
    GET  /login/auth/delete-account/  -> preflight {can_delete, blocking_reasons, requires_password, coin_balance}
    POST /login/auth/delete-account/  {"password": "...", "confirm": "DELETE"}   (password only if the
        account has one; Google-only accounts just send confirm). Irreversible — see account_deletion.py.
    """
    permission_classes = [_IsAuth]

    def get(self, request):
        from . import data_rights
        from .account_deletion import blocking_reasons

        reasons = blocking_reasons(request.user)
        return Response({
            "status": True,
            "can_delete": not reasons,
            "blocking_reasons": reasons,
            "requires_password": request.user.has_usable_password(),
            "coin_balance": getattr(request.user, "coin", 0),
            # S5 -- grace period + what goes / what stays (same text the Data & privacy screen shows)
            "grace_days": data_rights.deletion_grace_days(),
            "deletion": data_rights.deletion_status(request.user),
            "erasure": data_rights.erasure_summary(),
        })

    def post(self, request):
        from .account_deletion import blocking_reasons, delete_account

        user = request.user
        if str(request.data.get("confirm", "")).strip().upper() != "DELETE":
            return api_error("Type DELETE to confirm.", "delete_confirm_required", status.HTTP_400_BAD_REQUEST)
        if user.has_usable_password():
            pw = request.data.get("password") or ""
            if not user.check_password(pw):
                audit.record(
                    audit.Event.ACCOUNT_DELETE_FAILED, request, outcome=audit.FAILURE, user=user,
                    reason="incorrect_password",
                )
                return api_error("Incorrect password.", "incorrect_password", status.HTTP_403_FORBIDDEN)
        reasons = blocking_reasons(user)
        if reasons:
            return api_error(reasons[0], "delete_blocked", status.HTTP_409_CONFLICT, blocking_reasons=reasons)
        # S5 -- DPDP grace period. ACCOUNT_DELETION_GRACE_DAYS = 0 keeps the old T0.8 behaviour (erase now);
        # otherwise the account keeps working for N days (login + cancel allowed) and the daily sweeper
        # (login.run_due_account_deletions) erases it.
        from . import data_rights

        if data_rights.deletion_grace_days() > 0:
            already = data_rights.pending_deletion(user) is not None
            req = data_rights.schedule_deletion(user)
            if not already:
                audit.record(audit.Event.ACCOUNT_DELETION_SCHEDULED, request, user=user)
                data_rights.notify_deletion_scheduled(req)
            info = data_rights.deletion_status(user)
            return Response({
                "status": True,
                "scheduled": True,
                "scheduled_for": info.get("scheduled_for"),
                "days_left": info.get("days_left"),
                "grace_days": data_rights.deletion_grace_days(),
                "message": "Your account will be deleted after the waiting period. You can cancel any time before that.",
            })

        delete_account(user)
        # No identifier on purpose: delete_account() just blanked everything personal in the
        # audit trail for this user; this row only says "account X was deleted, from this device".
        audit.record(audit.Event.ACCOUNT_DELETED, request, user=user)
        return Response({"status": True, "scheduled": False, "message": "Your account has been deleted."})


#--------------------------------- captcha challenge (L1 gap 18) ----------------------------
class CaptchaChallengeView(UnifiedErrorsMixin, APIView):
    """
    GET /login/auth/captcha/  ->  a fresh challenge for the app to show.

    The login endpoints answer ``captcha_required`` WITH a challenge already inside
    (``captcha``), so this is only needed for the "refresh" button. Built-in provider:
    ``{"provider": "builtin", "captcha_id", "image": "data:image/png;base64,...",
    "expires_in"}``. Remote providers: ``{"provider": "turnstile", "site_key": ...}``.
    ``mode`` is "off" when captcha is switched off (no image is made then).
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "captcha"

    def get(self, request):
        current = captcha_guard.mode()
        if current == captcha_guard.MODE_OFF:
            return Response({"mode": current, "provider": captcha_guard.provider()}, status=status.HTTP_200_OK)
        body = captcha_guard.challenge_payload()
        body["mode"] = current
        return Response(body, status=status.HTTP_200_OK)

