# `user_profile` App — Complete Self-Contained Reference

> **v2 — updated after the latest patch round.** Ye ek hi file hai jisme
> poore **user_profile** Django app ka sara logic, code, connections,
> flows aur known issues cover hain. Iske alawa kisi aur file ki
> zaroorat nahi — sab kuch (models → serializers → views → urls → admin
> → tests) yahin milega.
>
> **v1 se kya badla, sabse pehle:** section 0 (Changelog) padho — usme
> sab naye fixes ek jagah list hain. Baaki poora document un fixes ko
> reflect karta hua, fully updated code ke saath, dubara likha gaya hai.

---

## 0. Changelog — v1 → v2 (is round me kya fix hua)

### 🔒 Security / correctness fixes
1. **Block enforcement — profile lookup.** `UserProfileDetailView` ab
   `is_blocked_between()` check karta hai — agar tum (ya wo) blocked ho,
   `Http404` milta hai (403 nahi — taaki blocking ka pata na chale ki
   kisne kisko block kiya).
2. **Block enforcement — follow.** `FollowAPIView` ab follow attempt se
   pehle `is_blocked_between()` check karta hai — blocked relationship
   me follow request bhej hi nahi sakte.
3. **Block enforcement — search.** `UserSearchView` ab khud ko aur
   kisi bhi block-relationship wale user ko results se exclude karta
   hai (pehle **sab** users aa jaate the, khud ko bhi).
4. **Block enforcement — chat contact search.** `MessageContactSearchView`
   ab connected-users set se blocked ids bhi discard karta hai.
5. **Private account ab actually enforce hota hai.** `is_private` field
   pehle sirf model + response me tha, koi check nahi karta tha — koi
   bhi authenticated user kisi private account ka poora bio/photo/counts
   dekh sakta tha sirf username jaan ke. Ab: owner khud, public account,
   ya accepted follower ko full data milta hai; baaki sabko
   `RestrictedTargetUserProfileSerializer` (minimal card) milta hai.
6. **Race condition on double-follow.** Do rapid duplicate follow
   requests (double-tap / retry) pehle raw `IntegrityError` (500) de
   sakte the. Ab `try/except IntegrityError` — dusri request ko clean
   200 response milta hai (existing follow ki state ke saath), 500 nahi.
7. **Self-follow / self-block / self-restrict ab DB-level enforce hote
   hain.** Pehle sirf serializer-level (`validate_blocked`) ya
   commented-out `clean()` tha — jo `.objects.create()` se seedha
   bypass ho jata tha. Ab har teeno model pe `CheckConstraint` hai.
8. **`username` uniqueness case-insensitive.** `ProfileUpdateSerializer.
   validate_username` ab `username__iexact` use karta hai — "Sam" aur
   "sam" ab coexist nahi kar sakte.
9. **`bio` field ab capped (500 chars).** Pehle unbounded `TextField` +
   `MultiPartParser` form field — easy abuse/DoS vector tha.
10. **`is_private` ab profile-update se editable hai.** Pehle field
    model pe tha, har jagah reference hota tha, par user ke paas ise
    flip karne ka koi API tarika nahi tha.

### ⚡ Performance fix
11. **N+1 query fix — `mutual_friends`.** `accepted_connection_ids()`
    per-row 2 queries chalata tha. Naya `bulk_accepted_connection_ids()`
    poore page (50 users) ka result **2 hi queries** me deta hai; views
    (`MessageContactSearchView`, `FollowersListView`, `FollowingListView`)
    ab ise ek baar per-request call karke `connections_map` context me
    pass karte hain.

### 🧹 Model / data-layer fixes
12. **`Follow.Meta.ordering = ["-created_at"]` add hui** — followers/
    following lists ab newest-first aati hain (pehle DB-default order).
13. **`coins` model → `CoinLedger`, aur is round me shape bhi redesign
    hui.** Standard PascalCase naam, `coin = ForeignKey(...)` →
    `user = ForeignKey(...)` (zyada sahi naam). Purana `credit`/`debit`
    (do alag `PositiveIntegerField`, jisme dono ek saath set ho sakte
    the aur "iska matlab kya hai" kabhi clear nahi tha) hata ke ab ek
    hi signed `amount = IntegerField()` hai — positive = credit,
    negative = debit — plus:
    - `transaction_type` (TextChoices: earn/purchase/spend/refund/
      gift_sent/gift_received/admin_adjustment) — ab row khud bata deti
      hai "ye balance kyun badla", context se guess nahi karna padta.
    - `reference` (indexed, blank-ok) — idempotency key; jo bhi
      transaction banaye (gift, withdrawal, payment receipt) apna id
      yahan pass kare aur `get_or_create(reference=..., defaults={...})`
      use kare, taaki retried webhook kabhi double-credit na kare.
    - `balance_after` — is entry ke turant baad `User.coin` ka snapshot.
      Isi se ledger self-auditing ban jaati hai: koi mismatch ho to
      ledger replay karke exact row pinpoint ho jaati hai.
    - `description` (human-readable reason) aur `metadata` (JSONField,
      kuch bhi structured jo apna column deserve nahi karta).
    - `Meta.indexes` me `(user, -created_at)` add hua — "is user ki
      transaction history" ka obvious query ab supported hai.
    - `CheckConstraint` add hui: `amount != 0` (zero-amount row ab
      insert hi nahi ho sakti — ye hamesha ek bug hoti, valid transaction
      nahi).
    ⚠️ Agar production DB me `coins` table pe already migration chal
    chuki hai, **table-rename migration khud se likho** (ya
    `Meta.db_table = "user_profile_coins"` pin karo) — blind
    `makemigrations` mat chalao. Since abhi tak is table me kuch likha
    hi nahi jaata (§0 item "Still NOT done"), production me zero rows
    honi chahiye — ye field-shape badalne ka sabse sasta/safe moment hai.
14. **`RestrictUser` ab bhi self-restrict se DB-level protected hai**
    (pehle koi constraint nahi thi) — feature abhi bhi kisi view/
    serializer se wired nahi hai, sirf data-integrity fix hai.

### 🗂️ Code hygiene
15. **Duplicate class definitions clean ho gayi:**
    `UserProfileDetailResponseSerializer` aur `UserProfileDetailView`
    dono pehle do-do baar defined the (Python silently sirf last wali
    rakhta tha) — ab sirf ek-ek, working version.
    `UserProfileDetailView` se bhi ek meaningless `parser_classes =
    [MultiPartParser, FormParser]` (GET-only view pe) hata diya gaya.
