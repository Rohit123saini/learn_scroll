# user_profile/serializers.py
import os
import re
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from common.pagination import get_max_page_size

from .models import (
    BlockUser,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
    UserPreference,
)

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "profile_photo",
            "bio",
            "is_private",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
            "coin",
        ]
        # 🔥 FIX: these are denormalized counters + a balance maintained by
        # views/other apps — never writable from the profile-update
        # payload, so they belong in read_only_fields, not just left out
        # of ProfileUpdateSerializer's field list (defense in depth).
        read_only_fields = [
            "id",
            "username",
            "coin",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
        ]


class UserSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "profile_photo"]


def accepted_connection_ids(user):
    """
    Jinke saath is user ka "real" (accepted) follow-relation hai — chahe
    is user ne unhe follow kiya ho ya unhone is user ko — dono taraf se.
    Mutual-friends count isi set ke overlap se nikalta hai.

    ⚠️ Naam me leading underscore JAANBUJH KAR nahi rakha — views.py
    ab explicit imports use karta hai, lekin agar kahin wildcard import
    reh gaya ho to bhi ye safe rahe isliye convention same rakha hai.
    """
    following_ids = set(
        Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED)
        .values_list("following_id", flat=True)
    )
    follower_ids = set(
        Follow.objects.filter(following=user, status=Follow.Status.ACCEPTED)
        .values_list("follower_id", flat=True)
    )
    return following_ids | follower_ids


_BULK_CONNECTION_CHUNK = 500


def bulk_accepted_connection_ids(user_ids, restrict_to_user=None):
    """
    🔥 FIX (N+1): `accepted_connection_ids()` runs 2 queries per user. The
    followers/following/chat-search list views were calling it once per
    row being serialized — for a page of 50 users that's 100 extra
    queries just to compute `mutual_friends`.

    This does the same computation for a whole batch of user ids in
    exactly 2 queries and returns {user_id: set(connected_user_ids)}, so a
    view can call this once per request and hand each row's set to the
    serializer via context instead of re-querying per row.

    ISSUE #5 (memory): without a bound, this pulled EVERY accepted Follow
    row of every user on the page into RAM — a page of 20 accounts with
    1M followers each is 20M rows, an easy OOM. The only thing callers do
    with these sets is intersect them with the viewer's own connections
    (mutual friends), so pass `restrict_to_user=<viewer>`: the database
    then returns only rows whose OTHER side is one of the viewer's
    connections (a subquery, never a Python list), which bounds the result
    to the actual mutual overlap. The returned sets are already
    intersected, so `my_connections & their_connections` in the
    serializer gives the identical number as before.

    With `restrict_to_user=None` the old unrestricted behaviour is kept
    (only safe for tiny pages — used by nothing in this app any more).
    """
    user_ids = list({uid for uid in user_ids if uid is not None})
    if not user_ids:
        return {}

    # Hard guard (issue #17): the UNRESTRICTED form loads every accepted
    # Follow row of every listed user into RAM, so it may only ever be used
    # for a page-sized list. Callers with more ids must pass
    # `restrict_to_user` (bounded by the mutual overlap) or paginate.
    if restrict_to_user is None and len(user_ids) > get_max_page_size():
        raise ValueError(
            f"bulk_accepted_connection_ids: {len(user_ids)} users without "
            f"restrict_to_user exceeds the {get_max_page_size()}-user safety limit."
        )

    connections = defaultdict(set)

    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED).order_by()

    viewer_id = None
    if restrict_to_user is not None:
        viewer_id = getattr(restrict_to_user, "pk", restrict_to_user)
        # The viewer's connections, as SQL subqueries (never materialised).
        viewer_followees = accepted.filter(follower_id=viewer_id).values("following_id")
        viewer_followers = accepted.filter(following_id=viewer_id).values("follower_id")

    # Chunked so even a caller that (wrongly) hands over thousands of ids
    # never builds one enormous IN (...) list / parameter set.
    for start in range(0, len(user_ids), _BULK_CONNECTION_CHUNK):
        chunk = user_ids[start:start + _BULK_CONNECTION_CHUNK]
        as_follower = accepted.filter(follower_id__in=chunk)
        as_following = accepted.filter(following_id__in=chunk)
        if viewer_id is not None:
            as_follower = as_follower.filter(
                Q(following_id__in=viewer_followees) | Q(following_id__in=viewer_followers)
            )
            as_following = as_following.filter(
                Q(follower_id__in=viewer_followees) | Q(follower_id__in=viewer_followers)
            )

        for follower_id, following_id in as_follower.values_list("follower_id", "following_id"):
            connections[follower_id].add(following_id)

        for following_id, follower_id in as_following.values_list("following_id", "follower_id"):
            connections[following_id].add(follower_id)

    return connections


