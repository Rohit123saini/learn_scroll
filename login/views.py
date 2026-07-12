from django.contrib.auth import authenticate
from rest_framework.generics import GenericAPIView
from django.utils import timezone
import random
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth.models import User
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

        # 1. Pehle check karein ki input username hai ya email database mein
        CustomUser = get_user_model()
        try:
            user_obj = CustomUser.objects.get(
                Q(username=username_or_email) | Q(email=username_or_email)
            )
            # Sahi username nikal rahe hain authenticate() ke liye
            actual_username = user_obj.username
        except CustomUser.DoesNotExist:
            actual_username = username_or_email  # Agar nahi mila toh default bhej do (auth handle kar lega)

        # 2. Ab default authenticate function ko sahi username aur password dein
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
        print(refresh)
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

        serializer = self.get_serializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

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




#----------------------------    otp verification  -----------------

class SendOTPView(APIView):
    # ↴ YEH LINE SWAGGER KO INPUT BOX DIKHANE PAR MAJBOOR KAREGI
    serializer_class = SendOTPSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)  # self.serializer_class use karein
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target = serializer.validated_data['email_or_phone']
        otp_code = str(random.randint(100000, 999999))

        OTPVerification.objects.update_or_create(
            target=target,
            defaults={'otp': otp_code, 'created_at': timezone.now()}
        )
        print(otp_code)
        return Response({
            "status": "success",
            "message": "OTP sent successfully.",
            "otp_preview": otp_code
        }, status=status.HTTP_200_OK)


class VerifyOTPView(APIView):
    serializer_class = VerifyOTPSerializer  # Swagger UI support

    def post(self, request):
        from django.contrib.auth import get_user_model
        CustomUser = get_user_model()

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email_or_phone = serializer.validated_data['email_or_phone']
        user_otp = serializer.validated_data['otp']


        try:
            # 1. OTP Table se record read karein
            otp_obj = OTPVerification.objects.get(target=email_or_phone)

            # Expiry Check
            if otp_obj.is_expired():
                otp_obj.delete()
                return Response({"status": "error", "message": "OTP has expired."}, status=status.HTTP_400_BAD_REQUEST)

            # Match Check
            if otp_obj.otp != user_otp:
                return Response({"status": "error", "message": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

            # OTP Match hone ke baad verification record delete kar sakte hain (Optional but secure)
            # otp_obj.delete()

            is_email = '@' in email_or_phone

            if is_email:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(email=email_or_phone))
            else:
                user_queryset = CustomUser.objects.filter(Q(username=email_or_phone) | Q(phone=email_or_phone))

            # 2. Check user existence logic
            print(user_queryset)
            if user_queryset.exists():

                # User already exists -> Login aur Token de do
                user = user_queryset.first()
                refresh = RefreshToken.for_user(user)
                print(refresh)
                return Response({
                    "status": "success",
                    "user_exists": True,
                    "message": "Login Successful!",
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                }, status=status.HTTP_200_OK)

            else:
                # User nahi hai -> Sirf success message do, token MAT do (No user creation here)
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
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        new_password = serializer.validated_data["new_password"]

        # Current password se same password allow nahi karna
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