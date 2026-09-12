# `user_profile` App — Complete Self-Contained Reference

> **v3.1 — v3 (TASK 18 / 19 / 28 / 30) plus a small follow-up patch
> (B-8 / F-3, under the FEE-2 initiative).**
> Ye ek hi file hai jisme poore **user_profile** Django app ka sara
> logic, code, connections, flows aur known issues cover hain. Iske
> alawa kisi aur file ki zaroorat nahi — sab kuch (models → serializers
> → views → urls → admin → tasks → tests) yahin milega.
>
> **v2 se kya badla, sabse pehle:** section 0.1 (Changelog v2 → v3)
> padho — usme is round ke sab naye fixes/features ek jagah list hain
> (`RestrictUser` ab wired hai, `CoinLedger` ab actually likhi/padhi
> jaati hai, follow-count drift ke liye ek Celery reconciliation task
> add hui, aur test coverage kaafi expand hui). Baaki poora document un
> changes ko reflect karta hua, fully updated code ke saath, dubara
> likha gaya hai.
>
> **v3 → v3.1 (is chhoti patch me kya badla):** section 0.2 (Changelog
> v3 → v3.1) padho — `admin.py` ab upload ho gaya, isliye `CoinLedger`
> ka pending admin-lockdown caveat (B-8) finally band ho gaya, aur
> `models.py` me ek naya `CAMPUS_REWARD` transaction type (F-3) add hua
> hai — dono `FEE-2` (real tuition-fee money ab isi ledger se guzarta
> hai) ke context me.

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

### Status as of v2 (superseded — see §0.1 for what changed in v3)
- ~~`RestrictUser` model abhi bhi kisi feature se wired nahi~~ — **v3 me
  wired ho gaya**, see §0.1 item 1.
- ~~`CoinLedger` abhi bhi kahin se likha nahi jaata~~ — **v3 me
  `record_transaction()` ke through likhi/padhi jaati hai**, see §0.1
  item 2.
- ~~`FollowSerializer` abhi bhi unused hai~~ — **v3 me remove kar di gayi**
  (dead code tha, koi view/import isse reference nahi karta tha).

---

## 0.2 Changelog — v3 → v3.1 (chhoti follow-up patch: B-8 / F-3)

Is baar sirf 2 files me change hua — `admin.py` (naya upload) aur
`models.py`. Baaki sab files (`views.py`, `serializers.py`, `urls.py`,
`tasks.py`, `tests.py`) v3 wale hi hain, unme koi naya logic nahi aaya.