16. **`admin.py` — wildcard `admin.site.register(*model)` se explicit
    `@admin.register` + custom `ModelAdmin` classes (list_display,
    list_filter, search_fields, autocomplete_fields) me convert hua** —
    sab 4 models (`Follow`, `BlockUser`, `RestrictUser`, `CoinLedger`)
    ab admin me usable/searchable hain.
17. **Explicit imports everywhere** — `views.py` aur `serializers.py`
    dono ab `from .models import *` / wildcard ki jagah explicit named
    imports use karte hain (`accepted_connection_ids` ka underscore-less
    naming convention isi wajah se ab bhi zaroori hai — neeche §5 dekho).
18. **`tests.py` ab khaali nahi hai** — `FollowModelTests` +
    `FollowAPITests` add hui (self-follow constraint, follow/unfollow
    counts, blocked-user-cannot-follow, private-profile-hides-bio,
    blocked-user-404-on-lookup).
19. **`urls.py`:** `blocked-users/<str:id>/` → `blocked-users/<int:id>/`
    (view sirf integer PKs se compare karta hai; ab non-numeric input
    pe Django khud 404 deta hai, ORM tak jaata hi nahi).

### Still NOT done (carried over from v1, still true)
- `RestrictUser` model abhi bhi kisi feature se wired nahi (data-layer
  fix hui hai, functionality nahi).
- `CoinLedger` abhi bhi kahin se likha nahi jaata — sirf admin me
  registered hai (naya shape ke saath: `amount`/`transaction_type`/
  `reference`/`balance_after`, see §0 item 13).
- `FollowSerializer` abhi bhi unused hai.

---

## 1. App Overview

**App name:** `user_profile`
**Purpose:** User profiles, follow/unfollow (with private-account request
flow), followers/following lists, user search, "chat contacts" search
(mutual/connected users only), block/unblock users (now actually
enforced across search/follow/profile-view), profile update (with image
upload + privacy toggle).

**Tech stack:** Django + Django REST Framework + `drf-spectacular` (for
OpenAPI docs via `@extend_schema`).

**Files in this app:**
| File | Responsibility |
|---|---|
| `models.py` | `Follow`, `BlockUser`, `RestrictUser`, `CoinLedger` models |
| `serializers.py` | All request/response serializers + `accepted_connection_ids()` / `bulk_accepted_connection_ids()` helpers |
| `views.py` | All API endpoint logic (class-based views) + `is_blocked_between()` helper |
| `urls.py` | URL routing |
| `admin.py` | Django admin registration (now with proper `ModelAdmin` configs) |
| `apps.py` | App config (`name = 'user_profile'`) |
| `tests.py` | `FollowModelTests`, `FollowAPITests` |

---

## 2. ⚠️ External Dependency — Custom User Model Fields Required

This app does **not** define its own `User` model — it uses
`settings.AUTH_USER_MODEL` / `get_user_model()`, i.e. a **custom User model
defined in a different app** (e.g. a `login` app — the test suite imports
`from login.models import User`-equivalent via `get_user_model()`). For
every view/serializer here to actually work, that custom `User` model
**must** have these fields already on it:

```python
# These fields are assumed to exist on your custom User model —
# they are NOT defined anywhere in this app.
profile_photo   = models.ImageField(...)
bio             = models.TextField(...)
is_private      = models.BooleanField(default=False)
is_verified     = models.BooleanField(default=False)
is_active       = models.BooleanField(default=True)   # used by UserSearchView's queryset filter now
followers_count = models.IntegerField(default=0)   # denormalized counter
following_count = models.IntegerField(default=0)   # denormalized counter
posts_count     = models.IntegerField(default=0)    # denormalized counter (read-only, updated elsewhere — e.g. a posts app)
coin            = models.IntegerField(default=0)    # or FK/related field, used in UserProfileSerializer — NOT the same as the CoinLedger model, see §4
```

If any of these are missing on your actual `User` model,
`UserProfileSerializer`, `TargetUserProfileSerializer`, and the
follow-count `F()` updates in `views.py` will throw `FieldError` at
runtime. This is the **one thing outside this app's own files** you
must double check.

Also required in your project's `settings.py`:
```python
AUTH_USER_MODEL = "your_app.User"   # must point to the custom user model above
```

And in your project's root `urls.py`:
```python
path('profile/', include('user_profile.urls')),
```

And in `INSTALLED_APPS`:
```python
INSTALLED_APPS = [
    ...
    'rest_framework',
    'drf_spectacular',
    'user_profile',
]
```

---

## 3. `apps.py`

```python
from django.apps import AppConfig


class UserProfileConfig(AppConfig):
    name = 'user_profile'
```

Nothing special — standard app config, unchanged from v1.

---

## 4. `models.py` (full current code)

