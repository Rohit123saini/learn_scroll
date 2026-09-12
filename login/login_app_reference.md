# `login` App — Complete Self-Contained Reference

> **v4 — updated after the latest patch round (fix B-7, forgot/reset
> password).** Ye ek hi file hai jisme poore **login** (auth) Django app
> ka sara logic, code, connections, flows aur known issues cover hain.
> Iske alawa kisi aur file ki zaroorat nahi — sab kuch (models →
> serializers → views → urls → admin → apps.py → sms_service.py) yahin
> milega, saath me har piece kya kaam karta hai uski explanation bhi.
>
> **v3 se kya badla, sabse pehle:** section 0.2 (Changelog v3 → v4)
> padho. Ek dedicated **forgot/reset password** flow (`ForgotPasswordView`/
> `ResetPasswordView`) add hui hai — pehle password-reset ka koi seedha
> rasta nahi tha, sirf OTP-login ka side-effect (§11 item 6, ab
> resolved).
>
> **v2 se v3 me kya badla:** section 0.1 (Changelog v2 → v3) padho.
> Phone OTP ab actually deliver hoti hai (MSG91 ke through), signup ab
> server-side check karta hai ki OTP verify hui thi ya nahi (pehle sirf
> frontend call-ordering pe trust tha), aur existing-user OTP-login ab
> ek heads-up email bhejta hai account owner ko.

---

## 0. Changelog — v1 → v2 (superseded, kept for history)

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

---

## 0.1 Changelog — v2 → v3 (is round me kya fix/add hua)

### 📱 TASK 14 — Phone OTP ab actually deliver hoti hai
Pehle `SendOTPView` phone targets ke liye seedha `501 Not Implemented`
return karta tha ("no SMS provider wired yet"). Ab:
- Naya file **`sms_service.py`** — `send_otp_sms(phone, otp_code)`,
  MSG91 ke `POST /api/v5/otp` endpoint ke through deliver karta hai.
  MSG91 isliye chuna gaya kyunki project ke paas already ek MSG91
  account hai (`liveclass/notifications.py` ke `_send_sms`/
  `_send_whatsapp` ke liye) — ek naya vendor (Twilio) khada karne ki
  zaroorat nahi.
- OTP generation/hashing/storage bilkul same rehta hai
  (`secrets.randbelow`, `OTPVerification.set_otp`) — MSG91 sirf delivery
  channel hai, `otp` query param me humara khud ka generated code bhejte
  hain, MSG91 ka apna auto-OTP feature use nahi hota. Isi wajah se
  `VerifyOTPView` ko phone vs email ke liye koi alag branching nahi
  chahiye — dono humare apne `otp_hash` ke against verify hote hain.
- **Fails loud, deliberately** — `liveclass`'s `_send_sms` best-effort
  hai (missed notification blocking nahi hai), lekin ek OTP jo silently
  fail ho jaye woh ek broken signup/login hai with no path forward. Har
  failure `SMSDeliveryError` raise karta hai, aur `SendOTPView` ise catch
  karke `503` return karta hai — same shape jaisa existing email-failure
  path already karta tha.
- Requires `MSG91_AUTH_KEY` (already shared with liveclass) aur naya
  `MSG91_OTP_TEMPLATE_ID` (settings.py) — DLT-registered OTP template
  jisme `##OTP##` variable ho (India TRAI/DLT regulation requirement).
  See §5a for full details.

### 🔒 TASK 15 — Signup ab server-side OTP-verified check karta hai
Pehle `SignupSerializer.create()` seedha `is_verified=True` set kar deta
tha "OTP verify-otp step se pehle hi ho chuka hota hai" trust ke saath —
lekin `/verify-otp/` aur `/signup/` do independent endpoints the jo sirf
*frontend* ki call-ordering se linked the. `/signup/` ko seedha, bina
kisi OTP step ke, hit karna bilkul waisे hi kaam karta.
- `OTPVerification` model me naya **`is_verified`** field — `VerifyOTPView`
  isse `True` set karta hai jab `check_otp()` signup branch (no matching
  user) ke liye succeed ho.
- `SignupSerializer.validate()` ab email ya phone ke against ek real,
  unexpired, `is_verified=True` `OTPVerification` row require karta hai
  — nahi to `"Please verify your email or phone with OTP before signing
  up."` error.
- Verified OTP row `create()` ke andar **consume (delete)** ho jaati hai
  account banne ke turant baad, taaki same verified OTP dobara kisi
  second signup ke liye replay na ho sake.
