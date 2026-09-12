# login/view.py
import logging

from django.contrib.auth import authenticate
from rest_framework.generics import GenericAPIView
from django.utils import timezone
import secrets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated
from .serializers import ChangePasswordSerializer
from .models import OTPVerification
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

# Google token verification
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)


#----------------    login   ------------------------------------------
class Login(GenericAPIView):
    serializer_class = LoginSerializer
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        summary="User Login",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Login Successful"),
            401: OpenApiResponse(description="Invalid Credentials"),
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username_or_email = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        CustomUser = get_user_model()
        try:
            user_obj = CustomUser.objects.get(
                Q(username=username_or_email) | Q(email=username_or_email)
            )
            actual_username = user_obj.username
        except CustomUser.DoesNotExist:
            actual_username = username_or_email

        user = authenticate(
            username=actual_username,
            password=password,
        )

        if user is None:
            return Response(
                {
                    "status": False,
                    "message": "Invalid Username or Password",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
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
                "token": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_200_OK,
        )


#--------------------------------- signup ----------------------------
class Signup(GenericAPIView):

    serializer_class = SignupSerializer

    authentication_classes = []
    permission_classes = []

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
        refresh = RefreshToken.for_user(user)

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
                "token": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_201_CREATED
        )


#--------------------------------- Google login / signup ----------------------------
class GoogleAuthView(APIView):
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
    serializer_class = GoogleLoginSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["id_token"]

        if not settings.GOOGLE_CLIENT_ID:
            return Response(
                {"status": False, "message": "Google Sign-In is not configured on the server."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            idinfo = google_id_token.verify_oauth2_token(
                token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
            )
        except ValueError:
            return Response(
                {"status": False, "message": "Invalid Google token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        email = idinfo.get("email")
        first_name = idinfo.get("given_name", "")
        last_name = idinfo.get("family_name", "")

        if not email:
            return Response(
                {"status": False, "message": "Google account has no email"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ SECURITY: Google idToken includes "email_verified" — if Google
        # itself hasn't verified this email, don't trust it to log someone
        # into (or create) an account under that address.
        if not idinfo.get("email_verified", False):
            return Response(
                {"status": False, "message": "This Google account's email is not verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ Normalize casing so "User@gmail.com" and "user@gmail.com" always
        # resolve to the same account (matches SignupSerializer's behaviour).
        email = email.strip().lower()

        CustomUser = get_user_model()

        try:
            user = CustomUser.objects.get(email=email)
            created = False
        except CustomUser.DoesNotExist:
            # ✅ avoid IntegrityError when two different emails share the
            # same local part (e.g. raj@gmail.com and raj@yahoo.com)
            base_username = email.split("@")[0]
            username = base_username
            suffix = 1
            while CustomUser.objects.filter(username=username).exists():
                username = f"{base_username}{suffix}"
                suffix += 1

            user = CustomUser.objects.create(
                email=email,
                username=username,
                first_name=first_name,
                last_name=last_name,
                is_verified=True,  # Google ne email verify kar di hai
            )
            created = True

        if created:
            user.set_unusable_password()
            user.save()

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "status": True,
                "message": "Signup Successful" if created else "Login Successful",
                "is_new_user": created,
                "phone_missing": not bool(user.phone),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                },
                "token": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_200_OK,
        )


#--------------------------------- complete profile (phone) ----------------------------
class CompleteProfileView(APIView):
    """User adds their phone number after Google signup."""

    permission_classes = [IsAuthenticated]
    serializer_class = CompleteProfileSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.phone = serializer.validated_data["phone"]
        user.save(update_fields=["phone"])

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


#----------------------------    otp verification  -----------------

class SendOTPView(APIView):
    serializer_class = SendOTPSerializer

    # ✅ SECURITY: OTP request rate-limit (settings.py me REST_FRAMEWORK
    # ["DEFAULT_THROTTLE_RATES"]["send_otp"] = "5/min" jaisa kuch set karo)
    # taaki koi ek target/IP ko baar baar OTP bhej ke spam/abuse na kare.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "send_otp"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target = serializer.validated_data['email_or_phone'].strip()
        is_email = '@' in target
        if is_email:
            target = target.lower()

        # ✅ SECURITY: `random` module is not cryptographically secure.
        # `secrets` uses the OS's CSPRNG — correct choice for anything
        # security-sensitive like an OTP.
        otp_code = str(secrets.randbelow(900000) + 100000)

        otp_obj, _ = OTPVerification.objects.update_or_create(
            target=target,
            defaults={'created_at': timezone.now()}
        )
        # ✅ SECURITY: hash store hota hai, raw OTP kabhi DB me nahi jaata
        otp_obj.set_otp(otp_code)
        otp_obj.save(update_fields=["otp_hash", "attempts"])

        if is_email:
            try:
                send_mail(
                    subject="Your verification code",
                    message=(
                        f"Your verification code is {otp_code}. "
                        f"It expires in {OTPVerification.EXPIRY_MINUTES} minutes. "
                        "Do not share this code with anyone."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[target],
                    fail_silently=False,
                )
            except Exception:
                # Email backend down / misconfigured -> don't leak internals,
                # but don't pretend it succeeded either.
                return Response(
                    {"status": "error", "message": "Could not send OTP right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        else:
            # ✅ Task 14 — wired up (was a hardcoded 501 before). Delivery
            # goes through MSG91 (see login/sms_service.py for why MSG91
            # over Twilio — the project already has an MSG91 account for
            # liveclass notifications). The OTP itself is unchanged: same
            # `secrets`-generated code, same hash stored above, MSG91 is
            # purely the delivery channel — so VerifyOTPView needs zero
            # changes to handle this path.
            try:
                send_otp_sms(target, otp_code)
            except SMSDeliveryError:
                # Same shape as the email failure path just above: don't
                # leak provider internals, don't pretend it succeeded.
                return Response(
                    {"status": "error", "message": "Could not send OTP right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        # ✅ SECURITY: response me ab OTP kahin nahi hai — sirf email/SMS me jaata hai
        return Response({
            "status": "success",
            "message": "OTP sent successfully. Please check your inbox.",
        }, status=status.HTTP_200_OK)


class VerifyOTPView(APIView):
    serializer_class = VerifyOTPSerializer

    # ✅ SECURITY: 6-digit OTP has only 1M combinations — without a rate
    # limit + attempt lock, it's brute-forceable. Set
    # REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["verify_otp"] = "10/min" in settings.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_otp"

    def post(self, request):
        from django.contrib.auth import get_user_model
        CustomUser = get_user_model()

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email_or_phone = serializer.validated_data['email_or_phone'].strip()
        is_email = '@' in email_or_phone
        if is_email:
            email_or_phone = email_or_phone.lower()
        user_otp = serializer.validated_data['otp']

        try:
            otp_obj = OTPVerification.objects.get(target=email_or_phone)

            if otp_obj.is_expired():
                otp_obj.delete()
                return Response({"status": "error", "message": "OTP has expired."}, status=status.HTTP_400_BAD_REQUEST)

            if otp_obj.is_locked():
                otp_obj.delete()
                return Response(
                    {"status": "error", "message": "Too many incorrect attempts. Please request a new OTP."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # ✅ SECURITY: hashed compare, plaintext otp field ab exist hi nahi karti
            if not otp_obj.check_otp(user_otp):
                otp_obj.register_failed_attempt()
                return Response({"status": "error", "message": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

            if is_email:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(email=email_or_phone))
            else:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(phone=email_or_phone))

            if user_queryset.exists():
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
                user = user_queryset.first()
                refresh = RefreshToken.for_user(user)
                # ✅ SECURITY: OTP consume ho gaya, dobara replay use nahi ho sakta
                otp_obj.delete()

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
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
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
                return Response({
                    "status": "success",
                    "user_exists": False,
                    "message": "OTP Verified Successfully! Please complete your registration.",
                }, status=status.HTTP_200_OK)

        except OTPVerification.DoesNotExist:
            return Response({"status": "error", "message": "No OTP request found for this identifier."},
                            status=status.HTTP_400_BAD_REQUEST)


#------------------------------------  change password   ------------------------------------

class ChangePasswordAPIView(APIView):

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def post(self, request, *args, **kwargs):

        serializer = self.serializer_class(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        new_password = serializer.validated_data["new_password"]

        if user.check_password(new_password):
            return Response(
                {
                    "status": False,
                    "message": "New password cannot be same as current password."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {
                "status": True,
                "message": "Password changed successfully."
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

class ForgotPasswordView(APIView):
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
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target = serializer.validated_data["email_or_phone"].strip()
        is_email = "@" in target
        if is_email:
            target = target.lower()

        CustomUser = get_user_model()
        if is_email:
            user_exists = CustomUser.objects.filter(Q(username=target) | Q(email=target)).exists()
        else:
            user_exists = CustomUser.objects.filter(Q(username=target) | Q(phone=target)).exists()

        # Same response whether or not an account exists — see docstring.
        generic_response = Response(
            {
                "status": "success",
                "message": "If an account exists for that email/phone, a reset code has been sent.",
            },
            status=status.HTTP_200_OK,
        )

        if not user_exists:
            return generic_response

        # ✅ SECURITY: same CSPRNG choice as SendOTPView — see its comment.
        otp_code = str(secrets.randbelow(900000) + 100000)

        otp_obj, _ = OTPVerification.objects.update_or_create(
            target=target,
            defaults={"created_at": timezone.now()},
        )
        # ✅ SECURITY: hash stored, raw code never persisted — same as SendOTPView.
        otp_obj.set_otp(otp_code)
        otp_obj.save(update_fields=["otp_hash", "attempts"])

        if is_email:
            try:
                send_mail(
                    subject="Your password reset code",
                    message=(
                        f"Your password reset code is {otp_code}. "
                        f"It expires in {OTPVerification.EXPIRY_MINUTES} minutes. "
                        "If you didn't request this, you can safely ignore this "
                        "email — your password will not be changed."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[target],
                    fail_silently=False,
                )
            except Exception:
                # Same posture as SendOTPView: don't leak internals, don't
                # pretend it succeeded.
                return Response(
                    {"status": "error", "message": "Could not send reset code right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        else:
            try:
                send_otp_sms(target, otp_code)
            except SMSDeliveryError:
                return Response(
                    {"status": "error", "message": "Could not send reset code right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return generic_response


class ResetPasswordView(APIView):
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
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email_or_phone = serializer.validated_data["email_or_phone"].strip()
        is_email = "@" in email_or_phone
        if is_email:
            email_or_phone = email_or_phone.lower()
        user_otp = serializer.validated_data["otp"]
        new_password = serializer.validated_data["new_password"]

        CustomUser = get_user_model()

        try:
            otp_obj = OTPVerification.objects.get(target=email_or_phone)

            if otp_obj.is_expired():
                otp_obj.delete()
                return Response(
                    {"status": "error", "message": "Reset code has expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if otp_obj.is_locked():
                otp_obj.delete()
                return Response(
                    {"status": "error", "message": "Too many incorrect attempts. Please request a new code."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # ✅ SECURITY: hashed compare, same as VerifyOTPView.
            if not otp_obj.check_otp(user_otp):
                otp_obj.register_failed_attempt()
                return Response({"status": "error", "message": "Invalid reset code."}, status=status.HTTP_400_BAD_REQUEST)

            if is_email:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(email=email_or_phone))
            else:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(phone=email_or_phone))

            user = user_queryset.first()
            if user is None:
                # Account could have been deleted between the request-code
                # and reset steps. Don't distinguish this from "bad code"
                # in the response — same don't-leak posture as everywhere
                # else in this flow.
                otp_obj.delete()
                return Response(
                    {"status": "error", "message": "Invalid or expired reset code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(new_password)
            user.save(update_fields=["password"])

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
            return Response(
                {"status": "error", "message": "No reset code request found for this identifier."},
                status=status.HTTP_400_BAD_REQUEST,
            )