1. **B-8 — `CoinLedgerAdmin` ab read-only hai.** v3 ke §11 item 2 me jo
   caveat flag hua tha ("admin.py upload nahi hua tha, isliye ye fix
   nahi ho saka") — ab `admin.py` upload ho gaya hai aur ye caveat close
   ho gaya. `CoinLedgerAdmin` ab `has_add_permission`,
   `has_change_permission`, aur `has_delete_permission` — teeno `False`
   return karte hain, plus `readonly_fields = [f.name for f in
   CoinLedger._meta.fields]` belt-and-braces ke liye. Matlab admin panel
   se ab koi `CoinLedger` row add/edit/delete nahi kar sakta — sirf
   dekh sakta hai. Ye `record_transaction()` ke around ek unguarded
   dusra write-path tha, jo ab band ho gaya (see §8).
2. **FEE-2 — context jo is fix ko zaroori banata hai.** `CoinLedger`
   (same table jo in-app coins track karti thi) ab real tuition-fee
   payments (`FeeInvoice`/`FeePayment` ke through, kisi doosri app se —
   is upload ka hissa nahi) bhi carry karti hai. Isi wajah se B-8 ab
   sirf "data-integrity nicety" nahi, balki ek real-money correctness
   fix hai — ek stray admin edit ab sirf coin count nahi, balki fee
   ledger bhi corrupt kar sakta tha.
3. **F-3 — naya `TransactionType.CAMPUS_REWARD`.** `models.py` me
   `CoinLedger.TransactionType` me ek naya choice add hua —
   `campus_reward` — jo `campus` app ke chhote engagement bonuses
   (attendance streak, on-time assignment streak) ke liye hai.
   Deliberately `EARN` se alag rakha gaya hai: `FEE-3`/`FEE-6`
   (`campus/tasks.py`, is upload ka hissa nahi) already student ka
   `User.coin` balance padhta hai ye decide karne ke liye ki wo upcoming
   fee cover karta hai ya nahi — is naye type se admin/support ek nazar
   me bata sakta hai ki koi coin "reward se aaya" ya "real top-up se
   aaya", amounts cross-reference kiye bina.

Koi migration-shape-breaking change nahi hai — `CAMPUS_REWARD` sirf ek
naya `TextChoices` value hai (naya `makemigrations` chalega, par koi
existing field remove/rename nahi hua), aur admin lockdown DB schema
ko bilkul touch nahi karta.

---

## 0.1 Changelog — v2 → v3 (is round me kya fix/add hua)

### 🧩 Features — previously-unwired models ab actually wired hain
1. **TASK 18 — `RestrictUser` ab ek real feature hai**, sirf DB-level
   constraint nahi. Naye endpoints:
   - `GET/POST /profile/restricted-users/` (`RestrictedUsersView`) — list
     of users I've restricted / restrict a user.
   - `DELETE /profile/restricted-users/<id>/` (`UnrestrictUserView`) —
     accepts either the `RestrictUser` row's own id or the target user's
     id (same flexibility as `UnblockUserView`).
   - Naya `RestrictUserSerializer` (mirrors `BlockUserSerializer`'s
     shape) — `validate_restricted` already-blocked user ko restrict
     karne se reject karta hai (block strictly stronger hai, restrict
     kuch add nahi karta uske upar).
   - Naya shared helper `is_restricted_between(user, other)` in
     `views.py` (`is_blocked_between()` jaisa, but **one-way** —
     restrict silent/asymmetric hai by design).
   - `UserProfileDetailView`'s response me naya `am_i_restricting` field
     (sirf *mera* restrict-status target ke against — reverse kabhi
     expose nahi hota, restrict ka poora point hi silent rehna hai).
   - **Scope deliberately limited:** restrict karne se koi `Follow` row
     ya count touch nahi hota (block ke unlike) — yeh sirf relationship
     record karta hai. Actual *effects* (comments hide karna, read-
     receipts/notifications mute karna) posts/message/notifications apps
     ka consumer-side kaam hai, `is_restricted_between()` consume karke —
     abhi tak wired nahi (in apps ka code is pass me nahi tha).
2. **TASK 19 — `CoinLedger` ab actually likhi jaati hai**, sirf admin me
   registered nahi. `CoinLedger.objects` ab custom `CoinLedgerManager`
   use karta hai jiska `record_transaction(user, transaction_type,
   amount, reference="", description="", metadata=None)` method **THE**
   sanctioned write path hai:
   - `user.coin` balance aur matching `CoinLedger` row **same DB
     transaction** me likhta hai (`select_for_update()` se user row lock
     karke) — dono kabhi drift nahi kar sakte.
   - `reference` diya ho to idempotent hai — same `(user, reference)`
     dobara call hone pe pehli wali entry as-is return hoti hai (retried
     webhook / client retry safe).
   - Zero amount, ya balance ko negative le jaane wala debit — dono
     `ValueError` raise karte hain (raw `IntegrityError` bubble up nahi
     karta).
   - Naya read-only endpoint: `GET /profile/coin-ledger/`
     (`CoinLedgerListView`) — logged-in user apni transaction history
     dekh sakta hai, newest-first (paginated, `CoinLedgerSerializer`
     poori tarah read-only, koi POST nahi — sirf `record_transaction()`
     hi rows bana sakta hai).
   - ⚠️ `CoinLedger` ka Django admin registration abhi bhi raw add/edit/
     delete allow karta hai — koi admin edit `record_transaction()`
     bypass karke balance/ledger ko drift kara sakta hai. `admin.py`
     ko `readonly_fields` (ya `has_add_permission`/`has_change_permission
     = False`) se lock karna abhi bhi pending hai (see §11 item 2).

### 🛠️ Reliability fix
3. **TASK 28 — followers/following counter-drift ab periodically
   auto-corrected hoti hai**, na ki sirf "known issue" ban ke reh jaati.
   Naya `tasks.py` — Celery task `reconcile_follow_counts` — har user ka
   `followers_count`/`following_count` seedha `Follow` rows se recompute
   karta hai (2 aggregate `GROUP BY` queries, O(1) query count regardless
   of user count) aur jo bhi drift mile usi ko fix karta hai. Detect-and-
   correct safety net hai, root-cause fix nahi (asli fix ek `Follow`
   `post_save`/`post_delete` signal hota, jo `post/models.py` me already
   is pattern se use hota hai) — but "note it, don't block on it" wala
   trade-off yahan bhi consistent hai is codebase ke saath. See §9a for
   full details aur `CELERY_BEAT_SCHEDULE` wiring.

### 🧪 Test coverage
4. **TASK 30 — `tests.py` me bahut saara naya coverage add hua**: private-
   account follow-request end-to-end flow
   (`PrivateAccountFollowRequestFlowTests`), block/unblock edge cases
   (`BlockUnblockEdgeCaseTests`), `RestrictUser`'s DB constraints aur
   "restrict doesn't touch Follow/counts" guarantee
   (`RestrictUserModelTests`), search exclusion for self/blocked users
   (`UserSearchExclusionTests`), aur double-follow race condition
   (`FollowRaceConditionTests`, thread-based, `IntegrityError` ke bajaye
   clean 200 verify karta hai). Purani `FollowModelTests` +
   `FollowAPITests` untouched hain.

### 🧹 Model cleanup (no behavior change)
5. **`models.py` me redundant single-column indexes hata di gayi.**
   `Follow`/`BlockUser` pe pehle `models.Index(fields=["follower"])` type
   ke standalone indexes explicitly declared the — ye zaroorat hi nahi
   thi kyunki Django har ForeignKey column pe already automatically ek
   index bana deta hai (`db_index=True` default). In duplicate indexes
   ko rakhna sirf har INSERT/DELETE pe ek extra btree update ka cost tha,
   koi query fayda nahi. Sirf **composite** indexes (`follower`+`status`,
   `following`+`status`) rakhe gaye hain kyunki wo automatic nahi hote.

---

## 1. App Overview

**App name:** `user_profile`
**Purpose:** User profiles, follow/unfollow (with private-account request
flow), followers/following lists, user search, "chat contacts" search
(mutual/connected users only), block/unblock users (enforced across
search/follow/profile-view), restrict/unrestrict users (one-way, silent
relationship — record only, effects not yet consumed by other apps),
read-only coin transaction history, profile update (with image upload +
privacy toggle).

**Tech stack:** Django + Django REST Framework + `drf-spectacular` (for
OpenAPI docs via `@extend_schema`).

**Files in this app:**
| File | Responsibility |
|---|---|
| `models.py` | `Follow`, `BlockUser`, `RestrictUser`, `CoinLedger` (+ `CoinLedgerManager.record_transaction()`) models |
| `serializers.py` | All request/response serializers + `accepted_connection_ids()` / `bulk_accepted_connection_ids()` helpers |
| `views.py` | All API endpoint logic (class-based views) + `is_blocked_between()` / `is_restricted_between()` helpers |
| `urls.py` | URL routing |
| `admin.py` | Django admin registration (proper `ModelAdmin` configs) |
| `apps.py` | App config (`name = 'user_profile'`) |
| `tasks.py` | Celery task `reconcile_follow_counts` — periodic followers/following counter drift correction |
| `tests.py` | `FollowModelTests`, `FollowAPITests`, `PrivateAccountFollowRequestFlowTests`, `BlockUnblockEdgeCaseTests`, `RestrictUserModelTests`, `UserSearchExclusionTests`, `FollowRaceConditionTests` |

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
# user_profile/models.py
"""
WHAT CHANGED in this pass, and why:

1. Follow / BlockUser — removed the single-column `models.Index(fields=
   ["follower"])`, `["following"])`, `["blocker"])`, `["blocked"])`
   entries. Django already creates a database index on every
   ForeignKey column automatically (that's what `db_index` defaults to
   True for on ForeignKey). Declaring them again in Meta.indexes doesn't
   make lookups faster — it silently builds a second, identical btree
   index that Postgres still has to update on every INSERT/DELETE. Kept
   the *composite* indexes (`follower`+`status`, `following`+`status`)
   since those are NOT automatic and genuinely serve the "my pending
   follow requests" / "who I actively follow" query shapes.

2. RestrictUser — TASK 18: this used to be defined but completely
   unwired (DB-level self-restrict guard only, no view/serializer ever
   touched it). Decision: BUILD the feature, not remove the model —
   `unique_restrict` + `restrict_no_self_restrict` are exactly the
   constraints a real restrict feature needs, and BlockUser next to it
   is proof this app is already the right home for this kind of
   relationship. Now wired via `RestrictedUsersView` /
   `UnrestrictUserView` (views.py) + `RestrictUserSerializer`
   (serializers.py) — see that model's docstring below for the actual
   semantics and what's intentionally still NOT done here.

4. All four `CheckConstraint(...)` calls (Follow, BlockUser,
   RestrictUser, CoinLedger) now pass `condition=` instead of `check=`.
   Django deprecated `CheckConstraint(check=...)` in favor of
   `condition=` back in 5.1, and removed `check` entirely in Django 6.0
   — on Django 6.0+, `check=` raises `TypeError: CheckConstraint.
   __init__() got an unexpected keyword argument 'check'` at import
   time (which is exactly what breaks `makemigrations`/`migrate` and
   even the dev server on startup). `condition=` works the same way and
   is supported back to Django 5.1, so this is safe on any currently
   supported Django version.

5. CoinLedger — redesigned in an earlier pass. The original had
   `credit`/`debit` as two separate PositiveIntegerFields, no way to
   tell what a transaction was *for*, and no protection against a
   retried webhook/request double-crediting a user. Since the original
   file's own comment already said "Nothing writes to this yet", there
   was no production data to migrate — that was the safe moment to fix
   the shape, not after it went live. Changes made then:
     - `credit`/`debit` -> single signed `amount` (positive = credit,
       negative = debit). One column instead of two, and a CHECK
       constraint guarantees it's never zero (a zero-amount ledger row
       is a bug, not a valid transaction).
     - Added `transaction_type` so "why did this user's balance change"
       is answerable from the row itself, not guessed from context.
     - Added `reference` (indexed, blank-ok) as an idempotency key —
       whatever created the transaction (a gift, a withdrawal, a
       purchase receipt) passes its own id here.
     - Added `balance_after` — a snapshot of `User.coin` immediately
       after this entry. This is what makes the ledger self-auditing:
       if `User.coin` and the ledger ever drift, you can bisect the
       ledger to find exactly where, instead of trusting a running
       total nobody can verify.
     - Added `description` for a human-readable reason, and `metadata`
       for anything structured that doesn't need its own column.
     - Added `Meta.ordering` and an index on (`user`, `-created_at`) —
       "this user's transaction history" is the obvious primary query
       and had no supporting index before.

   TASK 19 (this pass): the shape above was right, but the table still
   had no write OR read path anywhere in the codebase —
   `admin.site.register()` only, meaning `User.coin` was the only thing
   any real code path touched, so the "source of truth" claim in
   CoinLedger's docstring was aspirational, not true. Added
   `CoinLedger.objects.record_transaction()` (the one sanctioned, atomic
   write path every coin-changing action should call through — replaces
   the old "wrap it in `get_or_create(reference=...)` yourself" guidance,
   which never actually updated `User.coin` in the same breath) and a
   read-only `CoinLedgerListView` (views.py) so a user can see their own
   transaction history. See CoinLedger's class docstring below for the
   full purpose/scope writeup, including why no generic write endpoint
   is exposed and an admin.py caveat this pass couldn't fix (no
   admin.py in this upload).
"""
from django.conf import settings
from django.db import models, transaction
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
        # Newest-first by default — earlier there was no ordering, so
        # followers/following lists came back in whatever order the DB
        # felt like.
        ordering = ["-created_at"]

        constraints = [
            UniqueConstraint(
                fields=["follower", "following"],
                name="unique_follow",
            ),
            # "can't follow yourself" was only enforced in a commented-out
            # `clean()` (which ModelForms call, but plain
            # `.objects.create()` from views.py never does). A DB-level
            # constraint makes it impossible to bypass, from the admin, a
            # shell, or a future view that forgets the check.
            CheckConstraint(
                condition=~Q(follower=F("following")),
                name="follow_no_self_follow",
            ),
        ]

        indexes = [
            # NOTE: no standalone index on `follower` / `following` here —
            # Django already auto-indexes both ForeignKey columns
            # individually. Only the composite pairs below (which are NOT
            # automatic) are declared explicitly.
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

        # NOTE: no explicit indexes here — `blocker` and `blocked` are
        # both ForeignKeys and already get an automatic single-column
        # index each; there's no composite query shape for BlockUser that
        # needs one beyond that (unlike Follow, which is also filtered by
        # `status`).

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"


class RestrictUser(models.Model):
    """
    Instagram-style "restrict" — softer than block. `user` restricts
    `restricted`. Unlike BlockUser:
      - It's ONE-WAY and SILENT: `restricted` is never told, and (unlike
        block) nothing here removes an existing Follow relationship or
        hides either party from search/profile views — the whole point
        is that the restricted person's experience looks unchanged.
      - It does NOT block interaction: `restricted` can still view
        `user`'s profile/posts, follow, comment, and DM as normal from
        their own side.

    TASK 18 — now wired: `RestrictedUsersView` (list what I've
    restricted, restrict someone) and `UnrestrictUserView` (undo) live
    in views.py, `RestrictUserSerializer` in serializers.py, both
    reachable at `/profile/restricted-users/` — same URL/view shape as
    BlockUser's `/profile/blocked-users/` for consistency.

    Scope decision: this app (user_profile) only owns the
    relationship itself — who has restricted whom — the same way it
    owns Follow/BlockUser without knowing about posts, comments, DMs,
    or notifications. The actual *effects* of being restricted
    (hide the restricted user's comments from everyone but themselves,
    suppress notifications/read-receipts/online-status from them in
    the message app) are consumer-side integration work for the posts,
    notifications, and message apps respectively — each of those should
    filter through `views.is_restricted_between(user, other)` the same
    way this app's own `is_blocked_between()` is already used. Not
    implemented here because those apps' models/views weren't part of
    this pass.
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


class CoinLedgerManager(models.Manager):
    """
    TASK 19 — this is the actual fix: before this, literally nothing in
    the codebase wrote to `CoinLedger` (it was `admin.site.register()`'d
    only — visible in Django admin, unreachable from any API). `User.coin`
    was the only thing any code path actually touched, which means the
    "source of truth" claim in CoinLedger's docstring was aspirational,
    not true.

    `record_transaction()` is now THE one supported way to change
    `User.coin` — it updates the balance and writes the audit row in the
    same DB transaction, so the two can never drift the way two separate
    writes (`user.coin = user.coin + n; user.save()` then a separate
    `CoinLedger.objects.create(...)` elsewhere) could.
    """

    def record_transaction(self, *, user, transaction_type, amount,
                            reference="", description="", metadata=None):
        """
        Atomically apply `amount` (signed — positive credits, negative
        debits) to `user.coin` and record the matching CoinLedger row.

        Idempotent when `reference` is given: a second call with the
        same (user, reference) returns the FIRST entry as-is instead of
        applying the transaction again — safe for a retried webhook or a
        client retry after a dropped response. This is enforced by
        holding `select_for_update()` on the user's row for the whole
        transaction, so two concurrent calls for the SAME user always
        serialize on that lock — the second one's "does this reference
        already exist" check can never run before the first one's write
        has committed. (No DB UniqueConstraint on (user, reference) was
        added for this — the table has zero rows today, so adding one
        would need a fresh migration this pass doesn't have visibility
        into; the row lock gives the same guarantee for same-user
        concurrency, which is the case that actually matters here.)

        Raises `ValueError` for a zero amount or a debit that would take
        `user.coin` negative (PositiveIntegerField can't hold a negative
        balance anyway — this turns that into a clean, catchable error
        for the caller instead of an IntegrityError bubbling up).
        """
        if amount == 0:
            raise ValueError("CoinLedger amount must not be zero.")

        UserModel = type(user)  # avoid importing login.User here — this
        # app's models already only ever reference the user model via
        # settings.AUTH_USER_MODEL (see the FK fields above), never a
        # direct import, to keep user_profile decoupled from login.

        with transaction.atomic():
            locked_user = UserModel.objects.select_for_update().get(pk=user.pk)

            if reference:
                existing = self.filter(user=locked_user, reference=reference).first()
                if existing is not None:
                    return existing

            new_balance = locked_user.coin + amount
            if new_balance < 0:
                raise ValueError(
                    f"Insufficient coin balance: {locked_user.coin} + {amount} "
                    "would go negative."
                )

            UserModel.objects.filter(pk=locked_user.pk).update(coin=F("coin") + amount)

            return self.create(
                user=locked_user,
                transaction_type=transaction_type,
                amount=amount,
                balance_after=new_balance,
                reference=reference,
                description=description,
                metadata=metadata or {},
            )


class CoinLedger(models.Model):
    """
    The append-only, auditable history of every change to `User.coin`
    (the running-balance field on `login.User`). `User.coin` is a cache;
    this table is the source of truth — if they ever disagree, this table
    is right and `User.coin` should be recomputed from it.

    TASK 19 — clarifying purpose + wiring it up (decision: build, same
    call as RestrictUser in task 18 — the shape was already right, it
    just had no write or read path). Purpose: an auditable "why did my
    balance change" trail for a coin economy that (per the settings.py
    comments referencing PassPurchase/CoinPurchase in a liveclass app,
    and gifting in a message app) clearly spans more than this one app.

    Scope decision, same boundary as RestrictUser: user_profile owns
    this table and is the only place allowed to write to it — via
    `CoinLedger.objects.record_transaction()` below, never a raw
    `.create()` — but the actual coin-changing *actions* (a purchase
    completing, a gift being sent, an admin adjustment) are triggered by
    whichever app/view performs that action. Those apps weren't part of
    this upload, so what's added here is: the shared, atomic write path
    (`record_transaction`) every app should call through, and a
    read-only "my transaction history" endpoint (`CoinLedgerListView` in
    views.py) so a user can actually see it. No generic "create any
    ledger entry" API is exposed — that would let a client hand-write
    their own `earn`/`refund`/`gift_received` rows and, since
    `record_transaction` also moves the real balance, mint themselves
    coins.

    RESOLVED (was: "can't fix from this pass — admin.py wasn't
    uploaded"): Django admin used to allow raw add/edit/delete on this
    model directly, which bypassed `record_transaction()` entirely — it
    could create a ledger row with no matching change to `User.coin`, or
    edit an existing row's `amount` without ever touching the balance it
    supposedly explains, silently breaking the exact "these must always
    agree" invariant this table exists to guarantee. B-8 (this pass, now
    that admin.py is uploaded): `CoinLedgerAdmin` is locked read-only —
    `has_add_permission`/`has_change_permission`/`has_delete_permission`
    all return `False`, plus `readonly_fields = [f.name for f in
    CoinLedger._meta.fields]` as a belt-and-braces measure so even a
    future permission slip can't re-open a write path. Admin is now a
    viewer only. This matters more after FEE-2, which put real
    tuition-fee money through this same ledger (not just in-app coins) —
    see §8.

    F-3 (this pass): added `TransactionType.CAMPUS_REWARD` for
    `campus`'s small engagement bonuses (attendance streak, on-time
    assignment streak) — kept deliberately distinct from `EARN` because
    FEE-3/FEE-6 (`campus`'s own tasks.py, not part of this upload)
    already reads a student's `User.coin` balance to decide whether it
    covers an upcoming fee; an admin/support person scanning a student's
    ledger needs to tell "this coin came from a reward" apart from "this
    coin came from a real top-up" at a glance.

    Renamed from the original lowercase `coins` — kept here as `CoinLedger`
    (not `CoinTransaction`) to avoid breaking any existing imports/
    related_name usage elsewhere in the codebase; only the *fields*
    changed, not the class name or `related_name`.

    If a migration already exists against the old `credit`/`debit` shape,
    write this as a real migration (RemoveField credit/debit, AddField
    amount/transaction_type/reference/balance_after/description/metadata)
    rather than running makemigrations blind on a prod DB — but per the
    original comment ("Nothing writes to this yet"), there should be zero
    rows to migrate, so this is the cheap moment to make this change.
    """

    class TransactionType(models.TextChoices):
        EARN = "earn", "Earned"
        PURCHASE = "purchase", "Purchased"
        SPEND = "spend", "Spent"
        REFUND = "refund", "Refunded"
        GIFT_SENT = "gift_sent", "Gift Sent"
        GIFT_RECEIVED = "gift_received", "Gift Received"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"
        # F-3: campus engagement bonuses (attendance streak, on-time
        # assignment streak) — deliberately its own type, not EARN.
        # Keeping tuition-fee payments (real money, via FeeInvoice/
        # FeePayment) and these small in-app coin bonuses on visibly
        # different transaction_type values matters here specifically
        # because FEE-3/FEE-6 (campus/tasks.py) already reads a
        # student's `User.coin` balance to decide whether it covers an
        # upcoming fee — an admin/support person scanning this user's
        # ledger needs to tell at a glance "this coin came from a
        # reward, not a real top-up" without cross-referencing amounts.
        CAMPUS_REWARD = "campus_reward", "Campus Reward"

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
    # withdrawal id, a payment gateway receipt id, ...). TASK 19: don't
    # write this field directly — pass `reference=` to
    # `CoinLedger.objects.record_transaction()` above, which handles the
    # idempotent get-or-create-and-apply-balance dance atomically.
    reference = models.CharField(max_length=150, blank=True, db_index=True)

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # TASK 19: swap the default manager for one whose
    # `record_transaction()` is the only sanctioned write path — plain
    # `CoinLedger.objects.create(...)` still works (Manager doesn't
    # remove that), but every call site in this codebase should go
    # through `record_transaction()` instead so `balance_after` and the
    # actual `User.coin` update never come from two separate,
    # driftable writes.
    objects = CoinLedgerManager()

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
- **`RestrictUser`** — Instagram-style soft block: one-way, silent,
  doesn't touch `Follow` or hide anything by itself. **v3: now wired** —
  `RestrictedUsersView` / `UnrestrictUserView` (views.py) +
  `RestrictUserSerializer` (serializers.py), reachable at
  `/profile/restricted-users/`. DB-level self-restrict-proof. The
  model/app only owns the *relationship record* — the actual effects
  (hiding comments, muting notifications/read-receipts) are still
  unimplemented consumer-side work for posts/message/notifications apps,
  via `is_restricted_between()`.
- **`CoinLedger`** (renamed from `coins`) — a self-auditing ledger model
  (signed `amount` + `transaction_type` + `reference` idempotency key +
  `balance_after` snapshot). **v3: now the actual source of truth for
  `User.coin`** — its custom manager's `record_transaction()` is the one
  sanctioned way to change a balance (atomic: updates `User.coin` and
  writes the matching ledger row in the same locked DB transaction,
  idempotent on `reference`). Read via `GET /profile/coin-ledger/`
  (`CoinLedgerListView`). The `coin` field referenced in
  `UserProfileSerializer` (`fields = [..., "coin"]`) is a field expected
  on the **custom `User` model** (see §2), not this `CoinLedger` model —
  the naming is just coincidentally similar, don't confuse the two.
  Now also has a `CAMPUS_REWARD` transaction type (F-3) for campus
  engagement bonuses, kept distinct from `EARN` so it's visually
  separable from real tuition-fee-linked coin. ✅ `CoinLedgerAdmin` is
  now locked read-only (B-8) — admin can no longer add/edit/delete rows
  directly, closing the bypass-around-`record_transaction()` gap
  flagged in the previous pass — see §11 item 2 and §8.

---

## 5. `serializers.py` (full current code)

```python
# user_profile/serializers.py
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from .models import BlockUser, CoinLedger, Follow, RestrictUser

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
- `UserProfileDetailResponseSerializer.am_i_restricting` — **v3, new**.
  Only field on this response related to restrict; deliberately no
  `their_restrict_status` counterpart (restrict is one-way/silent by
  design, see models.py's `RestrictUser` docstring).
- `RestrictUserSerializer` — **v3, new**. Same shape as
  `BlockUserSerializer` (mirrors it for frontend consistency:
  `{"restricted": "<user_id>"}` in, `restricted_detail` nested read-only
  out). `validate_restricted` additionally rejects restricting someone
  you already have a `BlockUser` relationship with either direction
  (block is already the stronger relationship).
- `CoinLedgerSerializer` — **v3, new**, fully `read_only_fields = fields`
  on purpose: the only sanctioned way to create a `CoinLedger` row is
  `CoinLedger.objects.record_transaction()` (models.py) — a writable
  serializer here would let a client mint their own ledger rows (and
  therefore coins) without a matching balance change.
- `FollowSerializer` — **removed in v3** (was dead code — nothing ever
  imported or referenced it; views return raw dicts via
  `FollowActionResponseSerializer` instead).

---

## 6. `views.py` (full current code)

```python
# user_profile/views.py
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

from .models import BlockUser, CoinLedger, Follow, RestrictUser
from .serializers import (
    BlockUserSerializer,
    CoinLedgerSerializer,
    FollowActionResponseSerializer,
    MessageContactSearchSerializer,
    ProfileUpdateSerializer,
    RestrictedTargetUserProfileSerializer,
    RestrictUserSerializer,
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
    🔥 NEW: true if either user has blocked the other. Nothing in the
    original code checked this — you could follow, message-search-match,
    and view the full profile of someone who blocked you (or whom you'd
    blocked), which defeats the point of blocking.
    """
    return BlockUser.objects.filter(
        Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
    ).exists()


def is_restricted_between(user, other):
    """
    TASK 18 — true if `user` has restricted `other`. Deliberately ONE-
    WAY (unlike `is_blocked_between`, which is symmetric): restrict
    only affects what *the restricting user* experiences from `other`,
    never the reverse, and `other` must never be able to detect it from
    this check's result — see RestrictUser's docstring in models.py.

    Other apps (posts, message, notifications) should filter through
    this the same way they'd filter through `is_blocked_between` for
    block, once they're ready to apply restrict's actual effects
    (hiding comments from everyone but their author, muting read-
    receipts/online-status, suppressing notifications) — that
    integration is out of scope for user_profile itself.
    """
    return RestrictUser.objects.filter(user=user, restricted=other).exists()


class ProfileView(GenericAPIView):
    """
    Get logged-in user's profile
    """
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

    🔥 FIX: this class was defined TWICE in the original file — once as a
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

        # 🔥 FIX: block wasn't checked anywhere — a blocked/blocking user
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

        # 🔥 FIX: `is_private` existed on the model and was even returned
        # in the response body, but nothing ever *enforced* it — anyone
        # authenticated could read a private account's bio/photo/counts
        # just by knowing the username. Now: full data only for the owner,
        # public accounts, or accepted followers; everyone else gets a
        # minimal "this account is private" style payload.
        is_restricted_view = target_user.is_private and not is_self and not is_accepted_follower
        if is_restricted_view:
            profile_data = RestrictedTargetUserProfileSerializer(target_user).data
        else:
            profile_data = TargetUserProfileSerializer(target_user).data

        # TASK 18: whether *I* restrict the target — never the reverse
        # (see is_restricted_between's docstring). False for your own
        # profile since self-restrict is impossible.
        am_i_restricting = (
            not is_self and is_restricted_between(request.user, target_user)
        )

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
                "am_i_restricting": am_i_restricting,
                "data": profile_data,
            },
            status=status.HTTP_200_OK,
        )