- Login branch (existing user) is field ko touch nahi karta — woh row ko
  turant delete kar deta hai success par, is field ki zaroorat hi nahi.

### 🔔 TASK 16 — OTP-login ab ek heads-up notification bhejta hai
`VerifyOTPView`'s "user already exists" branch confirm kiya gaya as
**intended** passwordless "login via OTP" feature (WhatsApp/Telegram-
style alternate login — email/phone control prove karna hi yahan ka auth
factor hai, forgotten-password bypass nahi). Naya safety net: agar account
ka usable password hai (Google-only account nahi) aur `user.email` set hai,
ek "New sign-in to your account" email jaata hai us OTP-login ke baad —
best-effort (`fail_silently=True`, exception logged), taaki ek failed
notification kabhi legitimate login block na kare.

---

## 0.2 Changelog — v3 → v4 (is round me kya fix/add hua)

### 🔑 FIX B-7 — Dedicated Forgot/Reset Password flow (naya)
Pehle is app me password-reset ka koi seedha rasta nahi tha — sirf
`VerifyOTPView`'s OTP-login branch, jo email/phone control prove karke
seedha session issue kar deta tha, bina password ko kabhi touch kiye
(§11 item 6, ab resolved). Agar koi user apna password bhool jaaye
(lekin account access chahiye without necessarily wanting to reuse
OTP-login), koi clean path nahi tha. Ab:
- **Naya `ForgotPasswordSerializer`/`ResetPasswordSerializer`**
  (serializers.py, §4) aur **`ForgotPasswordView`/`ResetPasswordView`**
  (views.py, §5) — do-step flow, `SendOTPView`/`VerifyOTPView` se
  deliberately alag rakha gaya (§5's view notes me poori reasoning).
- `ForgotPasswordView` **account-enumeration-safe** hai — same generic
  200 response chahe account exist kare ya na kare; code sirf tabhi
  bhejta hai jab account genuinely exist kare.
- `ResetPasswordView` seedha naya password set kar deta hai (`set_password`
  + save) — **koi token issue nahi hota, koi session start nahi hoti** —
  user ko normally `/login/` se dobara login karna padta hai, bilkul
  `ChangePasswordAPIView` jaisa outcome, `VerifyOTPView`'s OTP-login
  jaisa nahi.
- Naye routes: `POST /auth/forgot-password/`, `POST /auth/reset-password/`
  (urls.py, §9). Naye throttle scopes: `forgot_password` (5/min
  suggested), `reset_password` (10/min suggested) — **abhi confirm nahi
  hue `settings.py` me**, see §11 item 5.
- See §10.8 for the full step-by-step flow.

---

## 1. App Overview

**App name:** `login`
**Purpose:** Full authentication system — signup (server-side OTP-gated),
login (username or email), Google Sign-In (login+signup combined),
email **and phone** OTP verification (phone via MSG91), OTP-based
passwordless login with a post-hoc security notification, change
password, complete-profile (post-Google phone capture), JWT
access/refresh tokens (via `rest_framework_simplejwt`).

**Tech stack:** Django + Django REST Framework + `rest_framework_simplejwt`
(JWT auth) + `drf-spectacular` (OpenAPI docs) + `google-auth` (Google
ID-token verification) + Django's `send_mail` (email OTP + login
notifications) + `requests` → MSG91 (`POST /api/v5/otp`, phone OTP
delivery).

**Custom User model:** This app **defines and owns** the project's custom
`User` model (`AbstractUser` subclass) — this is the app your
`AUTH_USER_MODEL` in `settings.py` must point to.

**Files in this app:**
| File | Responsibility |
|---|---|
| `models.py` | `User` (custom auth user) + `OTPVerification` model (now with `is_verified`) |
| `serializers.py` | All request serializers + shared password-strength/phone validators |
| `views.py` | All API endpoint logic (Login, Signup, Google auth, OTP, change-password, complete-profile) |
| `sms_service.py` | Phone-OTP delivery via MSG91 (`send_otp_sms`, `SMSDeliveryError`) |
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

# --- Email (used for OTP delivery + login-notification emails) ---
DEFAULT_FROM_EMAIL = "noreply@yourapp.com"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # or console backend for dev
# + EMAIL_HOST / EMAIL_PORT / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD / EMAIL_USE_TLS

