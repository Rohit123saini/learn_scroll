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
from django.contrib.postgres.indexes import GinIndex
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
MAX_PROFILE_LINKS = 5  # P1: was 3
PROFILE_LINK_TITLE_MAX = 40
PROFILE_LINK_URL_MAX = 200

# http/https ONLY — rejects javascript:, data:, ftp:, file:, etc. (stored XSS via
# a tappable profile link is the whole reason this is not just a CharField).
_link_url_validator = URLValidator(schemes=["http", "https"])

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp")


def is_public_http_url(url) -> bool:
    """P11: True only for an http(s) address a stranger may be sent to by tapping it.

    Refused: any other scheme (javascript:, data:, file:, ftp: ...), `user:pass@host` / `google.com@evil.com` style
    addresses (the part before @ is a classic phishing disguise), control characters / spaces, `localhost`, internal-only
    suffixes, single-label hosts, and IP addresses that are not public (loopback, 10.x, 192.168.x, 169.254.x ...).
    Never raises."""
    import ipaddress
    from urllib.parse import urlsplit

    try:
        if not isinstance(url, str):
            return False
        url = url.strip()
        if not url or any(ord(c) < 32 or c.isspace() for c in url):
            return False
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            return False
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            return False
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return "." in host  # a real domain has at least one dot
        return bool(ip.is_global)
    except Exception:
        return False


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
        if not is_public_http_url(url):  # P11: no user@host disguises, localhost, private IPs
            raise ValidationError(f"Link {i}: this address can't be used on a profile.")
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

    # L1 gap 4 -- True only when the owner proved they control `phone` with an
    # OTP (signup, complete-profile, or a successful OTP login on that number).
    # New phones are never saved without it. Rows that already had a phone
    # before this change start as False (unverified/legacy).
    phone_verified = models.BooleanField(default=False)

    # AbstractUser.email is NOT unique by default — made it unique here so
    # "sign in with email" / "email already registered" checks can rely on
    # the DB instead of a racy SELECT-then-INSERT in application code.
    email = models.EmailField("email address", unique=True, blank=True, null=True)

    profile_photo = models.ImageField(
        upload_to="profile/",
        null=True,
        blank=True,
    )
    # P10b - BlurHash (~28 chars) of profile_photo, drawn as the avatar placeholder while the photo downloads.
    # Filled by `_refresh_profile_photo_blurhash` (pre_save) when a NEW photo is uploaded, cleared when the photo is
    # removed; old accounts: `manage.py backfill_profile_blurhash`. Hand-written migration login/0013.
    profile_photo_blurhash = models.CharField(max_length=40, blank=True, default="")

    bio = models.TextField(blank=True)

    # P6-BE — bio upgrade. All optional; blank/[] means "not set".
    pronouns = models.CharField(max_length=30, blank=True, default="")
    # Free-text label under the name, e.g. "Teacher", "JEE Aspirant".
    category_label = models.CharField(max_length=40, blank=True, default="")
    # Up to 5 (MAX_PROFILE_LINKS) {"title": str, "url": http(s) str}. Validated by
    # clean_profile_links() above; the API also normalises through it.
    links = models.JSONField(default=list, blank=True, validators=[validate_profile_links])

    # P1 — profile header (hand-written migration login/0011_profile_header.py).
    # Optional banner behind the avatar; same upload checks as profile_photo (SafeCoverPhotoField).
    cover_photo = models.ImageField(upload_to="profile_covers/", null=True, blank=True)
    # Free-text "Kota, Rajasthan" — optional, never shown to others for under-18 accounts.
    location = models.CharField(max_length=60, blank=True, default="")
    # When the username was last changed (NULL = never). Drives the "1 change per 14 days" rule.
    username_changed_at = models.DateTimeField(null=True, blank=True)

    class VerifiedReason(models.TextChoices):
        IDENTITY = "identity", "Verified account"
        TEACHER = "teacher", "Verified teacher"
        CAMPUS = "campus", "Verified by campus"
        CREATOR = "creator", "Verified creator"
        OFFICIAL = "official", "Official account"

    # WHY the blue tick was given. Set by staff only (admin) — never writable through the profile API.
    # Blank on a verified account = legacy tick, shown as "Verified account".
    verified_reason = models.CharField(max_length=12, blank=True, default="", choices=VerifiedReason.choices)

    is_private = models.BooleanField(default=False)

    # Onboarding (core/onboarding_quickstart.py). Both optional/blank: set
    # in the 30-second first-run step, used to personalise the first feed
    # and suggested tests. Choices live in core/onboarding_quickstart.py.
    study_class = models.CharField(max_length=20, blank=True, default="")
    # T2.3 - role picked in onboarding ("student" | "teacher" | "parent"; "" = never chosen).
    # Only personalises first-run next steps / Home; it grants NO permissions (anyone can
    # still teach, learn or link a child) - see core/onboarding_quickstart.py.
    onboarding_role = models.CharField(max_length=10, blank=True, default="")
    target_exam = models.CharField(max_length=20, blank=True, default="")

    # Minor-safety. Optional so pre-existing accounts keep working; NEW
    # signups must supply it (SignupSerializer). See login/age.py.
    date_of_birth = models.DateField(null=True, blank=True)

    # P7 -- "show my birthday (day + month) on my profile". Off by default; can never be on for an under-18 account.
    # The year is never shown to anyone. See user_profile/profile_header.py::birthday_label.
    show_birthday = models.BooleanField(default=False)

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
            # P-C5: user search does `username/first_name/last_name icontains` = a sequential scan of the whole
            # user table. Trigram GIN lets Postgres use an index scan for substring matches. Needs `pg_trgm`
            # (login migration 0014 enables it); ignored on non-Postgres backends, same as the tuitionclass ones.
            GinIndex(fields=["username"], name="user_username_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["first_name"], name="user_first_name_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["last_name"], name="user_last_name_trgm", opclasses=["gin_trgm_ops"]),
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


