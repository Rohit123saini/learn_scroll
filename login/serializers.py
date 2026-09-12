# login/serializers.py
from rest_framework import serializers
from .models import User, OTPVerification, phone_validator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
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


def validate_phone_format(value):
    """
    Shared phone-format rule used by both SignupSerializer and
    CompleteProfileSerializer, so the two never silently drift apart —
    same spirit as validate_strong_password above.

    Reuses login.models.phone_validator (the same RegexValidator declared
    on User.phone) instead of a separate isdigit()+len() check. Before
    this, the old serializer-level check (plain isdigit(), max 15 chars)
    was LOOSER than the model's validator (which requires 8-15 digits,
    no leading zero, optional leading '+') — since these serializers
    override `phone` with a plain CharField(), the model's validators
    never actually ran (DRF only auto-attaches a model field's
    validators when it builds the field itself, not when you redeclare
    it). That gap meant bad phone numbers (too short, leading zero)
    could reach the DB without ever being rejected.
    """
    try:
        phone_validator(value)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message)
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

        value = validate_phone_format(value)

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

        # Task 15 (server-side OTP-verified check): the comment two lines
        # below this used to say "email OTP verify-otp step se pehle hi ho
        # chuka hota hai" and set `is_verified=True` on that trust alone —
        # but nothing here ever confirmed that step actually happened.
        # /verify-otp/ and /signup/ were two independent endpoints linked
        # only by the *frontend* calling them in order; hitting /signup/
        # directly with no prior OTP step worked exactly the same. This
        # now requires a real, unexpired, `is_verified=True`
        # OTPVerification row (set by VerifyOTPView — see models.py
        # OTPVerification.is_verified) for either the submitted email or
        # phone before an account can be created at all.
        email = attrs.get("email")
        phone = attrs.get("phone")

        otp_obj = (
            OTPVerification.objects.filter(
                Q(target=email) | Q(target=phone),
                is_verified=True,
            )
            .order_by("-created_at")
            .first()
        )

        if not otp_obj or otp_obj.is_expired():
            raise serializers.ValidationError(
                "Please verify your email or phone with OTP before signing up."
            )

        # Stashed for create() — consumed (deleted) once the account is
        # actually made, so this same verified OTP can't be replayed for
        # a second signup.
        attrs["_otp_obj"] = otp_obj

        return attrs

    def create(self, validated_data):

        otp_obj = validated_data.pop("_otp_obj")
        validated_data.pop("confirm_password")

        user = User.objects.create_user(

            username=validated_data["username"],
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data["phone"],
            password=validated_data["password"],
            # ✅ Actually true now — gated by the `validate()` check above
            # instead of assumed.
            is_verified=True,

        )

        # Consume the OTP row so it can't be reused for another signup.
        otp_obj.delete()

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

        value = validate_phone_format(value)

        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError(
                "Phone number already exists."
            )
        return value


class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)


#-------------  forgot / reset password (B-7)  ---------------------------
#
# Dedicated two-step flow, separate from VerifyOTPView's OTP-login branch
# (see Task 16's note on that view in views.py): that branch is an
# intentional passwordless-login shortcut and logs the user straight in
# on a correct OTP — reusing it for password reset would blur "proved I
# own this email" with "here is a session", exactly the ambiguity B-7
# flagged. These two serializers back a flow that never returns a
# session: ForgotPasswordView only sends a code, ResetPasswordView only
# spends that code on setting a new password.

class ForgotPasswordSerializer(serializers.Serializer):
    email_or_phone = serializers.CharField(max_length=100, required=True)


class ResetPasswordSerializer(serializers.Serializer):
    email_or_phone = serializers.CharField(max_length=255, required=True)
    otp = serializers.CharField(max_length=6, required=True)

    new_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate_new_password(self, value):
        # Same shared rule as signup/change-password — see
        # validate_strong_password's docstring at the top of this file.
        return validate_strong_password(value)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({
                "confirm_password":
                    "Password and Confirm Password do not match."
            })
        return attrs