# --- SMS (phone OTP delivery via MSG91 — v3, see §5a) ---
MSG91_AUTH_KEY = "your-msg91-auth-key"          # likely already set for liveclass notifications
MSG91_OTP_TEMPLATE_ID = "your-dlt-registered-otp-template-id"  # NEW — must contain a ##OTP## variable

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
# google-auth requests
```

Also required in your project's root `urls.py`:
```python
path('login/', include('login.urls')),   # or whatever prefix you choose — see §7 note
```

Without `DEFAULT_THROTTLE_RATES["send_otp"]` and `["verify_otp"]` set,
`SendOTPView`/`VerifyOTPView` will raise a throttle **misconfiguration
error** at request time (not silently skip throttling) — this is a hard
requirement, not optional.

**v3:** `MSG91_AUTH_KEY` / `MSG91_OTP_TEMPLATE_ID` are only required if
you actually want phone-OTP delivery to work. Missing/empty config
doesn't crash the app — `send_otp_sms()` fails loud with a caught
`SMSDeliveryError`, and `SendOTPView` turns that into a clean `503`
response for phone targets (email OTP is unaffected either way).

---

## 3. `models.py` (full code)

```python
# login/models.py
"""
Production hardening pass on the original login/models.py.

Everything changed here and WHY (so nothing gets silently "fixed" without
a paper trail — same spirit as the comments already in core/models.py):

1. profile_photo cleanup (old save()/delete() overrides) — REMOVED and
   replaced with signals (pre_save + post_delete). Reasons:
     - The old code used `old.profile_photo.path` / `os.path.isfile` /
       `os.remove`. `.path` only exists for `FileSystemStorage`. The
       moment this project moves `DEFAULT_FILE_STORAGE` to S3 / GCS /
       Azure (which almost every production Django app eventually does
       for user-uploaded media), `.path` raises `NotImplementedError`
       and EVERY save/delete of a User with a photo starts 500-ing.
     - `delete()` overrides are silently skipped by bulk operations —
       `User.objects.filter(...).delete()` and admin "delete selected"
       both call the *QuerySet's* delete(), never the model's
       `delete()`. So the old code only worked for `instance.delete()`,
       not the two most common ways users actually get deleted in
       practice (admin bulk actions, cleanup scripts, cascades).
     - Signals (`pre_save`, `post_delete`) fire in both cases and use
       `field.storage.delete(name)` / `field.storage.exists(name)`,
       which work identically on local disk or any cloud backend.
   `pre_save` still does one extra SELECT to diff old vs new photo —
   same cost as before, just relocated.

2. `phone` — added a format validator. Also: `unique=True` + `blank=True`
   on a CharField is a classic footgun — an unfilled phone saves as `''`,
   not `NULL`, so the *second* user who leaves phone blank hits a unique
   constraint violation on `''`. Normalized in `save()` so blank always
   becomes `None` (Postgres allows unlimited NULLs under a unique
   constraint, but only one `''`).

3. `email` — AbstractUser's default `email` is NOT unique. Login-by-email,
   password reset, and "email already registered" checks are all broken
   or race-prone without a DB-level uniqueness guarantee. Made unique at
   the DB level (still nullable/blank so social-only signups aren't
   forced to supply one).

4. `followers_count` / `following_count` / `posts_count` / `coin` —
   these are denormalized counters, not source of truth. Left the field
   types as-is (PositiveIntegerField already gets a DB-level >=0 CHECK
   constraint from Django 4.1+), but documented that these must only
   ever move via `F('...') + 1` inside a transaction from the
   Follow/Post/CoinLedger write paths — never via `user.followers_count
   = user.followers_count + 1; user.save()` (that's a read-modify-write
   race under concurrent requests).

5. Added indexes actually used by real queries (verified badge filter,
   private-account filter) instead of leaving every boolean unindexed.

OTPVerification:
6. Added an index on `created_at` — the periodic cleanup job ("delete
   expired OTP rows") and the expiry check both filter/sort on it; today
   that's a full table scan once this table has any real volume.
7. `register_failed_attempt` now wrapped so `is_locked()` can be trusted
   immediately after — no behavioural change, just documented the
   invariant since `check_otp` callers depend on it.
8. Added `is_verified` (task 15 — signup server-side OTP-verified check).
   Previously "OTP was verified" lived nowhere in the DB for the
   signup (no-existing-user) branch of VerifyOTPView — that view just
   returned `user_exists: False` and left the OTPVerification row
   sitting there, unconsumed, trusting the *client* to only call
   /signup/ after a successful /verify-otp/ response. Nothing stopped
   a client from skipping straight to /signup/ with no OTP step at
   all. `is_verified` is now the durable, server-side fact
   SignupSerializer checks before creating an account, and the row is
   deleted once signup consumes it (see serializers.py) so it can't be
   replayed for a second signup.
"""
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

    # Task 15: set True by VerifyOTPView the moment `check_otp()` succeeds
    # for a target with no matching user yet (i.e. the signup branch).
    # SignupSerializer.validate() requires this to be True (and the row
    # unexpired) before it will create an account — that's the actual
    # server-side link between "OTP verified" and "signup allowed";
    # without it, that link only ever existed in the frontend's call
    # ordering, which a direct API call could simply skip. Left False
    # (irrelevant) for the login branch, since that branch deletes the
    # row immediately on success instead of persisting it — see
    # VerifyOTPView / task 16.
    is_verified = models.BooleanField(default=False)

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
- **`OTPVerification.is_verified`** — **v3, new** (TASK 15). Set `True`
  by `VerifyOTPView` the moment `check_otp()` succeeds for a target with
  **no matching user yet** (the signup branch) — left `False` for the
  login branch, since that branch deletes the row immediately on success
  instead of persisting it. `SignupSerializer.validate()` now requires a
  real, unexpired, `is_verified=True` row for the submitted email/phone
  before it will create an account — this is the actual server-side link
  between "OTP verified" and "signup allowed" (see §4 notes). This
  replaces what used to be a comment-only assumption that the frontend
  always called `/verify-otp/` before `/signup/`.