@receiver(pre_save, sender=User)
def _refresh_profile_photo_blurhash(sender, instance: User, **kwargs):
    """P10b - keep `profile_photo_blurhash` in step with `profile_photo`.

    * no photo                      -> "" (removed / never had one)
    * a NEW upload (not yet stored) -> BlurHash computed from the uploaded file
    * same, already stored photo    -> untouched (no query, no file read)

    Never raises: a photo that cannot be decoded just gets no placeholder (the app falls back to the initial letter).
    NOTE: `save(update_fields=[...])` that lists `profile_photo` but not `profile_photo_blurhash` will not store the
    hash - the backfill command fixes such rows.
    """
    photo = instance.profile_photo
    if not photo:
        instance.profile_photo_blurhash = ""
        return
    if getattr(photo, "_committed", True):
        return  # already in storage, hash (if any) already computed
    try:
        from PIL import Image

        from post.services import blurhash_for_image  # lazy: login must not import post at load time

        f = photo.file
        try:
            f.seek(0)
        except Exception:  # noqa: BLE001 - some file objects are not seekable
            pass
        with Image.open(f) as img:
            instance.profile_photo_blurhash = blurhash_for_image(img)[:40]
        try:
            f.seek(0)  # storage must read the upload from the start
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - placeholder is optional, the save must go through
        instance.profile_photo_blurhash = ""


@receiver(pre_save, sender=User)
def _delete_old_cover_photo_on_change(sender, instance: User, **kwargs):
    """P1 — same as the profile photo: replacing or removing the cover deletes the old file."""
    if not instance.pk:
        return
    try:
        old = sender.objects.only("cover_photo").get(pk=instance.pk).cover_photo
    except sender.DoesNotExist:
        return
    if old and old != instance.cover_photo:
        old.storage.delete(old.name)