```python
from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, F, Q, UniqueConstraint


class Follow(models.Model):

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="following_relation",
        on_delete=models.CASCADE,
    )

    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="followers_relation",
        on_delete=models.CASCADE,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACCEPTED,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # newest-first by default
        ordering = ["-created_at"]

        constraints = [
            UniqueConstraint(
                fields=["follower", "following"],
                name="unique_follow",
            ),
            # DB-level guard against self-follow — bypasses any
            # serializer/view that forgets the check (admin, shell, etc).
            CheckConstraint(
                condition=~Q(follower=F("following")),
                name="follow_no_self_follow",
            ),
        ]

        indexes = [
            models.Index(fields=["follower"]),
            models.Index(fields=["following"]),
            models.Index(fields=["status"]),
            models.Index(fields=["follower", "status"]),
            models.Index(fields=["following", "status"]),
        ]

    def __str__(self):
        return f"{self.follower.username} -> {self.following.username} ({self.status})"


class BlockUser(models.Model):

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocked_relation",
        on_delete=models.CASCADE,
    )

    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocked_by_relation",
        on_delete=models.CASCADE,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            UniqueConstraint(
                fields=["blocker", "blocked"],
                name="unique_block",
            ),
            # Same self-relation gap as Follow — serializer-level
            # `validate_blocked` was the only guard before this.
            CheckConstraint(
                condition=~Q(blocker=F("blocked")),
                name="block_no_self_block",
            ),
        ]

        indexes = [
            models.Index(fields=["blocker"]),
            models.Index(fields=["blocked"]),
        ]

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"


class RestrictUser(models.Model):
    """
    ⚠️ Defined but not wired into any view/serializer yet (Instagram-style
    "restrict" — softer than block: restricted user can still see/comment
    but you don't see notifications from them, etc). Left as-is since
    building that feature is a product decision, not a bug fix — but
    flagging it so it doesn't get forgotten or shipped half-done.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="restricted_users",
    )

    restricted = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="restricted_by",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["user", "restricted"],
                name="unique_restrict",
            ),
            CheckConstraint(
                condition=~Q(user=F("restricted")),
                name="restrict_no_self_restrict",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} restricted {self.restricted.username}"


class CoinLedger(models.Model):
    """
    The append-only, auditable history of every change to `User.coin`
    (the running-balance field on the custom User model). `User.coin` is
    a cache; this table is the source of truth — if they ever disagree,
    this table is right and `User.coin` should be recomputed from it.

    Renamed from the original lowercase `coins` — kept here as
    `CoinLedger` (not `CoinTransaction`) to avoid breaking any existing
    imports/related_name usage; only the *fields* changed vs the first
    `CoinLedger` pass (which just had `credit`/`debit`), not the class
    name or `related_name`.

    ⚠️ If a migration already exists against the old `coins` name, or
    against the old `credit`/`debit` shape, write a real migration
    (rename table / RemoveField credit,debit + AddField amount,
    transaction_type,reference,balance_after,description,metadata)
    rather than running `makemigrations` blind on a prod DB. Since
    nothing writes to this table yet, there should be zero rows to
    migrate either way — the cheap moment to make this change.
    """

    class TransactionType(models.TextChoices):
        EARN = "earn", "Earned"
        PURCHASE = "purchase", "Purchased"
        SPEND = "spend", "Spent"
        REFUND = "refund", "Refunded"
        GIFT_SENT = "gift_sent", "Gift Sent"
        GIFT_RECEIVED = "gift_received", "Gift Received"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_ledger_entries",
    )

    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)

    # Signed: positive = credit (balance goes up), negative = debit
    # (balance goes down). Replaces the old separate `credit`/`debit`
    # PositiveIntegerFields — one column, and a row can never accidentally
    # have both credit and debit set (which the old shape allowed and
    # meant nothing).
    amount = models.IntegerField()

    # Snapshot of User.coin immediately after this entry was applied.
    # Makes the ledger self-auditing: replay it in order and every
    # balance_after must equal the running sum — any mismatch pinpoints
    # exactly which write went wrong.
    balance_after = models.PositiveIntegerField()

    # Idempotency key for whatever created this row (a gift id, a
    # withdrawal id, a payment gateway receipt id, ...). Callers should
    # `get_or_create(reference=..., defaults={...})` so a retried
    # webhook/request can never double-apply the same transaction.
    reference = models.CharField(max_length=150, blank=True, db_index=True)

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=~Q(amount=0), name="coinledger_amount_not_zero"),
        ]

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"{self.user.username}: {sign}{self.amount} ({self.transaction_type})"
```

### Model notes
- **`Follow`** is the core relation. `status` handles the private-account
  "request → accept/reject" flow. Unique constraint prevents duplicate
  follow rows for the same (follower, following) pair. **Now also**
  ordered newest-first and DB-level self-follow-proof. No standalone
  index on `follower`/`following` — Django already auto-indexes every
  ForeignKey column; only the composite `(follower, status)` /
  `(following, status)` indexes are declared, since those aren't
  automatic.
- **`BlockUser`** — one-directional block record. Unique constraint on
  (blocker, blocked) prevents duplicate blocks. **Now also** DB-level
  self-block-proof.
- **`RestrictUser`** — model exists but **no view/serializer uses it yet**
  (defined for future "restrict" feature — Instagram-style soft block).
  Now DB-level self-restrict-proof too.
- **`CoinLedger`** (renamed from `coins`) — a self-auditing ledger model
  (signed `amount` + `transaction_type` + `reference` idempotency key +
  `balance_after` snapshot), registered in admin but **not used anywhere
  in views/serializers**. The `coin` field referenced in
  `UserProfileSerializer` (`fields = [..., "coin"]`) is a field expected
  on the **custom `User` model** (see §2), not this `CoinLedger` model —
  the naming is just coincidentally similar, don't confuse the two.

---

## 5. `serializers.py` (full current code)

```python
from collections import defaultdict

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Follow, BlockUser

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
        # These are denormalized counters + a balance maintained by
        # views/other apps — never writable from the profile-update
        # payload, so they belong in read_only_fields (defense in depth,
        # on top of them being excluded from ProfileUpdateSerializer's
        # own field list).
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
    N+1 FIX: `accepted_connection_ids()` runs 2 queries per user. The
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

        # Prefer a precomputed batch map from the view (no extra query at
        # all). Falls back to the old per-object query so this serializer
        # still works standalone if a caller doesn't pass one.
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
    NEW: what a private account shows to a viewer who isn't an accepted
    follower (and isn't the account owner) — Instagram-style "this account
    is private" card. Before this, `UserProfileDetailView` returned the
    full `TargetUserProfileSerializer` payload (bio, counts, photo) to
    *anyone*, regardless of `is_private` — the field existed on the model
    but nothing ever checked it.
    """

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "is_private", "is_verified"]


class FollowSerializer(serializers.ModelSerializer):
    follower_username = serializers.CharField(source="follower.username", read_only=True)
    following_username = serializers.CharField(source="following.username", read_only=True)

    class Meta:
        model = Follow
        fields = ["id", "follower", "follower_username", "following", "following_username", "status", "created_at"]
        read_only_fields = ["follower", "created_at"]


class FollowActionResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    status = serializers.CharField(allow_null=True)
    follow_id = serializers.IntegerField(required=False)


class UserProfileDetailResponseSerializer(serializers.Serializer):
    """
    This class was defined TWICE in the original upload (an old
    one-way-follow shape, then a two-way shape). Python silently keeps
    only the second definition, so the first was already dead — but it's
    confusing dead code and a trap for the next edit. Keeping only the
    two-way shape that views.py actually returns, plus the new
    `is_restricted_view` flag (see §6, block/privacy enforcement).
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
    data = serializers.DictField()


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # `is_private` is a user-facing privacy toggle — it was defined
        # on the model and referenced everywhere in views, but there was
        # no way for a user to actually flip it via the API. Now included.
        fields = ["username", "first_name", "last_name", "bio", "profile_photo", "is_private"]
        extra_kwargs = {
            "username": {"required": False},
            "first_name": {"required": False},
            "last_name": {"required": False},
            # Unbounded TextField + MultiPartParser form field with no cap
            # is an easy abuse/DoS vector. 500 chars is a reasonable
            # Instagram-style bio limit — adjust to taste.
            "bio": {"required": False, "max_length": 500},
            "profile_photo": {"required": False},
            "is_private": {"required": False},
        }

    def validate_username(self, value):
        user = self.context["request"].user
        # Case-sensitive uniqueness lets "Sam" and "sam" coexist, which is
        # a common source of impersonation/confusion complaints in
        # production. Compare case-insensitively.
        if User.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Ye username already taken hai.")
        return value


# Block / Unblock user
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
```