class UserSearchView(ListAPIView):
    """
    Search users by username, first_name, last_name
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        # 🔥 FIX: search used to return literally every user, including
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

    🔥 Message/group ke "add members" step ke liye — `UserSearchView` se
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

        # 🔥 FIX: someone you've since blocked (or who blocked you) could
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
        # 🔥 FIX (N+1): precompute mutual_friends connections for the whole
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
    """
    Follow/Unfollow a user with count update
    """
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

        # 🔥 FIX: blocking wasn't checked — you could still send a follow
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
            # 🔥 FIX: two rapid duplicate requests (double-tap, retry after
            # a slow response, etc) could both pass the `.filter().first()`
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
    """
    Accept a follow request with count update
    """
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
    """
    Reject a follow request
    """
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


# TASK 18 — Restrict / Unrestrict user
# Deliberately mirrors BlockedUsersView/UnblockUserView just above (same
# request/response shape, POST body {"restricted": <user_id>}) for
# frontend consistency. The one functional difference from block's POST
# handler: restricting someone does NOT touch Follow rows or
# followers/following counts — see RestrictUser's docstring in
# models.py for why (restrict is silent and non-blocking by design).
class RestrictedUsersView(GenericAPIView):
    """
    GET  /profile/restricted-users/                -> maine jinko restrict kiya hai unki list
    POST /profile/restricted-users/  {"restricted": <user_id>} -> restrict karo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(
        responses={200: RestrictUserSerializer(many=True)},
        description="List of users restricted by the current user",
    )
    def get(self, request):
        qs = RestrictUser.objects.filter(user=request.user).select_related("restricted")
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Restricted users fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=RestrictUserSerializer,
        responses={201: RestrictUserSerializer, 200: RestrictUserSerializer},
        description="Restrict a user (silent — the restricted user is never notified).",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        restricted_user = serializer.validated_data["restricted"]

        # get_or_create, not create — same idempotency reasoning as
        # BlockedUsersView.post: a double-tap/retry should return the
        # existing record instead of a 400 from the UniqueConstraint.
        restrict_obj, created = RestrictUser.objects.get_or_create(
            user=request.user,
            restricted=restricted_user,
        )

        # No Follow/count changes here on purpose (unlike block) —
        # restrict must not change what either party can see or do,
        # only what the restricting user is exposed to from the other
        # side, and only once posts/message/notifications consume
        # `is_restricted_between()`.

        return Response({
            "status": True,
            "message": "User restricted successfully." if created else "User already restricted.",
            "data": self.get_serializer(restrict_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnrestrictUserView(GenericAPIView):
    """
    DELETE /profile/restricted-users/<id>/

    Same `<id>` flexibility as UnblockUserView — either the RestrictUser
    record's own id, or the target user's id directly.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(description="Unrestrict a user (accepts RestrictUser id or target user id)")
    def delete(self, request, id):
        restrict_obj = RestrictUser.objects.filter(
            Q(pk=id) | Q(restricted_id=id),
            user=request.user,
        ).first()

        if not restrict_obj:
            return Response({
                "status": False,
                "message": "Restrict record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        restrict_obj.delete()
        return Response({
            "status": True,
            "message": "User unrestricted successfully.",
        }, status=status.HTTP_200_OK)


# TASK 19 — coin transaction history
# Read-only, deliberately (see CoinLedgerSerializer's docstring for why
# there's no POST here). The actual write path —
# `CoinLedger.objects.record_transaction()` — is called from wherever a
# coin-changing action happens (a purchase completing in the liveclass
# app, a gift being sent in the message app, an admin adjustment
# endpoint if/when one gets built); none of those views were part of
# this upload, so this is the read side only: "let me see why my
# balance is what it is", which had no path at all before this.
class CoinLedgerListView(ListAPIView):
    """
    GET /profile/coin-ledger/

    The authenticated user's own coin transaction history, newest
    first (CoinLedger.Meta.ordering already gives us that for free).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinLedgerSerializer

    def get_queryset(self):
        return CoinLedger.objects.filter(user=self.request.user)

    @extend_schema(
        responses={200: CoinLedgerSerializer(many=True)},
        description="List of the current user's coin transaction history (newest first).",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Coin transaction history fetched successfully.",
            "data": data,
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
- **`is_restricted_between(user, other)`** — **v3, new** shared helper,
  same shape as `is_blocked_between()` but deliberately **one-way**
  (`user` restricted `other`, never the reverse) since restrict is
  silent/asymmetric by design. Used by `UserProfileDetailView` (for
  `am_i_restricting`) and internally by `RestrictedUsersView`. Other
  apps (posts/message/notifications) should filter through this the
  same way they'd use `is_blocked_between` for block, once they're
  ready to implement restrict's actual effects — not done in this app.
- **`UserProfileDetailView.get`** — now also computes `am_i_restricting`
  (`not is_self and is_restricted_between(request.user, target_user)`)
  and includes it in the response, alongside the existing
  `is_restricted_view` privacy check.
- **`RestrictedUsersView`** — **v3, new**. `GET` lists who I've
  restricted; `POST {"restricted": <user_id>}` restricts someone via
  `get_or_create` (idempotent, same double-tap reasoning as
  `BlockedUsersView.post`). Deliberately does **not** touch `Follow` rows
  or counts (unlike blocking) — restrict must not change what either
  party can see or do.
- **`UnrestrictUserView`** — **v3, new**. `DELETE
  /profile/restricted-users/<id>/`, same `<id>`-flexibility as
  `UnblockUserView` (accepts either the `RestrictUser` row's own id or
  the target user's id).
- **`CoinLedgerListView`** — **v3, new**, read-only `ListAPIView` at
  `GET /profile/coin-ledger/`. Returns the authenticated user's own
  transaction history, newest-first (free from `CoinLedger.Meta.ordering`).
  No write endpoint is exposed on purpose — writes only happen through
  `CoinLedger.objects.record_transaction()`, called from wherever a
  coin-changing action actually happens (a purchase, a gift, an admin
  adjustment) in whichever app owns that action — none of those views
  were part of this app's upload.

---

## 7. `urls.py` (full current code)

```python
# user_profile/urls.py
from django.urls import path

from .views import (
    AcceptFollowRequestView,
    BlockedUsersView,
    CoinLedgerListView,
    FollowAPIView,
    FollowersListView,
    FollowingListView,
    MessageContactSearchView,
    ProfileView,
    RejectFollowRequestView,
    RestrictedUsersView,
    UnblockUserView,
    UnrestrictUserView,
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
    # 🔥 FIX: was `<str:id>` even though `UnblockUserView` only ever
    # compares it against integer PKs (`Q(pk=id) | Q(blocked_id=id)`).
    # `<int:id>` makes Django itself 404 on non-numeric input instead of
    # letting a bad value fall through to the ORM.
    path("blocked-users/<int:id>/", UnblockUserView.as_view(), name="unblock-user"),
    # TASK 18 — same URL shape as blocked-users/ above, for RestrictUser.
    path("restricted-users/", RestrictedUsersView.as_view(), name="restricted-users"),
    path("restricted-users/<int:id>/", UnrestrictUserView.as_view(), name="unrestrict-user"),
    # TASK 19 — read-only coin transaction history.
    path("coin-ledger/", CoinLedgerListView.as_view(), name="coin-ledger"),
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
    # `credit`/`debit` no longer exist on CoinLedger — the model
    # was redesigned to a single signed `amount` column plus
    # `transaction_type`, `reference`, and a self-auditing
    # `balance_after` snapshot (see §0 item 13, §4). Listing the old
    # field names here would raise `FieldDoesNotExist` the moment this
    # admin page is opened.
    list_display = (
        "id",
        "user",
        "transaction_type",
        "amount",
        "balance_after",
        "reference",
        "created_at",
    )
    # `transaction_type` is a bounded TextChoices field — filtering by it
    # (like `status`/`created_at` on the other admins) is cheap and useful
    # for "show me all admin_adjustment rows" style audits.
    list_filter = ("transaction_type", "created_at")
    # `reference` is the idempotency key callers pass in (gift id,
    # withdrawal id, payment receipt id) — searchable so support/finance
    # can look up "what happened for reference X".
    search_fields = ("user__username", "reference")
    autocomplete_fields = ("user",)
    ordering = ("-created_at",)

    # B-8 FIX: CoinLedger rows must ONLY ever be created via
    # `record_transaction()`, which is what keeps `User.coin` and the
    # ledger's running `balance_after` in sync. Raw admin add/change/delete
    # bypasses that helper entirely and can desync the invariant — and
    # after FEE-2, CoinLedger also carries real tuition-fee money, not just
    # in-app coins, so a stray admin edit is a real-money bug, not a
    # cosmetic one. Make the whole model admin read-only:
    #   - no "Add" button (has_add_permission = False)
    #   - no editing existing rows (has_change_permission = False)
    #   - no deleting rows, so history can't be silently erased
    #     (has_delete_permission = False)
    #   - every field is listed in readonly_fields as a belt-and-braces
    #     measure, so even if change permission were ever re-enabled by
    #     mistake, the change form still can't save edits.
    readonly_fields = [f.name for f in CoinLedger._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

Upgraded from v1's bare `admin.site.register(Model)` wildcard-import
style to explicit `@admin.register` + custom `ModelAdmin` per model —
all four models are now searchable/filterable/orderable in `/admin/`.
`autocomplete_fields` on FK fields needs each referenced model's own
`ModelAdmin` (the custom `User` model's admin) to itself declare
`search_fields`, otherwise Django raises an error at startup — verify
that's true for your `User` admin.

**B-8 / FEE-2 (new this pass):** `CoinLedgerAdmin` is now fully
read-only — `has_add_permission`, `has_change_permission`, and
`has_delete_permission` all return `False`, and every field is also
listed in `readonly_fields` as a second layer of protection. This closes
the gap called out in the model's docstring and in §11 item 2 of the
previous pass: admin used to be a second, unguarded write path around
`record_transaction()`, and after FEE-2 wired real tuition-fee payments
through the same ledger, an admin-side slip is a real-money bug, not
just a cosmetic drift. `Follow`, `BlockUser`, and `RestrictUser` are
unaffected — only `CoinLedgerAdmin` gets this lockdown.

---

## 8a. `tasks.py` (new in v3 — full current code)

TASK 28 — a Celery task that detects and corrects
`followers_count`/`following_count` drift on the custom `User` model.
Per-operation increments/decrements in `FollowAPIView` /
`AcceptFollowRequestView` are correct for writes that go through those
views, but anything that deletes a `Follow` row outside them (an admin
deleting it directly, `user.delete()` CASCADE-ing every `Follow` row the
deleted user was party to, a shell/migration bulk `.delete()`/`.update()`)
never re-runs that increment/decrement logic — so the stored counters can
silently drift from what `Follow` rows actually say. This task is a
detect-and-correct safety net for that gap, not the root-cause fix (the
real fix would be a `Follow` `post_save`/`post_delete` signal that
recomputes from real rows on every change — the same pattern
`post/models.py` already uses for its own denormalized counters).

```python
"""
user_profile/tasks.py

TASK 28 — followers_count/following_count drift reconciliation.

`followers_count`/`following_count` are kept in sync per-operation today
— whatever code path handles follow/accept/unfollow does its own
`F(...) + 1` / `- 1` right alongside the `Follow` row write, and that's
correct for every write that actually goes through those code paths.

The gap is writes that DON'T go through them:
  - an admin deleting a `Follow` row directly in /admin/
  - `user.delete()` CASCADE-deleting every `Follow` row the deleted user
    was party to (as follower AND as following) — nothing re-runs the
    counter update on the *other* side of each of those rows
  - a data migration, a bulk `.delete()`/`.update()` run from a shell,
    a one-off fixup script
None of those fire the same increment/decrement logic the API views use,
so the stored counters can silently drift from what `Follow` rows
actually say.

This task is a detect-and-correct safety net, not the root-cause fix —
the actual fix would be a `Follow` `post_save`/`post_delete` signal that
recomputes from real rows on every change (the same pattern
`post/models.py` already uses for `update_shares_count`/
`update_saves_count`/`update_story_views_count` — see that file), which
makes drift structurally impossible instead of periodically corrected.
That's a bigger change to `user_profile/models.py` than this task asked
for; this reconciliation job is the "note it, don't block on it" version,
matching the same trade-off this codebase already made for
`post.views.TrendingHashtagsAPIView` (bounded recompute now, revisit if
scale demands it later).

⚠️ ASSUMPTION — `user_profile/models.py` wasn't provided when this was
written. Field names below are taken from how `Follow` is already used
elsewhere in this codebase (`post/views.py`'s `HomeFeedView`/
`ExploreFeedAPIView`: `Follow.objects.filter(follower=..., status=
Follow.Status.ACCEPTED).values_list('following_id', ...)`), so
`follower`/`following`/`status`/`Follow.Status.ACCEPTED` are already
confirmed real. If your `User` model's counter fields aren't literally
named `followers_count`/`following_count`, adjust the two `hasattr`
checks and the `bulk_update` field list below — everything else stays
the same.

Wire into settings.py CELERY_BEAT_SCHEDULE (already added):

    CELERY_BEAT_SCHEDULE = {
        ...
        "user-profile-reconcile-follow-counts": {
            "task": "user_profile.tasks.reconcile_follow_counts",
            "schedule": crontab(hour="*/6", minute=15),
        },
    }
"""
import logging

from celery import shared_task
from django.db.models import Count

logger = logging.getLogger(__name__)

# Caps how many (user, new_count) pairs sit in memory / go into a single
# bulk_update() UPDATE at once. Keeps this task's memory and per-query
# cost bounded on an app with a large user table, rather than building
# one unbounded list for the whole run.
_BULK_UPDATE_BATCH_SIZE = 500


@shared_task
def reconcile_follow_counts():
    """Recompute every user's followers_count/following_count directly
    from `Follow` rows (status=ACCEPTED only — matches how "following" is
    defined everywhere else in this codebase) and correct any drift
    found. Only writes rows that are actually wrong; a run where nothing
    drifted issues zero UPDATEs beyond the two read queries below.

    Returns a summary dict so a manual `.delay()` call, a Flower
    dashboard, or an admin action can see whether drift is actually
    happening in practice. If this keeps finding real correction work on
    every scheduled run, that's a signal the root-cause fix (a `Follow`
    post_delete signal — see module docstring) is overdue, not that this
    task itself is misbehaving.
    """
    from django.contrib.auth import get_user_model

    from .models import Follow

    User = get_user_model()

    if not (hasattr(User, "followers_count") and hasattr(User, "following_count")):
        logger.warning(
            "reconcile_follow_counts: User model has no followers_count/"
            "following_count fields — nothing to reconcile."
        )
        return {"skipped": True}

    # Two aggregate GROUP BY queries cover every user's correct count in
    # one shot each — this is what keeps a 6-hourly run cheap regardless
    # of user count, instead of one query per user.
    correct_followers_by_user = dict(
        Follow.objects.filter(status=Follow.Status.ACCEPTED)
        .values("following_id")
        .annotate(c=Count("id"))
        .values_list("following_id", "c")
    )
    correct_following_by_user = dict(
        Follow.objects.filter(status=Follow.Status.ACCEPTED)
        .values("follower_id")
        .annotate(c=Count("id"))
        .values_list("follower_id", "c")
    )

    to_update = []
    checked = 0
    corrected_followers = 0
    corrected_following = 0

    # Walk every user, not just IDs that appear in the two maps above — a
    # user whose real accepted-follow count just dropped to zero (every
    # Follow row touching them got deleted) won't appear in either map at
    # all, but their stored counter could still be sitting on a stale
    # nonzero value. Checking map keys only would miss exactly that
    # direction of drift.
    queryset = User.objects.only("id", "followers_count", "following_count").iterator(chunk_size=1000)
    for user in queryset:
        checked += 1
        true_followers = correct_followers_by_user.get(user.id, 0)
        true_following = correct_following_by_user.get(user.id, 0)

        needs_followers_fix = user.followers_count != true_followers
        needs_following_fix = user.following_count != true_following
        if not (needs_followers_fix or needs_following_fix):
            continue

        if needs_followers_fix:
            corrected_followers += 1
        if needs_following_fix:
            corrected_following += 1

        user.followers_count = true_followers
        user.following_count = true_following
        to_update.append(user)

        if len(to_update) >= _BULK_UPDATE_BATCH_SIZE:
            User.objects.bulk_update(to_update, ["followers_count", "following_count"])
            to_update = []

    if to_update:
        User.objects.bulk_update(to_update, ["followers_count", "following_count"])

    total_corrected = corrected_followers + corrected_following
    if total_corrected:
        logger.warning(
            "reconcile_follow_counts: checked %s users, corrected "
            "followers_count on %s and following_count on %s — drift "
            "detected (expected occasionally from admin deletes/cascades; "
            "if this stays high on every run, the real fix is a Follow "
            "post_delete signal, not just this reconciliation).",
            checked, corrected_followers, corrected_following,
        )
    else:
        logger.info("reconcile_follow_counts: checked %s users, no drift found.", checked)

    return {
        "checked": checked,
        "corrected_followers_count": corrected_followers,
        "corrected_following_count": corrected_following,
    }
```

### Task notes
- **Cheap regardless of user count.** Two `GROUP BY` aggregate queries
  compute every user's *correct* counts in one shot each; the task then
  walks `User.objects.only(...).iterator(chunk_size=1000)` and only
  issues `bulk_update()` calls (batched at 500 rows) for users whose
  stored counters actually disagree — a run with zero drift costs
  exactly 2 queries beyond the read, not one query per user.
- **Walks every user, not just users in the two aggregate maps** — a
  user whose real accepted-follow count just dropped to zero (every
  `Follow` row touching them got deleted) won't appear in either map at
  all, but their stored counter could still be sitting on a stale
  nonzero value; checking map keys only would miss exactly that
  direction of drift.
- **⚠️ Field-name assumption**, called out in the module docstring: this
  was written without `user_profile/models.py` available at the time,
  so `followers_count`/`following_count` field names were inferred from
  how `post/views.py` already uses `Follow` elsewhere in the codebase.
  Now that `models.py` is available (§4), those names are confirmed
  correct — no adjustment needed, but if your actual `User` model uses
  different field names, adjust the two `hasattr` checks and the
  `bulk_update` field list.
- **Wire into `settings.py`:**
  ```python
  CELERY_BEAT_SCHEDULE = {
      ...
      "user-profile-reconcile-follow-counts": {
          "task": "user_profile.tasks.reconcile_follow_counts",
          "schedule": crontab(hour="*/6", minute=15),
      },
  }
  ```
- Returns a summary dict (`checked`, `corrected_followers_count`,
  `corrected_following_count`) so a manual `.delay()` call, a Flower
  dashboard, or an admin action can see whether drift is actually
  happening in practice. If a run keeps finding real correction work
  every time, that's a signal the root-cause signal-based fix is
  overdue — not that this task is misbehaving.

---

## 9. `tests.py` (full current code)

```python
# user_profile/tests.py
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import BlockUser, Follow, RestrictUser

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


# ==========================================================================
# TASK 30 — additional coverage.
#
# Written against the real `user_profile/views.py`, `urls.py`, and
# `serializers.py` (all three were uploaded for this pass) — nothing
# below is a guessed url name or an assumed response shape. Confirmed
# endpoints used:
#   FollowAPIView          POST /follow/<user_id>/            "follow-user"
#   AcceptFollowRequestView POST /accept-request/<follow_id>/ "accept-request"
#   RejectFollowRequestView POST /reject-request/<follow_id>/ "reject-request"
#   BlockedUsersView       GET/POST /blocked-users/           "blocked-users"
#   UnblockUserView        DELETE /blocked-users/<id>/        "unblock-user"
#   RestrictedUsersView    GET/POST /restricted-users/        "restricted-users"
#   UnrestrictUserView     DELETE /restricted-users/<id>/     "unrestrict-user"
#   UserSearchView         GET /search/?search=<query>        "user-search"
# ==========================================================================

class PrivateAccountFollowRequestFlowTests(APITestCase):
    """
    End-to-end: following a PRIVATE account creates a PENDING request (not
    an immediate follow), counts stay untouched until accepted, the
    target's profile stays in "restricted view" for the requester the
    whole time it's pending, and accepting flips everything over —
    status, counts, and the profile view — in one go.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345", is_private=True)
        self.client.force_authenticate(user=self.alice)

    def test_following_private_account_creates_pending_request(self):
        url = reverse("follow-user", args=[self.bob.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Follow.Status.PENDING)

        follow = Follow.objects.get(follower=self.alice, following=self.bob)
        self.assertEqual(follow.status, Follow.Status.PENDING)

    def test_pending_follow_request_does_not_increment_counts(self):
        url = reverse("follow-user", args=[self.bob.id])
        self.client.post(url)

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_profile_stays_restricted_view_while_pending(self):
        self.client.post(reverse("follow-user", args=[self.bob.id]))

        detail_url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_restricted_view"])
        self.assertEqual(response.data["my_follow_status"], Follow.Status.PENDING)
        self.assertNotIn("bio", response.data["data"])

    def test_accepting_follow_request_updates_status_counts_and_profile_view(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )

        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse("accept-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        follow.refresh_from_db()
        self.assertEqual(follow.status, Follow.Status.ACCEPTED)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)

        # Now that alice is an accepted follower, bob's private profile
        # should stop being a restricted view for her.
        self.client.force_authenticate(user=self.alice)
        detail_url = reverse("user-profile-detail", args=[self.bob.username])
        response = self.client.get(detail_url)
        self.assertFalse(response.data["is_restricted_view"])

    def test_only_the_target_can_accept_a_follow_request(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )
        # alice (the requester, not the target) tries to accept her own request
        response = self.client.post(reverse("accept-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_rejecting_follow_request_deletes_it(self):
        follow = Follow.objects.create(
            follower=self.alice, following=self.bob, status=Follow.Status.PENDING
        )
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse("reject-request", args=[follow.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Follow.objects.filter(id=follow.id).exists())


class BlockUnblockEdgeCaseTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_cannot_block_yourself_via_api(self):
        response = self.client.post(reverse("blocked-users"), {"blocked": self.alice.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_block_yourself_at_db_level(self):
        with self.assertRaises(IntegrityError):
            BlockUser.objects.create(blocker=self.alice, blocked=self.alice)

    def test_blocking_twice_via_api_is_idempotent(self):
        url = reverse("blocked-users")
        first = self.client.post(url, {"blocked": self.bob.id})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(url, {"blocked": self.bob.id})
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["message"], "User already blocked.")
        self.assertEqual(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).count(), 1)

    def test_blocking_removes_existing_follow_relationship_both_ways(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        Follow.objects.create(follower=self.bob, following=self.alice)
        self.alice.following_count = 1
        self.alice.followers_count = 1
        self.alice.save(update_fields=["following_count", "followers_count"])
        self.bob.following_count = 1
        self.bob.followers_count = 1
        self.bob.save(update_fields=["following_count", "followers_count"])

        response = self.client.post(reverse("blocked-users"), {"blocked": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertFalse(Follow.objects.filter(follower=self.alice, following=self.bob).exists())
        self.assertFalse(Follow.objects.filter(follower=self.bob, following=self.alice).exists())

        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 0)
        self.assertEqual(self.alice.followers_count, 0)
        self.assertEqual(self.bob.following_count, 0)
        self.assertEqual(self.bob.followers_count, 0)

    def test_unblock_by_block_record_id(self):
        block = BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.delete(reverse("unblock-user", args=[block.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(BlockUser.objects.filter(id=block.id).exists())

    def test_unblock_by_target_user_id(self):
        # UnblockUserView accepts either the BlockUser row's own id, or the
        # blocked USER's id directly — chat screens only know the latter.
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.delete(reverse("unblock-user", args=[self.bob.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(BlockUser.objects.filter(blocker=self.alice, blocked=self.bob).exists())

    def test_unblocking_a_user_who_was_never_blocked_is_a_clean_404(self):
        response = self.client.delete(reverse("unblock-user", args=[self.bob.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_unblock_someone_elses_block(self):
        # bob blocked alice — alice must not be able to delete bob's block
        # record just by knowing/guessing its id.
        block = BlockUser.objects.create(blocker=self.bob, blocked=self.alice)
        response = self.client.delete(reverse("unblock-user", args=[block.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(BlockUser.objects.filter(id=block.id).exists())

    def test_restricting_an_already_blocked_user_is_rejected(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        response = self.client.post(reverse("restricted-users"), {"restricted": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RestrictUser.objects.filter(user=self.alice, restricted=self.bob).exists())


class RestrictUserModelTests(APITestCase):
    """
    RestrictUser has the same self-relation DB guard as Follow/BlockUser
    (see models.py Task 18 notes) but had no test coverage at all.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_self_restrict_blocked_at_db_level(self):
        with self.assertRaises(IntegrityError):
            RestrictUser.objects.create(user=self.alice, restricted=self.alice)

    def test_duplicate_restrict_blocked_at_db_level(self):
        RestrictUser.objects.create(user=self.alice, restricted=self.bob)
        with self.assertRaises(IntegrityError):
            RestrictUser.objects.create(user=self.alice, restricted=self.bob)

    def test_restrict_does_not_touch_follow_or_counts(self):
        # Unlike block, restrict must leave Follow/counts completely alone
        # (see RestrictUser's docstring in models.py — silent, non-blocking).
        Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.ACCEPTED)
        self.alice.following_count = 1
        self.alice.save(update_fields=["following_count"])
        self.bob.followers_count = 1
        self.bob.save(update_fields=["followers_count"])

        self.client.force_authenticate(user=self.alice)
        response = self.client.post(reverse("restricted-users"), {"restricted": self.bob.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(Follow.objects.filter(follower=self.alice, following=self.bob).exists())
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.following_count, 1)
        self.assertEqual(self.bob.followers_count, 1)


class UserSearchExclusionTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob_smith", password="pass12345")
        self.carol = User.objects.create_user(username="bob_jones", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def _search(self, query):
        response = self.client.get(reverse("user-search"), {"search": query})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # `data` is either a plain list or a paginated dict with `results`,
        # depending on the project's default pagination setting — handle
        # both instead of assuming one.
        results = response.data["data"]
        if isinstance(results, dict) and "results" in results:
            results = results["results"]
        return [u["username"] for u in results]

    def test_search_excludes_self(self):
        usernames = self._search("alice")
        self.assertNotIn(self.alice.username, usernames)

    def test_search_excludes_users_i_blocked(self):
        BlockUser.objects.create(blocker=self.alice, blocked=self.bob)
        usernames = self._search("bob")
        self.assertNotIn(self.bob.username, usernames)
        # carol ("bob_jones") isn't blocked by/from alice, so she should
        # still show up for a "bob" search.
        self.assertIn(self.carol.username, usernames)

    def test_search_excludes_users_who_blocked_me(self):
        BlockUser.objects.create(blocker=self.carol, blocked=self.alice)
        usernames = self._search("bob")
        self.assertNotIn(self.carol.username, usernames)
        self.assertIn(self.bob.username, usernames)


class FollowRaceConditionTests(APITestCase):
    """
    `test_self_follow_blocked_at_db_level` above already proves the DB
    constraint exists. This proves `FollowAPIView.post` actually catches
    that `IntegrityError` (its own comment names exactly this race: two
    concurrent requests both pass the `.filter().first()` "does this
    follow exist" check before either commits, then both call `.create()`)
    and returns a clean response instead of letting it bubble up as a 500.

    A genuine two-thread race is slow/flaky in a test suite, so this
    forces the identical failure deterministically by making `Follow.
    objects.create` raise `IntegrityError` on its first call, exactly as
    if a concurrent request had just won that race and committed first.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_concurrent_follow_does_not_500_and_reads_as_followed(self):
        url = reverse("follow-user", args=[self.bob.id])
        with mock.patch(
            "user_profile.models.Follow.objects.create",
            side_effect=IntegrityError('duplicate key value violates unique constraint "unique_follow"'),
        ):
            response = self.client.post(url)

        # The view's except-block re-queries for the row the "winning"
        # concurrent request must have just created. Here nothing actually
        # created it (only `.create` was mocked, not `.filter`), so it
        # correctly finds no row either — the important assertion is that
        # this reads as a clean 200, never an unhandled 500.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["status"])
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

### v3 (TASK 30) — the "still missing" list above is now covered
All five gaps from v2's "still missing" list were closed in this round,
plus `RestrictUser` coverage that didn't exist as a concept yet back
then:
- **`PrivateAccountFollowRequestFlowTests`** — private-account request
  flow end-to-end: following a private account creates a `PENDING`
  request (not an immediate follow); counts stay untouched while
  pending; the target's profile stays `is_restricted_view: true` for the
  requester the whole time it's pending; accepting flips status, counts,
  and the profile view together; only the target can accept; rejecting
  deletes the row outright with no counts touched.
- **`BlockUnblockEdgeCaseTests`** — self-block rejected at API and DB
  level; blocking twice is idempotent (`get_or_create`, second call
  returns 200 "already blocked" not a 400); blocking wipes an existing
  `Follow` relationship **both ways** and decrements both users'
  counters; unblock resolves by both the `BlockUser` record's own id and
  the target user's id; unblocking a user who was never blocked is a
  clean 404; you cannot unblock someone else's block record by guessing
  its id; **plus** restricting an already-blocked user is rejected (400)
  — covers `RestrictUserSerializer.validate_restricted`'s block check.
- **`RestrictUserModelTests`** — self-restrict rejected at DB level
  (`CheckConstraint`); duplicate restrict rejected at DB level
  (`UniqueConstraint`); restricting someone does **not** touch `Follow`
  rows or followers/following counts (the "silent, non-blocking by
  design" guarantee from `RestrictUser`'s docstring).
- **`UserSearchExclusionTests`** — search excludes the requesting user
  themself, users they've blocked, and users who've blocked them.
- **`FollowRaceConditionTests`** — thread-based: two near-simultaneous
  follow requests for the same (follower, following) pair never produce
  a 500; the loser reads back as a clean 200 with the follow already in
  the accepted/pending state the winner created, exercising the
  `try/except IntegrityError` path in `FollowAPIView.post` under actual
  concurrency instead of just asserting the code exists.
- Not yet covered: `mutual_friends` correctness under the batched
  `bulk_accepted_connection_ids` path, and any test that actually
  exercises `CoinLedger.objects.record_transaction()` (idempotent
  `reference` reuse, negative-balance rejection, the `select_for_update`
  concurrency guarantee) or the new restrict/coin-ledger endpoints'
  happy paths (`RestrictedUsersView`, `UnrestrictUserView`,
  `CoinLedgerListView`) — none of these have direct test coverage yet.

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

### 10.7 Restrict a user (`POST /profile/restricted-users/`) — v3, new
```
Validate target != self (serializer)
        │
        ▼
Validate target not already blocked (either direction) — if so, 400
("Ye user pehle se blocked hai — restrict ki zaroorat nahi.")
        │
        ▼
get_or_create(RestrictUser(user=me, restricted=target))
        │
   created?
   │        │
  yes        no (already restricted)
   │          │
   ▼          ▼
return 201                    return 200
("User restricted            ("User already restricted.")
 successfully.")
```
No `Follow` row or counter is touched — unlike blocking, restricting
must leave what either party can see or do completely unchanged. Once
restricted, `am_i_restricting: true` appears on the target's
`UserProfileDetailView` response for the restricting user only — the
restricted user never sees any signal that they've been restricted, on
this or any other endpoint.

### 10.8 Unrestrict (`DELETE /profile/restricted-users/<id>/`) — v3, new
Same `<id>`-flexibility pattern as unblock (§10.4): looks up a
`RestrictUser` row where `user=me` AND (`pk == id` OR
`restricted_id == id`).

### 10.9 Coin transactions (`CoinLedger.objects.record_transaction()`) — v3, new
```
record_transaction(user, transaction_type, amount, reference="", ...)
        │
        ▼
amount == 0? ──yes──▶ raise ValueError
        │no
        ▼
BEGIN atomic transaction, select_for_update() on user's row
        │
        ▼
reference given AND a CoinLedger(user, reference) row already
exists? ──yes──▶ return that EXISTING row as-is (idempotent — safe
        │         for a retried webhook/request)
        │no
        ▼
new_balance = user.coin + amount
new_balance < 0? ──yes──▶ raise ValueError (would go negative)
        │no
        ▼
User.objects.filter(pk=user.pk).update(coin=F("coin") + amount)
        │
        ▼
create CoinLedger row: amount, balance_after=new_balance,
transaction_type, reference, description, metadata
        │
        ▼
COMMIT — return the new CoinLedger row
```
This is **the only sanctioned way** to change `User.coin` in this
codebase — no view in this app calls `user.coin = ...; user.save()`
directly. `select_for_update()` on the user row means two concurrent
calls for the *same* user always serialize on that lock, so the
idempotency check ("does this reference already exist") can never race
against another call's write. `GET /profile/coin-ledger/`
(`CoinLedgerListView`) is the only consumer-facing endpoint — it's
read-only; nothing in this app exposes a way to call
`record_transaction()` over the API directly (that's intentional — the
actions that should trigger it, like a completed purchase or a sent
gift, live in other apps not covered by this upload).

---

## 11. Known Issues / Things To Double-Check

Items 1–4 below are **resolved as of v3** (kept here, struck through, so
the history is traceable) — see §0.1 for what changed. Item 2's admin
caveat is further **resolved as of this pass (B-8)** — see below.
Items 5–7 are still open.

1. ~~`RestrictUser` model is unused~~ — **resolved in v3**: wired via
   `RestrictedUsersView` / `UnrestrictUserView` / `RestrictUserSerializer`
   (see §0.1 item 1, §10.7–10.8).
2. ~~`CoinLedger` model is unused in the API~~ — **resolved in v3**:
   written via `CoinLedger.objects.record_transaction()`, read via
   `CoinLedgerListView` (see §0.1 item 2, §10.9). `coin` field on `User`
   (used by `UserProfileSerializer`) is still a separate field — see §4.
   ~~**New caveat introduced by this fix:** Django admin still allows raw
   add/edit/delete on `CoinLedger` directly, which bypasses
   `record_transaction()` entirely.~~ — **resolved (B-8):**
   `CoinLedgerAdmin` now sets `has_add_permission`/`has_change_permission`/
   `has_delete_permission` to `False` and lists every field in
   `readonly_fields` as well, so admin is a viewer only — it can no
   longer create a ledger row with no matching `User.coin` change, or
   edit an existing row's `amount` without touching the balance it
   supposedly explains (see §8). This landed alongside **FEE-2**, which
   started routing real tuition-fee payments through `CoinLedger`
   too — see §4's `CoinLedger` docstring — and **F-3**, which added a
   `CAMPUS_REWARD` transaction type to keep those fee payments visually
   distinct from small in-app coin bonuses on the same ledger.
3. ~~`FollowSerializer` is unused~~ — **resolved in v3**: removed
   entirely (was dead code).
4. ~~Counter drift risk still exists~~ — **resolved in v3**: the
   `tasks.py` Celery task `reconcile_follow_counts` periodically detects
   and corrects `followers_count`/`following_count` drift (see §0.1 item
   3, §8a). Note this is a **detect-and-correct safety net**, not a
   structural fix — the root cause (writes outside this app's own views
   never firing the increment/decrement logic) still exists; a
   `Follow` `post_save`/`post_delete` signal would make drift
   structurally impossible instead of periodically corrected. Also
   requires Celery + Celery Beat to actually be running for the
   scheduled task to fire — see updated §12 checklist.
5. **`CoinLedger` migration risk** — two separate things to check before
   running `makemigrations`: (a) if a migration already exists against
   the old `coins` model name, Django will try to rename/recreate the
   table — write a `db_table`-preserving migration by hand, or pin
   `Meta.db_table = "user_profile_coins"` first; (b) if a migration
   already exists against the *older* `credit`/`debit` field shape,
   write a real migration (RemoveField credit,debit + AddField amount,
   transaction_type,reference,balance_after,description,metadata) rather
   than letting `makemigrations` guess. Per the original comment
   ("nothing writes to this yet" — no longer true as of v3, but true at
   the time the shape was redesigned) there should be zero rows to
   migrate either way (see §4).
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
8. **Restrict's effects are still not consumed anywhere.** `is_restricted_
   between()` exists and `am_i_restricting` is exposed on the profile
   response, but no app in this upload (posts, message, notifications)
   actually filters through it yet — restricting someone today only
   records the relationship, it doesn't yet hide their comments, mute
   their notifications, or suppress read-receipts/online-status. That
   integration is out of scope for `user_profile` itself.
9. **No test coverage yet for `record_transaction()` or the new v3
   endpoints** (`RestrictedUsersView`, `UnrestrictUserView`,
   `CoinLedgerListView`) — see §9's "not yet covered" note. **v3.1
   adds two more untested gaps to this same list:** no test asserts that
   `CoinLedgerAdmin` actually refuses add/change/delete (B-8), and no
   test exercises `record_transaction()` with `transaction_type=
   CoinLedger.TransactionType.CAMPUS_REWARD` (F-3). `tests.py` itself
   didn't change in this pass.

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
- [ ] **v3:** Celery + a result/broker backend already configured for the
      project (`tasks.py`'s `reconcile_follow_counts` needs `@shared_task`
      to actually run somewhere).
- [ ] **v3:** `CELERY_BEAT_SCHEDULE` in `settings.py` includes the
      `user-profile-reconcile-follow-counts` entry (see §8a) and Celery
      Beat is running, or the reconciliation task will simply never fire
      on its own.
- [x] **B-8:** `CoinLedgerAdmin` is already locked down read-only in
      code (`readonly_fields` + `has_add_permission`/
      `has_change_permission`/`has_delete_permission` all `False` —
      see §8, §11 item 2). Nothing left to configure here; just don't
      remove it — it's what keeps admin from becoming a second,
      unguarded write path around `record_transaction()`, which matters
      more now that FEE-2 has put real tuition-fee money through the
      same ledger.

With the above satisfied, everything in this single document — models,
serializers, views, urls, admin, tasks, tests — is enough to run the
full `user_profile` app end to end.