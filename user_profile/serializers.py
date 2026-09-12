# user_profile/serializers.py
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from .models import (
    BlockUser,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
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


def bulk_accepted_connection_ids(user_ids):
    """
    🔥 FIX (N+1): `accepted_connection_ids()` runs 2 queries per user. The
    followers/following/chat-search list views were calling it once per
    row being serialized — for a page of 50 users that's 100 extra
    queries just to compute `mutual_friends`.

    This does the same computation for a whole batch of user ids in
    exactly 2 queries and returns {user_id: set(connected_user_ids)}, so a
    view can call this once per request and hand each row's set to the
    serializer via context instead of re-querying per row.
    """
    user_ids = list({uid for uid in user_ids if uid is not None})
    if not user_ids:
        return {}

    connections = defaultdict(set)

    as_follower = Follow.objects.filter(
        follower_id__in=user_ids, status=Follow.Status.ACCEPTED
    ).values_list("follower_id", "following_id")
    for follower_id, following_id in as_follower:
        connections[follower_id].add(following_id)

    as_following = Follow.objects.filter(
        following_id__in=user_ids, status=Follow.Status.ACCEPTED
    ).values_list("following_id", "follower_id")
    for following_id, follower_id in as_following:
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

        my_connections = self.context.get("_my_connections")
        if my_connections is None:
            my_connections = accepted_connection_ids(request.user)
            self.context["_my_connections"] = my_connections

        # 🔥 FIX: prefer a precomputed batch map from the view (no extra
        # query at all). Falls back to the old per-object query so this
        # serializer still works standalone if a caller doesn't pass one.
        connections_map = self.context.get("connections_map")
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


class ProfileUpdateSerializer(serializers.ModelSerializer):
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
            "profile_photo": {"required": False},
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

    def validate_gateway_reference(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("gateway_reference is required.")
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

    def validate(self, attrs):
        """
        payout_details' required keys depend on payout_method — kept as
        cross-field validation here rather than a DB constraint, same
        reasoning CoinWithdrawalRequest.payout_details' own field
        comment gives for staying JSON: a new payout method should
        never need a migration, just a new branch here.
        """
        method = attrs.get("payout_method")
        details = attrs.get("payout_details") or {}

        if method == CoinWithdrawalRequest.PayoutMethod.BANK_TRANSFER:
            required = {"account_holder", "account_number", "ifsc"}
        elif method == CoinWithdrawalRequest.PayoutMethod.UPI:
            required = {"upi_id"}
        else:
            required = set()

        missing = required - set(details.keys())
        if missing:
            raise serializers.ValidationError(
                {
                    "payout_details": (
                        f"Missing required field(s) for {method}: "
                        f"{', '.join(sorted(missing))}."
                    )
                }
            )
        return attrs