### Serializer notes
- `accepted_connection_ids(user)` — still the module-level, per-user
  helper (2 queries). Kept for single-object use-cases and as the
  fallback path inside `get_mutual_friends`.
- `bulk_accepted_connection_ids(user_ids)` — **new**, batch version (2
  queries for N users). Every list view that shows `mutual_friends` now
  calls this once and passes the result in as `connections_map` via
  `get_serializer_context()`.
- `MessageContactSearchSerializer.get_mutual_friends` — caches
  `_my_connections` in `self.context` (computed once per request), and
  now **prefers** `connections_map` (from `bulk_accepted_connection_ids`)
  over the old per-object fallback query.
- `RestrictedTargetUserProfileSerializer` — **new**, minimal 5-field
  payload used when the profile-detail view decides the viewer shouldn't
  see full data (see §6).
- `UserProfileDetailResponseSerializer.data` — changed from a nested
  `TargetUserProfileSerializer()` field to a generic `DictField()`,
  because the view can now return **either**
  `TargetUserProfileSerializer` or `RestrictedTargetUserProfileSerializer`
  data depending on the privacy check — a fixed nested serializer
  couldn't represent both shapes.

---

## 6. `views.py` (full current code)

```python
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BlockUser, Follow
from .serializers import (
    BlockUserSerializer,
    FollowActionResponseSerializer,
    MessageContactSearchSerializer,
    ProfileUpdateSerializer,
    RestrictedTargetUserProfileSerializer,
    TargetUserProfileSerializer,
    UserProfileDetailResponseSerializer,
    UserProfileSerializer,
    UserSearchSerializer,
    accepted_connection_ids,
    bulk_accepted_connection_ids,
)

User = get_user_model()


def is_blocked_between(user_a, user_b):
    """
    NEW: true if either user has blocked the other. Nothing in the
    original code checked this — you could follow, message-search-match,
    and view the full profile of someone who blocked you (or whom you'd
    blocked), which defeats the point of blocking.
    """
    return BlockUser.objects.filter(
        Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
    ).exists()


class ProfileView(GenericAPIView):
    """Get logged-in user's profile"""
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        responses={200: UserProfileSerializer},
        description="Get current authenticated user's profile",
    )
    def get(self, request):
        serializer = self.get_serializer(request.user)
        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class UserProfileDetailView(GenericAPIView):
    """
    Get any user's profile by username, with two-way follow status.

    FIX: this class was defined TWICE in the original file — once as a
    one-way-follow version, and again (further down) as a two-way version
    that also, oddly, declared `parser_classes = [MultiPartParser,
    FormParser]` on a GET-only view (parsers only matter for request
    bodies; harmless but meaningless here, so dropped). Since Python keeps
    the last class body, the first definition was already dead code and
    `urls.py` was always hitting the second one — keeping only that,
    cleaned up, plus the block/privacy checks neither version had.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileDetailResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="username",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="Username of target user",
            )
        ],
        responses={200: UserProfileDetailResponseSerializer, 404: OpenApiTypes.OBJECT},
        description="Get user profile with two-way follow status. Full profile data is only "
        "returned if the account is public, it's your own profile, or you're an accepted "
        "follower; otherwise a minimal card is returned.",
    )
    def get(self, request, username):
        target_user = get_object_or_404(User, username=username)

        # FIX: block wasn't checked anywhere — a blocked/blocking user
        # could still look up the full profile. Mimic "user not found"
        # rather than a 403, so blocking doesn't leak who blocked whom.
        if target_user != request.user and is_blocked_between(request.user, target_user):
            raise Http404

        my_follow_obj = Follow.objects.filter(
            follower=request.user, following=target_user
        ).first()
        their_follow_obj = Follow.objects.filter(
            follower=target_user, following=request.user
        ).first()

        is_self = target_user == request.user
        is_accepted_follower = bool(
            my_follow_obj and my_follow_obj.status == Follow.Status.ACCEPTED
        )

        # FIX: `is_private` existed on the model and was even returned in
        # the response body, but nothing ever *enforced* it — anyone
        # authenticated could read a private account's bio/photo/counts
        # just by knowing the username. Now: full data only for the owner,
        # public accounts, or accepted followers; everyone else gets a
        # minimal "this account is private" style payload.
        is_restricted_view = target_user.is_private and not is_self and not is_accepted_follower
        if is_restricted_view:
            profile_data = RestrictedTargetUserProfileSerializer(target_user).data
        else:
            profile_data = TargetUserProfileSerializer(target_user).data

        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "my_id": request.user.id,
                "my_username": request.user.username,
                "target_user_id": target_user.id,
                "target_username": target_user.username,
                "my_follow_status": my_follow_obj.status if my_follow_obj else None,
                "my_follow_id": my_follow_obj.id if my_follow_obj else None,
                "their_follow_status": their_follow_obj.status if their_follow_obj else None,
                "their_follow_id": their_follow_obj.id if their_follow_obj else None,
                "is_restricted_view": is_restricted_view,
                "data": profile_data,
            },
            status=status.HTTP_200_OK,
        )


class UserSearchView(ListAPIView):
    """Search users by username, first_name, last_name"""
    permission_classes = [IsAuthenticated]
    serializer_class = UserSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        # FIX: search used to return literally every user, including
        # yourself and anyone in a block relationship with you.
        blocked_ids = BlockUser.objects.filter(
            Q(blocker=self.request.user) | Q(blocked=self.request.user)
        ).values_list("blocker_id", "blocked_id")
        excluded_ids = {self.request.user.id}
        for blocker_id, blocked_id in blocked_ids:
            excluded_ids.add(blocker_id)
            excluded_ids.add(blocked_id)

        return User.objects.filter(is_active=True).exclude(id__in=excluded_ids)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search users",
    )
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Users fetched successfully.",
            "data": response.data,
        })


class MessageContactSearchView(ListAPIView):
    """
    GET /profile/chat-search/?search=<query>

    Message/group ke "add members" step ke liye — `UserSearchView` se
    ALAG hai: yahan poore app ke users nahi, sirf wahi log aate hain
    jinko maine follow kiya hua hai YA jinhone mujhe follow kiya hua hai
    (dono me se ek bhi kaafi hai, pura mutual hona zaroori nahi — warna
    list bahut chhoti reh jaati). Response me profile_photo/bio waghera
    nahi, sirf id/username/first_name/last_name/mutual_friends.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        connected_ids = accepted_connection_ids(self.request.user)
        connected_ids.discard(self.request.user.id)

        # FIX: someone you've since blocked (or who blocked you) could
        # still show up here as a "connection" and be pickable as a chat
        # contact.
        blocked_ids = BlockUser.objects.filter(
            Q(blocker=self.request.user) | Q(blocked=self.request.user)
        ).values_list("blocker_id", "blocked_id")
        for blocker_id, blocked_id in blocked_ids:
            connected_ids.discard(blocker_id)
            connected_ids.discard(blocked_id)

        return User.objects.filter(id__in=connected_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search only within users who follow you or whom you follow (for chat/group member picking)",
    )
    def list(self, request, *args, **kwargs):
        # N+1 FIX: precompute mutual_friends connections for the whole
        # page in 2 queries instead of 2 queries per row (see
        # bulk_accepted_connection_ids docstring).
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Contacts fetched successfully.",
            "data": data,
        })


class FollowersListView(ListAPIView):
    """
    GET /profile/profile/<username>/followers/

    Target user (URL me diye gaye username) ke saare ACCEPTED followers —
    same `MessageContactSearchSerializer` reuse kiya hai isliye response
    me id/username/first_name/last_name ke saath tumhare (request.user)
    sath unka mutual_friends count bhi milta hai.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer

    def get_queryset(self):
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        follower_ids = Follow.objects.filter(
            following=target_user, status=Follow.Status.ACCEPTED
        ).values_list("follower_id", flat=True)
        return User.objects.filter(id__in=follower_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(description="List of a user's followers, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Followers fetched successfully.",
            "data": data,
        })


class FollowingListView(ListAPIView):
    """
    GET /profile/profile/<username>/following/

    Target user (URL me diye gaye username) jinhe follow karta hai unki
    list — same shape/serializer jaisa `FollowersListView`.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer

    def get_queryset(self):
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        following_ids = Follow.objects.filter(
            follower=target_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        return User.objects.filter(id__in=following_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(description="List of who a user is following, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Following fetched successfully.",
            "data": data,
        })


class FollowAPIView(GenericAPIView):
    """Follow/Unfollow a user with count update"""
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of user to follow/unfollow",
            )
        ],
        responses={
            200: FollowActionResponseSerializer,
            201: FollowActionResponseSerializer,
            400: OpenApiTypes.OBJECT,
        },
        description="Follow or unfollow a user",
    )
    @transaction.atomic
    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"error": "You cannot follow yourself"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        following_user = get_object_or_404(User, id=user_id)

        # FIX: blocking wasn't checked — you could still send a follow
        # request to (or be followed by) someone in a block relationship.
        if is_blocked_between(request.user, following_user):
            return Response(
                {"error": "You can't follow this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        follow_obj = Follow.objects.filter(
            follower=request.user, following=following_user
        ).first()

        if follow_obj:
            # Unfollow: count minus karo
            if follow_obj.status == Follow.Status.ACCEPTED:
                User.objects.filter(id=request.user.id).update(
                    following_count=F("following_count") - 1
                )
                User.objects.filter(id=following_user.id).update(
                    followers_count=F("followers_count") - 1
                )

            follow_obj.delete()
            return Response({
                "message": "Unfollowed successfully",
                "status": None,
            }, status=status.HTTP_200_OK)

        # Naya follow create karo
        is_private = following_user.is_private
        try:
            # FIX: two rapid duplicate requests (double-tap, retry after a
            # slow response, etc) could both pass the `.filter().first()`
            # check above before either commits, then both try to
            # `.create()` — the DB's UniqueConstraint would correctly
            # reject the second one, but as an unhandled IntegrityError
            # that surfaces as a raw 500 instead of a clean response.
            new_follow = Follow.objects.create(
                follower=request.user,
                following=following_user,
                status=Follow.Status.PENDING if is_private else Follow.Status.ACCEPTED,
            )
        except IntegrityError:
            existing = Follow.objects.filter(
                follower=request.user, following=following_user
            ).first()
            return Response({
                "message": "Follow request sent" if existing and existing.status == Follow.Status.PENDING else "Followed successfully",
                "status": existing.status if existing else None,
                "follow_id": existing.id if existing else None,
            }, status=status.HTTP_200_OK)

        if not is_private:
            User.objects.filter(id=request.user.id).update(
                following_count=F("following_count") + 1
            )
            User.objects.filter(id=following_user.id).update(
                followers_count=F("followers_count") + 1
            )

        return Response({
            "message": "Follow request sent" if new_follow.status == Follow.Status.PENDING else "Followed successfully",
            "status": new_follow.status,
            "follow_id": new_follow.id,
        }, status=status.HTTP_201_CREATED)


class AcceptFollowRequestView(GenericAPIView):
    """Accept a follow request with count update"""
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to accept",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Accept a pending follow request",
    )
    @transaction.atomic
    def post(self, request, follow_id):
        # Sirf jis user ko request aayi hai wahi accept kar sakta hai
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        follow_request.status = Follow.Status.ACCEPTED
        follow_request.save(update_fields=["status"])

        User.objects.filter(id=follow_request.follower_id).update(
            following_count=F("following_count") + 1
        )
        User.objects.filter(id=request.user.id).update(
            followers_count=F("followers_count") + 1
        )

        return Response({
            "message": "Follow request accepted",
            "status": follow_request.status,
        }, status=status.HTTP_200_OK)


class RejectFollowRequestView(GenericAPIView):
    """Reject a follow request"""
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to reject",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Reject a pending follow request",
    )
    def post(self, request, follow_id):
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        follow_request.delete()

        return Response({
            "message": "Follow request rejected",
            "status": None,
        }, status=status.HTTP_200_OK)


class UpdateProfileView(GenericAPIView):
    """
    Update logged-in user's profile
    Allowed fields: username, first_name, last_name, bio, profile_photo, is_private
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProfileUpdateSerializer
    parser_classes = [MultiPartParser, FormParser]  # Image upload ke liye zaruri

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={200: ProfileUpdateSerializer},
        description="Update current user's profile. Send only fields you want to update.",
    )
    def patch(self, request):
        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": True,
                "message": "Profile updated successfully.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)

        return Response({
            "status": False,
            "message": "Validation failed.",
            "errors": serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)


# Block / Unblock user
# Model (BlockUser) profile app me hai isliye API bhi yahin — message
# app sirf inhe consume karega (chat screen "is-blocked?" check).
class BlockedUsersView(GenericAPIView):
    """
    GET  /profile/blocked-users/          -> maine jinko block kiya hai unki list
    POST /profile/blocked-users/  {"blocked": <user_id>} -> block karo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer

    @extend_schema(
        responses={200: BlockUserSerializer(many=True)},
        description="List of users blocked by the current user",
    )
    def get(self, request):
        qs = BlockUser.objects.filter(blocker=request.user).select_related("blocked")
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Blocked users fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=BlockUserSerializer,
        responses={201: BlockUserSerializer, 200: BlockUserSerializer},
        description="Block a user",
    )
    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        blocked_user = serializer.validated_data["blocked"]

        block_obj, created = BlockUser.objects.get_or_create(
            blocker=request.user,
            blocked=blocked_user,
        )

        if created:
            # Block hote hi dono taraf ka follow-relation khatam karo, aur
            # jo ACCEPTED tha uska count bhi ghata do (FollowAPIView ke
            # unfollow wale logic jaisa hi).
            follow_qs = Follow.objects.filter(
                Q(follower=request.user, following=blocked_user)
                | Q(follower=blocked_user, following=request.user)
            )
            for f in follow_qs:
                if f.status == Follow.Status.ACCEPTED:
                    User.objects.filter(id=f.follower_id).update(
                        following_count=F("following_count") - 1
                    )
                    User.objects.filter(id=f.following_id).update(
                        followers_count=F("followers_count") - 1
                    )
            follow_qs.delete()

        return Response({
            "status": True,
            "message": "User blocked successfully." if created else "User already blocked.",
            "data": self.get_serializer(block_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnblockUserView(GenericAPIView):
    """
    DELETE /profile/blocked-users/<id>/

    `<id>` ya to BlockUser record ki apni id ho sakti hai, ya seedha
    target USER ki id — dono support karte hain (chat screen seedha
    otherParticipant.id pass karta hai, alag se record-id track nahi karta).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer

    @extend_schema(description="Unblock a user (accepts BlockUser id or target user id)")
    def delete(self, request, id):
        block_obj = BlockUser.objects.filter(
            Q(pk=id) | Q(blocked_id=id),
            blocker=request.user,
        ).first()

        if not block_obj:
            return Response({
                "status": False,
                "message": "Block record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        block_obj.delete()
        return Response({
            "status": True,
            "message": "User unblocked successfully.",
        }, status=status.HTTP_200_OK)
```