class MessageContactSearchSerializer(serializers.ModelSerializer):
    """
    Message/group "add members" search ke response ke liye — sirf ye 5
    fields, koi profile_photo/bio waghera nahi.
    """
    mutual_friends = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "mutual_friends"]

    def get_mutual_friends(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return 0

        # 🔥 FIX: prefer a precomputed batch map from the view (no extra
        # query at all). Falls back to the old per-object query so this
        # serializer still works standalone if a caller doesn't pass one.
        connections_map = self.context.get("connections_map")

        # Issue #17: when the view built the map with
        # bulk_accepted_connection_ids(..., restrict_to_user=viewer) every set
        # in it is ALREADY the overlap with the viewer's connections, so the
        # count is just its size — the viewer's own (possibly huge) connection
        # set never has to be loaded into memory at all.
        if connections_map is not None and self.context.get("connections_map_is_mutual_only"):
            return len(connections_map.get(obj.id, ()))

        my_connections = self.context.get("_my_connections")
        if my_connections is None:
            my_connections = accepted_connection_ids(request.user)
            self.context["_my_connections"] = my_connections

        if connections_map is not None:
            their_connections = connections_map.get(obj.id, set())
        else:
            their_connections = accepted_connection_ids(obj)

        return len(my_connections & their_connections)


class TargetUserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "profile_photo",
            "bio",
            "is_private",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
        ]


class RestrictedTargetUserProfileSerializer(serializers.ModelSerializer):
    """
    🔥 NEW: what a private account shows to a viewer who isn't an accepted
    follower (and isn't the account owner) — Instagram-style "this account
    is private" card. Before this, `UserProfileDetailView` returned the
    full `TargetUserProfileSerializer` payload (bio, counts, photo) to
    *anyone*, regardless of `is_private` — the field existed on the model
    but nothing ever checked it.
    """

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "is_private", "is_verified"]


class FollowActionResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    status = serializers.CharField(allow_null=True)
    follow_id = serializers.IntegerField(required=False)


class UserProfileDetailResponseSerializer(serializers.Serializer):
    """
    🔥 FIX: this class was defined TWICE in the original file (an old
    one-way-follow shape, then a two-way shape). Python silently keeps
    only the second definition, so the first was already dead — but it's
    confusing dead code and a trap for the next edit. Keeping only the
    two-way shape that views.py actually returns.
    """

    status = serializers.BooleanField()
    message = serializers.CharField()
    my_id = serializers.IntegerField()
    my_username = serializers.CharField()
    target_user_id = serializers.IntegerField()
    target_username = serializers.CharField()
    my_follow_status = serializers.CharField(allow_null=True)
    my_follow_id = serializers.IntegerField(allow_null=True)
    their_follow_status = serializers.CharField(allow_null=True)
    their_follow_id = serializers.IntegerField(allow_null=True)
    is_restricted_view = serializers.BooleanField()
    # TASK 18: whether *I* (request.user) have restricted the target —
    # deliberately the only restrict-related field on this response.
    # There is no `their_restrict_status` counterpart the way follow has
    # one — restrict is one-way and silent by design (see RestrictUser's
    # docstring in models.py), so the target's profile response must
    # never reveal whether *they* are restricting *me*, or whether I am
    # restricted by them.
    am_i_restricting = serializers.BooleanField()
    data = serializers.DictField()


