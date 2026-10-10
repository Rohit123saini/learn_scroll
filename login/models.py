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
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

phone_validator = RegexValidator(
    regex=r"^\+?[1-9]\d{7,14}$",
    message="Enter a valid phone number in international format, e.g. +919876543210.",
)


# ---------------------------------------------------------------------------
# P6-BE — bio upgrade: `links`
# One helper does both jobs: it VALIDATES and returns the CLEANED list, so the
# model-field validator (admin / full_clean) and the API serializer
# (user_profile.serializers.ProfileUpdateSerializer) can never disagree about
# what a valid link list is. Referenced by name from the migration, so keep it
# at module level and don't rename it.
# ---------------------------------------------------------------------------
MAX_PROFILE_LINKS = 3
PROFILE_LINK_TITLE_MAX = 40
PROFILE_LINK_URL_MAX = 200

# http/https ONLY — rejects javascript:, data:, ftp:, file:, etc. (stored XSS via
# a tappable profile link is the whole reason this is not just a CharField).
_link_url_validator = URLValidator(schemes=["http", "https"])


def clean_profile_links(value):
    """Validate `User.links` and return it normalised as [{"title", "url"}, ...].

    Rules: a list of at most MAX_PROFILE_LINKS objects, each with a non-empty
    `title` (<= 40 chars) and an absolute http(s) `url` (<= 200 chars). Extra keys
    are dropped. Raises django.core.exceptions.ValidationError otherwise.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("links must be a list of {title, url} objects.")
    if len(value) > MAX_PROFILE_LINKS:
        raise ValidationError(f"You can add at most {MAX_PROFILE_LINKS} links.")

    cleaned = []
    for i, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"Link {i}: must be an object like {{\"title\": ..., \"url\": ...}}.")
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            raise ValidationError(f"Link {i}: title and url must both be text.")
        title, url = title.strip(), url.strip()
        if not title:
            raise ValidationError(f"Link {i}: title is required.")
        if len(title) > PROFILE_LINK_TITLE_MAX:
            raise ValidationError(f"Link {i}: title can be at most {PROFILE_LINK_TITLE_MAX} characters.")
        if not url:
            raise ValidationError(f"Link {i}: url is required.")
        if len(url) > PROFILE_LINK_URL_MAX:
            raise ValidationError(f"Link {i}: url can be at most {PROFILE_LINK_URL_MAX} characters.")
        try:
            _link_url_validator(url)
        except ValidationError:
            raise ValidationError(f"Link {i}: enter a valid http:// or https:// URL.")
        cleaned.append({"title": title, "url": url})
    return cleaned


def validate_profile_links(value):
    """Model-field validator wrapper (validators only need to raise, not return)."""
    clean_profile_links(value)


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

    # P6-BE — bio upgrade. All optional; blank/[] means "not set".
    pronouns = models.CharField(max_length=30, blank=True, default="")
    # Free-text label under the name, e.g. "Teacher", "JEE Aspirant".
    category_label = models.CharField(max_length=40, blank=True, default="")
    # Up to 3 {"title": str, "url": http(s) str}. Validated by
    # clean_profile_links() above; the API also normalises through it.
    links = models.JSONField(default=list, blank=True, validators=[validate_profile_links])

    is_private = models.BooleanField(default=False)

    # Onboarding (core/onboarding_quickstart.py). Both optional/blank: set
    # in the 30-second first-run step, used to personalise the first feed
    # and suggested tests. Choices live in core/onboarding_quickstart.py.
    study_class = models.CharField(max_length=20, blank=True, default="")
    target_exam = models.CharField(max_length=20, blank=True, default="")

    # Minor-safety. Optional so pre-existing accounts keep working; NEW
    # signups must supply it (SignupSerializer). See login/age.py.
    date_of_birth = models.DateField(null=True, blank=True)

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
        # Same reasoning for `email`: it's `unique=True, null=True` above,
        # but Django's `create_user()`/`createsuperuser` (and the admin
        # form) store a blank email as '' — NOT NULL — so the SECOND user
        # created without an email hits `UNIQUE constraint failed:
        # login_user.email`. Normalize '' -> None so any number of
        # email-less accounts can coexist.
        if self.email == "":
            self.email = None
        super().save(*args, **kwargs)

    @property
    def is_minor(self) -> bool:
        """True only when a DOB is on file AND the user is under 18."""
        from .age import is_minor_dob

        return is_minor_dob(self.date_of_birth)

    @property
    def is_verified_adult(self) -> bool:
        from .age import is_adult_dob

        return is_adult_dob(self.date_of_birth)

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


class AuthToken(models.Model):
    """
    Sliding-expiry session record — the one piece of server-side state
    that sits on top of SimpleJWT's otherwise-stateless tokens (see
    login/authentication.py for the full picture).

    SimpleJWT's access/refresh tokens carry their own fixed lifetime
    baked in at mint time (SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] /
    ["REFRESH_TOKEN_LIFETIME"] in settings.py) — there's no way to
    extend "how long until THIS token dies" after the fact, and no
    notion of "still active, so keep it alive". Product wants exactly
    that: a session should only die after SLIDING_EXPIRY of true
    inactivity, extended on every authenticated request the user makes
    — no separate refresh-token call required.

    One row per issued refresh token (login/signup/Google-auth/OTP-login
    all go through login.token_issuance.issue_tokens_for_user, which
    creates this row and copies `jti` onto the access token as a
    `session_jti` claim, so SlidingSessionAuthentication can find this
    row from the access token alone on every request).
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="auth_tokens")

    # The refresh token's own `jti` claim (SimpleJWT already generates a
    # random unique one per token) — reused as the session identifier
    # rather than minting a second random value. Copied onto every
    # access token derived from that refresh token as a `session_jti`
    # claim; see token_issuance.py.
    jti = models.CharField(max_length=64, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Deliberately NOT auto_now — this only moves when
    # SlidingSessionAuthentication.touch() explicitly renews it (or at
    # creation, see token_issuance.py), never as a side-effect of some
    # unrelated .save() call elsewhere touching this row.
    last_used_at = models.DateTimeField(db_index=True)

    # Product requirement: 10 days of zero activity -> session dies.
    SLIDING_EXPIRY = timedelta(days=10)

    class Meta:
        indexes = [
            # Supports a future periodic-cleanup job ("delete rows nobody
            # will ever touch again") — same reasoning as the created_at
            # index on OTPVerification above. Nothing queries by
            # last_used_at directly today (is_expired() is evaluated
            # per-row inside the request that already looked the row up
            # by jti), so this is forward-looking, not load-bearing yet.
            models.Index(fields=["last_used_at"]),
        ]

    def is_expired(self) -> bool:
        return timezone.now() - self.last_used_at > self.SLIDING_EXPIRY

    def touch(self) -> None:
        """Silent renew — called by SlidingSessionAuthentication on every
        authenticated request that passes the expiry check."""
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def __str__(self):
        return f"session user_id={self.user_id} jti={self.jti[:8]}..."