---

## 4. `serializers.py` (full code)

```python
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
- **`SignupSerializer.validate()` — v3, new server-side OTP gate**
  (TASK 15). Looks up `OTPVerification.objects.filter(Q(target=email) |
  Q(target=phone), is_verified=True).order_by("-created_at").first()`
  and rejects the signup (`"Please verify your email or phone with OTP
  before signing up."`) unless a matching, unexpired row exists. The
  matched row is stashed in `attrs["_otp_obj"]` and consumed (deleted)
  inside `create()` once the account actually exists, so the same
  verified OTP can never be replayed for a second signup. Previously
  `is_verified=True` was set on the new user unconditionally, on the
  comment-only assumption that `/verify-otp/` had already run.
- **`ForgotPasswordSerializer` / `ResetPasswordSerializer` — NEW, v4
  (fix B-7).** Deliberately their own dedicated pair, not a reuse of
  `SendOTPSerializer`/`VerifyOTPSerializer` — see the module-level
  comment right above them and §5's view notes for why this needs to be
  a separate flow from the OTP-login branch of `VerifyOTPView`.
  `ResetPasswordSerializer.validate_new_password` reuses
  `validate_strong_password()` (same shared rule as signup/change-
  password — never a weaker password accepted just because it arrived
  via the reset flow), and `validate()` enforces the same
  `new_password`/`confirm_password` match check `ChangePasswordSerializer`
  already does.

---

## 5. `views.py` (full code)

```python
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


#-------------  forgot / reset password (B-7)  ---------------------------

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
```

### View notes / what changed in v3
- **`SendOTPView`'s phone branch** (TASK 14) — previously a hardcoded
  `501 Not Implemented`. Now calls `send_otp_sms(target, otp_code)`
  (§5a); a caught `SMSDeliveryError` becomes a `503`, same response
  shape as the existing email-send failure path just above it in the
  code. No branching needed anywhere else — phone and email OTPs are
  verified identically in `VerifyOTPView` since both check against our
  own `otp_hash`.
- **`VerifyOTPView`'s "user exists" branch** (TASK 16) now sends a
  best-effort "New sign-in to your account" email after issuing tokens,
  but only if the account `has_usable_password()` (skips Google-only
  accounts, which have no password to reset) and has an email on file.
  `fail_silently=True` + a logged exception — a failed notification must
  never block a legitimate OTP login.
