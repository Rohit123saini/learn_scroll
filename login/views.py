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

# Google token verification
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests


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
                }
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
            # TODO(prod): wire up an SMS provider (e.g. Twilio/MSG91) here for
            # phone targets. Until this is implemented, phone-based OTP has
            # no delivery channel — don't ship this path to production as-is.
            return Response(
                {"status": "error", "message": "Phone OTP delivery is not configured yet."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
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
                user = user_queryset.first()
                refresh = RefreshToken.for_user(user)
                # ✅ SECURITY: OTP consume ho gaya, dobara replay use nahi ho sakta
                otp_obj.delete()
                return Response({
                    "status": "success",
                    "user_exists": True,
                    "message": "Login Successful!",
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                }, status=status.HTTP_200_OK)

            else:
                # ✅ SECURITY: yahan delete NAHI kar rahe — signup flow ka agla
                # step (/signup/) OTP dobara check nahi karta, isliye is entry
                # ko thodi der zinda rehne dena zaroori hai taaki agar user
                # signup form submit karte waqt thoda ruke to fail na ho.
                # Ye apne aap is_expired() se expire ho jayega.
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