class SafeProfilePhotoField(serializers.ImageField):
    """
    ImageField that also enforces what the bare field doesn't: a size cap,
    an extension allow-list, a real-format allow-list and a pixel cap.

    Order matters — the cheap checks (declared size, extension) run BEFORE
    the file is handed to Pillow, so an oversized/wrong-type upload is
    refused without being decoded. The parent then confirms it is a
    genuinely decodable image (this is what stops a renamed non-image), and
    we finally check the format Pillow actually detected (a file named
    .jpg that is really a GIF/BMP/TIFF/SVG is refused) and its dimensions
    (decompression-bomb guard).
    """

    _ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

    def to_internal_value(self, data):
        max_bytes = getattr(settings, "PROFILE_PHOTO_MAX_BYTES", 5 * 1024 * 1024)
        allowed_ext = {
            e.lower().lstrip(".")
            for e in getattr(settings, "PROFILE_PHOTO_ALLOWED_EXTENSIONS", ("jpg", "jpeg", "png", "webp"))
        }
        max_dim = getattr(settings, "PROFILE_PHOTO_MAX_DIMENSION", 8000)

        size = getattr(data, "size", None)
        if size is not None and size > max_bytes:
            raise serializers.ValidationError(
                f"Photo must be at most {max_bytes // (1024 * 1024)} MB."
            )
        ext = os.path.splitext(getattr(data, "name", "") or "")[1].lower().lstrip(".")
        if ext not in allowed_ext:
            raise serializers.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(sorted(allowed_ext))}."
            )

        value = super().to_internal_value(data)

        image = getattr(value, "image", None)
        if image is not None:
            if (image.format or "").upper() not in self._ALLOWED_FORMATS:
                raise serializers.ValidationError("Unsupported image format. Use JPEG, PNG or WebP.")
            width, height = image.size
            if width > max_dim or height > max_dim:
                raise serializers.ValidationError(
                    f"Image dimensions too large (max {max_dim}x{max_dim} px)."
                )
        return value


class ProfileUpdateSerializer(serializers.ModelSerializer):
    # Explicit (instead of the auto ImageField) so uploads are validated —
    # `max_length=100` mirrors the model field's filename limit.
    profile_photo = SafeProfilePhotoField(required=False, allow_null=True, max_length=100)

    class Meta:
        model = User
        # 🔥 FIX: `is_private` is a user-facing privacy toggle — it was
        # defined on the model and referenced everywhere in views, but
        # there was no way for a user to actually flip it via the API.
        fields = ["username", "first_name", "last_name", "bio", "profile_photo", "is_private"]
        extra_kwargs = {
            "username": {"required": False},
            "first_name": {"required": False},
            "last_name": {"required": False},
            # 🔥 FIX: unbounded TextField + MultiPartParser form field with
            # no cap is an easy abuse/DoS vector. 500 chars is a reasonable
            # Instagram-style bio limit — adjust to taste.
            "bio": {"required": False, "max_length": 500},
            "is_private": {"required": False},
        }

    def validate_username(self, value):
        user = self.context["request"].user
        # 🔥 FIX: case-sensitive uniqueness lets "Sam" and "sam" coexist,
        # which is a common source of impersonation/confusion complaints
        # in production. Compare case-insensitively.
        if User.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Ye username already taken hai.")
        return value


