# `login` App — Complete Self-Contained Reference

Ye ek hi file hai jisme poore **login** (auth) Django app ka sara logic,
code, connections, flows aur known issues cover hain. Iske alawa kisi aur
file ki zaroorat nahi — sab kuch (models → serializers → views → urls →
admin → apps.py) yahin milega, saath me har piece kya kaam karta hai uski
explanation bhi.

---

## 0. Changelog — latest upload check

Latest upload (`admin.py`, `apps.py`, `models.py`, `serializers.py`,
`tests.py`, `urls.py`, `views.py`) ko line-by-line diff kiya gaya.

**`models.py` me is baar real functional changes hain** (production
hardening pass — see the module docstring at the top of the new
`models.py` for the full "why" on each point):

- **Profile-photo cleanup ab `save()`/`delete()` overrides se nahi,
  `pre_save`/`post_delete` **signals** se hota hai.** Isse do cheezein
  fix hoti hain: (a) old code `.path` use karta tha jo sirf local-disk
  storage pe kaam karta hai — cloud storage (S3/GCS/Azure) pe move karte
  hi crash karega; naya code `field.storage.delete()/exists()` use karta
  hai jo storage-agnostic hai; (b) `delete()` override bulk
  `queryset.delete()` / admin "delete selected" me silently skip ho jata
  tha (sirf `instance.delete()` pe chalta tha) — signals dono cases me
  fire hote hain.
- **`phone`** — ab `phone_validator` (RegexValidator,
  `^\+?[1-9]\d{7,14}$`) model field pe directly attached hai (pehle
  koi field-level validator nahi tha, sirf `serializers.py` apni taraf
  se `phone_validator` ko import/reuse kar raha tha — is doc ka §4
  already yahi dikhata tha, `models.py` ka §3 sirf outdated tha). Plus
  `save()` me blank string ko `None` normalize kiya gaya hai taaki
  `unique=True` + `blank=True` ka classic footgun (do blank-phone
  signups `''` pe unique-constraint clash karte the) na ho.
- **`email`** — ab DB-level `unique=True` (pehle `AbstractUser`'s
  default non-unique email tha). Login-by-email, "email already
  registered" check, password reset — sab ab race-proof hain.
- **Indexes added**: `User.Meta.indexes` me `is_private`, aur
  `OTPVerification.Meta.indexes` me `created_at` (expiry-sweep/cleanup
  job aur expiry check dono isi par filter/sort karte hain). `is_verified`
  ab field-level `db_index=True` carry karta hai.

`views.py`, `serializers.py`, `admin.py`, `apps.py`, `tests.py`,
`urls.py` — **byte-for-byte same** as the version this doc already
documents; sirf `models.py` me naya docstring/comments hain jo upar ke
points explain karte hain.

**Section 3 (models.py) neeche fully naye code/notes se replace kiya
gaya hai. §11 Known Issues me profile-photo-cleanup wala point ab
"fixed" mark kiya gaya hai.**

---

## 1. App Overview

**App name:** `login`
**Purpose:** Full authentication system — signup, login (username or
email), Google Sign-In (login+signup combined), email OTP verification,
change password, complete-profile (post-Google phone capture), JWT
access/refresh tokens (via `rest_framework_simplejwt`).

**Tech stack:** Django + Django REST Framework + `rest_framework_simplejwt`
(JWT auth) + `drf-spectacular` (OpenAPI docs) + `google-auth` (Google
ID-token verification) + Django's `send_mail` (OTP delivery).

**Custom User model:** This app **defines and owns** the project's custom
`User` model (`AbstractUser` subclass) — this is the app your
`AUTH_USER_MODEL` in `settings.py` must point to.

**Files in this app:**
| File | Responsibility |
|---|---|
| `models.py` | `User` (custom auth user) + `OTPVerification` model |
| `serializers.py` | All request serializers + shared password-strength validator |
| `views.py` | All API endpoint logic (Login, Signup, Google auth, OTP, change-password, complete-profile) |
| `urls.py` | URL routing (+ JWT refresh endpoint) |
| `admin.py` | Django admin registration |
| `apps.py` | App config (`name = 'login'`) |
| `tests.py` | Empty — no tests written yet |