### View notes / what changed vs v1
- **Explicit imports now** — no more `from .serializers import *` /
  `from .models import *`. If you add a new serializer/model and forget
  to import it in `views.py`, you'll get a clean `NameError` at import
  time instead of it silently working via wildcard.
- **`is_blocked_between()`** is the one new shared helper — reused by
  both `UserProfileDetailView` and `FollowAPIView`. If you add more
  block-sensitive endpoints later, reuse this instead of re-writing the
  `Q(blocker=...) | Q(blocked=...)` query.
- **Pagination-aware `mutual_friends` batching** — note the repeated
  pattern in `MessageContactSearchView.list`, `FollowersListView.list`,
  `FollowingListView.list`: `filter_queryset` → `paginate_queryset` →
  build `_connections_map` from **only the current page's rows** → serialize.
  If you add a fourth view using `MessageContactSearchSerializer` with
  `many=True`, copy this exact pattern (don't just call
  `accepted_connection_ids` per row again).

---

## 7. `urls.py` (full current code)

```python
from django.urls import path

from .views import (
    AcceptFollowRequestView,
    BlockedUsersView,
    FollowAPIView,
    FollowersListView,
    FollowingListView,
    MessageContactSearchView,
    ProfileView,
    RejectFollowRequestView,
    UnblockUserView,
    UpdateProfileView,
    UserProfileDetailView,
    UserSearchView,
)

urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("chat-search/", MessageContactSearchView.as_view(), name="message-contact-search"),
    path("profile/<str:username>/", UserProfileDetailView.as_view(), name="user-profile-detail"),
    path("profile/<str:username>/followers/", FollowersListView.as_view(), name="user-followers"),
    path("profile/<str:username>/following/", FollowingListView.as_view(), name="user-following"),
    path("follow/<int:user_id>/", FollowAPIView.as_view(), name="follow-user"),
    path("accept-request/<int:follow_id>/", AcceptFollowRequestView.as_view(), name="accept-request"),
    path("reject-request/<int:follow_id>/", RejectFollowRequestView.as_view(), name="reject-request"),
    path("update/", UpdateProfileView.as_view(), name="update-profile"),
    path("blocked-users/", BlockedUsersView.as_view(), name="blocked-users"),
    # FIX: was `<str:id>` even though `UnblockUserView` only ever
    # compares it against integer PKs (`Q(pk=id) | Q(blocked_id=id)`).
    # `<int:id>` makes Django itself 404 on non-numeric input instead of
    # letting a bad value fall through to the ORM.
    path("blocked-users/<int:id>/", UnblockUserView.as_view(), name="unblock-user"),
]
```