# 🔥 Block / Unblock user
# Model (BlockUser) profile app me hi hai, isliye API bhi yahin banai
# hai — message app ise sirf consume karega (chat screen "blocked?"
# check waghera ke liye).
class BlockUserSerializer(serializers.ModelSerializer):
    # Flutter POST body me sirf {"blocked": "<user_id>"} bhejta hai.
    blocked_detail = UserSearchSerializer(source="blocked", read_only=True)

    class Meta:
        model = BlockUser
        fields = ["id", "blocked", "blocked_detail", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_blocked(self, value):
        request = self.context["request"]
        if value == request.user:
            raise serializers.ValidationError("Aap khud ko block nahi kar sakte.")
        return value


# 🔥 TASK 18 — Restrict / Unrestrict user
# Same shape as BlockUserSerializer just above (mirrors it deliberately
# for consistency), but restrict is NOT a stronger/weaker version of
# block — it's a different relationship (see RestrictUser's docstring
# in models.py for the one-way/silent/non-blocking semantics).
class RestrictUserSerializer(serializers.ModelSerializer):
    # Flutter POST body: {"restricted": "<user_id>"} — same shape as
    # BlockUserSerializer's {"blocked": "<user_id>"}.
    restricted_detail = UserSearchSerializer(source="restricted", read_only=True)

    class Meta:
        model = RestrictUser
        fields = ["id", "restricted", "restricted_detail", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_restricted(self, value):
        request = self.context["request"]
        if value == request.user:
            raise serializers.ValidationError("Aap khud ko restrict nahi kar sakte.")

        # Restricting someone you're already blocked with (either
        # direction) is a no-op that would be confusing to allow — block
        # is already the strictly stronger relationship, so there's
        # nothing restrict adds on top of it.
        already_blocked = BlockUser.objects.filter(
            Q(blocker=request.user, blocked=value) | Q(blocker=value, blocked=request.user)
        ).exists()
        if already_blocked:
            raise serializers.ValidationError(
                "Ye user pehle se blocked hai — restrict ki zaroorat nahi."
            )

        return value


# 🔥 TASK 19 — Coin transaction history
# Read-only on purpose: the only sanctioned way to CREATE a CoinLedger
# row is `CoinLedger.objects.record_transaction()` (models.py), which
# also moves the real `User.coin` balance in the same DB transaction.
# Exposing a writable serializer here would let a client hand-write
# their own `earn`/`refund`/`gift_received` row without ever touching
# `record_transaction()` — i.e. mint themselves coins with no matching
# balance change, exactly the drift this table exists to prevent.
class CoinLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoinLedger
        fields = [
            "id",
            "transaction_type",
            "amount",
            "balance_after",
            "reference",
            "description",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


# 🔥 TASK 3 — Buy-Coin flow
# `CoinPurchaseRequest` is the request/receipt row; this serializer is
# used for BOTH directions of `BuyCoinView`: as input to start a
# purchase (gateway_reference/amount/coins/gateway) and as output for
# the created/existing row (adds status/failure_reason/timestamps,
# read-only). `status`/`failure_reason` are read-only here on purpose —
# same reasoning as `CoinLedgerSerializer` being entirely read-only: the
# only sanctioned way to move a request out of PENDING is
# `CoinPurchaseRequest.objects.confirm_success()` /
# `.mark_failed()` (models.py), never a client-supplied status field.
class CoinPurchaseRequestSerializer(serializers.ModelSerializer):
    # Explicit (not just relying on the model field) so a bad amount/
    # coins value is rejected at validation time with a clear message,
    # instead of surfacing later as a DB CheckConstraint violation.
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )
    coins = serializers.IntegerField(min_value=1)

    class Meta:
        model = CoinPurchaseRequest
        fields = [
            "id",
            "gateway",
            "gateway_reference",
            "amount",
            "coins",
            "status",
            "failure_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "failure_reason", "created_at", "updated_at"]
        # DRF auto-adds a uniqueness validator for the model's
        # UniqueConstraint(gateway, gateway_reference), which would reject a
        # repeated purchase with 400 BEFORE the view runs — making
        # BuyCoinView's documented idempotent retry (200 + the existing row)
        # and its cross-user 409 unreachable. Uniqueness is still enforced by
        # the DB constraint + start_purchase()'s get-or-create, so the
        # serializer-level validator is switched off.
        validators = []

    def validate_gateway_reference(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("gateway_reference is required.")
        return value

    _GATEWAY_RE = re.compile(r"^[a-z0-9_-]{0,30}$")

    def validate_gateway(self, value):
        # (gateway, gateway_reference) is the purchase's identity, so the
        # gateway string must be canonical: "Razorpay" and "razorpay " may
        # not become two different identities (nor miss the settings key
        # the webhook secret is looked up under).
        value = (value or "").strip().lower()
        if not self._GATEWAY_RE.match(value):
            raise serializers.ValidationError(
                "gateway may only contain letters, digits, '_' and '-' (max 30)."
            )
        return value


# 🔥 TASK 3 — confirm/webhook payload for `BuyCoinConfirmView`.
# Deliberately a plain Serializer, not a ModelSerializer: this doesn't
# create/update a `CoinPurchaseRequest` row itself — the manager methods
# it hands off to (`confirm_success`/`mark_failed`) own that, with their
# own locking/idempotency — this is only validating the shape of the
# confirm payload.
class CoinPurchaseConfirmSerializer(serializers.Serializer):
    gateway_reference = serializers.CharField(max_length=150)
    status = serializers.ChoiceField(choices=["success", "failed"])
    # Only meaningful when status="failed"; harmless if sent (and
    # ignored) alongside status="success".
    failure_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    # Optional: which gateway this reference belongs to. Only needed when
    # the same reference string exists on more than one gateway (see
    # BuyCoinConfirmView); the gateway-specific URL does the same job.
    gateway = serializers.CharField(required=False, allow_blank=True, max_length=30)


# Staff withdrawal lifecycle action — validated here (not by hand-rolled
# `request.data.get(...)` branches in the view) so a bad body gets the same
# standard 400 {"errors": {...}} shape as every other endpoint, and the
# reject reason can't exceed the DB column (failure_reason max_length=255).
class CoinWithdrawalActionSerializer(serializers.Serializer):
    ACTION_PROCESSING = "processing"
    ACTION_SUCCESS = "success"
    ACTION_REJECT = "reject"
    VALID_ACTIONS = (ACTION_PROCESSING, ACTION_SUCCESS, ACTION_REJECT)

    action = serializers.ChoiceField(choices=VALID_ACTIONS)
    # Only meaningful for action="reject"; ignored otherwise.
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


# 🔥 TASK 4 — Withdraw-Coin flow
# `CoinWithdrawalRequest` is the request/receipt row for cashing coins
# out to real money — the mirror image of `CoinPurchaseRequest` above
# (coins -> money instead of money -> coins). `status`/`failure_reason`/
# both ledger-entry back-links are read-only here for the same reason
# `CoinPurchaseRequestSerializer`'s are: the only sanctioned way to move
# a request out of PENDING is `CoinWithdrawalRequest.objects.
# request_withdrawal()` / `.mark_processing()` / `.confirm_success()` /
# `.reject()` (models.py), never a client-supplied status field.
class CoinWithdrawalRequestSerializer(serializers.ModelSerializer):
    # Explicit (not just relying on the model field) so a bad coins
    # value is rejected at validation time with a clear message,
    # instead of surfacing later as a DB CheckConstraint violation —
    # same reasoning CoinPurchaseRequestSerializer's explicit
    # amount/coins fields give.
    coins = serializers.IntegerField(min_value=1)

    class Meta:
        model = CoinWithdrawalRequest
        fields = [
            "id",
            "coins",
            "payout_method",
            "payout_details",
            "status",
            "failure_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "failure_reason", "created_at", "updated_at"]
        # The MODEL keeps `payout_method` blank=True (internal/admin callers
        # of request_withdrawal() may omit it), which makes the auto-generated
        # serializer field optional + blank-allowed — so an API caller could
        # send "" (or nothing) and skip every payout check below, creating a
        # request ops has nowhere to pay. Over the API it is mandatory.
        extra_kwargs = {
            "payout_method": {"required": True, "allow_blank": False},
        }

    _IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
    _UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,100}@[A-Za-z][A-Za-z0-9.]{1,63}$")
    _ACCOUNT_NO_RE = re.compile(r"^[0-9]{9,18}$")

    def validate(self, attrs):
        """
        payout_details' required keys depend on payout_method — kept as
        cross-field validation here rather than a DB constraint, same
        reasoning CoinWithdrawalRequest.payout_details' own field
        comment gives for staying JSON: a new payout method should
        never need a migration, just a new branch here.

        Beyond "the keys exist" this now checks each value is a non-empty
        string of the right shape, and stores ONLY the known keys (so a
        caller can't stash arbitrary/huge JSON in the row). The cleaned
        dict replaces what the client sent.
        """
        method = attrs.get("payout_method")
        raw = attrs.get("payout_details")
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise serializers.ValidationError(
                {"payout_details": "payout_details must be an object."}
            )

        if method == CoinWithdrawalRequest.PayoutMethod.BANK_TRANSFER:
            required = ("account_holder", "account_number", "ifsc")
        elif method == CoinWithdrawalRequest.PayoutMethod.UPI:
            required = ("upi_id",)
        else:
            raise serializers.ValidationError(
                {"payout_method": "A valid payout method is required."}
            )

        def _clean(key):
            value = raw.get(key)
            return value.strip() if isinstance(value, str) else ""

        missing = [k for k in required if not _clean(k)]
        if missing:
            raise serializers.ValidationError(
                {
                    "payout_details": (
                        f"Missing required field(s) for {method}: "
                        f"{', '.join(sorted(missing))}."
                    )
                }
            )

        cleaned = {k: _clean(k) for k in required}
        errors = {}
        if method == CoinWithdrawalRequest.PayoutMethod.BANK_TRANSFER:
            cleaned["account_number"] = re.sub(r"[\s-]", "", cleaned["account_number"])
            cleaned["ifsc"] = cleaned["ifsc"].upper()
            if not (2 <= len(cleaned["account_holder"]) <= 100):
                errors["account_holder"] = "Must be 2-100 characters."
            if not self._ACCOUNT_NO_RE.match(cleaned["account_number"]):
                errors["account_number"] = "Must be 9-18 digits."
            if not self._IFSC_RE.match(cleaned["ifsc"]):
                errors["ifsc"] = "Invalid IFSC code."
        else:
            if not self._UPI_RE.match(cleaned["upi_id"]):
                errors["upi_id"] = "Invalid UPI id (expected name@bank)."
        if errors:
            raise serializers.ValidationError({"payout_details": errors})

        attrs["payout_details"] = cleaned
        return attrs

# TASK 1 -- theme/language preferences.
# Row is get-or-created via UserPreference.for_user() in the view
# (models.py) -- this serializer only ever sees a row that already
# exists, so "user" itself isn't a field here (it's set by
# for_user(), never by client input). updated_at is read-only for the
# same reason CoinPurchaseRequestSerializer's timestamps are: it's
# maintained by auto_now, not something a PATCH body should be able
# to set.
class UserPreferenceSerializer(serializers.ModelSerializer):
    # e.g. "en", "hi", "en-US", "zh-Hans" — shape only; whether the language
    # is actually supported is checked against settings.SUPPORTED_LANGUAGES.
    _LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,4})?$")

    class Meta:
        model = UserPreference
        fields = ["theme", "language", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_language(self, value):
        value = (value or "").strip()
        supported = [c.lower() for c in getattr(settings, "SUPPORTED_LANGUAGES", ("en",))]
        if not self._LANG_TAG_RE.match(value) or value.split("-")[0].lower() not in supported:
            raise serializers.ValidationError(
                f"Unsupported language. Supported: {', '.join(supported)}."
            )
        # Canonical form so "EN-us" and "en-US" don't become two values.
        parts = value.split("-")
        canonical = [parts[0].lower()]
        for extra in parts[1:]:
            canonical.append(
                extra.title() if len(extra) == 4 else extra.upper() if len(extra) == 2 else extra
            )
        return "-".join(canonical)