@receiver(post_delete, sender=User)
def _delete_profile_photo_on_user_delete(sender, instance: User, **kwargs):
    """Fires for both `instance.delete()` and bulk `queryset.delete()`,
    unlike an overridden `Model.delete()` (which bulk deletes bypass
    entirely)."""
    if instance.profile_photo:
        instance.profile_photo.storage.delete(instance.profile_photo.name)
    if instance.cover_photo:  # P1
        instance.cover_photo.storage.delete(instance.cover_photo.name)


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
    # L1 gap 12 -- phone-first signup: after the OTP is verified the user still
    # has to fill name / DOB / password, so a VERIFIED row lives longer than an
    # unverified code. It is still single-use (deleted when signup consumes it).
    VERIFIED_EXPIRY_MINUTES = 15

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
        minutes = self.VERIFIED_EXPIRY_MINUTES if self.is_verified else self.EXPIRY_MINUTES
        return timezone.now() > self.created_at + timedelta(minutes=minutes)

    def is_locked(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def attempts_left(self) -> int:
        """Wrong guesses still allowed before this code locks (L1 gap 14)."""
        return max(self.MAX_ATTEMPTS - self.attempts, 0)

    def register_failed_attempt(self) -> None:
        # Atomic increment: two parallel wrong guesses must both be counted,
        # otherwise the "N attempts left" shown to the user (and the lock
        # itself) can undercount. refresh_from_db keeps self.attempts exact.
        type(self).objects.filter(pk=self.pk).update(attempts=models.F("attempts") + 1)
        self.refresh_from_db(fields=["attempts"])

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

    # L1 gap 11 -- device info, so the user can see "where am I signed in" and
    # end one device. All of it is filled once, when the session is created
    # (login/device_info.py), and is informational only: nothing here is used
    # for authentication. `device_id` is a random per-install id the app sends
    # (kept across logouts); it lets a fresh login on the same phone REPLACE the
    # old row instead of piling up duplicates.
    device_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    device_name = models.CharField(max_length=120, blank=True, default="")
    platform = models.CharField(max_length=20, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")

    # Product requirement: 10 days of zero activity -> session dies.
    SLIDING_EXPIRY = timedelta(days=10)

    # L1 gap 11 -- at most this many live sessions per user; a new login beyond
    # it ends the least-recently-used one (override: settings.AUTH_MAX_SESSIONS_PER_USER).
    MAX_SESSIONS_PER_USER = 10

    class Meta:
        indexes = [
            models.Index(fields=["user", "last_used_at"], name="login_auth_user_used_idx"),
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


class AuthAuditLog(models.Model):
    """
    L1 gap 19 -- append-only audit trail of login / OTP / password-reset / session
    events. Written ONLY through ``login.audit.record()`` (never directly), which
    also masks the identifier, drops secrets and de-duplicates attacker-triggerable
    noise -- read that module's docstring for the rules.

    Answers: "was this account attacked?" (many ``login_failed`` / ``otp_failed``
    rows for one user), "which device reset the password?" (``reset_success`` +
    ip / device_id), "who is pumping SMS?" (``otp_send_blocked`` by ip).

    Design notes
      * ``user`` is a plain nullable FK with ``db_constraint=False`` + SET_NULL: an
        audit insert can never fail (or fail at COMMIT, FK checks are deferred on
        Postgres) because the account vanished, and deleting a user never
        cascades into -- or is blocked by -- its history.
      * The identifier is stored masked (``identifier_masked``) and as a keyed hash
        (``identifier_hash``) -- never in clear. Account deletion blanks both for
        that user's rows (login/account_deletion.py).
      * Rows are never edited: ``save()`` refuses updates, the admin is read-only.
        Old rows are removed in bulk by the retention task
        (login.tasks.purge_auth_audit_logs, AUTH_AUDIT_RETENTION_DAYS).
    """

    OUTCOME_SUCCESS = "success"
    OUTCOME_FAILURE = "failure"  # tried and failed (wrong password / code)
    OUTCOME_BLOCKED = "blocked"  # refused by a guard (lockout, rate limit, captcha)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    # login.audit.Event.* -- plain string, deliberately without `choices` so adding an
    # event later never needs a migration.
    event = models.CharField(max_length=40, db_index=True)
    outcome = models.CharField(max_length=10, default=OUTCOME_SUCCESS)

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auth_audit_logs",
        db_constraint=False,
    )

    identifier_kind = models.CharField(max_length=10, blank=True, default="")  # email | phone | username
    identifier_masked = models.CharField(max_length=120, blank=True, default="")
    identifier_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    device_id = models.CharField(max_length=64, blank=True, default="")
    platform = models.CharField(max_length=20, blank=True, default="")

    reason = models.CharField(max_length=64, blank=True, default="")  # stable machine code
    metadata = models.JSONField(default=dict, blank=True)  # small extras: attempts_left, retry_after ...

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event", "created_at"], name="login_audit_ev_created_idx"),
            models.Index(fields=["user", "created_at"], name="login_audit_user_created_idx"),
            models.Index(fields=["ip_address", "created_at"], name="login_audit_ip_created_idx"),
            models.Index(fields=["identifier_hash", "created_at"], name="login_audit_idh_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("AuthAuditLog rows are append-only and cannot be edited.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.event} [{self.outcome}] user_id={self.user_id}"



# ---------------------------------------------------------------------------
# S5 -- DPDP Act 2023 data rights: export my data, delete with grace, consent history.
# ---------------------------------------------------------------------------
class DataExportRequest(models.Model):
    """One "Download my data" job. The ZIP is built by a Celery task (login/data_rights.py) and kept in the
    default storage under a private path; it is handed out ONLY through the authenticated download endpoint
    until `expires_at` (7 days by default), then the file is deleted and the row is marked expired."""

    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_exports")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    file_name = models.CharField(max_length=255, blank=True, default="")  # storage key, empty until ready
    size_bytes = models.PositiveBigIntegerField(default=0)
    download_count = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=255, blank=True, default="")
    notified_at = models.DateTimeField(null=True, blank=True)  # "your export is ready" message sent

    class Meta:
        ordering = ["-requested_at", "-id"]
        indexes = [models.Index(fields=["user", "-requested_at"], name="login_dexp_user_req_idx")]

    def __str__(self):
        return f"export #{self.pk} user={self.user_id} {self.status}"


class AccountDeletionRequest(models.Model):
    """Delete-account with a grace period. While `status == pending` the account still works (the person can
    log in and cancel); at `scheduled_for` the Celery sweeper runs the real anonymise + erase
    (login/account_deletion.py::delete_account)."""

    STATUS_PENDING = "pending"
    STATUS_CANCELLED = "cancelled"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_COMPLETED, "Completed"),
    ]

    # no DB constraint on the FK target after anonymisation: the user row is kept (anonymised), so a plain FK is fine
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="deletion_requests")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    requested_at = models.DateTimeField(default=timezone.now)
    scheduled_for = models.DateTimeField(db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)  # "3 days left" nudge

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(status="pending"), name="login_delreq_one_pending_per_user"
            ),
        ]

    def __str__(self):
        return f"deletion #{self.pk} user={self.user_id} {self.status} @ {self.scheduled_for:%Y-%m-%d}"


