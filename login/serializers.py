from rest_framework import serializers
from .models import User
import re


def validate_strong_password(value):
    """
    Shared password-strength rule used by both signup and change-password,
    so the two never silently drift apart.
    """
    if value != value.strip():
        raise serializers.ValidationError(
            "Password cannot start or end with spaces."
        )

    if len(value) < 8:
        raise serializers.ValidationError(
            "Password must be at least 8 characters."
        )

    if not re.search(r"[A-Z]", value):
        raise serializers.ValidationError(
            "Password must contain one uppercase letter."
        )

    if not re.search(r"[a-z]", value):
        raise serializers.ValidationError(
            "Password must contain one lowercase letter."
        )

    if not re.search(r"[0-9]", value):
        raise serializers.ValidationError(
            "Password must contain one number."
        )

    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", value):
        raise serializers.ValidationError(
            "Password must contain one special character."
        )

    return value


#--------------    login -------------------------------------------
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )






#-----------------    signup    ------------------------------------------
class SignupSerializer(serializers.ModelSerializer):

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    phone = serializers.CharField()

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "password",
            "confirm_password",
        ]

    def validate_username(self, value):

        value = value.strip()

        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError(
                "Username already exists."
            )

        return value

    def validate_email(self, value):

        value = value.strip().lower()

        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "Email already exists."
            )

        return value

    def validate_phone(self, value):

        value = value.strip()

        if not value.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain digits only."
            )

        if len(value) > 15:
            raise serializers.ValidationError(
                "Phone number is too long."
            )

        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError(
                "Phone number already exists."
            )

        return value

    def validate_password(self, value):
        return validate_strong_password(value)

    def validate(self, attrs):

        if attrs["password"] != attrs["confirm_password"]:

            raise serializers.ValidationError({

                "confirm_password":
                    "Password and Confirm Password do not match."

            })

        return attrs

    def create(self, validated_data):

        validated_data.pop("confirm_password")

        user = User.objects.create_user(

            username=validated_data["username"],
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data["phone"],
            password=validated_data["password"],
            # ✅ email OTP verify-otp step se pehle hi ho chuka hota hai
            # (signup flow me), isliye account ko verified mark kar rahe hain.
            is_verified=True,

        )

        return user





#----------------- OTP Verification ------------------------------------

class SendOTPSerializer(serializers.Serializer):
    email_or_phone = serializers.CharField(max_length=100, required=True)

class VerifyOTPSerializer(serializers.Serializer):
    email_or_phone = serializers.CharField(max_length=255, required=True)
    otp = serializers.CharField(max_length=6, required=True)


#-------------  change password ------------------------------------------

class ChangePasswordSerializer(serializers.Serializer):

    new_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},required=True
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},required=True
    )

    def validate_new_password(self, value):
        return validate_strong_password(value)

    def validate(self, attrs):

        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({
                "confirm_password":
                    "Password and Confirm Password do not match."
            })

        return attrs




class CompleteProfileSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)

    def validate_phone(self, value):
        value = value.strip()

        if not value.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain digits only."
            )
        if len(value) > 15:
            raise serializers.ValidationError(
                "Phone number must be valid."
            )
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError(
                "Phone number already exists."
            )
        return value


class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)