### Full endpoint table
(assuming this is included in root urls as `path('profile/', include('user_profile.urls'))`)

| Method | URL | View | Auth | Purpose |
|---|---|---|---|---|
| GET | `/profile/` | `ProfileView` | ✅ | My own profile |
| GET | `/profile/search/?search=` | `UserSearchView` | ✅ | Search users (self + blocked relationships excluded, `is_active=True` only) |
| GET | `/profile/chat-search/?search=` | `MessageContactSearchView` | ✅ | Search only connected users, blocked excluded (for chat/group add-member) |
| GET | `/profile/profile/<username>/` | `UserProfileDetailView` | ✅ | Target user's profile + two-way follow status; 404 if blocked either way; restricted card if private & not accepted-follower |
| GET | `/profile/profile/<username>/followers/` | `FollowersListView` | ✅ | Target user's accepted followers, with batched mutual_friends |
| GET | `/profile/profile/<username>/following/` | `FollowingListView` | ✅ | Who target user follows (accepted), with batched mutual_friends |
| POST | `/profile/follow/<user_id>/` | `FollowAPIView` | ✅ | Follow/unfollow toggle; blocked pairs rejected; race-safe |
| POST | `/profile/accept-request/<follow_id>/` | `AcceptFollowRequestView` | ✅ | Accept pending follow request |
| POST | `/profile/reject-request/<follow_id>/` | `RejectFollowRequestView` | ✅ | Reject pending follow request |
| PATCH | `/profile/update/` | `UpdateProfileView` | ✅ | Update my profile (partial, multipart) — now includes `is_private` |
| GET | `/profile/blocked-users/` | `BlockedUsersView` | ✅ | List users I've blocked |
| POST | `/profile/blocked-users/` | `BlockedUsersView` | ✅ | Block a user |
| DELETE | `/profile/blocked-users/<int:id>/` | `UnblockUserView` | ✅ | Unblock (id = block record id OR target user id) |