class ConsentRecord(models.Model):
    """Append-only consent history (DPDP: when did the person agree / take it back). The CURRENT state of a
    purpose is its newest row. Rows are never edited; the sweeper that erases an account keeps them (they hold no
    personal data beyond the user id and a coarse source) as proof of the consent trail."""

    PURPOSE_MARKETING = "marketing"
    PURPOSE_ANALYTICS = "analytics"
    PURPOSE_PERSONALISATION = "personalisation"
    PURPOSES = (PURPOSE_MARKETING, PURPOSE_ANALYTICS, PURPOSE_PERSONALISATION)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consent_records")
    purpose = models.CharField(max_length=30, db_index=True)
    granted = models.BooleanField()
    notice_version = models.CharField(max_length=20, blank=True, default="")  # privacy-notice version shown
    source = models.CharField(max_length=20, blank=True, default="app")  # app | signup | system
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "purpose", "-created_at"], name="login_consent_up_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("ConsentRecord rows are append-only and cannot be edited.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"user={self.user_id} {self.purpose} {'granted' if self.granted else 'withdrawn'} @ {self.created_at:%Y-%m-%d}"


class LinkedIdentity(models.Model):
    """S10 -- an outside sign-in account (today: Google) tied to a LearnScroll account.

    One row per (user, provider). `subject` is the provider's stable id (Google ``sub``), NOT the e-mail, so the link
    keeps working when the e-mail on either side changes. Unlinking sets `revoked_at` instead of deleting the row:
    that is what stops "Sign in with Google" from silently re-attaching itself through the old match-by-e-mail rule.
    """

    PROVIDER_GOOGLE = "google"
    PROVIDER_CHOICES = [(PROVIDER_GOOGLE, "Google")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="linked_identities")
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, default=PROVIDER_GOOGLE)
    subject = models.CharField(max_length=64)
    email = models.EmailField(blank=True, default="")  # e-mail of the provider account when it was linked
    display_name = models.CharField(max_length=120, blank=True, default="")
    linked_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="login_linkid_provider_subject_uniq"),
            models.UniqueConstraint(fields=["user", "provider"], name="login_linkid_user_provider_uniq"),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __str__(self):
        return f"user={self.user_id} {self.provider} {'active' if self.is_active else 'unlinked'}"


class QRLoginRequest(models.Model):
    """S10 -- one "sign in on a computer by scanning a QR with the phone" attempt.

    The computer (no session yet) creates the row and shows a QR holding the public `code`; it keeps the private
    `secret` and polls with both. The signed-in phone scans the code and approves. Only HASHES of code / secret are
    stored. `match_code` (4 digits) is shown on the computer and must be typed on the phone -- a QR held up by an
    attacker cannot be approved blindly.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_CONSUMED = "consumed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DENIED, "Denied"),
        (STATUS_CONSUMED, "Consumed"),
    ]

    code_hash = models.CharField(max_length=64, unique=True)
    secret_hash = models.CharField(max_length=64)
    match_code = models.CharField(max_length=4)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    # the computer that asked (from its request headers, informational only)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)
    requester_device_name = models.CharField(max_length=120, blank=True, default="")
    requester_platform = models.CharField(max_length=20, blank=True, default="")
    requester_user_agent = models.CharField(max_length=255, blank=True, default="")
    requester_device_id = models.CharField(max_length=64, blank=True, default="")

    # the phone that answered
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="qr_login_requests")
    wrong_match_attempts = models.PositiveSmallIntegerField(default=0)
    approved_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    # AuthToken.jti of the session this QR created -> "Linked apps" list
    session_jti = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"qr-login {self.status} user={self.user_id}"
