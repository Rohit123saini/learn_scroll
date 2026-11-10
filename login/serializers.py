# login/serializers.py
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail
from django.contrib.auth.password_validation import validate_password as django_validate_password
from .models import User, OTPVerification, phone_validator
from .age import is_minor_dob
from .disposable import (  # L1 gap 18 -- disposable email / virtual phone block
    CODE_DISPOSABLE_PHONE,
    FIELD_EMAIL_DISPOSABLE,
    FIELD_PHONE_DISPOSABLE,
    FIELD_PHONE_INVALID,
    MSG_DISPOSABLE_EMAIL,
    is_disposable_email,
    phone_problem,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
import re


def password_problem_pairs(value, user=None, phone=None):
    """
    Run Django's AUTH_PASSWORD_VALIDATORS (L1 gap 15) and return
    ``(message, code)`` pairs (empty list = fine). The code (``password_too_common``,
    ``password_too_similar``, ``password_contains_phone``...) is what the app
    translates into Hindi/English (L1 gap 17); the message is the English fallback.
    ``password_problems()`` below returns just the messages.

    Before this, nothing ever called ``validate_password()``, so the validators
    configured in settings.py -- the common-password list, "entirely numeric",
    similarity to username/name/email, and the staff policy -- were dead
    config: "P@ssw0rd" (upper + lower + digit + symbol, yet on every common-
    password list) sailed through signup.

    ``user`` may be an unsaved ``User(...)`` built from the signup form (so the
    similarity check works before the account exists). ``phone`` additionally
    stops a password that simply contains the person's own phone number, which
    is the first thing someone who knows them would try.
    """
    problems = []
    try:
        django_validate_password(value, user=user)
    except DjangoValidationError as exc:
        for err in exc.error_list:
            problems.append((err.messages[0], err.code or "password_invalid"))

    phone_digits = re.sub(r"\D", "", phone or getattr(user, "phone", "") or "")
    if len(phone_digits) >= 8 and phone_digits[-8:] in re.sub(r"\D", "", value):
        problems.append(("Password cannot contain your phone number.", "password_contains_phone"))
    return problems


def password_problems(value, user=None, phone=None):
    """Just the messages of ``password_problem_pairs`` (empty list = fine)."""
    return [message for message, _code in password_problem_pairs(value, user=user, phone=phone)]


def validate_strong_password(value, user=None, phone=None):
    """
    Shared password-strength rule used by signup, change-password and
    reset-password, so they never silently drift apart.

    The composition rules below are mirrored by the app's strength meter
    (lib/login/password_strength.dart) -- change one, change the other.
    """
    if value != value.strip():
        raise serializers.ValidationError(
            "Password cannot start or end with spaces.",
            code="password_edge_spaces",
        )

    if len(value) < 8:
        raise serializers.ValidationError(
            "Password must be at least 8 characters.",
            code="password_min_length",
        )

    if not re.search(r"[A-Z]", value):
        raise serializers.ValidationError(
            "Password must contain one uppercase letter.",
            code="password_no_upper",
        )

    if not re.search(r"[a-z]", value):
        raise serializers.ValidationError(
            "Password must contain one lowercase letter.",
            code="password_no_lower",
        )

    if not re.search(r"[0-9]", value):
        raise serializers.ValidationError(
            "Password must contain one number.",
            code="password_no_digit",
        )

    # Any symbol counts (it used to be a fixed list, so "_" or "-" or "~" were
    # refused while the staff policy accepted them). Whitespace does not count.
    if not re.search(r"[^A-Za-z0-9\s]", value):
        raise serializers.ValidationError(
            "Password must contain one special character.",
            code="password_no_special",
        )

    # L1 gap 15 -- Django's validators (common list, numeric-only, similarity...).
    pairs = password_problem_pairs(value, user=user, phone=phone)
    if pairs:
        raise serializers.ValidationError([ErrorDetail(m, code=c) for m, c in pairs])

    return value


def raise_if_bad_new_phone(value):
    """L1 gap 18 -- refuse a NEW number that is fake-looking or in a blocked virtual
    range. ``value`` is already normalised. Same rules as send-otp, minus the paid
    carrier lookup (that ran when the code was requested)."""
    problem = phone_problem(value)
    if problem:
        code, message = problem
        raise serializers.ValidationError(
            message,
            code=FIELD_PHONE_DISPOSABLE if code == CODE_DISPOSABLE_PHONE else FIELD_PHONE_INVALID,
        )


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

    Phone is now optional everywhere it's used, so this only runs when a
    value was actually supplied — an empty/blank phone skips format
    validation entirely instead of being rejected.
    """
    if not value:
        return value
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

    # L1 gaps 4 + 12 -- phone-first signup: the phone is REQUIRED and must have
    # been verified with an OTP (validate() below checks the verified
    # OTPVerification row). Email is now optional, and if it is sent it must be
    # OTP-verified too, so nobody can attach someone else's address to an account.
    phone = serializers.CharField(required=True, allow_blank=False)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)

    # Minor-safety: required at signup. Under MIN_SIGNUP_AGE is refused;
    # under 18 the account is created private and stays private.
    date_of_birth = serializers.DateField(required=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "date_of_birth",
            "password",
            "confirm_password",
        ]

    def validate_username(self, value):

        value = value.strip()

        # L1 gap 5: a username must never look like someone's email or phone
        # number (it used to be possible to register "victim@gmail.com" and
        # collide with / hijack that person's email login). See identifiers.py.
        from .identifiers import username_problem

        problem = username_problem(value)
        if problem:
            raise serializers.ValidationError(problem[1], code=problem[0])

        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError(
                "Username already exists.",
                code="username_exists",
            )

        return value

    def validate_email(self, value):

        value = (value or "").strip().lower()
        if not value:
            return None

        # L1 gap 18 -- no throw-away mailbox domains on a new account.
        if is_disposable_email(value):
            raise serializers.ValidationError(MSG_DISPOSABLE_EMAIL, code=FIELD_EMAIL_DISPOSABLE)

        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "Email already exists.",
                code="email_exists",
            )

        return value

    def validate_phone(self, value):

        # Same normalisation SendOTPView / VerifyOTPView use, so the value here
        # is exactly the OTPVerification.target that was verified.
        from .identifiers import KIND_PHONE, normalize_identifier

        value, kind = normalize_identifier(value)
        if kind != KIND_PHONE:
            raise serializers.ValidationError(
                "Enter a valid phone number in international format, e.g. +919876543210.",
                code="phone_invalid_format",
            )

        raise_if_bad_new_phone(value)  # L1 gap 18

        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError(
                "Phone number already exists.",
                code="phone_exists",
            )

        return value

    def validate_date_of_birth(self, value):
        from .age import dob_problem

        problem = dob_problem(value)
        if problem:
            raise serializers.ValidationError(problem[1], code=problem[0])
        return value

    def validate_password(self, value):
        return validate_strong_password(value)

    def validate(self, attrs):

        if attrs["password"] != attrs["confirm_password"]:

            raise serializers.ValidationError({
                "confirm_password": ErrorDetail(
                    "Password and Confirm Password do not match.", code="passwords_mismatch"
                )
            })

        # L1 gap 15 -- validate_password() with the person's own details, so a
        # password built from their username / name / phone is refused. (The
        # user-less checks already ran in validate_password() above.)
        pairs = password_problem_pairs(
            attrs["password"],
            user=User(
                username=attrs.get("username", ""),
                first_name=attrs.get("first_name", ""),
                last_name=attrs.get("last_name", ""),
                email=attrs.get("email") or "",
            ),
            phone=attrs.get("phone"),
        )
        if pairs:
            raise serializers.ValidationError({"password": [ErrorDetail(m, code=c) for m, c in pairs]})

        # L1 gaps 4 + 12 -- the phone must have been verified by VerifyOTPView
        # (a real, unexpired, is_verified=True OTPVerification row for exactly
        # this number). Nothing else can create the account, so a phone can no
        # longer be saved without its owner proving control of it.
        phone = attrs["phone"]
        email = attrs.get("email")

        phone_otp = OTPVerification.objects.filter(target=phone, is_verified=True).first()
        if phone_otp is None or phone_otp.is_expired():
            raise serializers.ValidationError({
                "phone": ErrorDetail(
                    "Please verify your phone number with OTP before signing up.", code="phone_not_verified"
                )
            })
        otp_objs = [phone_otp]

        if email:
            email_otp = OTPVerification.objects.filter(target=email, is_verified=True).first()
            if email_otp is None or email_otp.is_expired():
                raise serializers.ValidationError({
                    "email": ErrorDetail(
                        "Verify this email with an OTP before adding it.", code="email_not_verified"
                    )
                })
            otp_objs.append(email_otp)

        # Stashed for create() -- consumed (deleted) once the account is
        # actually made, so a verified OTP can't be replayed for a second signup.
        attrs["_otp_objs"] = otp_objs

        return attrs

    def create(self, validated_data):

        otp_objs = validated_data.pop("_otp_objs")
        validated_data.pop("confirm_password")

        user = User.objects.create_user(

            username=validated_data["username"],
            email=validated_data.get("email"),
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data["phone"],
            phone_verified=True,  # L1 gap 4 -- proven by the OTP row checked in validate()
            date_of_birth=validated_data["date_of_birth"],
            # Under-18 accounts start (and stay) private.
            is_private=is_minor_dob(validated_data["date_of_birth"]),
            password=validated_data["password"],
            # ✅ Actually true now — gated by the `validate()` check above
            # instead of assumed.
            is_verified=True,

        )

        # Consume the OTP rows so they can't be reused for another signup.
        for otp_obj in otp_objs:
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

    # S3 -- re-auth. Checked in ChangePasswordAPIView (accounts WITHOUT a usable password, i.e. Google-only, have none).
    current_password = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )

    new_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},required=True
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},required=True
    )

    def validate_new_password(self, value):
        # L1 gap 15: the view passes context={"user": request.user}.
        return validate_strong_password(value, user=self.context.get("user"))

    def validate(self, attrs):

        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({
                "confirm_password": ErrorDetail(
                    "Password and Confirm Password do not match.", code="passwords_mismatch"
                )
            })

        return attrs




class SetDateOfBirthSerializer(serializers.Serializer):
    """One-time DOB entry for accounts that don't have one yet (Google
    signups, and accounts that pre-date the minor-safety feature)."""

    date_of_birth = serializers.DateField(required=True)

    def validate_date_of_birth(self, value):
        from .age import dob_problem

        problem = dob_problem(value)
        if problem:
            raise serializers.ValidationError(problem[1], code=problem[0])
        return value


class CompleteProfileSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)
    # L1 gap 4 -- the code that was sent to `phone` (via /auth/send-otp/). The
    # view spends it; the number is only saved if it is correct.
    otp = serializers.CharField(max_length=6, required=True)

    def validate_phone(self, value):
        from .identifiers import KIND_PHONE, normalize_identifier

        value, kind = normalize_identifier(value)
        if kind != KIND_PHONE:
            raise serializers.ValidationError(
                "Enter a valid phone number in international format, e.g. +919876543210.",
                code="phone_invalid_format",
            )

        raise_if_bad_new_phone(value)  # L1 gap 18

        user = self.context.get("user")
        others = User.objects.filter(phone=value)
        if user is not None:
            others = others.exclude(pk=user.pk)
        if others.exists():
            raise serializers.ValidationError(
                "Phone number already exists.",
                code="phone_exists",
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
                "confirm_password": ErrorDetail(
                    "Password and Confirm Password do not match.", code="passwords_mismatch"
                )
            })
        return attrs