- **`VerifyOTPView`'s "user doesn't exist" branch** (TASK 15) now sets
  `otp_obj.is_verified = True` and saves it, instead of just returning
  `user_exists: False` and leaving the row's verification status
  unrecorded. Still not deleted here — `/signup/` is what consumes it
  (see §4's `SignupSerializer.validate()`/`create()` notes) — it still
  naturally expires via `is_expired()` if signup is never completed.
- `logger = logging.getLogger(__name__)` — new at module level, used by
  the OTP-login notification's exception handler.

### View notes — what changed in v4 (this round)
- **`ForgotPasswordView` / `ResetPasswordView` — NEW (fix B-7).** Closes
  the exact gap §11's old "Known Issues" list used to flag ("there is no
  separate 'forgot password' flow" — see that section for the note this
  supersedes). A dedicated two-step flow, deliberately **not** built on
  `VerifyOTPView`/`SendOTPView`:
  - **`ForgotPasswordView`** (`POST /auth/forgot-password/`, `AllowAny`,
    throttled `forgot_password`) — unlike `SendOTPView` (which is fine
    sending a code to a target that doesn't have an account yet, since
    that's the signup case), this view would be an **account-enumeration
    oracle** if it behaved the same way here ("submit an email, learn
    whether it has an account" via whether a code arrives). So it always
    returns the same generic 200 (`"If an account exists for that
    email/phone, a reset code has been sent."`) whether or not a match
    was found, and only actually sends a code (email via `send_mail`, or
    phone via `sms_service.send_otp_sms`, §5a) when a matching `User`
    genuinely exists. A real email/SMS delivery failure still surfaces
    as a 503 for a target that *does* have an account (can't return a
    generic success there without silently losing the reset), same
    don't-pretend-it-worked posture as `SendOTPView`.
  - **`ResetPasswordView`** (`POST /auth/reset-password/`, `AllowAny`,
    throttled `reset_password`) — verifies the code from step 1
    (hashed compare, same lockout/expiry rules as every other
    `OTPVerification` row) and, on success, sets the new password
    directly (`user.set_password()` + `save(update_fields=["password"])`)
    in the same request. Deliberately mirrors `ChangePasswordAPIView`'s
    outcome (a changed password, no session issued) rather than
    `VerifyOTPView`'s OTP-login branch (which logs the caller in) — a
    correct reset code proves control of the email/phone, not an
    intent to start a session. Best-effort "your password was reset"
    email afterwards (`fail_silently=True`, logged on failure), same
    spirit as `VerifyOTPView`'s OTP-login notification (TASK 16).
  - Both throttle scopes (`forgot_password` 5/min suggested,
    `reset_password` 10/min suggested) need `REST_FRAMEWORK
    ["DEFAULT_THROTTLE_RATES"]` entries in `settings.py` — **not
    confirmed present** in this app's own settings excerpt (§2 doesn't
    list them); same recurring bug class flagged elsewhere in this doc
    for other throttle scopes — an unconfigured scope raises
    `ImproperlyConfigured` on first use, not a silent no-op.

---

## 5a. `sms_service.py` (new in v3 — full code)

Phone-OTP delivery via MSG91. Not exposed as its own endpoint — it's a
plain module called from `SendOTPView` (§5).

```python
# login/sms_service.py
"""
Phone-OTP delivery via MSG91.

WHY MSG91 and not Twilio: the project already has an MSG91 account wired
up for liveclass notifications (see `liveclass/notifications.py`
`_send_sms` / `_send_whatsapp`, and `MSG91_AUTH_KEY` /
`MSG91_SMS_SENDER_ID` in settings.py). Standing up a second SMS vendor
(Twilio) just for login OTP would mean two vendor accounts, two sets of
credentials, and two billing relationships for the exact same job — this
reuses the existing MSG91 account instead.

WHY the OTP endpoint specifically (not the generic MSG91 "send SMS" /
flow API that `_send_sms` uses): MSG91's `POST /api/v5/otp` endpoint is
purpose-built for OTP delivery and — critically — accepts an `otp`
query param so *we* still generate and hash the OTP ourselves (see
`OTPVerification.set_otp` in models.py, using `secrets`, never MSG91's
own auto-generated code). MSG91 is used purely as the delivery channel;
the code, its hash, expiry and attempt-lock all continue to live in our
own DB exactly as they do for the email path. This also means
`VerifyOTPView` doesn't need any branching for phone vs email — both
paths verify the same way, against our own `otp_hash`.

Needs a DLT-registered OTP template on the MSG91 dashboard containing a
`##OTP##` variable (India's TRAI/DLT regulations require this for any
transactional SMS) — its ID goes in `MSG91_OTP_TEMPLATE_ID` (settings.py).
`MSG91_AUTH_KEY` is already shared with the liveclass notifications.