---

## 2. ⚠️ External Settings / Dependencies Required

For every view in this app to actually work, your project's
`settings.py` must have:

```python
AUTH_USER_MODEL = "login.User"   # points to the User model defined in §3 below

INSTALLED_APPS = [
    ...
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',
    'login',
]

# --- Google Sign-In ---
GOOGLE_CLIENT_ID = "your-google-oauth-client-id.apps.googleusercontent.com"

# --- Email (used for OTP delivery) ---
DEFAULT_FROM_EMAIL = "noreply@yourapp.com"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # or console backend for dev
# + EMAIL_HOST / EMAIL_PORT / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD / EMAIL_USE_TLS

# --- JWT ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "send_otp": "5/min",     # required — SendOTPView uses this scope
        "verify_otp": "10/min",  # required — VerifyOTPView uses this scope
    },
}

# Packages needed (pip):
# djangorestframework djangorestframework-simplejwt drf-spectacular
# google-auth
```

Also required in your project's root `urls.py`:
```python
path('login/', include('login.urls')),   # or whatever prefix you choose — see §7 note
```

Without `DEFAULT_THROTTLE_RATES["send_otp"]` and `["verify_otp"]` set,
`SendOTPView`/`VerifyOTPView` will raise a throttle **misconfiguration
error** at request time (not silently skip throttling) — this is a hard
requirement, not optional.

---

## 3. `models.py` (full code)

```python
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

phone_validator = RegexValidator(
    regex=r"^\+?[1-9]\d{7,14}$",
    message="Enter a valid phone number in international format, e.g. +919876543210.",
)


class User(AbstractUser):

    phone = models.CharField(
        max_length=15,
        unique=True,
        null=True,
        blank=True,
        validators=[phone_validator],
    )

    # AbstractUser.email is NOT unique by default — made it unique here so
    # "sign in with email" / "email already registered" checks can rely on
    # the DB instead of a racy SELECT-then-INSERT in application code.
    email = models.EmailField("email address", unique=True, blank=True, null=True)

    profile_photo = models.ImageField(
        upload_to="profile/",
        null=True,
        blank=True,
    )

    bio = models.TextField(blank=True)

    is_private = models.BooleanField(default=False)

    is_verified = models.BooleanField(default=False, db_index=True)

    # Denormalized counters — NEVER write these directly from a
    # read-modify-write in a view/serializer. Always mutate via
    # `User.objects.filter(pk=x).update(followers_count=F('followers_count') + 1)`
    # (or an equivalent atomic F() update) from inside the same transaction
    # as the Follow/Post row that caused the change, or the count WILL
    # drift under concurrent requests.
    followers_count = models.PositiveIntegerField(default=0)
    following_count = models.PositiveIntegerField(default=0)
    posts_count = models.PositiveIntegerField(default=0)

    # Cached total — source of truth is user_profile.CoinLedger. Same
    # atomic-update rule as the counters above applies here.
    coin = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["is_private"]),
        ]

    def save(self, *args, **kwargs):
        # Blank CharField saves as '' not NULL — normalize so the unique
        # constraint on `phone` only ever sees at most one NULL-equivalent
        # value's worth of "no phone", never a collision between two
        # blank signups.
        if self.phone == "":
            self.phone = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username


@receiver(pre_save, sender=User)
def _delete_old_profile_photo_on_change(sender, instance: User, **kwargs):
    """Remove the previous profile_photo from storage when it's replaced.

    Storage-backend agnostic (works for local disk, S3, GCS, ...) because
    it goes through `field.storage`, never a raw filesystem path.
    """
    if not instance.pk:
        return  # new user, nothing to diff against
    try:
        old_photo = sender.objects.only("profile_photo").get(pk=instance.pk).profile_photo
    except sender.DoesNotExist:
        return
    if old_photo and old_photo != instance.profile_photo:
        old_photo.storage.delete(old_photo.name)


@receiver(post_delete, sender=User)
def _delete_profile_photo_on_user_delete(sender, instance: User, **kwargs):
    """Fires for both `instance.delete()` and bulk `queryset.delete()`,
    unlike an overridden `Model.delete()` (which bulk deletes bypass
    entirely)."""
    if instance.profile_photo:
        instance.profile_photo.storage.delete(instance.profile_photo.name)


class OTPVerification(models.Model):
    # SECURITY: OTP is never stored in plaintext — only its hash (Django's
    # PBKDF2 hasher) is persisted, so a DB leak/backup access alone doesn't
    # expose usable codes.
    target = models.CharField(max_length=100, unique=True)
    otp_hash = models.CharField(max_length=128)

    # SECURITY: brute-force guard — a 6-digit OTP has only 10^6
    # combinations, so a failed-attempt counter with a hard cap is
    # required, not optional.
    attempts = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    MAX_ATTEMPTS = 5
    EXPIRY_MINUTES = 5  # 2 min was too tight for real-world email/SMS delivery delay

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),  # expiry-sweep / cleanup job
        ]

    def set_otp(self, raw_otp: str) -> None:
        """Hash and store — the raw OTP is never persisted."""
        self.otp_hash = make_password(raw_otp)
        self.attempts = 0

    def check_otp(self, raw_otp: str) -> bool:
        return check_password(raw_otp, self.otp_hash)

    def is_expired(self) -> bool:
        return timezone.now() > self.created_at + timedelta(minutes=self.EXPIRY_MINUTES)

    def is_locked(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def register_failed_attempt(self) -> None:
        self.attempts += 1
        self.save(update_fields=["attempts"])

    def __str__(self):
        return f"{self.target} - OTP (hashed, attempts={self.attempts})"
```