Note: the URL pattern `/profile/profile/<username>/` looks doubled
because the app itself is mounted at `profile/` in the root urls, and
this app's own path also starts with `profile/<str:username>/`. That's
intentional given the current routing — just be aware of it when wiring
the frontend.

---

## 8. `admin.py` (full current code)

```python
from django.contrib import admin

from .models import BlockUser, CoinLedger, Follow, RestrictUser


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("id", "follower", "following", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("follower__username", "following__username")
    autocomplete_fields = ("follower", "following")
    ordering = ("-created_at",)


@admin.register(BlockUser)
class BlockUserAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    autocomplete_fields = ("blocker", "blocked")
    ordering = ("-created_at",)


@admin.register(RestrictUser)
class RestrictUserAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "restricted", "created_at")
    search_fields = ("user__username", "restricted__username")
    autocomplete_fields = ("user", "restricted")
    ordering = ("-created_at",)


@admin.register(CoinLedger)
class CoinLedgerAdmin(admin.ModelAdmin):
    # `credit`/`debit` no longer exist on CoinLedger — updated for the
    # redesigned amount/transaction_type/reference/balance_after shape
    # (see §0 item 13, §4).
    list_display = (
        "id",
        "user",
        "transaction_type",
        "amount",
        "balance_after",
        "reference",
        "created_at",
    )
    list_filter = ("transaction_type", "created_at")
    search_fields = ("user__username", "reference")
    autocomplete_fields = ("user",)
    ordering = ("-created_at",)
```