Fails LOUD, on purpose: `_send_sms` in liveclass is a best-effort
notification (it no-ops on missing config, since a missed "class
starting soon" ping isn't blocking). An OTP that silently fails to send
is a broken signup/login with no path forward for the user — so every
failure here raises `SMSDeliveryError`, and `SendOTPView` (views.py)
catches it and returns 503, same shape as the existing email failure
path.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MSG91_OTP_URL = "https://api.msg91.com/api/v5/otp"
REQUEST_TIMEOUT_SECONDS = 8


class SMSDeliveryError(Exception):
    """Raised whenever the OTP could not be handed off to MSG91 for
    delivery — missing config, network failure, or a non-success
    response from MSG91 itself. Callers should treat this the same as
    the existing `send_mail` failure path (503, don't leak internals)."""


def send_otp_sms(phone: str, otp_code: str) -> None:
    """Send `otp_code` to `phone` via MSG91's OTP API.

    `phone` is expected in the same international format already
    enforced by `login.models.phone_validator` (e.g. +919876543210).
    MSG91 wants the number without a leading '+', so that's stripped
    here rather than pushing this MSG91-specific quirk up into the view.

    Raises `SMSDeliveryError` on any failure. Returns None on success.
    """
    if not (settings.MSG91_AUTH_KEY and settings.MSG91_OTP_TEMPLATE_ID):
        # Fail loud (unlike liveclass's best-effort notifications) —
        # see module docstring. An unconfigured SMS provider must not
        # look like a successful send to the caller.
        logger.error(
            "Phone OTP requested but MSG91_AUTH_KEY / MSG91_OTP_TEMPLATE_ID "
            "is not configured."
        )
        raise SMSDeliveryError("SMS provider is not configured.")

    mobile = phone.lstrip("+")

    try:
        response = requests.post(
            MSG91_OTP_URL,
            params={
                "template_id": settings.MSG91_OTP_TEMPLATE_ID,
                "mobile": mobile,
                "otp": otp_code,
                "authkey": settings.MSG91_AUTH_KEY,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.exception("MSG91 OTP request failed for phone=%s", mobile)
        raise SMSDeliveryError("Could not reach SMS provider.") from None

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    # MSG91 returns HTTP 200 with `{"type": "success", ...}` on success
    # and `{"type": "error", "message": "..."}` on failure (bad
    # template id, unroutable number, insufficient balance, etc.) — a
    # 200 status code alone does NOT mean delivery was accepted.
    if response.status_code != 200 or payload.get("type") != "success":
        logger.error(
            "MSG91 OTP send failed for phone=%s status=%s body=%s",
            mobile,
            response.status_code,
            payload or response.text[:500],
        )
        raise SMSDeliveryError("SMS provider rejected the request.")
```

### `sms_service.py` notes
- **Why MSG91, not Twilio:** the project already has an MSG91 account
  wired up for `liveclass` notifications (`_send_sms`/`_send_whatsapp`
  in `liveclass/notifications.py`, `MSG91_AUTH_KEY`/`MSG91_SMS_SENDER_ID`
  in settings.py). Standing up a second SMS vendor just for login OTP
  would mean a second vendor account, credentials, and billing
  relationship for the exact same job.
- **Why the OTP-specific endpoint** (`/api/v5/otp`), not MSG91's generic
  "send SMS" API: it accepts an `otp` query param, so **this codebase
  still generates and hashes the OTP itself** (`secrets`, then
  `OTPVerification.set_otp`) — MSG91 is purely the delivery channel, its
  own auto-generated-code feature is never used. This is also why
  `VerifyOTPView` needs zero phone-vs-email branching: both paths verify
  against the same locally-stored `otp_hash`.
- **Fails loud, on purpose** — unlike `liveclass`'s best-effort
  `_send_sms` (a missed "class starting soon" ping isn't blocking), a
  silently-failed OTP is a broken signup/login with no path forward for
  the user. Every failure mode (missing config, network failure, a
  non-2xx or `{{"type": "error", ...}}` response from MSG91) raises
  `SMSDeliveryError`; `SendOTPView` catches it and returns `503`, the
  same shape as the existing email-failure path.
- `phone.lstrip("+")` before sending — MSG91 wants the number without a
  leading `+`; `login.models.phone_validator` already guarantees the
  international-format input this expects (e.g. `+919876543210`).
- **`MSG91_AUTH_KEY` / `MSG91_OTP_TEMPLATE_ID`** must both be set in
  `settings.py` (see §2) — `MSG91_OTP_TEMPLATE_ID` needs a DLT-registered
  template on the MSG91 dashboard containing a `##OTP##` variable
  (required by India's TRAI/DLT regulations for transactional SMS).
- A `200` HTTP status from MSG91 does **not** by itself mean the OTP was
  accepted for delivery — MSG91 returns `200` with
  `{{"type": "error", "message": "..."}}` for failures like a bad
  template id, an unroutable number, or insufficient balance. The code
  checks `payload.get("type") != "success"` in addition to the status
  code.

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
# login/urls.py
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

    # 🔥 FIX (B-7) — dedicated forgot-password flow. Two steps, neither of
    # which returns a session: request a code for a known account, then
    # spend that code + a new password to actually change it. See
    # ForgotPasswordView / ResetPasswordView docstrings in views.py for why
    # this isn't just VerifyOTPView reused — that view's OTP-login branch
    # logs the user in and never touches the password, a different feature.
    path("auth/forgot-password/", ForgotPasswordView.as_view(), name="forgot-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="reset-password"),

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
| POST | `/login/auth/send-otp/` | `SendOTPView` | ❌ (public, throttled) | Send OTP to email (`send_mail`) or phone (MSG91, v3) |
| POST | `/login/auth/verify-otp/` | `VerifyOTPView` | ❌ (public, throttled) | Verify OTP; logs in if user exists, else signals "proceed to signup" |
| POST | `/login/auth/change-password/` | `ChangePasswordAPIView` | ✅ | Change password for logged-in user |
| POST | `/login/auth/google/` | `GoogleAuthView` | ❌ (public) | Google Sign-In — login or signup in one call |
| POST | `/login/auth/complete-profile/` | `CompleteProfileView` | ✅ | Add phone number after Google signup |
| POST | `/login/auth/forgot-password/` | `ForgotPasswordView` | ❌ (public, throttled) | **NEW, v4 (B-7)** — request a reset code for an existing account; same generic response whether or not the account exists |
| POST | `/login/auth/reset-password/` | `ResetPasswordView` | ❌ (public, throttled) | **NEW, v4 (B-7)** — verify that reset code and set a new password in the same call; never issues tokens |
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
          phone unique + format valid (phone_validator),
          password strength rule, password == confirm_password
        │
        ▼
v3 — SERVER-SIDE OTP GATE (TASK 15): look up
OTPVerification.objects.filter(Q(target=email) | Q(target=phone),
                                is_verified=True)
                       .order_by("-created_at").first()
  → none found, or found but expired
        → 400 "Please verify your email or phone with OTP before
           signing up."
  → found + unexpired → stash it (attrs["_otp_obj"]) for create()
        │
        ▼
create_user(..., is_verified=True)   # now actually gated by the check
        │                              above, not assumed
        ▼
Consume (delete) the matched OTPVerification row — can't be replayed
for a second signup
        │
        ▼
Issue RefreshToken.for_user(user) → 201 with user info + tokens
```
✅ **v3 fix (TASK 15):** `Signup` now **does** re-check server-side that
an OTP was verified for this email/phone — previously the flow only
*assumed* the frontend called `/auth/send-otp/` → `/auth/verify-otp/`
first, with nothing stopping a direct `/signup/` call with no OTP step
at all. That link is now enforced via `OTPVerification.is_verified`
(set by `VerifyOTPView`'s "user doesn't exist" branch — see §10.4) and
checked in `SignupSerializer.validate()`.

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

### 10.4 Email / Phone OTP flow (Send → Verify)
```
POST /auth/send-otp/  {"email_or_phone": "..."}
   - generate 6-digit secrets.randbelow() code, hash + store
     (update_or_create — old OTP for same target overwritten)
   - email targets → deliver via send_mail(); failure → 503
   - phone targets → v3 (TASK 14): deliver via send_otp_sms() (MSG91,
     see §5a); failure (SMSDeliveryError) → 503, same shape as the
     email failure path
   - OTP itself never appears in the response either way
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
           → v3 (TASK 16): if the account has_usable_password() (not a
             Google-only account) and has an email on file, also sends
             a best-effort "New sign-in to your account" heads-up email
             (fail_silently=True, exception logged) — never blocks the
             login on failure
       - user does not exist
           → v3 (TASK 15): row's `is_verified` is set True and saved
             (NOT deleted yet — /signup/ consumes/deletes it), so
             SignupSerializer.validate() can trust it server-side;
             "user_exists": false → frontend should now call /signup/
             to finish registration
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

### 10.8 Forgot / Reset Password (`POST /login/auth/forgot-password/` → `POST /login/auth/reset-password/`) — **NEW, v4 (fix B-7)**
```
Step 1 — POST /login/auth/forgot-password/
Body: {"email_or_phone": "..."}
        │
        ▼
Does a User exist for this email/phone (or username)?
  → NO  → still 200 "If an account exists for that email/phone, a
           reset code has been sent." (no code actually sent — same
           generic response either way, so the response alone can't be
           used to enumerate accounts)
  → YES → generate a 6-digit OTP (secrets.randbelow), update_or_create
           the OTPVerification row for this target, send it via
           send_mail (email) or sms_service.send_otp_sms (phone)
        → delivery failure → 503 (only reachable for a target that DOES
           have an account — a nonexistent target never gets this far)
        → delivery success → same generic 200 as the "no account" branch

Step 2 — POST /login/auth/reset-password/
Body: {"email_or_phone": "...", "otp": "123456",
       "new_password": "...", "confirm_password": "..."}
        │
        ▼
Look up OTPVerification for this target
  → none found → 400 "No reset code request found for this identifier."
  → expired → delete row, 400 "Reset code has expired."
  → locked (MAX_ATTEMPTS reached) → delete row, 429 "Too many incorrect
     attempts. Please request a new code."
  → wrong code → register_failed_attempt(), 400 "Invalid reset code."
  → correct code → look up the User (by username/email or
     username/phone); if the account vanished in the meantime, delete
     the row and return the same generic "Invalid or expired reset
     code." 400 rather than a distinct error
        │
        ▼
user.set_password(new_password) + save(update_fields=["password"])
        │
        ▼
Delete the OTPVerification row (can't be replayed for a second reset)
        │
        ▼
Best-effort "your password was reset" email (fail_silently=True,
logged on failure) — never blocks the response
        │
        ▼
200 "Password has been reset successfully. Please log in with your new
password." — **no tokens issued, no session started** — the user logs
in normally afterwards via `POST /login/` with the new password.
```
Deliberately **not** the same code path as `VerifyOTPView`'s OTP-login
branch (§10.4) — that branch's whole point is a passwordless login
shortcut (logs the caller in on a correct OTP, never touches the
password); building password-reset on top of it would conflate "proved
I own this email" with "here is a session", which is exactly the
ambiguity this dedicated flow avoids. See §4/§5 notes for the
serializer/view-level detail.

---

## 11. Known Issues / Security Notes To Double-Check

1. ✅ **FIXED (v3, TASK 14)** — Phone OTP previously returned `501` for
   non-email targets. Now delivered via MSG91 (`login/sms_service.py`,
   `send_otp_sms()`) — see §5a. A misconfigured/unreachable provider
   fails loud (`SMSDeliveryError` → `503`), it does not silently pretend
   to succeed.
2. ✅ **FIXED (v3, TASK 15)** — Signup previously didn't verify OTP
   itself; the link between "OTP was verified" and "signup is now
   allowed" existed only in frontend flow ordering. Now
   `OTPVerification.is_verified` (set by `VerifyOTPView`) is a real,
   server-side, DB-backed fact that `SignupSerializer.validate()`
   requires before it will create an account — see §10.2.
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
   **v4 note:** the two new scopes from the forgot/reset-password flow
   (`forgot_password`, `reset_password` — §5/§10.8) are the same
   requirement and are **not confirmed present** in this app's own
   `settings.py` excerpt (§2) — same failure mode, `ImproperlyConfigured`
   on the very first request, not a silent skip.
6. ~~**`VerifyOTPView` "user_exists" branch effectively double-authenticates
   as login** — verifying an OTP for an *existing* user's email logs them
   straight in with tokens, no password needed. This is by design (OTP
   login), but make sure this endpoint is not reachable/misused as a
   password-reset bypass unless that's intended — currently there's no
   separate "forgot password" flow, so this OTP-login path may be doing
   double duty. Worth confirming this matches your intended product
   behavior.~~ **Resolved, v4 (fix B-7)** — a dedicated
   `ForgotPasswordView`/`ResetPasswordView` flow now exists (§5/§10.8),
   completely separate from `VerifyOTPView`'s OTP-login branch. The two
   remain intentionally distinct rather than one being removed:
   OTP-login (`VerifyOTPView`) proves email/phone ownership and starts a
   session without ever touching the password; forgot/reset password
   proves the same thing but only ever sets a new password and never
   issues tokens. Worth a product-level confirm that this dual-path
   design (two different ways to prove the same email/phone ownership,
   for two different outcomes) is intended, rather than a leftover gap —
   but it's no longer a *missing* feature, just two deliberate paths.
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
- [ ] For phone OTP to actually work (v3 — already wired to MSG91): set
      `MSG91_AUTH_KEY` and `MSG91_OTP_TEMPLATE_ID` in `settings.py` (see
      §2, §5a). Without these, phone-OTP requests fail loud with a `503`
      instead of silently pretending to succeed.

With the above satisfied, everything in this single document — models,
serializers, views, urls, admin — is enough to run the full `login` app
end to end.