### Model notes
- **`User`** extends Django's `AbstractUser` (so still has `username`,
  `email`, `first_name`, `last_name`, `password`, `is_active`,
  `is_staff`, etc. built-in) and adds: `phone` (unique, optional —
  because Google signup creates users without one, now with a field-level
  `phone_validator`), `email` (now unique at the DB level), `profile_photo`,
  `bio`, `is_private`, `is_verified` (now `db_index=True`), plus
  **social-graph counters** (`followers_count`, `following_count`,
  `posts_count`) and `coin` — this is exactly the field set the separate
  `user_profile` app (see that app's own reference doc) depends on
  existing.
- **`phone`** — validated against `phone_validator`
  (`^\+?[1-9]\d{7,14}$`: optional leading `+`, no leading zero, 8-15
  digits). `save()` normalizes an empty string to `None` so two blank
  signups don't collide on the `unique=True` constraint (Postgres allows
  unlimited `NULL`s under a unique constraint, but only one `''`).
- **`email`** — unique at the DB level (still nullable/blank so
  social-only signups aren't forced to supply one). `AbstractUser`'s
  default `email` field is *not* unique, so this closes a real gap for
  login-by-email / "email already registered" / password-reset checks.
- **Counters (`followers_count`, `following_count`, `posts_count`,
  `coin`)** are denormalized, not source of truth — only ever mutate them
  via an atomic `F()` update inside the same transaction as the write
  that caused the change; a plain `user.followers_count += 1; user.save()`
  is a read-modify-write race under concurrent requests.
- **Profile-photo cleanup is now signal-based, not `save()`/`delete()`
  overrides**: a `pre_save` signal diffs the old vs. new `profile_photo`
  and deletes the old file when it changes; a `post_delete` signal
  deletes the file when the `User` row is deleted. Both go through
  `field.storage.delete()/exists()` — storage-backend agnostic (local
  disk, S3, GCS, ...) — and, unlike the old `delete()` override, the
  `post_delete` signal also fires for bulk `queryset.delete()` and admin
  "delete selected" (a `Model.delete()` override is bypassed by those).
  ✅ This resolves the caveat that used to be here about `.path`/`os.remove`
  breaking on cloud storage — see §11.
- **`OTPVerification`** — one row per `target` (email or phone), unique
  constraint means requesting a new OTP for the same target **overwrites**
  the previous one (see `update_or_create` in `SendOTPView`). Never stores
  the raw OTP — only a Django-hashed version, checked via `check_password`.
  `created_at` now has a DB index since the expiry check and the periodic
  "delete expired OTP rows" cleanup job both filter/sort on it.

---

## 4. `serializers.py` (full code)

```python
from rest_framework import serializers
from .models import User, phone_validator
from django.core.exceptions import ValidationError as DjangoValidationError
import re


def validate_strong_password(value):
    """
    Shared password-strength rule used by both signup and change-password,
    so the two never silently drift apart.
    """
    if value != value.strip():
        raise serializers.ValidationError("Password cannot start or end with spaces.")
    if len(value) < 8:
        raise serializers.ValidationError("Password must be at least 8 characters.")
    if not re.search(r"[A-Z]", value):
        raise serializers.ValidationError("Password must contain one uppercase letter.")
    if not re.search(r"[a-z]", value):
        raise serializers.ValidationError("Password must contain one lowercase letter.")
    if not re.search(r"[0-9]", value):
        raise serializers.ValidationError("Password must contain one number.")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", value):
        raise serializers.ValidationError("Password must contain one special character.")
    return value


def validate_phone_format(value):
    """
    Shared phone-format rule used by both SignupSerializer and
    CompleteProfileSerializer — same drift-prevention spirit as
    validate_strong_password above. Reuses login.models.phone_validator
    (the exact RegexValidator declared on User.phone: `^\+?[1-9]\d{7,14}$`,
    optional leading '+', no leading zero, 8-15 digits) instead of the
    old plain isdigit()+len() check, which was looser than what the model
    itself enforces and rejected valid international-format numbers like
    "+919876543210".
    """
    try:
        phone_validator(value)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message)
    return value


#--------------    login -------------------------------------------
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


#-----------------    signup    ------------------------------------------
class SignupSerializer(serializers.ModelSerializer):

    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    confirm_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    phone = serializers.CharField()

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "phone", "password", "confirm_password"]

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value

    def validate_phone(self, value):
        value = value.strip()
        value = validate_phone_format(value)
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Phone number already exists.")
        return value

    def validate_password(self, value):
        return validate_strong_password(value)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Password and Confirm Password do not match."})
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
            # email OTP verify-otp step se pehle hi ho chuka hota hai
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

    new_password = serializers.CharField(write_only=True, style={"input_type": "password"}, required=True)
    confirm_password = serializers.CharField(write_only=True, style={"input_type": "password"}, required=True)

    def validate_new_password(self, value):
        return validate_strong_password(value)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Password and Confirm Password do not match."})
        return attrs


class CompleteProfileSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)

    def validate_phone(self, value):
        value = value.strip()
        value = validate_phone_format(value)
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Phone number already exists.")
        return value


class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)
```

### Serializer notes
- `validate_strong_password()` is a **module-level shared function**
  (not a method) — both `SignupSerializer.validate_password` and
  `ChangePasswordSerializer.validate_new_password` call it, so the rule
  can never drift between signup and change-password.
- Password rule: min 8 chars, no leading/trailing spaces, ≥1 uppercase,
  ≥1 lowercase, ≥1 digit, ≥1 special char from `` !@#$%^&*(),.?":{}|<> ``.
- `validate_phone_format()` is the same pattern applied to phone —
  a module-level shared function used by both `SignupSerializer` and
  `CompleteProfileSerializer`, wrapping `login.models.phone_validator`
  directly instead of re-implementing a separate regex/isdigit() check.
  ⚠️ This matters because both serializers **redeclare** `phone =
  serializers.CharField(...)` — DRF only auto-attaches a model field's
  validators when it *builds* the field itself from the model, not when
  you override it like this. Without the explicit call, the model's
  `phone_validator` would never actually run on signup/complete-profile,
  and a phone value that violates it could still reach the DB.
- Phone rule (mirrors the model): optional leading `+`, first digit
  1-9 (no leading zero), 8-15 digits total — e.g. `+919876543210` or
  `9876543210`. Plain digit strings shorter than 8 chars, or starting
  with `0`, are now rejected at the serializer level too.
- `LoginSerializer.username` actually accepts **either username or
  email** — the disambiguation happens in the view (§5.1), not here.

---

## 5. `views.py` (full code)

```python
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
from drf_spectacular.utils import extend_schema, OpenApiResponse
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

        user = authenticate(username=actual_username, password=password)

        if user is None:
            return Response(
                {"status": False, "message": "Invalid Username or Password"},
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
                {"status": False, "message": "New password cannot be same as current password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"status": True, "message": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )
```

---

## 6. `admin.py` (full code)

```python
from django.contrib import admin
from .models import *
# Register your models here.
admin.site.register(User)
admin.site.register(OTPVerification)
```

Both models registered with Django's default `ModelAdmin`. Note:
registering the custom `User` model this way (instead of extending
`UserAdmin`) means the admin list/detail view won't have the nice
password-change widget or fieldset grouping Django's built-in `UserAdmin`
gives you — works, but consider `class UserAdmin(admin.ModelAdmin)` with
custom `fieldsets` later if the admin UI matters to you.

---

## 7. `apps.py`

```python
from django.apps import AppConfig


class LoginConfig(AppConfig):
    name = 'login'
```

---

## 8. `tests.py`

```python
from django.test import TestCase

# Create your tests here.
```

No tests currently written. Suggested minimum coverage: login with
username vs. email, invalid credentials, signup validation (duplicate
username/email/phone, password mismatch, weak password), Google auth
new-user vs. existing-user paths, OTP expiry/lockout/replay, change
password reject-same-password, complete-profile duplicate phone.

---

## 9. `urls.py` (full code)

```python
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import *

urlpatterns = [
    path("", Login.as_view(), name="Login"),
    path("signup/", Signup.as_view(), name="signup"),
    path('auth/send-otp/', SendOTPView.as_view(), name='send_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path("auth/change-password/", ChangePasswordAPIView.as_view(), name="change-password"),
    path("auth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("auth/complete-profile/", CompleteProfileView.as_view(), name="complete-profile"),

    # 🔥 FIX — koi refresh-token redeem endpoint nahi tha. Tokens already
    # standard `RefreshToken.for_user()` se ban rahe the (Login/Signup/
    # GoogleAuthView/VerifyOTPView sab isi se), isliye simplejwt ka
    # built-in `TokenRefreshView` bina kisi custom logic ke kaam karega —
    # request {"refresh": "..."} -> response {"access": "..."}.
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]

# ==============================================================================
# ⚠️ CONFIRM KARO — is app ka top-level project urls.py mein `include()`
# kis prefix ke peeche hai (e.g. `path('login/', include('login.urls'))`?
# ya root pe direct?). Us prefix + upar wale path ko jodke hi Flutter side
# ka `_refreshEndpoint` (auth_service.dart) ka poora URL banta hai — agar
# prefix `/login/` hai to poora path `/login/auth/token/refresh/` hoga,
# agar koi prefix nahi hai to `/auth/token/refresh/`.
# ==============================================================================
```

### ⚠️ Confirm your URL prefix
Whatever prefix you `include()` this app under in the **root** urls.py
decides the final client-facing paths. E.g.:
```python
# project/urls.py
path('login/', include('login.urls')),
```
...would make the full paths `/login/`, `/login/signup/`,
`/login/auth/send-otp/`, `/login/auth/token/refresh/`, etc. If instead
you mount it at the root (`path('', include('login.urls'))`), drop the
`login/` prefix from the table below. **This matters for your frontend's
`_refreshEndpoint`/base-URL config** — double-check it matches however
you actually wired the root urls.

### Full endpoint table (assuming mounted at `/login/`)
| Method | URL | View | Auth | Purpose |
|---|---|---|---|---|
| POST | `/login/` | `Login` | ❌ (public) | Login with username **or** email + password |
| POST | `/login/signup/` | `Signup` | ❌ (public) | Create account, returns JWT tokens immediately |
| POST | `/login/auth/send-otp/` | `SendOTPView` | ❌ (public, throttled) | Send OTP to email (phone not yet implemented) |
| POST | `/login/auth/verify-otp/` | `VerifyOTPView` | ❌ (public, throttled) | Verify OTP; logs in if user exists, else signals "proceed to signup" |
| POST | `/login/auth/change-password/` | `ChangePasswordAPIView` | ✅ | Change password for logged-in user |
| POST | `/login/auth/google/` | `GoogleAuthView` | ❌ (public) | Google Sign-In — login or signup in one call |
| POST | `/login/auth/complete-profile/` | `CompleteProfileView` | ✅ | Add phone number after Google signup |
| POST | `/login/auth/token/refresh/` | `TokenRefreshView` (simplejwt built-in) | ❌ (needs valid refresh token) | Exchange refresh token for new access token |

---

## 10. Business Logic Flows

### 10.1 Login (`POST /login/`)
```
Body: {"username": "<username-or-email>", "password": "..."}
        │
        ▼
Look up User by username OR email match
  → found: use its actual `username` for authenticate()
  → not found: try authenticate() with the raw input anyway
        │
        ▼
Django authenticate(username, password)
  → None: 401 "Invalid Username or Password"
  → User: issue RefreshToken.for_user(user) → 200 with user info + tokens
```

### 10.2 Signup (`POST /login/signup/`)
```
Validate: username unique, email unique (case-insensitive),
          phone unique + digits-only + ≤15 chars,
          password strength rule, password == confirm_password
        │
        ▼
create_user(..., is_verified=True)   # assumes OTP was already verified
        │                              via /auth/verify-otp/ before this call
        ▼
Issue RefreshToken.for_user(user) → 201 with user info + tokens
```
⚠️ Note: `Signup` itself does **not** re-check that an OTP was verified
for this email/phone — the flow *assumes* the frontend called
`/auth/send-otp/` → `/auth/verify-otp/` first. There's no server-side
enforcement linking OTP verification to this signup call. If you need
that guarantee, add a check here (e.g. require a short-lived
"verified" token/flag from `VerifyOTPView`).

### 10.3 Google Sign-In (`POST /login/auth/google/`)
```
Body: {"id_token": "<google-id-token>"}
        │
        ▼
Verify token with Google (google.oauth2.id_token.verify_oauth2_token)
  → invalid: 401 "Invalid Google token"
        │
Check idinfo.email_verified == True
  → False: 400 "email is not verified"
        │
Normalize email (strip + lowercase)
        │
User.objects.get(email=email)
  │                              │
 exists                      DoesNotExist
  │                              │
created=False              generate unique username from email
  │                        local-part (+numeric suffix if taken)
  │                        create user, set_unusable_password()
  │                        created=True
  └──────────┬───────────────────┘
             ▼
   Issue RefreshToken.for_user(user)
   Response includes: is_new_user, phone_missing (True if user.phone empty)
```
Frontend should check `phone_missing` → if `True`, prompt the user and
call `/auth/complete-profile/` next.

### 10.4 Email OTP flow (Send → Verify)
```
POST /auth/send-otp/  {"email_or_phone": "..."}
   - phone targets → 501 Not Implemented (no SMS provider wired yet)
   - email targets → generate 6-digit secrets.randbelow() code,
     hash + store (update_or_create — old OTP for same target overwritten),
     email it via send_mail(); OTP itself never appears in the response.
        │
        ▼
POST /auth/verify-otp/  {"email_or_phone": "...", "otp": "123456"}
   - no OTPVerification row for target → 400 "No OTP request found"
   - expired (> 5 min) → row deleted, 400 "OTP has expired"
   - locked (≥5 failed attempts) → row deleted, 429 "Too many incorrect attempts"
   - wrong code → attempts += 1, 400 "Invalid OTP" (row NOT deleted — re-tryable
     until MAX_ATTEMPTS)
   - correct code:
       - user already exists (by username/email or username/phone match)
           → issue tokens, delete OTP row, "user_exists": true, effectively LOGS THEM IN
       - user does not exist
           → OTP row intentionally NOT deleted yet (so /signup/ has a
             short grace window), "user_exists": false → frontend should
             now call /signup/ to finish registration
```

### 10.5 Complete Profile (`POST /login/auth/complete-profile/`)
Authenticated-only. Sets `phone` on `request.user`, validates it's
digits-only, ≤15 chars, and not already taken by another account.

### 10.6 Change Password (`POST /login/auth/change-password/`)
Authenticated-only. Validates new password strength + confirm match,
rejects if identical to current password, otherwise `set_password()` +
save.

### 10.7 Token Refresh (`POST /login/auth/token/refresh/`)
Standard `simplejwt` `TokenRefreshView` — no custom code needed since
every token in this app is issued via the standard
`RefreshToken.for_user()`. Request `{"refresh": "<token>"}` → response
`{"access": "<new-token>"}`.

---

## 11. Known Issues / Security Notes To Double-Check

1. **Phone OTP not implemented** — `SendOTPView` returns `501` for
   non-email targets. If your signup/login flow needs phone OTP, you
   must wire an SMS provider (Twilio, MSG91, etc.) before shipping that
   path.
2. **Signup doesn't verify OTP itself** — see §10.2 caveat. The link
   between "OTP was verified" and "signup is now allowed" exists only in
   frontend flow ordering, not enforced server-side.
3. **`GoogleAuthView` username collision handling** is a `while` loop
   incrementing a numeric suffix — fine at normal scale, but under very
   high concurrent signup load with the same email local-part there's a
   theoretical (small) race window between the `exists()` check and
   `create()`. Consider `get_or_create` with `unique=True` + retry-on-
   `IntegrityError` if this ever becomes a real bottleneck.
4. ✅ **FIXED** — Profile-photo cleanup previously used local filesystem
   calls (`os.path.isfile`, `os.remove`) in `User.save()`/`delete()`,
   which would break on cloud storage and silently skip on bulk deletes.
   Now done via `pre_save`/`post_delete` signals using `field.storage`
   (storage-agnostic, fires on bulk deletes too) — see §3 note.
5. **Throttle scopes must be configured** — `send_otp` / `verify_otp`
   scopes in `DEFAULT_THROTTLE_RATES` are **required**, not optional
   (see §2). Missing them causes a runtime error, not silent bypass.
6. **`VerifyOTPView` "user_exists" branch effectively double-authenticates
   as login** — verifying an OTP for an *existing* user's email logs them
   straight in with tokens, no password needed. This is by design (OTP
   login), but make sure this endpoint is not reachable/misused as a
   password-reset bypass unless that's intended — currently there's no
   separate "forgot password" flow, so this OTP-login path may be doing
   double duty. Worth confirming this matches your intended product
   behavior.
7. **`OTPVerification.target` unique constraint** means only **one**
   pending OTP can exist per email/phone at a time — resending overwrites
   the old one entirely (old code becomes permanently invalid the moment
   a new one is requested), which is generally the safer behavior.
8. **Admin registration of `User`** doesn't use Django's `UserAdmin` —
   see §6 note if you want the nicer built-in admin UX for user
   management.

---

## 12. Quick Setup Checklist (to run this app standalone)

- [ ] `AUTH_USER_MODEL = "login.User"` in `settings.py`.
- [ ] `'rest_framework'`, `'rest_framework_simplejwt'`, `'drf_spectacular'`,
      `'login'` in `INSTALLED_APPS`.
- [ ] `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]` includes
      `JWTAuthentication`.
- [ ] `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` has `send_otp` and
      `verify_otp` scopes set.
- [ ] `GOOGLE_CLIENT_ID` set (from Google Cloud Console OAuth credentials).
- [ ] Email backend + `DEFAULT_FROM_EMAIL` configured (for OTP emails).
- [ ] `MEDIA_URL` / `MEDIA_ROOT` configured (for `profile_photo` uploads).
- [ ] `pip install djangorestframework djangorestframework-simplejwt drf-spectacular google-auth`.
- [ ] `path('login/', include('login.urls'))` (or your chosen prefix) in
      root `urls.py` — confirm it matches your frontend's base URL (see §9
      warning).
- [ ] Run `python manage.py makemigrations login && python manage.py migrate`.
- [ ] For phone OTP to actually work: wire an SMS provider into
      `SendOTPView`'s `else` branch (currently `501 Not Implemented`).

With the above satisfied, everything in this single document — models,
serializers, views, urls, admin — is enough to run the full `login` app
end to end.