Upgraded from v1's bare `admin.site.register(Model)` wildcard-import
style to explicit `@admin.register` + custom `ModelAdmin` per model —
all four models are now searchable/filterable/orderable in `/admin/`.
`autocomplete_fields` on FK fields needs each referenced model's own
`ModelAdmin` (the custom `User` model's admin) to itself declare
`search_fields`, otherwise Django raises an error at startup — verify
that's true for your `User` admin.

---

## 9. `tests.py` (full current code)

```python
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import BlockUser, Follow

User = get_user_model()


class FollowModelTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_self_follow_blocked_at_db_level(self):
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.alice, following=self.alice)


class FollowAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_cannot_follow_yourself(self):
        url = reverse("follow-user", args=[self.alice.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_follow_then_unfollow_updates_counts(self):
        url = reverse("follow-user", args=[self.bob.id])
        self.client.post(url)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)

        self.client.post(url)  # unfollow
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_blocked_user_cannot_be_followed(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("follow-user", args=[self.bob.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_private_profile_hides_bio_from_non_follower(self):
        self.bob.is_private = True
        self.bob.bio = "secret bio"
        self.bob.save()

        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_restricted_view"])
        self.assertNotIn("bio", response.data["data"])

    def test_blocked_user_gets_404_on_profile_lookup(self):
        BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

### Coverage this gives you
- Self-follow rejected at DB level (`IntegrityError`) — covers the new
  `CheckConstraint`.
- Self-follow rejected at API level (400, before it ever reaches the DB).
- Follow → unfollow correctly increments/decrements both users' counters.
- A user blocked by the target cannot send a follow request (400).
- A private profile viewed by a non-follower returns
  `is_restricted_view: true` and omits `bio` from `data`.
- A profile lookup between blocked users returns 404 (not 403 — see
  §6's `is_blocked_between` note on why).

### Still missing (suggested next tests, if/when you add more)
- Private-account request flow end-to-end (pending → accept / reject
  actually flips status + counts).
- `BlockedUsersView`/`UnblockUserView` — block wipes existing follow
  relations + counts both ways; unblock resolving by both id types;
  duplicate-block idempotency via `get_or_create`.
- `UserSearchView` excludes self and blocked users from results.
- `MessageContactSearchView` / `FollowersListView` / `FollowingListView`
  — `mutual_friends` correctness with the new batched
  `bulk_accepted_connection_ids`.
- Race-condition test for `FollowAPIView`'s `IntegrityError` handling
  (two near-simultaneous follow requests → one 201, one clean 200, no 500).

---

## 10. Business Logic Flows

### 10.1 Follow / Unfollow (`POST /profile/follow/<user_id>/`)
```
Request from user A to follow user B
        │
        ▼
A == B?  ──yes──▶ 400 "You cannot follow yourself"
        │no
        ▼
Blocked either direction (A↔B)? ──yes──▶ 400 "You can't follow this user."
        │no
        ▼
Existing Follow(A→B) row?
   │                    │
  yes                   no
   │                     │
   ▼                     ▼
UNFOLLOW / CANCEL     is B private?
- if status ACCEPTED:      │            │
  decrement A.following   yes           no
  decrement B.followers     │            │
- delete row               ▼            ▼
- return 200          create PENDING  create ACCEPTED
                       row, no count    row, increment
                       change yet       counts immediately
                            │            │
                            └────┬───────┘
                                 ▼
                     IntegrityError (race,
                     duplicate create)? ──yes──▶ re-fetch existing row,
                                                  return 200 (not 500)
                                 │no
                                 ▼
                            return 201
```

### 10.2 Accept / Reject a pending request
- **Accept** (`POST /profile/accept-request/<follow_id>/`): only the
  *target* of the request (`following=request.user`) can accept; status
  flips `PENDING → ACCEPTED` (via `save(update_fields=["status"])`);
  **only now** do the follower/following counts increment (this is why
  counts weren't touched at request-creation time for private accounts).
- **Reject** (`POST /profile/reject-request/<follow_id>/`): only the
  target can reject; the `Follow` row is deleted outright, no counts
  touched (none were ever added).

### 10.3 Block a user (`POST /profile/blocked-users/`)
```
Validate target != self
        │
        ▼
get_or_create(BlockUser(blocker=me, blocked=target))
        │
   created?
   │        │
  yes        no (already blocked)
   │          │
   ▼          ▼
Find ANY Follow row between me & target
(either direction) →
  - if it was ACCEPTED: decrement the
    relevant counts on both sides
  - delete all such Follow rows
   │
   ▼
return 201                    return 200
("blocked successfully")   ("already blocked")
```
Blocking is **one-directional as a record** (only "me → target" is
stored) but its *side-effect* wipes the follow relation **both ways**
(A follows B, or B follows A, or a pending request in either direction —
all get deleted). Once blocked, **both directions** are now also
enforced going forward on: profile lookup, follow attempts, user search,
and chat-contact search (see §0 changelog items 1–4).

### 10.4 Unblock (`DELETE /profile/blocked-users/<id>/`)
Looks up a `BlockUser` row where `blocker=me` AND (`pk == id` OR
`blocked_id == id`) — so the frontend can pass either the block record's
own id, or simply the other user's id, without tracking which one it has.

### 10.5 Profile lookup & privacy (`GET /profile/profile/<username>/`)
```
Fetch target_user by username (404 if not found)
        │
        ▼
target != me AND blocked-either-way? ──yes──▶ 404 (looks identical to "not found")
        │no
        ▼
is_self = target == me
is_accepted_follower = my Follow(me→target).status == ACCEPTED
        │
        ▼
is_restricted_view = target.is_private AND NOT is_self AND NOT is_accepted_follower
        │                                         │
       yes                                        no
        │                                         │
        ▼                                         ▼
RestrictedTargetUserProfileSerializer      TargetUserProfileSerializer
(id, username, first/last name,            (full: + bio, photo, counts)
 is_private, is_verified only)
```

### 10.6 Mutual friends (`accepted_connection_ids` / `bulk_accepted_connection_ids`)
For any user, their "connections" = union of:
- everyone they follow with status ACCEPTED, **and**
- everyone who follows them with status ACCEPTED.

`mutual_friends` on `MessageContactSearchSerializer` = size of the
intersection of *my* connection set and *their* connection set. Used in
`MessageContactSearchView`, `FollowersListView`, `FollowingListView` (all
share `MessageContactSearchSerializer`). All three views now compute
connections for their **entire page** in one `bulk_accepted_connection_ids`
call rather than per-row.

---

## 11. Known Issues / Things To Double-Check

Carried-over items from v1 that are **still open** (see §0 changelog for
what got resolved this round):

1. **`RestrictUser` model is unused** — no view/serializer touches it
   yet, only DB-level self-restrict protection was added. Either wire it
   up (Instagram-style "restrict" feature) or remove it if not planned.
2. **`CoinLedger` model is unused in the API** — only registered in
   admin; no endpoint creates/reads ledger rows (`amount`/
   `transaction_type`/`reference`/`balance_after`) currently. `coin`
   field on `User` (used by `UserProfileSerializer`) is separate — see
   §4.
3. **`FollowSerializer` is unused** — defined but no view references it
   (views return raw dicts via `FollowActionResponseSerializer` instead).
   Harmless, but dead code if not needed elsewhere.
4. **Counter drift risk still exists** — `F('following_count') - 1` etc.
   are atomic at the SQL level (good), but nothing stops counts from
   drifting if a `Follow` row is ever deleted outside this app's views
   (e.g. via admin, or a cascade from user deletion). Consider a
   periodic reconciliation job or signal-based recount if this matters
   for your product.
5. **`CoinLedger` migration risk** — two separate things to check before
   running `makemigrations`: (a) if a migration already exists against
   the old `coins` model name, Django will try to rename/recreate the
   table — write a `db_table`-preserving migration by hand, or pin
   `Meta.db_table = "user_profile_coins"` first; (b) if a migration
   already exists against the *older* `credit`/`debit` field shape,
   write a real migration (RemoveField credit,debit + AddField amount,
   transaction_type,reference,balance_after,description,metadata) rather
   than letting `makemigrations` guess. Per item (11.2) nothing writes to
   this table yet, so in practice there should be zero rows to migrate
   either way (see §4/§0 item 13).
6. **`autocomplete_fields` in `admin.py`** requires the target model's
   own admin (your custom `User` model's `ModelAdmin`) to declare
   `search_fields` — verify this exists on your `User` admin, or Django
   will raise a startup error (`E040`).
7. **`CheckConstraint` needs Django ≥ 5.1.** All four `CheckConstraint`
   calls (`Follow`, `BlockUser`, `RestrictUser`, `CoinLedger`) use
   `condition=` rather than the older `check=` kwarg. `condition=` was
   only added in Django 5.1; on anything older, use `check=` instead. On
   Django 6.0+, `check=` was removed outright and raises `TypeError:
   CheckConstraint.__init__() got an unexpected keyword argument
   'check'` at model-import time — which surfaces as `makemigrations`/
   `migrate`/`runserver` all failing with a traceback pointing at
   `models.py`, not as a normal migration error. Run
   `python -m django --version` to confirm you're on 5.1+ before relying
   on `condition=`.

---

## 12. Quick Setup Checklist (to run this app standalone)

- [ ] Custom `User` model has: `profile_photo`, `bio`, `is_private`,
      `is_verified`, `is_active`, `followers_count`, `following_count`,
      `posts_count`, `coin` (see §2).
- [ ] `AUTH_USER_MODEL` set in `settings.py`.
- [ ] `'rest_framework'`, `'drf_spectacular'`, `'user_profile'` in
      `INSTALLED_APPS`.
- [ ] `path('profile/', include('user_profile.urls'))` in root `urls.py`.
- [ ] `MEDIA_URL` / `MEDIA_ROOT` configured (for `profile_photo` uploads
      via `MultiPartParser`).
- [ ] Your `User` model's own `ModelAdmin` declares `search_fields` (for
      `autocomplete_fields` in this app's `admin.py` to work).
- [ ] If a `coins` table/migration already exists in prod, handle the
      `CoinLedger` rename migration by hand (see §4, §11 item 5).
- [ ] Run `python manage.py makemigrations user_profile && python manage.py migrate`.
- [ ] (Optional) Add `REST_FRAMEWORK` / `SPECTACULAR_SETTINGS` in
      `settings.py` if not already global for the project.

With the above satisfied, everything in this single document — models,
serializers, views, urls, admin, tests — is enough to run the full
`user_profile` app end to end.