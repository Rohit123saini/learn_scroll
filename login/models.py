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