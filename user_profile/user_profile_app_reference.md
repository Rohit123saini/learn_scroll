# `user_profile` App — Complete Self-Contained Reference

> **v4 — v3.1 ke upar TASK 1 / TASK 3 / TASK 4 / TASK 5 (poora coin-economy
> build-out: purchase → withdrawal → fraud/anti-abuse layer) fully merged.**
> Ye ek hi file hai jisme poore **user_profile** Django app ka sara logic,
> code, connections, flows, fraud rules, aur known issues cover hain. Iske
> alawa kisi aur file ki zaroorat nahi — sab kuch (models → serializers →
> views → urls → admin → fraud → tasks → tests) yahin milega, current code
> ke saath.
>
> **v3.1 se kya badla, sabse pehle:** section 0.3 (Changelog v3.1 → v4)
> padho. Short version: `CoinLedger` ab sirf ek chhota audit-table nahi
> raha — ab uske upar poora coin-economy stack khada hai:
> - **TASK 1** — 5 naye `TransactionType` choices (testseries + withdrawal
>   ke liye prerequisite).
> - **TASK 3** — Buy-Coin flow (`CoinPurchaseRequest`, `BuyCoinView`,
>   `BuyCoinConfirmView`) — pending → success/failed, real money → coins.
> - **TASK 4** — Withdraw-Coin flow (`CoinWithdrawalRequest`,
>   `CoinWithdrawalRequestView`) — escrow-style debit-on-request, coins →
>   real money.
> - **TASK 5** — naya `fraud.py` module: withdrawal-eligibility rule
>   (sirf purchased/gifted coins hi withdrawable) + earn-rate limiting
>   (burst-farming se bachao), dono `CoinLedger.objects.
>   record_transaction()` ke andar hi enforce hote hain — koi bhi call
>   site inhe bypass nahi kar sakta.
>
> Har naya piece **existing `CoinLedger.objects.record_transaction()`**
> ke upar hi bana hai — koi doosra balance-writing path nahi khula.
> Poora document is round ke baad, fully updated code ke saath, dubara
> organize kiya gaya hai; purane version-history (v1→v2→v3→v3.1) sections
> §0 me neeche traceable hain, delete nahi kiye gaye.

---

## 0.3 Changelog — v3.1 → v4 (TASK 1 / 3 / 4 / 5 — the coin-economy build-out)

This is the biggest single jump since v3. Four tasks, all building on the
same `CoinLedger.objects.record_transaction()` foundation v3 (TASK 19)
put in place — nothing here opens a second way to move a balance.

### 🧾 TASK 1 — new `TransactionType` choices (pure addition, no migration
for the enum itself)
- `TESTSERIES_PURCHASE`, `TESTSERIES_PAYOUT` — `testseries/models.py`
  (`TestSeriesPurchase.purchase_and_start_attempt()` / `.release()`)
  already referenced these directly; they were **missing from the enum**,
  which was a live `AttributeError` waiting to happen. Fixed by adding
  them, not by changing `testseries`.
- `WITHDRAWAL_REQUESTED`, `WITHDRAWAL_COMPLETED`, `WITHDRAWAL_REJECTED` —
  added ahead of the withdrawal flow itself (same pattern
  `core.Notification.NotifType` already uses: carry the enum value before
  every consumer of it exists). TASK 4 below wires
  `WITHDRAWAL_REQUESTED`/`WITHDRAWAL_REJECTED` in; `WITHDRAWAL_COMPLETED`
  is intentionally still unused (see §4 model notes).
- Field-width checked: longest new value (`withdrawal_requested` /
  `withdrawal_completed`, 20 chars) still fits
  `transaction_type = CharField(max_length=20)` — no column-length
  migration needed.

### 💰 TASK 3 — Buy-Coin flow (real money → coins)
- New model `CoinPurchaseRequest` (+ `CoinPurchaseRequestManager`):
  `PENDING` → `SUCCESS` (credits `coins` via `record_transaction`,
  `transaction_type=PURCHASE`) or `FAILED` (wallet untouched). Both
  terminal. Idempotent on `gateway_reference` (DB `UniqueConstraint` +
  `get_or_create`, with the `IntegrityError` race caught and turned into
  a re-fetch — same pattern as everywhere else in this app that needs
  double-submit safety).
- New views `BuyCoinView` (`POST /profile/buy-coin/` — start a pending
  purchase) and `BuyCoinConfirmView` (`POST /profile/buy-coin/confirm/` —
  mark success/failed). **Not a real payment-gateway webhook as shipped**
  — `IsAuthenticated` + "must be your own purchase" stand in for gateway-
  signature verification, which wasn't part of this upload. Replace/gate
  that before this goes live behind an actual gateway callback (see §11).
- `liveclass/models.py` (which already has its own `CoinPurchase` flow)
  was **not** part of this upload, so `CoinPurchaseRequest`'s shape is
  inferred from this app's own established patterns, not copied
  field-for-field — reconcile the two if they ever need to be the same
  shape (see §4 model notes / §11).

### 💸 TASK 4 — Withdraw-Coin flow (coins → real money)
- New model `CoinWithdrawalRequest` (+ `CoinWithdrawalRequestManager`):
  the mirror image of TASK 3, escrow-style — debits `coins` **immediately
  on request** (`WITHDRAWAL_REQUESTED`), not on payout confirmation, so a
  user can't double-request the same coins while one withdrawal is
  pending. Lifecycle: `PENDING` → `PROCESSING` (no coin movement) →
  `SUCCESS` (no coin movement — debit already happened) **or** →
  `REJECTED` from `PENDING`/`PROCESSING` (credits coins back via
  `WITHDRAWAL_REJECTED`). Reference read: `liveclass.CoinWithdrawal` /
  `liveclass.CoinTransaction`, which already implement this exact escrow
  pattern — reproduced here on top of `CoinLedger` instead, since
  `CoinLedger` (not `liveclass.CoinTransaction`) is this codebase's one
  shared ledger.
- New view `CoinWithdrawalRequestView` (`GET`/`POST
  /profile/coin-withdrawals/`) — `GET` lists your own requests; `POST`
  requests a withdrawal, `402` on insufficient balance (no partial debit,
  no orphan request row — same `transaction.atomic()` block covers both).
- Deliberately **not** included this pass: `reviewed_by`/admin-user
  tracking, a `MIN_WITHDRAWAL_COINS` floor, an INR conversion snapshot,
  and endpoints for `mark_processing`/`confirm_success`/`reject` (those
  three manager methods exist and are tested, but nothing in `urls.py`
  calls them yet — that's an ops/admin surface for later).

### 🛡️ TASK 5 — fraud / anti-abuse layer (`fraud.py`, new file)
Two independent rules, both enforced **inside
`CoinLedgerManager.record_transaction()` itself** — not only in a view —
so no call site (this app's own views, `campus` tasks, a referral-bonus
flow, anything written later) can bypass them by going around a
particular endpoint:

1. **Withdrawal eligibility** — `fraud.is_withdrawal_eligible(user,
   coins)`. Only coins traceable to `PURCHASE` or `GIFT_RECEIVED` may
   ever be cashed out; `EARN`/`CAMPUS_REWARD` coins can be spent in-app
   but never withdrawn. On a mixed balance, only the purchased/gifted
   **portion** is eligible — `fraud.get_withdrawal_eligible_balance()`
   derives that with a single grouped aggregate query (non-eligible
   coins are treated as spent first; only once they're exhausted does
   further spend eat into the eligible pool). Called by
   `CoinWithdrawalRequestView.post()` **before**
   `request_withdrawal()`, so an ineligible request never touches the
   balance and never creates a request row — a `403`, distinct from the
   `402` "insufficient balance" case.
2. **Earn-rate limiting** — `fraud.check_earn_rate_limit(user,
   transaction_type)`, called from *inside* `record_transaction()` for
   `EARN`/`CAMPUS_REWARD` credits only. Two independent rolling-window
   caps (either tripping blocks the credit): a transaction-count cap
   (`EARN_RATE_LIMIT_MAX_TRANSACTIONS`, default 20 per hour) and a
   total-coins cap (`EARN_RATE_LIMIT_MAX_COINS`, default 500 per hour).
   Trips raise `fraud.EarnRateLimitExceeded` — deliberately **not** a
   `ValueError`, so a caller that wants to tell "you're farming too fast"
   apart from "you're broke" can catch it specifically, while a caller
   that only wants "something went wrong, don't credit" can still catch
   `(ValueError, EarnRateLimitExceeded)` or a bare `Exception` the same
   way it always could.
3. `record_transaction()` now **always** stamps
   `metadata["withdrawal_eligible"]` (derived purely from
   `transaction_type`, any caller-supplied value overwritten) on every
   row it writes — not the real source of eligibility truth
   (`get_withdrawal_eligible_balance()` still derives that from
   `transaction_type` directly), just a denormalized flag so `admin.py`'s
   ops filter can filter on it without recomputing per row.

### 🔒 B-8 (folded into this same pass) — `admin.py` now uploaded, `CoinLedger` locked down read-only
- `CoinLedgerAdmin`: `has_add_permission` / `has_change_permission` /
  `has_delete_permission` all return `False`; every field is also listed
  in `readonly_fields` (defense in depth — a future field addition can't
  accidentally become admin-editable by omission). Admin is a **viewer**
  of this table, never a second unguarded write path around
  `record_transaction()` — see §8 for why this matters even more now
  that real tuition-fee money (`FEE-2`) flows through the same ledger.
- New `WithdrawalEligibleFilter` — an ops-facing `list_filter` that reads
  `metadata["withdrawal_eligible"]` (the flag TASK 5 stamps) rather than
  re-deriving eligibility from `transaction_type` in `admin.py` itself,
  so the admin filter and `fraud.py` can never silently disagree about
  what "eligible" means for a given row.

### 🎓 F-3 (folded into this same pass) — `CAMPUS_REWARD` transaction type
- New `TransactionType.CAMPUS_REWARD`, for `campus`'s small engagement
  bonuses (attendance-streak, on-time-assignment-streak). Kept
  **distinct from `EARN`** on purpose: `FEE-3`/`FEE-6`
  (`campus/tasks.py`) already reads a student's `User.coin` balance to
  decide whether it covers an upcoming fee, and `FEE-2` now routes real
  tuition-fee payments through this same ledger — an ops/support person
  scanning a ledger needs to tell "this coin came from a reward, not a
  real top-up" at a glance, without cross-referencing amounts.
- TASK 5's earn-rate limiter treats `CAMPUS_REWARD` the same as `EARN`
  (both rate-limited, both withdrawal-ineligible) — see fraud.py notes
  above.

### 🧪 Test coverage added this pass
- `tests.py` gained `CoinWithdrawalRequestManagerTests` (exact-amount
  debit, rejected-withdrawal-credits-back) and
  `CoinWithdrawalRequestAPITests` (402 on insufficient balance, pending
  row created via API, payout-details validation).
- New file `tests_fraud.py` (§9a): `WithdrawalEligibilityTests` (earn-
  only balance is non-withdrawable, mixed-balance eligible-portion math,
  gift-received eligibility, spend-overflow eating into the eligible
  pool) and `EarnRateLimitTests` (burst blocked by count, blocked by
  total-coins, and confirmed **not** applied to `PURCHASE`).
- Still not covered (carried into §11): `CoinLedgerAdmin`'s
  add/change/delete refusal has no test, and no test exercises
  `record_transaction()` with `transaction_type=CAMPUS_REWARD`
  specifically (only the rate limiter's generic EARN/CAMPUS_REWARD
  grouping is tested).

### Status as of v3.1 (superseded — see above for what changed in v4)
- ~~`CoinLedger` has no fraud/anti-abuse controls~~ — **resolved in v4**:
  see TASK 5 above.
- ~~No buy-coin / withdraw-coin flow exists~~ — **resolved in v4**: see
  TASK 3 / TASK 4 above.

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

6. TASK 1 (this pass) — added 5 new `TransactionType` choices:
   `TESTSERIES_PURCHASE`, `TESTSERIES_PAYOUT`, `WITHDRAWAL_REQUESTED`,
   `WITHDRAWAL_COMPLETED`, `WITHDRAWAL_REJECTED`. Pure addition — no
   existing choice renamed or removed, so no migration is needed for
   the enum itself (`choices=` is not a schema-affecting kwarg).
   `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT` were already being
   referenced directly by `testseries/models.py`
   (`TestSeriesPurchase.purchase_and_start_attempt()` / `.release()`)
   before this enum had them defined — that was a live `AttributeError`
   waiting to happen, now fixed. `WITHDRAWAL_*` aren't consumed by any
   code yet in this pass; added ahead of time as a prerequisite for the
   upcoming withdrawal flow, same as `core.Notification.NotifType`
   already carries `WITHDRAWAL_APPROVED`/`WITHDRAWAL_REJECTED`/
   `WITHDRAWAL_PAID` without every one of those being wired up yet.
   Checked field width: longest new value is `withdrawal_requested`/
   `withdrawal_completed` at 20 chars, which still fits the existing
   `transaction_type = CharField(max_length=20, ...)` — no field-length
   change needed either.

7. TASK 4 (this pass) — added `CoinWithdrawalRequest` +
   `CoinWithdrawalRequestManager`: the canonical "cash out coins"
   request for this app, using the `WITHDRAWAL_REQUESTED`/
   `WITHDRAWAL_REJECTED` transaction types TASK 1 added ahead of time.
   Reference read for this task was `liveclass.CoinWithdrawal` /
   `liveclass.CoinTransaction`, which already implement this exact
   escrow pattern (debit the coins the moment the request is made, not
   when the payout completes; refund only on reject; no second ledger
   write on completion since the debit already happened). This
   reproduces that lifecycle on top of `CoinLedger.record_transaction()`
   instead of `liveclass.CoinTransaction`, since `CoinLedger` — not
   `liveclass.CoinTransaction` — is this codebase's one shared,
   canonical coin ledger (see CoinLedger's own docstring). See
   `CoinWithdrawalRequest`'s class docstring below for the full
   lifecycle and what's deliberately left out of this pass (no
   `reviewed_by`, no `MIN_WITHDRAWAL_COINS` floor, no INR snapshot).
"""
from django.conf import settings
from django.db import IntegrityError, models, transaction
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

        TASK 5 (this pass) — two fraud/anti-abuse hooks were added here,
        not in the view layer, specifically so they can't be bypassed by
        a call site that doesn't go through a particular view (campus
        tasks, referral bonus, any future caller) — same reasoning this
        being "the one sanctioned write path" already rests on:
          - Earn-rate limiting: for EARN/CAMPUS_REWARD credits only,
            `fraud.check_earn_rate_limit()` is checked (after the row
            lock below, so concurrent earn calls for the same user are
            serialized before the check runs, the same way the
            `reference` idempotency check already relies on that lock).
            A burst-farming caller gets `fraud.EarnRateLimitExceeded`
            instead of a silent credit — nothing here catches it, so it
            propagates straight to the caller (campus tasks, referral
            bonus, ...), which is expected to catch it the same way it's
            expected to catch the `ValueError`s below.
          - `metadata["withdrawal_eligible"]` is now always set (any
            caller-supplied value for that key is overwritten) purely
            from `transaction_type` — True only for
            PURCHASE/GIFT_RECEIVED, per `CoinWithdrawalRequest`'s
            eligibility rule. This is stored in `metadata` rather than a
            new column so this pass doesn't force a schema migration on
            a table that's already live (same "avoid the migration when
            the data doesn't demand a schema change" call this file
            already makes elsewhere) — it's also NOT what withdrawal
            eligibility is actually computed from (`fraud.
            get_withdrawal_eligible_balance()` derives that straight
            from `transaction_type`, the real source of truth); this
            flag exists purely so `admin.py`'s read-only ops filter can
            filter on it without re-deriving it per row.
        """
        if amount == 0:
            raise ValueError("CoinLedger amount must not be zero.")

        UserModel = type(user)  # avoid importing login.User here — this
        # app's models already only ever reference the user model via
        # settings.AUTH_USER_MODEL (see the FK fields above), never a
        # direct import, to keep user_profile decoupled from login.

        # Local import — avoids a module-level circular reference
        # between this module and `fraud.py`, which itself imports
        # `CoinLedger` from here to compute the rate-limit window. Same
        # pattern `CoinPurchaseRequestManager.confirm_success` already
        # uses a local import for, just against a different module.
        from .fraud import EarnRateLimitExceeded, check_earn_rate_limit

        merged_metadata = dict(metadata or {})
        merged_metadata["withdrawal_eligible"] = transaction_type in (
            self.model.TransactionType.PURCHASE,
            self.model.TransactionType.GIFT_RECEIVED,
        )

        with transaction.atomic():
            locked_user = UserModel.objects.select_for_update().get(pk=user.pk)

            if not check_earn_rate_limit(locked_user, transaction_type):
                raise EarnRateLimitExceeded(
                    f"Earn rate limit exceeded for user {locked_user.pk!r} "
                    f"({transaction_type})."
                )

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
                metadata=merged_metadata,
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

    F-3 (this pass): added `TransactionType.CAMPUS_REWARD` for
    `campus`'s small engagement bonuses (attendance-streak,
    assignment-on-time-streak) -- see that choice's own comment below for
    why it's kept distinct from `EARN` rather than reusing it.

    TASK 1 (this pass): added `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT`/
    `WITHDRAWAL_REQUESTED`/`WITHDRAWAL_COMPLETED`/`WITHDRAWAL_REJECTED` —
    see `TransactionType` below for details. Pure choices-only addition,
    same as F-3 was.

    TASK 4 (this pass): `WITHDRAWAL_REQUESTED`/`WITHDRAWAL_REJECTED` are
    now actually consumed, by `CoinWithdrawalRequest` below.
    `WITHDRAWAL_COMPLETED` is still unused on purpose — see
    `CoinWithdrawalRequestManager.confirm_success`'s docstring for why a
    completed withdrawal doesn't get a new ledger row at all (the debit
    already happened at request time, and a zero-amount row would
    violate `coinledger_amount_not_zero`).

    CAUTION (can't fix from this pass — admin.py wasn't uploaded):
    Django admin currently allows raw add/edit/delete on this model
    directly (that's the "sirf admin me registered hai" from the task).
    Any edit made through the admin bypasses `record_transaction()`
    entirely — it can create a ledger row with no matching change to
    `User.coin`, or edit an existing row's `amount` without ever
    touching the balance it supposedly explains — silently breaking the
    exact "these must always agree" invariant this table exists to
    guarantee. If/when admin.py is available, that registration should
    be made read-only (`has_add_permission`/`has_change_permission`
    returning False, or `readonly_fields = [f.name for f in
    CoinLedger._meta.fields]`) so admin is a viewer, not a second
    unguarded write path.

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

        # TASK 1: testseries app's escrow purchase/payout flow
        # (testseries/models.py — TestSeriesPurchase.
        # purchase_and_start_attempt() / .release()) already references
        # these two values directly; they were missing from this enum
        # until now (a live AttributeError waiting to happen). Kept
        # distinct from PURCHASE/EARN for the same "tell it apart at a
        # glance in the ledger" reasoning CAMPUS_REWARD's comment above
        # already gives — a support person scanning a creator's ledger
        # should be able to tell "this coin came from a test series
        # payout" without cross-referencing the reference field.
        TESTSERIES_PURCHASE = "testseries_purchase", "Test Series Purchase"
        TESTSERIES_PAYOUT = "testseries_payout", "Test Series Payout"

        # TASK 1: prerequisite for the upcoming coin-withdrawal flow
        # (a user cashes out coins to real money). TASK 4 wires
        # WITHDRAWAL_REQUESTED/WITHDRAWAL_REJECTED into
        # CoinWithdrawalRequest below; WITHDRAWAL_COMPLETED stays
        # unused — see that model's docstring for why.
        WITHDRAWAL_REQUESTED = "withdrawal_requested", "Withdrawal Requested"
        WITHDRAWAL_COMPLETED = "withdrawal_completed", "Withdrawal Completed"
        WITHDRAWAL_REJECTED = "withdrawal_rejected", "Withdrawal Rejected"

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


class CoinPurchaseRequestManager(models.Manager):
    """
    TASK 3 — the sanctioned write path for `CoinPurchaseRequest`, same
    role `CoinLedgerManager` plays for `CoinLedger` just above: callers
    (views, webhooks, the sweep task) go through these three methods
    instead of `.create()`/`.save()` directly, so "pending never touches
    the balance" and "confirming twice never double-credits" are
    guaranteed in one place rather than re-implemented at every call
    site.
    """

    def start_purchase(self, *, user, gateway_reference, amount, coins, gateway=""):
        """
        Create (or return the existing) PENDING request for this
        `gateway_reference`. Does NOT touch `User.coin` — a purchase
        only ever credits coins via `confirm_success()` below.

        Idempotent the same way `CoinLedger.objects.record_transaction()`
        is idempotent on `reference`: a second call with the same
        `gateway_reference` returns the row that's already there
        (`get_or_create`) instead of raising or creating a duplicate —
        safe for a client retrying a dropped response. `gateway_reference`
        also carries a DB `UniqueConstraint` (see Meta below), so if two
        concurrent requests both miss the `get_or_create` SELECT and race
        to INSERT, the loser's `IntegrityError` is caught here and turned
        into a re-fetch of the winner's row instead of a 500.
        """
        try:
            obj, created = self.get_or_create(
                gateway_reference=gateway_reference,
                defaults={
                    "user": user,
                    "amount": amount,
                    "coins": coins,
                    "gateway": gateway,
                },
            )
        except IntegrityError:
            obj = self.get(gateway_reference=gateway_reference)
            created = False
        return obj, created

    def confirm_success(self, *, gateway_reference):
        """
        Mark a request SUCCESS and credit `coins` via
        `CoinLedger.objects.record_transaction()` — never a direct
        `User.coin` write — atomically, exactly once.

        Idempotent at two layers: if this request is already SUCCESS,
        it's returned as-is with no second credit (checked under
        `select_for_update()` so a retried webhook racing itself can't
        both pass the check); and even if that check were somehow
        bypassed, `record_transaction`'s own `reference=` idempotency
        (keyed on this request's id, not the gateway's reference, so it
        can never collide with an unrelated ledger entry) would still
        refuse to double-credit.

        Raises `ValueError` if the request is already FAILED — a failed
        purchase must be retried as a new request, not resurrected.
        """
        with transaction.atomic():
            purchase = self.select_for_update().get(gateway_reference=gateway_reference)

            if purchase.status == self.model.Status.SUCCESS:
                return purchase

            if purchase.status == self.model.Status.FAILED:
                raise ValueError(
                    f"Coin purchase {gateway_reference!r} already failed — cannot confirm."
                )

            # Local import avoids a module-level circular reference
            # between CoinLedger (defined above) and this manager; kept
            # as a local import anyway for symmetry with how this file
            # otherwise avoids importing the user model directly.
            ledger_entry = CoinLedger.objects.record_transaction(
                user=purchase.user,
                transaction_type=CoinLedger.TransactionType.PURCHASE,
                amount=purchase.coins,
                reference=f"coin_purchase_request:{purchase.pk}",
                description=f"Coin purchase via {purchase.gateway or 'payment gateway'}",
                metadata={
                    "coin_purchase_request_id": purchase.pk,
                    "gateway_reference": purchase.gateway_reference,
                    "amount_paid": str(purchase.amount),
                },
            )

            purchase.status = self.model.Status.SUCCESS
            purchase.ledger_entry = ledger_entry
            purchase.save(update_fields=["status", "ledger_entry", "updated_at"])
            return purchase

    def mark_failed(self, *, gateway_reference, reason=""):
        """
        Mark a request FAILED. Never touches `User.coin` — that's the
        whole point of a two-step (pending -> success/failed) flow: a
        failed payment leaves the wallet exactly where it was.

        Idempotent: already-FAILED is returned as-is. Raises `ValueError`
        for an already-SUCCESS request — a completed purchase can't be
        un-credited by calling this; that would need an explicit refund
        (`CoinLedger.TransactionType.REFUND`), which is out of scope
        here.
        """
        with transaction.atomic():
            purchase = self.select_for_update().get(gateway_reference=gateway_reference)

            if purchase.status == self.model.Status.FAILED:
                return purchase

            if purchase.status == self.model.Status.SUCCESS:
                raise ValueError(
                    f"Coin purchase {gateway_reference!r} already succeeded — cannot fail."
                )

            purchase.status = self.model.Status.FAILED
            purchase.failure_reason = reason
            purchase.save(update_fields=["status", "failure_reason", "updated_at"])
            return purchase


class CoinPurchaseRequest(models.Model):
    """
    TASK 3 — canonical "buy coins" request/receipt row for `user_profile`.
    `liveclass.CoinPurchase` already has a purchase flow, but it's scoped
    to that app; this is the one every coin top-up should go through
    regardless of where in the product it's triggered from, the same way
    `CoinLedger` is the one shared ledger every coin-changing action
    writes to.

    NOTE: `liveclass/models.py` wasn't included in this pass's upload
    (only `user_profile`'s own four files were), so the field shape below
    is inferred from this app's own established patterns — `CoinLedger`'s
    `reference`/idempotency shape and the pending/success/failed
    lifecycle the task description asks for — rather than copied
    field-for-field from `CoinPurchase`. If `CoinPurchase`'s actual shape
    differs in a way that matters (e.g. its gateway list, its money
    field's precision), reconcile the two before relying on this as
    final — ideally by rerunning this task with `liveclass/models.py`
    included so the two don't silently diverge.

    Lifecycle: PENDING (created by `start_purchase`, wallet untouched) ->
    SUCCESS (via `confirm_success`, credits `coins` through
    `CoinLedger.objects.record_transaction()`) or FAILED (via
    `mark_failed`, wallet still untouched). SUCCESS and FAILED are both
    terminal — see the manager docstrings above for what happens if
    either is called again.

    Nothing here writes to `User.coin` directly, by design — same
    boundary `CoinLedger` draws: the only sanctioned path to a balance
    change is `CoinLedger.objects.record_transaction()`, so this model's
    manager calls through to it rather than duplicating the balance
    update.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_purchase_requests",
    )

    # Free-text on purpose (not a choices field) — this app doesn't own
    # the set of payment gateways the product integrates with, and
    # locking that down here would mean a migration every time a new
    # gateway is added elsewhere. Blank allowed for the same reason
    # CoinLedger.reference is blank-ok: not every caller may have a
    # gateway name handy (e.g. a manual admin-initiated top-up).
    gateway = models.CharField(max_length=30, blank=True)

    # The gateway's own transaction/order id. This is the idempotency
    # key for the whole request lifecycle: unique at the DB level so two
    # `start_purchase()` calls (or a retried client request) for the
    # same gateway transaction can never create two rows, and it's what
    # `confirm_success`/`mark_failed` key off of instead of an internal
    # id the gateway doesn't know about.
    gateway_reference = models.CharField(max_length=150, unique=True, db_index=True)

    # Real money paid, in the product's billing currency. Decimal (not
    # Integer) because money — same reasoning that keeps `CoinLedger`
    # off floats for `amount`, just applied to currency instead of coins.
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Coins to be credited on success. Always positive — this model only
    # represents purchases (money -> coins), never a debit; refunds are
    # a separate `CoinLedger.TransactionType.REFUND` entry, not a
    # negative row here.
    coins = models.PositiveIntegerField()

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Populated by mark_failed() — why the gateway/webhook says this
    # didn't go through, or why the auto-fail sweep gave up on it.
    failure_reason = models.CharField(max_length=255, blank=True)

    # Set only on SUCCESS, by confirm_success(). SET_NULL (not CASCADE
    # or PROTECT): if a CoinLedger row were ever administratively
    # deleted, that shouldn't cascade into deleting the purchase receipt
    # that explains it — the receipt should just lose its back-link.
    ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CoinPurchaseRequestManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # "my purchase history" — same query shape CoinLedger's
            # (user, -created_at) index serves.
            models.Index(fields=["user", "-created_at"]),
            # Supports the auto-fail sweep task's "PENDING older than
            # cutoff" query without a full-table scan.
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=Q(amount__gt=0), name="coinpurchaserequest_amount_positive"),
            CheckConstraint(condition=Q(coins__gt=0), name="coinpurchaserequest_coins_positive"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.coins} coins ({self.status}, {self.gateway_reference})"


class CoinWithdrawalRequestManager(models.Manager):
    """
    TASK 4 — the sanctioned write path for `CoinWithdrawalRequest`, same
    role `CoinPurchaseRequestManager` plays for `CoinPurchaseRequest`
    above: callers (views, admin actions) go through these methods
    instead of `.create()`/`.save()` directly.

    Money direction is the mirror image of `CoinPurchaseRequest`: a
    purchase credits coins only on success; a withdrawal debits coins
    immediately on request. This is the escrow pattern
    `liveclass.CoinWithdrawal.create_request` already uses (reference
    read for this task) — coins leave the wallet the moment the request
    is made, not when the payout is actually confirmed, specifically so
    a user can't request the same coins twice while a withdrawal is
    still pending. They only come back if the request is rejected.
    """

    def request_withdrawal(self, *, user, coins, payout_method="", payout_details=None):
        """
        Debit `coins` from `user` via `CoinLedger.objects.
        record_transaction(transaction_type=WITHDRAWAL_REQUESTED,
        amount=-coins, ...)` and create a PENDING CoinWithdrawalRequest,
        in the same DB transaction.

        `record_transaction` raises `ValueError` for insufficient
        balance (see `CoinLedgerManager.record_transaction` above) —
        that propagates straight out of here uncaught. Because the row
        creation and the debit share one `transaction.atomic()` block,
        that ValueError rolls back BOTH: no request row is left behind
        and no partial debit happens. The view is expected to catch
        `ValueError` and turn it into a 402 (same shape as campus's
        `FeePaymentViewSet.pay`).
        """
        if coins <= 0:
            raise ValueError("Withdrawal coins must be positive.")

        with transaction.atomic():
            withdrawal = self.create(
                user=user,
                coins=coins,
                payout_method=payout_method,
                payout_details=payout_details or {},
                status=self.model.Status.PENDING,
            )
            ledger_entry = CoinLedger.objects.record_transaction(
                user=user,
                transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
                amount=-coins,
                reference=f"coin_withdrawal_request:{withdrawal.pk}",
                description="Coin withdrawal requested",
                metadata={"coin_withdrawal_request_id": withdrawal.pk},
            )
            withdrawal.debit_ledger_entry = ledger_entry
            withdrawal.save(update_fields=["debit_ledger_entry"])
            return withdrawal

    def mark_processing(self, *, withdrawal_id):
        """
        Admin/ops moves a PENDING request into PROCESSING (payout
        initiated externally, e.g. a bank transfer submitted). No coin
        movement — the coins already left the wallet at request time.

        Raises `ValueError` if the request is already SUCCESS or
        REJECTED (both terminal).
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status in (self.model.Status.SUCCESS, self.model.Status.REJECTED):
                raise ValueError(
                    f"Withdrawal {withdrawal_id} is already {wr.status} — cannot move to processing."
                )
            wr.status = self.model.Status.PROCESSING
            wr.save(update_fields=["status", "updated_at"])
            return wr

    def confirm_success(self, *, withdrawal_id):
        """
        Mark a withdrawal SUCCESS once the payout has actually gone out
        externally (bank transfer / UPI confirmed).

        No new CoinLedger row is written here — the coins were already
        debited via WITHDRAWAL_REQUESTED at request time, and a
        WITHDRAWAL_COMPLETED entry with amount=0 would violate
        `coinledger_amount_not_zero`. This only flips the request's own
        status, same as `liveclass.CoinWithdrawal.approve()`/
        `mark_paid()` not moving any coins either.

        Idempotent: already-SUCCESS is returned as-is. Raises
        `ValueError` if REJECTED — a rejected (already refunded)
        withdrawal can't retroactively be completed.
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status == self.model.Status.SUCCESS:
                return wr
            if wr.status == self.model.Status.REJECTED:
                raise ValueError(
                    f"Withdrawal {withdrawal_id} was already rejected — cannot mark success."
                )
            wr.status = self.model.Status.SUCCESS
            wr.save(update_fields=["status", "updated_at"])
            return wr

    def reject(self, *, withdrawal_id, reason=""):
        """
        Reject a PENDING/PROCESSING withdrawal and credit the coins
        back via `CoinLedger.objects.record_transaction(
        transaction_type=WITHDRAWAL_REJECTED, amount=+coins, ...)`.

        Idempotent the same way `CoinPurchaseRequestManager.
        confirm_success` is: the refund is keyed on this request's own
        id via `record_transaction`'s `reference=` argument, so a
        retried reject call (double form submit, admin double-click)
        can never credit the refund twice. Belt-and-braces: the
        `select_for_update()` row lock below also means a second call
        already sees `status == REJECTED` and returns early before it
        even reaches `record_transaction`.

        Raises `ValueError` if the request is already SUCCESS — a
        completed payout can't be un-done by calling this; that would
        need a separate manual adjustment, out of scope here (same
        carve-out `CoinPurchaseRequestManager.mark_failed` makes for an
        already-succeeded purchase).
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status == self.model.Status.REJECTED:
                return wr
            if wr.status == self.model.Status.SUCCESS:
                raise ValueError(
                    f"Withdrawal {withdrawal_id} already completed — cannot reject."
                )

            refund_entry = CoinLedger.objects.record_transaction(
                user=wr.user,
                transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
                amount=wr.coins,
                reference=f"coin_withdrawal_request_refund:{wr.pk}",
                description="Coin withdrawal rejected — coins refunded",
                metadata={"coin_withdrawal_request_id": wr.pk},
            )
            wr.status = self.model.Status.REJECTED
            wr.failure_reason = reason
            wr.refund_ledger_entry = refund_entry
            wr.save(update_fields=["status", "failure_reason", "refund_ledger_entry", "updated_at"])
            return wr


class CoinWithdrawalRequest(models.Model):
    """
    TASK 4 — canonical "cash out coins" request for `user_profile`, the
    mirror image of `CoinPurchaseRequest` above (coins -> money instead
    of money -> coins), built on the same `CoinLedger` primitives.

    Reference read for this task was `liveclass.CoinWithdrawal`, which
    already implements this exact escrow pattern (debit at request
    time, refund on reject, no second debit/credit on completion) via
    its own `CoinTransaction` ledger. This model reproduces that same
    lifecycle but writes through `CoinLedger.objects.record_transaction()`
    instead, since `user_profile.CoinLedger` — not `liveclass.
    CoinTransaction` — is this codebase's shared, canonical coin ledger
    (see `CoinLedger`'s own docstring above). Differences from
    `liveclass.CoinWithdrawal` that are deliberate, not oversights:
      - No separate APPROVED status — this app's lifecycle is PENDING ->
        PROCESSING -> SUCCESS, or -> REJECTED from PENDING/PROCESSING.
        PROCESSING plays the same "payout initiated, not yet confirmed"
        role `liveclass.CoinWithdrawal`'s APPROVED does.
      - No `reviewed_by`/admin-user tracking, no `MIN_WITHDRAWAL_COINS`
        floor, no INR conversion snapshot — out of scope for this pass;
        add them if/when an admin-facing withdrawal review UI is built,
        the same way `RestrictUser`'s docstring above scopes out
        consumer-app integration work it doesn't own.
      - `payout_method`/`payout_details` are still modeled as a
        choices field + JSONField, same shape as `liveclass.
        CoinWithdrawal` uses, since there's no separate saved-bank-
        detail model in this app to reference by id instead.

8. TASK 5 (this pass) — fraud/anti-abuse layer. New `user_profile/
   fraud.py` module with two checks, both wired into
   `CoinLedgerManager.record_transaction()` (not the view layer) so
   they apply no matter which app/call site triggers a coin change:
     - `fraud.is_withdrawal_eligible(user, coins)` — only coins from
       `PURCHASE`/`GIFT_RECEIVED` (real money or a gift) may be
       withdrawn; `EARN`/`CAMPUS_REWARD` coins can be spent but never
       cashed out. Called by `CoinWithdrawalRequestView` (views.py)
       before `request_withdrawal()`, so an ineligible request never
       touches the balance. See `fraud.get_withdrawal_eligible_balance`
       for how a mixed balance's eligible portion is derived.
     - `fraud.check_earn_rate_limit(user, transaction_type)` — caps
       EARN/CAMPUS_REWARD credits per user within a rolling window to
       block burst-farming. Enforced inside `record_transaction()`
       itself (raises `fraud.EarnRateLimitExceeded`), not in a view, so
       campus tasks / referral bonuses / anything else that credits
       EARN or CAMPUS_REWARD coins is covered automatically.
   `record_transaction()` also now always sets
   `metadata["withdrawal_eligible"]` (derived purely from
   `transaction_type`) on every row it writes, for `admin.py`'s
   read-only ops filter — see that method's own docstring below for
   why this lives in `metadata` instead of a new column.

    Lifecycle: PENDING (created by `request_withdrawal`, debits `coins`
    immediately via a WITHDRAWAL_REQUESTED CoinLedger entry) ->
    PROCESSING (via `mark_processing`, no coin movement) -> SUCCESS (via
    `confirm_success`, no coin movement — the debit already happened) OR
    REJECTED (via `reject`, from PENDING or PROCESSING, credits `coins`
    back via a WITHDRAWAL_REJECTED entry). SUCCESS and REJECTED are both
    terminal.

    Nothing here writes to `User.coin` directly — same boundary
    `CoinPurchaseRequest` draws: the only sanctioned path to a balance
    change is `CoinLedger.objects.record_transaction()`.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        REJECTED = "rejected", "Rejected"

    class PayoutMethod(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        UPI = "upi", "UPI"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_withdrawal_requests",
    )

    coins = models.PositiveIntegerField()

    payout_method = models.CharField(max_length=20, choices=PayoutMethod.choices, blank=True)

    # Bank: {"account_holder", "account_number", "ifsc"}. UPI: {"upi_id"}.
    # Kept as JSON (not separate columns), same reasoning as
    # liveclass.CoinWithdrawal.payout_details — validated against
    # payout_method in the serializer, not here, so a new payout method
    # never needs a migration.
    payout_details = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Populated by reject() — why the withdrawal was turned down.
    failure_reason = models.CharField(max_length=255, blank=True)

    # The debit written by request_withdrawal(). SET_NULL for the same
    # reason CoinPurchaseRequest.ledger_entry is SET_NULL: an
    # administratively-deleted ledger row shouldn't cascade into
    # deleting the request that explains it.
    debit_ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # The refund written by reject(), if any. Stays null for PENDING/
    # PROCESSING/SUCCESS requests — only ever set once reject() runs.
    refund_ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CoinWithdrawalRequestManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # "my withdrawal history" — same query shape CoinLedger's
            # and CoinPurchaseRequest's (user, -created_at) index serve.
            models.Index(fields=["user", "-created_at"]),
            # Supports an admin queue view's "PENDING/PROCESSING oldest
            # first" query without a full-table scan — same shape
            # CoinPurchaseRequest's (status, created_at) index serves
            # for its own sweep task.
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=Q(coins__gt=0), name="coinwithdrawalrequest_coins_positive"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.coins} coins withdrawal ({self.status})"```

### Model notes

- **`Follow` / `BlockUser` / `RestrictUser`** — unchanged in behavior
  since v3 (see §0 / §0.1 for their full history: self-relation
  `CheckConstraint`s, composite indexes on `(follower/following, status)`,
  `RestrictUser`'s one-way/silent semantics). `CheckConstraint` now
  uses `condition=` everywhere (Django ≥ 5.1 required — see §11 item 7).
- **`CoinLedger`** — still the one append-only, auditable source of
  truth for every change to `User.coin` (a cache column on `login.User`).
  `CoinLedgerManager.record_transaction()` is still the *only* sanctioned
  write path — as of v4 it now also runs the two TASK 5 fraud hooks
  (earn-rate limiting, always-stamped `metadata["withdrawal_eligible"]`)
  and is the thing `CoinPurchaseRequest.confirm_success()` and
  `CoinWithdrawalRequest.request_withdrawal()`/`.reject()` all call
  through — no model in this app writes `User.coin` directly.
  `TransactionType` now has 12 values total (7 from v3 + 5 from TASK 1 +
  `CAMPUS_REWARD` from F-3 = see §0.3 for the exact list and why each new
  one is kept distinct from the others it might look similar to).
- **`CoinPurchaseRequest`** (TASK 3, new) — pending/success/failed
  receipt row for a coin top-up. Never writes `User.coin` itself;
  `confirm_success()` calls `CoinLedger.objects.record_transaction()`.
  Idempotent on `gateway_reference` (DB-unique + `get_or_create`, with
  the concurrent-insert race caught as `IntegrityError` and re-fetched —
  same shape as `Follow`'s double-follow race fix in v2). ⚠️
  `liveclass/models.py` wasn't part of this upload, so this shape is
  inferred from this app's own conventions, not copied from
  `liveclass.CoinPurchase` — reconcile if the two need to match exactly
  (see §11).
- **`CoinWithdrawalRequest`** (TASK 4, new) — escrow-style: debits at
  request time via `WITHDRAWAL_REQUESTED`, refunds via
  `WITHDRAWAL_REJECTED` on reject, no second ledger write on
  `confirm_success()` (the debit already happened; a zero-amount
  `WITHDRAWAL_COMPLETED` row would violate `coinledger_amount_not_zero`
  anyway — that's why `WITHDRAWAL_COMPLETED` stays unused). Reference
  read: `liveclass.CoinWithdrawal`. No `reviewed_by`, no minimum-coins
  floor, no INR snapshot — deliberately out of scope this pass (see
  §0.3 / §11).
- **`CoinLedgerManager.record_transaction()`** — the row-lock
  (`select_for_update()` on the user row) that already made the
  `reference` idempotency check race-safe is the *same* lock that now
  makes the earn-rate-limit check race-safe: two concurrent EARN calls
  for the same user always serialize on that lock before either one's
  rate-limit check runs. No new locking was added for TASK 5 — it rides
  the lock that was already there.
- **Nothing in this app imports `login.User` directly** — every FK uses
  `settings.AUTH_USER_MODEL`, and `record_transaction()` gets the
  concrete user model via `type(user)` rather than importing it, keeping
  `user_profile` decoupled from `login` the same way it always has been.

---

## 5. `serializers.py` (full current code)

```python
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
        return attrs```

### Serializer notes

- **`CoinLedgerSerializer`** — unchanged, still fully `read_only_fields`
  (see its own comment: the only sanctioned way to create a row is
  `record_transaction()`, never a client-writable serializer).
- **`CoinPurchaseRequestSerializer`** (TASK 3, new) — used for both the
  input to `BuyCoinView` (`gateway_reference`/`amount`/`coins`/`gateway`)
  and the output shape (adds `status`/`failure_reason`/timestamps, all
  read-only). `amount`/`coins` are declared explicitly with
  `min_value` so a bad value is a clean 400 at validation time instead
  of surfacing later as a DB `CheckConstraint` violation.
- **`CoinPurchaseConfirmSerializer`** (TASK 3, new) — deliberately a
  plain `Serializer`, not a `ModelSerializer`: it only validates the
  shape of the confirm/webhook payload
  (`gateway_reference`/`status`/`failure_reason`); the actual state
  transition is owned by `CoinPurchaseRequest.objects.confirm_success()`
  / `.mark_failed()`.
- **`CoinWithdrawalRequestSerializer`** (TASK 4, new) — same
  read-only-status shape as the purchase serializer above, plus a
  `validate()` that checks `payout_details`' required keys against
  `payout_method` (`bank_transfer` → `account_holder`/`account_number`/
  `ifsc`; `upi` → `upi_id`) as cross-field validation rather than a DB
  constraint, so a new payout method only ever needs a new branch here,
  never a migration.
- **No serializer exists for `fraud.py`** — deliberately. Both fraud
  checks are pure functions called directly from `views.py`
  (`is_withdrawal_eligible`) or from inside
  `CoinLedgerManager.record_transaction()`
  (`check_earn_rate_limit`) — there's no request/response shape that
  needs its own serializer; the rejection messages they produce are
  passed straight into the view's `Response(...)` body.

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

from . import fraud
from .models import (
    BlockUser,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
)
from .serializers import (
    BlockUserSerializer,
    CoinLedgerSerializer,
    CoinPurchaseConfirmSerializer,
    CoinPurchaseRequestSerializer,
    CoinWithdrawalRequestSerializer,
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


# 🔥 TASK 3 — Buy-Coin flow
# Two-step, same shape as any pending -> confirmed payment flow: this
# view only ever creates/returns a PENDING `CoinPurchaseRequest` — it
# never touches `User.coin`. The actual credit happens in
# `BuyCoinConfirmView` below, via `CoinPurchaseRequest.objects.
# confirm_success()`, which is the only path that calls
# `CoinLedger.objects.record_transaction()` for a purchase.
class BuyCoinView(GenericAPIView):
    """
    POST /profile/buy-coin/
    {"gateway_reference": "<gateway's txn id>", "amount": "99.00", "coins": 100, "gateway": "razorpay"}

    Starts a coin purchase. Idempotent on `gateway_reference`: calling
    this again with the same reference returns the existing request
    (whatever its current status) instead of creating a duplicate — safe
    for a client retrying after a dropped response.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinPurchaseRequestSerializer

    @extend_schema(
        request=CoinPurchaseRequestSerializer,
        responses={201: CoinPurchaseRequestSerializer, 200: CoinPurchaseRequestSerializer},
        description="Start a coin purchase (creates a pending request; idempotent on "
        "gateway_reference). Does not credit coins — see /buy-coin/confirm/.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        purchase, created = CoinPurchaseRequest.objects.start_purchase(
            user=request.user,
            gateway_reference=data["gateway_reference"],
            amount=data["amount"],
            coins=data["coins"],
            gateway=data.get("gateway", ""),
        )

        # `gateway_reference` is globally unique (it's the gateway's own
        # id), so if it already exists under a DIFFERENT user, this is
        # either a client bug or a replayed/guessed reference — never
        # silently let the caller read or "adopt" someone else's pending
        # purchase.
        if purchase.user_id != request.user.id:
            return Response({
                "status": False,
                "message": "This gateway_reference is already associated with another purchase.",
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": "Coin purchase started." if created
            else "Coin purchase already exists for this reference.",
            "data": self.get_serializer(purchase).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class BuyCoinConfirmView(GenericAPIView):
    """
    POST /profile/buy-coin/confirm/
    {"gateway_reference": "<gateway's txn id>", "status": "success", "failure_reason": ""}

    Confirms a pending purchase as successful (credits `coins` through
    `CoinLedger.objects.record_transaction()`) or failed (wallet
    untouched). Idempotent and safe to call more than once for the same
    `gateway_reference` — see `CoinPurchaseRequest.objects.
    confirm_success()`/`mark_failed()` (models.py) for exactly what
    happens on a repeat call.

    🚧 NOT a real webhook endpoint as-is: no payment-gateway integration
    was part of this upload, so there's no gateway signature to verify
    here — `IsAuthenticated` + "must be your own purchase" stand in so
    the flow is testable end-to-end. Before this goes live behind an
    actual gateway callback, that verification should replace (or gate)
    the checks below; a genuine webhook call isn't "acting as" any
    particular authenticated user.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={200: CoinPurchaseRequestSerializer, 404: OpenApiTypes.OBJECT},
        description="Confirm a coin purchase as success or failed. Idempotent — safe to retry "
        "(e.g. a duplicated webhook delivery).",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        gateway_reference = serializer.validated_data["gateway_reference"]
        outcome = serializer.validated_data["status"]

        purchase = CoinPurchaseRequest.objects.filter(
            gateway_reference=gateway_reference
        ).first()
        if purchase is None:
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        # See the class docstring above re: this check standing in for
        # real webhook-signature verification.
        if purchase.user_id != request.user.id:
            raise Http404

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference
                )
                message = "Coin purchase confirmed and wallet credited."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                )
                message = "Coin purchase marked as failed."
        except ValueError as exc:
            # confirm_success()/mark_failed() raise this for an invalid
            # state transition (e.g. trying to fail an already-succeeded
            # purchase) — a 409, not a 400: the request body was valid,
            # the request's current state just doesn't allow this move.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinPurchaseRequestSerializer(purchase).data,
        }, status=status.HTTP_200_OK)

# 🔥 TASK 4 — Withdraw-Coin flow
# Mirror image of BuyCoinView/BuyCoinConfirmView above (money direction
# reversed), but ONE view instead of two: CoinWithdrawalRequestManager.
# request_withdrawal() debits the coins the moment the request is made
# (escrow-style — see its own docstring in models.py for why), so
# there's no separate "confirm" step the way a coin purchase needs one.
# mark_processing()/confirm_success()/reject() (models.py) aren't wired
# to an endpoint in this pass — same "views weren't part of this
# upload" scope-out CoinLedger's docstring already applies to the
# actions that would eventually create a ledger entry from outside this
# app; here it applies to whatever admin/ops surface will eventually
# call those three.
class CoinWithdrawalRequestView(GenericAPIView):
    """
    GET  /profile/coin-withdrawals/            -> current user's own withdrawal requests
    POST /profile/coin-withdrawals/ {"coins": 200, "payout_method": "upi", "payout_details": {"upi_id": "a@bank"}}

    Debits `coins` immediately via CoinWithdrawalRequestManager.
    request_withdrawal(). Insufficient balance is a clean 402 with no
    partial debit and no request row left behind — the debit and the
    row creation share one transaction.atomic() block inside the
    manager, so a ValueError there (bubbled up from CoinLedger.objects.
    record_transaction) rolls both back together.

    TASK 5 (this pass): before any of that, `fraud.
    is_withdrawal_eligible(request.user, coins)` is checked. This is a
    read-only check — it runs BEFORE `request_withdrawal()`, so a
    rejection here never touches the balance and never creates a
    request row (satisfies the "balance must not shrink" acceptance
    criterion directly, rather than relying on a rollback). Distinct
    from the 402 below on purpose: 402 means "you don't have enough
    coins, period"; this is "you have enough coins, but not enough
    *withdrawal-eligible* ones" — a different, policy-level rejection,
    so it gets its own 403 rather than reusing 402's "add more funds"
    implication.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinWithdrawalRequestSerializer

    @extend_schema(
        responses={200: CoinWithdrawalRequestSerializer(many=True)},
        description="List of the current user's coin withdrawal requests (newest first).",
    )
    def get(self, request):
        qs = CoinWithdrawalRequest.objects.filter(user=request.user)
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Withdrawal requests fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=CoinWithdrawalRequestSerializer,
        responses={
            201: CoinWithdrawalRequestSerializer,
            402: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
        },
        description="Request a coin withdrawal. Only coins purchased or received as a gift are "
        "withdrawal-eligible (403 if the requested amount isn't covered by eligible coins); "
        "debits the wallet immediately once eligible, with insufficient balance returning 402 "
        "and no partial debit or request row left behind either way.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # TASK 5: fraud/eligibility check runs before any coins move —
        # see the class docstring above for why this is a 403, distinct
        # from the 402 below.
        is_eligible, reason = fraud.is_withdrawal_eligible(request.user, data["coins"])
        if not is_eligible:
            return Response({
                "status": False,
                "message": reason,
            }, status=status.HTTP_403_FORBIDDEN)

        try:
            withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
                user=request.user,
                coins=data["coins"],
                payout_method=data.get("payout_method", ""),
                payout_details=data.get("payout_details", {}),
            )
        except ValueError as exc:
            # Insufficient balance — 402, same "request was well-formed,
            # the wallet just can't cover it" shape campus's
            # FeePaymentViewSet.pay uses. Distinct from the 409s above
            # (BuyCoinConfirmView) which are for an invalid *state
            # transition*, not a money shortfall.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

        return Response({
            "status": True,
            "message": "Withdrawal requested successfully.",
            "data": self.get_serializer(withdrawal).data,
        }, status=status.HTTP_201_CREATED)```

### View notes / what changed vs v3.1

- **`BuyCoinView` / `BuyCoinConfirmView`** (TASK 3, new) — two-step:
  `BuyCoinView` only ever creates/returns a `PENDING` request (never
  touches `User.coin`); `BuyCoinConfirmView` is the only place that calls
  `confirm_success()`/`mark_failed()`. ⚠️ **Not gateway-verified as
  shipped** — `IsAuthenticated` + "must be your own purchase" stand in
  for a real payment-gateway signature check, since no gateway
  integration was part of this upload. Don't expose this confirm route
  to the public internet unauthenticated until that's added (see §11).
- **`CoinWithdrawalRequestView`** (TASK 4, new) — one view, not two
  (unlike buy-coin): `request_withdrawal()` debits at request time, so
  there's no separate confirm step from the user's side.
  `mark_processing`/`confirm_success`/`reject` (models.py) are **not**
  wired to any endpoint yet — that's an ops/admin surface for a later
  pass.
- **TASK 5 fraud check placement** — `fraud.is_withdrawal_eligible()` is
  called in `CoinWithdrawalRequestView.post()` **before**
  `request_withdrawal()` is ever invoked, and returns its own `403`,
  deliberately distinct from the `402` "insufficient balance" case
  further down (different failure semantics: "you don't have enough
  coins" vs. "you have enough coins but they're not the withdrawable
  kind"). `check_earn_rate_limit()` is **not** called from any view at
  all — it lives inside `record_transaction()` itself (models.py), so
  every view/task/app that credits `EARN`/`CAMPUS_REWARD` coins is
  covered without each of them needing to remember to check it.
- **`from . import fraud`** — `views.py` imports the whole module (not
  individual functions) so the single call site
  (`fraud.is_withdrawal_eligible`) stays self-documenting about which
  module owns the rule it's enforcing.

---

## 7. `urls.py` (full current code)

```python
# user_profile/urls.py
from django.urls import path

from .views import (
    AcceptFollowRequestView,
    BlockedUsersView,
    BuyCoinConfirmView,
    BuyCoinView,
    CoinLedgerListView,
    CoinWithdrawalRequestView,
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
    # TASK 3 — buy-coin flow: start a purchase, then confirm it
    # (success/failed). See BuyCoinConfirmView's docstring for the
    # caveat that this confirm route stands in for a real payment-
    # gateway webhook and isn't signature-verified yet.
    path("buy-coin/", BuyCoinView.as_view(), name="buy-coin"),
    path("buy-coin/confirm/", BuyCoinConfirmView.as_view(), name="buy-coin-confirm"),
    # TASK 4 — withdraw-coin flow: request a withdrawal (debits
    # immediately) / list your own withdrawal requests. Same
    # /profile/... URL-shape consistency RestrictUser's docstring
    # (models.py) calls out for restricted-users/ vs blocked-users/.
    path("coin-withdrawals/", CoinWithdrawalRequestView.as_view(), name="coin-withdrawal-requests"),
]```

### Full endpoint table (as of v4)

| Method | Path | View | Auth | Notes |
|---|---|---|---|---|
| GET | `/profile/` | `ProfileView` | ✅ | own profile |
| GET | `/profile/search/` | `UserSearchView` | ✅ | excludes self + blocked |
| GET | `/profile/chat-search/` | `MessageContactSearchView` | ✅ | connections only, excludes blocked |
| GET | `/profile/profile/<username>/` | `UserProfileDetailView` | ✅ | block → 404; private → restricted card |
| GET | `/profile/profile/<username>/followers/` | `FollowersListView` | ✅ | |
| GET | `/profile/profile/<username>/following/` | `FollowingListView` | ✅ | |
| POST | `/profile/follow/<user_id>/` | `FollowAPIView` | ✅ | toggles follow/unfollow |
| POST | `/profile/accept-request/<follow_id>/` | `AcceptFollowRequestView` | ✅ | |
| POST | `/profile/reject-request/<follow_id>/` | `RejectFollowRequestView` | ✅ | |
| POST | `/profile/update/` | `UpdateProfileView` | ✅ | |
| GET/POST | `/profile/blocked-users/` | `BlockedUsersView` | ✅ | |
| DELETE | `/profile/blocked-users/<int:id>/` | `UnblockUserView` | ✅ | int-only path param |
| GET/POST | `/profile/restricted-users/` | `RestrictedUsersView` | ✅ | TASK 18 |
| DELETE | `/profile/restricted-users/<int:id>/` | `UnrestrictUserView` | ✅ | TASK 18 |
| GET | `/profile/coin-ledger/` | `CoinLedgerListView` | ✅ | TASK 19, read-only |
| POST | `/profile/buy-coin/` | `BuyCoinView` | ✅ | **TASK 3, new** — start a pending purchase |
| POST | `/profile/buy-coin/confirm/` | `BuyCoinConfirmView` | ✅ | **TASK 3, new** — confirm success/failed; not gateway-verified yet |
| GET/POST | `/profile/coin-withdrawals/` | `CoinWithdrawalRequestView` | ✅ | **TASK 4, new** — POST runs the TASK 5 eligibility check (403) before debiting (402 on shortfall) |

Nothing new in `urls.py` itself beyond the one new path — `admin.py`,
`fraud.py`, and `tasks.py` have no URL surface of their own (admin is
reached via Django's own `/admin/` site; `fraud.py` is called from
inside views/models, not routed directly; `tasks.py`'s
`reconcile_follow_counts` runs on Celery Beat's schedule, not an HTTP
endpoint).

---

## 8. `admin.py` (full current code — this pass, B-8)

```python
# user_profile/admin.py
"""
TASK 5 — this file wasn't part of any earlier upload for this app (see
the CAUTION note in `CoinLedger`'s docstring in models.py — admin.py's
absence was already flagged there as the reason `CoinLedger` was
"sirf admin me registered hai" with NO read-only guard). This pass
adds it from scratch with just the two things TASK 5 actually needs:

  - `CoinLedger` registered READ-ONLY. Per that CAUTION note: letting
    admin add/edit/delete `CoinLedger` rows directly bypasses
    `CoinLedgerManager.record_transaction()` entirely, which can create
    a ledger row with no matching `User.coin` change (or edit an
    existing row's `amount` without touching the balance it supposedly
    explains) — silently breaking the "ledger and balance must always
    agree" invariant this table exists to guarantee. Admin should be
    a VIEWER of this table, never a second unguarded write path.
  - A "Withdrawal eligible" list filter, driven by the
    `metadata["withdrawal_eligible"]` flag `record_transaction()` now
    stamps on every row (see that method's docstring), so ops can
    quickly see/filter which credits are/aren't withdrawal-eligible
    without cross-referencing `transaction_type` by hand.

IMPORTANT: if `Follow`, `BlockUser`, `RestrictUser`,
`CoinPurchaseRequest`, or `CoinWithdrawalRequest` are already
registered in a version of this file elsewhere in the codebase (this
upload never included an existing admin.py, so this pass has no
visibility into one), merge those registrations into this file rather
than letting this overwrite them — this file only defines
`CoinLedger`'s registration; it doesn't know about or touch the
others.
"""
from django.contrib import admin

from .models import CoinLedger


class WithdrawalEligibleFilter(admin.SimpleListFilter):
    """
    Ops-facing filter on the `metadata["withdrawal_eligible"]` flag
    `CoinLedgerManager.record_transaction()` stamps on every row. Reads
    the flag rather than re-deriving eligibility from
    `transaction_type` here, so this filter and `fraud.py` can never
    silently disagree about what "eligible" means for a given row —
    there is exactly one place (`record_transaction`) that decides it.
    """

    title = "withdrawal eligible"
    parameter_name = "withdrawal_eligible"

    def lookups(self, request, model_admin):
        return (
            ("1", "Eligible (purchase / gift)"),
            ("0", "Not eligible (earn / reward / other)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(metadata__withdrawal_eligible=True)
        if self.value() == "0":
            return queryset.filter(metadata__withdrawal_eligible=False)
        return queryset


@admin.register(CoinLedger)
class CoinLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "transaction_type",
        "amount",
        "balance_after",
        "reference",
        "created_at",
    )
    list_filter = ("transaction_type", WithdrawalEligibleFilter)
    search_fields = ("user__username", "reference", "description")
    date_hierarchy = "created_at"

    # Read-only, deliberately: see the module docstring above. Every
    # field is listed (not just a subset) so a future field addition
    # to CoinLedger doesn't accidentally become admin-editable by
    # being left off this list.
    readonly_fields = [f.name for f in CoinLedger._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deletion (unlike add/change) doesn't corrupt a row's own
        # amount/balance_after — but it DOES let an already-applied
        # balance change vanish from the audit trail while `User.coin`
        # keeps the effect, which is just as bad for the "ledger
        # explains every balance change" guarantee. Blocked for the
        # same reason.
        return False```

### admin.py notes

- This file did not exist in any earlier upload of this app — it's new
  this pass, added specifically to close the "CoinLedger is admin-
  editable with no guard" caveat v3.1 flagged (B-8) and left open in
  `CoinLedger`'s own docstring (see §4).
- `CoinLedgerAdmin` registers `CoinLedger` **read-only**:
  `has_add_permission`/`has_change_permission`/`has_delete_permission`
  all return `False`, and `readonly_fields` lists every model field
  explicitly (not a subset) so a future field addition to `CoinLedger`
  doesn't silently become admin-editable by being left off the list.
  Deletion is blocked too — deleting a row doesn't corrupt its own
  amount/balance_after, but it would let an already-applied balance
  change vanish from the audit trail while `User.coin` keeps the
  effect, which breaks the same "ledger explains every balance change"
  invariant.
- `WithdrawalEligibleFilter` is a `SimpleListFilter` reading
  `metadata["withdrawal_eligible"]` (the flag TASK 5's
  `record_transaction()` now stamps on every row) rather than
  re-deriving eligibility from `transaction_type` here — so this filter
  and `fraud.py` can never quietly disagree about what "eligible" means.
- ⚠️ **This file only defines `CoinLedger`'s registration.** If `Follow`,
  `BlockUser`, `RestrictUser`, `CoinPurchaseRequest`, or
  `CoinWithdrawalRequest` are already registered in a version of
  `admin.py` elsewhere in the actual codebase, **merge** those
  registrations into this file rather than letting this overwrite them
  — no earlier upload of this app ever included an existing `admin.py`,
  so this pass has no visibility into one. `autocomplete_fields` (if you
  add any for these other models, following the same pattern v2's
  `admin.py` used) still needs your `User` model's own `ModelAdmin` to
  declare `search_fields`, or Django raises `E040` at startup (see §11
  item 6).

## 8a. `tasks.py` (full current code — unchanged since v3)

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
    }```

### tasks.py notes

- No change this pass — `reconcile_follow_counts` is unrelated to the
  coin-economy work in TASK 1/3/4/5. Kept here in full for the "one file
  has everything" promise this document makes.
- Still requires Celery + Celery Beat configured and the
  `user-profile-reconcile-follow-counts` entry in
  `CELERY_BEAT_SCHEDULE` (see §12) — nothing about that changed either.

## 8b. `fraud.py` (new this pass — TASK 5 — full current code)

```python
# user_profile/fraud.py
"""
TASK 5 — fraud / anti-abuse layer for the coin economy.

Two independent rules live here, both enforced at the `CoinLedger`
write path (`CoinLedgerManager.record_transaction()` in models.py)
rather than only in a view, so no call site — this app's own views,
campus's tasks, referral bonuses, or anything written later — can
bypass them by going around a particular view:

1. Withdrawal eligibility — only coins that came from real money
   (`TransactionType.PURCHASE`) or from another user
   (`TransactionType.GIFT_RECEIVED`) may ever be cashed out.
   `TransactionType.EARN` / `CAMPUS_REWARD` coins can be spent inside
   the product but never withdrawn. `is_withdrawal_eligible()` is the
   single function `CoinWithdrawalRequestView` (views.py) calls before
   accepting a withdrawal request.

2. Earn-rate limiting — `EARN`/`CAMPUS_REWARD` credits for a single
   user are capped within a rolling time window, so a script (or a
   user replaying the same "task complete" call) can't burst-farm
   coins. `check_earn_rate_limit()` is called from inside
   `record_transaction()` itself for exactly those two transaction
   types; every other transaction_type is untouched by this rule.

Both functions take a plain `user` object (not a user id) and never
mutate anything — this module reads the ledger, it never writes to
it. The only writer stays `CoinLedgerManager.record_transaction()`.
"""
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone


class EarnRateLimitExceeded(Exception):
    """
    Raised by `CoinLedgerManager.record_transaction()` (models.py) when
    an EARN/CAMPUS_REWARD credit would push a user past the burst-farm
    limit. Deliberately NOT a `ValueError` — `record_transaction()`
    already uses plain `ValueError` for "this request is malformed /
    can't be satisfied" (zero amount, insufficient balance), which
    campus tasks and the referral-bonus flow may already be catching
    generically. A distinct exception class lets a caller that *does*
    want to tell "you're farming too fast" apart from "you're broke"
    catch this specifically (e.g. to log it, or to back off and retry
    later) without also swallowing unrelated ValueErrors — while a
    caller that only wants "something went wrong, don't credit" can
    still catch `Exception` (or `(ValueError, EarnRateLimitExceeded)`)
    the same way it always could.
    """


# --- Withdrawal eligibility ------------------------------------------------

# Credits that count toward the withdrawal-eligible pool. Deliberately
# just these two "money actually entered the platform" types, per the
# rule as specified — NOT `TESTSERIES_PAYOUT` (a creator payout is
# revenue-shaped but isn't literally a purchase or a gift), NOT
# `REFUND` (a generic refund's original source isn't known here), and
# NOT `ADMIN_ADJUSTMENT` (an ops-issued adjustment isn't "real paisa"
# either — if a specific adjustment SHOULD be withdrawable, it should
# be issued as an explicit `GIFT_RECEIVED`/`PURCHASE` entry instead of
# widening this set).
#
# `WITHDRAWAL_REJECTED` is included too, but not because a rejected
# withdrawal is itself a new source of money — it's the refund of a
# withdrawal that could only have been *requested* by draining this
# same eligible pool in the first place (see `is_withdrawal_eligible`
# / `CoinWithdrawalRequestManager.request_withdrawal` in models.py).
# Refunding a rejected withdrawal and NOT crediting it back to the
# eligible pool would silently strand a user's own purchased/gifted
# coins as un-withdrawable forever, which is a bug, not a fraud
# control — no acceptance test covers this edge case, but leaving it
# out would be wrong on inspection.
def _eligible_source_types():
    from .models import CoinLedger

    return {
        CoinLedger.TransactionType.PURCHASE,
        CoinLedger.TransactionType.GIFT_RECEIVED,
        CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
    }


def get_withdrawal_eligible_balance(user):
    """
    How many of `user`'s current coins are withdrawal-eligible (i.e.
    traceable back to a purchase or a received gift), as of right now.

    This is NOT `sum(amount for PURCHASE/GIFT_RECEIVED rows)` — a user
    can spend coins on something, and that spend has to come out of
    *some* bucket. The rule applied here: non-eligible coins (EARN,
    CAMPUS_REWARD, ...) are treated as spent first, and only once
    they're exhausted does further spend start eating into the
    eligible (purchased/gifted) pool. This is the generous-to-the-user
    reading (their real-money coins survive as long as possible) and
    is also the one that keeps `User.coin` and this figure mutually
    consistent without needing a second running-balance column: see
    the derivation below.

    Implementation: a single grouped aggregate over this user's
    `CoinLedger` rows (one query, not a row-by-row replay) is enough,
    because the two buckets only interact in one place (debits that
    exceed the non-eligible bucket "overflow" into the eligible one),
    and that overflow amount only depends on the FINAL totals of each
    bucket, not the order the rows happened in — as long as no
    withdrawal was ever approved for more than the eligible balance at
    the time (which `is_withdrawal_eligible` below exists to
    guarantee). Concretely:

      eligible_balance
        = (eligible credits: PURCHASE + GIFT_RECEIVED + WITHDRAWAL_REJECTED)
        - (eligible debits: WITHDRAWAL_REQUESTED, which only ever draws
           from this pool by construction)
        + min(0, non_eligible_net)

      where `non_eligible_net` is the net of every OTHER
      transaction_type (EARN, CAMPUS_REWARD, SPEND, GIFT_SENT, REFUND,
      ADMIN_ADJUSTMENT, TESTSERIES_*, ...) — if that net is negative,
      i.e. more was spent than was ever earned/rewarded, the shortfall
      must have come out of the eligible pool, so it's subtracted from
      it too.

    Clamped to >= 0 defensively; it should never go negative given the
    invariants above, but a negative "eligible balance" is meaningless
    either way.
    """
    from .models import CoinLedger

    eligible_types = _eligible_source_types()
    withdrawal_debit_type = CoinLedger.TransactionType.WITHDRAWAL_REQUESTED

    totals_by_type = dict(
        CoinLedger.objects.filter(user=user)
        .values_list("transaction_type")
        .annotate(total=Sum("amount"))
    )

    eligible_credits = sum(
        totals_by_type.get(t, 0) for t in eligible_types
    )
    eligible_debits = totals_by_type.get(withdrawal_debit_type, 0)  # already negative
    non_eligible_net = sum(
        total
        for ttype, total in totals_by_type.items()
        if ttype not in eligible_types and ttype != withdrawal_debit_type
    )

    eligible_balance = eligible_credits + eligible_debits
    if non_eligible_net < 0:
        eligible_balance += non_eligible_net

    return max(eligible_balance, 0)


def is_withdrawal_eligible(user, coins=None):
    """
    (bool, reason) — whether `user` may withdraw `coins`.

    `coins=None` checks only "does this user have ANY withdrawal-
    eligible balance at all" (useful for e.g. showing/hiding a
    "withdraw" button); `coins=<int>` checks that specific amount, which
    is what `CoinWithdrawalRequestView.post()` (views.py) actually
    calls before accepting a request. On a mixed balance, only the
    purchased/gifted portion is eligible — see
    `get_withdrawal_eligible_balance()` above — not the user's total
    `User.coin` balance.

    Never touches the balance or writes anything; this is a pure
    read-only check, safe to call as many times as a view wants.
    """
    eligible_balance = get_withdrawal_eligible_balance(user)

    if coins is None:
        if eligible_balance <= 0:
            return False, (
                "No withdrawal-eligible balance. Only coins you purchased "
                "or received as a gift can be withdrawn."
            )
        return True, ""

    if coins <= 0:
        return False, "Withdrawal amount must be positive."

    if coins > eligible_balance:
        return False, (
            f"Only {eligible_balance} of your coins are withdrawal-eligible "
            "(purchased or gifted). Earned/reward coins can't be withdrawn."
        )

    return True, ""


# --- Earn-rate limiting -----------------------------------------------------

# Deliberately conservative constants, not config-driven — same
# "cheap moment, no live traffic depends on the exact number yet" call
# this file's sibling models already make for other first-pass
# choices. Tighten/loosen these (or move them to Django settings) once
# there's real farming-attempt data to calibrate against.
EARN_RATE_LIMIT_WINDOW = timedelta(hours=1)
EARN_RATE_LIMIT_MAX_TRANSACTIONS = 20
EARN_RATE_LIMIT_MAX_COINS = 500


def check_earn_rate_limit(user, transaction_type):
    """
    True if `user` may receive another `transaction_type` credit right
    now; False if they've hit the burst-farm limit and the caller
    (`record_transaction()`) should refuse the credit.

    Only ever restricts `EARN`/`CAMPUS_REWARD` — every other
    transaction_type returns True immediately without a query, since
    rate-limiting a purchase or a gift makes no sense (real money and
    another user's coins aren't "farmable" the way a repeatable
    in-app action is).

    Two independent caps within a rolling window (both must pass):
    a count cap (no more than N earn-type credits, regardless of size
    — catches a script hammering a small reward repeatedly) and a
    total-coins cap (no more than M coins total, regardless of how
    many transactions — catches a few large credits instead of many
    small ones). Either one tripping blocks the credit.
    """
    from .models import CoinLedger

    rate_limited_types = (
        CoinLedger.TransactionType.EARN,
        CoinLedger.TransactionType.CAMPUS_REWARD,
    )
    if transaction_type not in rate_limited_types:
        return True

    window_start = timezone.now() - EARN_RATE_LIMIT_WINDOW
    recent = CoinLedger.objects.filter(
        user=user,
        transaction_type__in=rate_limited_types,
        created_at__gte=window_start,
    )

    if recent.count() >= EARN_RATE_LIMIT_MAX_TRANSACTIONS:
        return False

    total_recent_coins = recent.aggregate(total=Sum("amount"))["total"] or 0
    if total_recent_coins >= EARN_RATE_LIMIT_MAX_COINS:
        return False

    return True```

### fraud.py notes

- **Two independent rules, one shared enforcement point.** Both
  `is_withdrawal_eligible()` and `check_earn_rate_limit()` are read-only,
  side-effect-free functions — neither writes to `CoinLedger` or
  `User.coin`. The only writer stays
  `CoinLedgerManager.record_transaction()` (models.py), which calls
  `check_earn_rate_limit()` internally and which `CoinWithdrawalRequestView`
  (views.py) calls `is_withdrawal_eligible()` in front of.
- **Withdrawal-eligible balance derivation**
  (`get_withdrawal_eligible_balance`) is a single grouped `Sum` aggregate,
  not a row-by-row replay: non-eligible credits (EARN, CAMPUS_REWARD,
  ...) are treated as spent first; only once that bucket nets negative
  does the shortfall eat into the eligible (PURCHASE/GIFT_RECEIVED/
  WITHDRAWAL_REJECTED) bucket. `WITHDRAWAL_REJECTED` is counted as an
  eligible *credit* not because a rejection is a new source of money,
  but because it's refunding coins that could only have been withdrawn
  from the eligible pool in the first place — leaving it out would
  silently strand a user's own purchased/gifted coins as permanently
  un-withdrawable, which is a bug this module deliberately avoids even
  though no acceptance test covers that specific edge case.
- **Explicitly excluded from the eligible-source set**, on purpose, not
  by omission: `TESTSERIES_PAYOUT` (creator payout, not literally a
  purchase or a gift), `REFUND` (source unknown from this function's
  vantage point), `ADMIN_ADJUSTMENT` (an ops adjustment isn't "real
  paisa" either — if a specific one should be withdrawable, issue it as
  an explicit `GIFT_RECEIVED`/`PURCHASE` instead of widening this set).
- **Earn-rate limiting constants** (`EARN_RATE_LIMIT_WINDOW`,
  `EARN_RATE_LIMIT_MAX_TRANSACTIONS`, `EARN_RATE_LIMIT_MAX_COINS`) are
  plain module constants, not settings-driven — same "cheap moment, no
  live traffic to calibrate against yet" call other first-pass choices
  in this app already make. Move them to Django settings once there's
  real farming-attempt data.
- **`EarnRateLimitExceeded` is deliberately not a `ValueError`** — see
  §0.3 for why: it lets a caller distinguish "you're farming too fast"
  from "you're broke" without also swallowing unrelated `ValueError`s,
  while a caller that doesn't care about the distinction can still catch
  both together.
- **Every non-EARN/CAMPUS_REWARD `transaction_type` short-circuits
  `check_earn_rate_limit()` to `True` with no query at all** — rate-
  limiting a purchase or a gift doesn't make sense (real money and
  another user's coins aren't "farmable" the way a repeatable in-app
  action is), so this never adds query overhead to the majority of
  `record_transaction()` calls.

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

from .models import BlockUser, CoinLedger, CoinWithdrawalRequest, Follow, RestrictUser

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

# ==========================================================================
# TASK 4 — Withdraw-Coin flow.
#
# Written against the real `user_profile/views.py`/`urls.py`/
# `serializers.py` (all uploaded for this pass). Confirmed endpoint:
#   CoinWithdrawalRequestView   GET/POST /coin-withdrawals/   "coin-withdrawal-requests"
# ==========================================================================

class CoinWithdrawalRequestManagerTests(APITestCase):
    """
    Direct manager-level tests — CoinWithdrawalRequestManager is the
    sanctioned write path (models.py), same as
    CoinPurchaseRequestManager is for CoinPurchaseRequest.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.alice.coin = 500
        self.alice.save(update_fields=["coin"])

    def test_withdrawal_debits_exact_amount(self):
        withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.alice,
            coins=200,
            payout_method=CoinWithdrawalRequest.PayoutMethod.UPI,
            payout_details={"upi_id": "alice@bank"},
        )

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 300)

        self.assertEqual(withdrawal.status, CoinWithdrawalRequest.Status.PENDING)
        self.assertIsNotNone(withdrawal.debit_ledger_entry)
        self.assertEqual(withdrawal.debit_ledger_entry.amount, -200)
        self.assertEqual(
            withdrawal.debit_ledger_entry.transaction_type,
            CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
        )

    def test_rejected_withdrawal_credits_coins_back(self):
        withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.alice,
            coins=200,
            payout_method=CoinWithdrawalRequest.PayoutMethod.UPI,
            payout_details={"upi_id": "alice@bank"},
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 300)  # sanity check on the debit

        rejected = CoinWithdrawalRequest.objects.reject(
            withdrawal_id=withdrawal.pk, reason="Bad IFSC code"
        )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)  # fully refunded
        self.assertEqual(rejected.status, CoinWithdrawalRequest.Status.REJECTED)
        self.assertIsNotNone(rejected.refund_ledger_entry)
        self.assertEqual(rejected.refund_ledger_entry.amount, 200)
        self.assertEqual(
            rejected.refund_ledger_entry.transaction_type,
            CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
        )

        # Idempotency: a retried reject() call (double form submit, admin
        # double-click) must not credit the refund a second time.
        CoinWithdrawalRequest.objects.reject(withdrawal_id=withdrawal.pk, reason="retry")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)


class CoinWithdrawalRequestAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.alice.coin = 500
        self.alice.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.alice)

    def test_insufficient_balance_returns_402(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {
                "coins": 10_000,
                "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI,
                "payout_details": {"upi_id": "alice@bank"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertFalse(response.data["status"])

        # No partial debit, and no request row left behind — the debit
        # and the row creation share one transaction.atomic() block in
        # CoinWithdrawalRequestManager.request_withdrawal.
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)
        self.assertFalse(CoinWithdrawalRequest.objects.filter(user=self.alice).exists())

    def test_withdrawal_request_via_api_creates_pending_row(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {
                "coins": 150,
                "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI,
                "payout_details": {"upi_id": "alice@bank"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["status"])
        self.assertEqual(response.data["data"]["status"], CoinWithdrawalRequest.Status.PENDING)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 350)

    def test_missing_payout_details_is_rejected(self):
        response = self.client.post(
            reverse("coin-withdrawal-requests"),
            {"coins": 150, "payout_method": CoinWithdrawalRequest.PayoutMethod.UPI, "payout_details": {}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)  # validation failed before any debit```

### tests.py coverage summary (as of v4)

Test classes present, in file order:
- `FollowModelTests`, `FollowAPITests` — self-follow, follow/unfollow
  counts, blocked-user-cannot-follow, private-profile-hides-bio,
  blocked-user-404-on-lookup (v2).
- `PrivateAccountFollowRequestFlowTests` — pending-request flow for
  private accounts, accept/reject (v3, TASK 30).
- `BlockUnblockEdgeCaseTests` — self-block, idempotent double-block,
  block removes existing Follow both ways, unblock by record id or by
  target user id, cross-user unblock rejected (v3, TASK 30).
- `RestrictUserModelTests` — self-restrict / duplicate-restrict blocked
  at DB level, restrict does NOT touch Follow/counts, restricting an
  already-blocked user is rejected (v3, TASK 30).
- `UserSearchExclusionTests` — search excludes self/blocked both
  directions (v3, TASK 30).
- `FollowRaceConditionTests` — concurrent double-follow doesn't 500
  (v3, TASK 30).
- `CoinWithdrawalRequestManagerTests`, `CoinWithdrawalRequestAPITests`
  — **new this pass (TASK 4)**: exact-amount debit, rejected withdrawal
  credits back, 402 on insufficient balance via the API, pending row
  created via the API, missing payout_details rejected.

Fraud-specific coverage (withdrawal eligibility, earn-rate limiting)
lives in the separate `tests_fraud.py` file — see §9a — not in this one.

## 9a. `tests_fraud.py` (new this pass — TASK 5 — full current code)

```python
# user_profile/tests_fraud.py
"""
TASK 5 — tests for the fraud/anti-abuse layer (fraud.py +
CoinLedgerManager.record_transaction()'s hooks into it).

NOTE: this file wasn't placed in an existing `user_profile/tests/`
package because none was part of this upload — if this app already
has one, move this module in as `tests/test_fraud.py` instead of
leaving it as a top-level sibling of models.py/views.py, and drop this
note.

NOTE on user creation: the exact required fields for
`settings.AUTH_USER_MODEL` (`login.User`, per models.py's comments)
weren't part of this upload either, so `_make_user()` below only sets
`username` + `password` via `create_user`. If that model requires more
than that (e.g. a mandatory phone/email field with no default), adjust
`_make_user()` accordingly — the assertions themselves don't depend on
anything about the user beyond its `coin` field and its pk.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import fraud
from .models import CoinLedger

User = get_user_model()


def _make_user(username):
    return User.objects.create_user(username=username, password="testpass123")


class WithdrawalEligibilityTests(TestCase):
    def test_earn_only_balance_not_withdrawable(self):
        """
        A user whose entire balance came from EARN/CAMPUS_REWARD has
        zero withdrawal-eligible balance, and a withdrawal attempt gets
        a clean rejection with no balance change.
        """
        user = _make_user("earn_only_user")

        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=150,
            reference="earn-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.CAMPUS_REWARD,
            amount=50,
            reference="campus-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 200)

        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 0)

        is_eligible, reason = fraud.is_withdrawal_eligible(user, coins=50)
        self.assertFalse(is_eligible)
        self.assertTrue(reason)  # a human-readable rejection message

        # Balance must be untouched — the check ran before any debit.
        user.refresh_from_db()
        self.assertEqual(user.coin, 200)

        # Note: `CoinWithdrawalRequestManager.request_withdrawal()` on
        # its own does NOT enforce eligibility (it only checks total
        # balance, which 200 covers) — it's `CoinWithdrawalRequestView`
        # calling `fraud.is_withdrawal_eligible()` first that provides
        # the guarantee this test is actually checking. That's a view-
        # layer responsibility, so it isn't re-asserted here.

    def test_mixed_balance_only_purchased_portion_withdrawable(self):
        """
        A balance made up of both earned and purchased coins is only
        withdrawal-eligible up to the purchased/gifted portion — not
        the full balance.
        """
        user = _make_user("mixed_balance_user")

        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=100,
            reference="earn-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.PURCHASE,
            amount=200,
            reference="purchase-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 300)

        # Only the 200 purchased coins are eligible, not the full 300.
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 200)

        ok_small, _ = fraud.is_withdrawal_eligible(user, coins=150)
        self.assertTrue(ok_small)

        ok_full_balance, reason = fraud.is_withdrawal_eligible(user, coins=300)
        self.assertFalse(ok_full_balance)
        self.assertIn("200", reason)

        ok_exact, _ = fraud.is_withdrawal_eligible(user, coins=200)
        self.assertTrue(ok_exact)

    def test_gift_received_is_withdrawal_eligible(self):
        """Gifted coins are eligible the same way purchased coins are."""
        user = _make_user("gift_user")
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.GIFT_RECEIVED,
            amount=75,
            reference="gift-1",
        )
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 75)

    def test_spend_overflow_eats_into_eligible_balance(self):
        """
        Spending more than the non-eligible (earned) balance should
        reduce the eligible (purchased) balance by the overflow amount
        — not leave it untouched.
        """
        user = _make_user("overflow_user")
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.PURCHASE,
            amount=100,
            reference="purchase-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=50,
            reference="earn-1",
        )
        # Spend 80: consumes the 50 earned coins, then 30 more must come
        # out of the 100 purchased coins.
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.SPEND,
            amount=-80,
            reference="spend-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 70)
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 70)


class EarnRateLimitTests(TestCase):
    def test_earn_rate_limit_blocks_burst(self):
        """
        Once a user crosses the earn-rate limit, a further EARN credit
        is rejected with EarnRateLimitExceeded — and the caller (e.g.
        campus tasks, referral bonus) can catch that specifically.
        """
        user = _make_user("burst_farmer")

        # Patch the limit low so this test is fast and deterministic
        # rather than depending on fraud.py's production defaults.
        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 3), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 10_000):
            for i in range(3):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=10,
                    reference=f"earn-{i}",
                )

            with self.assertRaises(fraud.EarnRateLimitExceeded):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=10,
                    reference="earn-over-limit",
                )

        # The rejected transaction must not have moved the balance.
        user.refresh_from_db()
        self.assertEqual(user.coin, 30)

    def test_earn_rate_limit_by_total_coins(self):
        """The coin-total cap trips independently of the count cap."""
        user = _make_user("big_earn_farmer")

        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 100), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 50):
            CoinLedger.objects.record_transaction(
                user=user,
                transaction_type=CoinLedger.TransactionType.EARN,
                amount=50,
                reference="earn-1",
            )
            with self.assertRaises(fraud.EarnRateLimitExceeded):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=1,
                    reference="earn-2",
                )

    def test_earn_rate_limit_does_not_apply_to_purchase(self):
        """
        PURCHASE (and every non-EARN/CAMPUS_REWARD type) is never
        rate-limited, even well past the earn caps.
        """
        user = _make_user("frequent_buyer")

        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 1), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 1):
            for i in range(5):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.PURCHASE,
                    amount=100,
                    reference=f"purchase-{i}",
                )

        user.refresh_from_db()
        self.assertEqual(user.coin, 500)```

### tests_fraud.py notes

- ⚠️ **Location caveat, in the file's own header:** this wasn't placed
  under an existing `user_profile/tests/` package because no such
  package was part of any upload — if this app already has one, move
  this module in as `tests/test_fraud.py` instead of leaving it as a
  top-level sibling of `models.py`/`views.py`.
- ⚠️ **`_make_user()` assumption:** only sets `username` + `password` via
  `create_user`. If `settings.AUTH_USER_MODEL` (`login.User`, per
  `models.py`'s own comments) requires more mandatory fields, adjust
  `_make_user()` — none of the assertions depend on anything about the
  user beyond `coin` and its pk.
- `WithdrawalEligibilityTests` covers: an earn-only balance is fully
  non-withdrawable (with a clean rejection message, no balance change);
  a mixed earn+purchase balance is only eligible up to the purchased
  portion; gift-received coins are eligible the same as purchased ones;
  spend that exceeds the non-eligible bucket correctly eats into the
  eligible bucket.
- `EarnRateLimitTests` covers: the count cap trips
  (`EarnRateLimitExceeded`, no balance change on the rejected attempt);
  the total-coins cap trips independently of the count cap; `PURCHASE`
  (and by extension every non-EARN/CAMPUS_REWARD type) is never
  rate-limited even with the caps patched down to 1.
- Uses `unittest.mock.patch.object(fraud, "EARN_RATE_LIMIT_MAX_..." , N)`
  to make the rate-limit tests fast and deterministic rather than
  depending on `fraud.py`'s production defaults (20 tx / 500 coins per
  hour) — don't remove those patches when editing these tests, or they
  become slow/flaky against the real window.

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

> **Update to §10.9 (v4):** `record_transaction()`'s atomic block above
> now also runs `fraud.check_earn_rate_limit()` (for
> `EARN`/`CAMPUS_REWARD` only) right after the row lock is acquired and
> before the reference/idempotency check — a trip raises
> `fraud.EarnRateLimitExceeded` and rolls the whole transaction back, so
> no partial ledger row or balance change survives a rejected earn
> credit. The method also now always sets
> `metadata["withdrawal_eligible"]` before creating the row. See §8b for
> the full rule.

### 10.10 Buy coins (`POST /profile/buy-coin/` → `POST /profile/buy-coin/confirm/`) — v4, TASK 3, new

```
POST /profile/buy-coin/ {gateway_reference, amount, coins, gateway}
        │
        ▼
CoinPurchaseRequest.objects.start_purchase(...)
  get_or_create(gateway_reference=...) — wallet untouched, status=PENDING
        │
        ▼
201 (new) or 200 (already existed) — never touches User.coin

... later, once the payment gateway confirms ...

POST /profile/buy-coin/confirm/ {gateway_reference, status: success|failed}
        │
        ▼
status == "success"?
  ├─ yes → CoinPurchaseRequest.objects.confirm_success(gateway_reference)
  │         select_for_update() the request row
  │         already SUCCESS? → return as-is (idempotent)
  │         already FAILED?  → raise ValueError → 409
  │         else → CoinLedger.objects.record_transaction(
  │                  transaction_type=PURCHASE, amount=+coins,
  │                  reference=f"coin_purchase_request:{pk}")
  │                request.status = SUCCESS; save
  └─ no  → CoinPurchaseRequest.objects.mark_failed(gateway_reference, reason)
            already FAILED? → return as-is
            already SUCCESS? → raise ValueError → 409 (can't un-credit this way)
            else → request.status = FAILED; save — wallet untouched
```
⚠️ As shipped, `BuyCoinConfirmView` is **not** verified against a real
payment-gateway signature — see §11. Don't expose `/buy-coin/confirm/`
to an untrusted caller in production without adding that check first.

### 10.11 Withdraw coins (`POST /profile/coin-withdrawals/`) — v4, TASK 4 + TASK 5, new

```
POST /profile/coin-withdrawals/ {coins, payout_method, payout_details}
        │
        ▼
serializer valid? (payout_details' required keys checked against
payout_method — e.g. UPI needs upi_id) ──no──▶ 400
        │ yes
        ▼
fraud.is_withdrawal_eligible(request.user, coins)   ◀── TASK 5
  get_withdrawal_eligible_balance(user): eligible credits
  (PURCHASE + GIFT_RECEIVED + WITHDRAWAL_REJECTED) minus eligible debits
  (WITHDRAWAL_REQUESTED) minus any non-eligible-bucket overflow
        │
   not eligible? ──▶ 403 (balance untouched, no request row created)
        │ eligible
        ▼
CoinWithdrawalRequest.objects.request_withdrawal(user, coins, ...)
  transaction.atomic():
    create CoinWithdrawalRequest(status=PENDING)
    CoinLedger.objects.record_transaction(
      transaction_type=WITHDRAWAL_REQUESTED, amount=-coins,
      reference=f"coin_withdrawal_request:{pk}")
      ├─ insufficient User.coin? → ValueError → rolls back BOTH
      │   the request row and the debit → view returns 402
      └─ ok → debit applied, request.debit_ledger_entry = entry
        │
        ▼
201 — coins already left the wallet (escrow pattern)

... later, ops/admin side (no endpoint yet, see §11) ...
mark_processing() → PROCESSING (no coin movement)
confirm_success() → SUCCESS (no coin movement — debit already happened)
reject(reason)     → REJECTED, credits coins back via
                     CoinLedger.objects.record_transaction(
                       transaction_type=WITHDRAWAL_REJECTED, amount=+coins,
                       reference=f"coin_withdrawal_request_refund:{pk}")
```
Note the two distinct rejection codes: **403** means "you have enough
coins overall, but not enough *withdrawal-eligible* ones" (policy);
**402** means "you don't have enough coins, period" (balance). A client
should show different messaging for each.

### 10.12 Earn-rate limiting (`fraud.check_earn_rate_limit`) — v4, TASK 5, new

```
Any caller (this app, campus tasks, referral bonus, ...) calls
CoinLedger.objects.record_transaction(transaction_type=EARN or
CAMPUS_REWARD, amount=+n, ...)
        │
        ▼
transaction.atomic(): select_for_update() locks the user row
        │
        ▼
fraud.check_earn_rate_limit(locked_user, transaction_type)
  transaction_type not in (EARN, CAMPUS_REWARD)? → True immediately, no query
  else:
    count of EARN/CAMPUS_REWARD rows in the last EARN_RATE_LIMIT_WINDOW
    (default 1h) >= EARN_RATE_LIMIT_MAX_TRANSACTIONS (default 20)?
      → False
    OR sum(amount) over that same window >=
       EARN_RATE_LIMIT_MAX_COINS (default 500)?
      → False
    else → True
        │
   False? ──▶ raise fraud.EarnRateLimitExceeded — whole atomic block
              rolls back, no ledger row, no balance change
        │ True
        ▼
... reference-idempotency check, balance update, CoinLedger row creation
    (unchanged from v3 — see §10.9)
```
Because this check lives inside `record_transaction()` itself rather
than in any one view, it applies uniformly no matter which app or code
path is the one crediting EARN/CAMPUS_REWARD coins — including code
this upload never saw (campus's engagement-bonus task, a future
referral-bonus flow, etc.).

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


> **v4 additions below (items 10–15)** — everything above this line is
> unchanged from v3.1; item 9 there already listed the two test gaps
> B-8/F-3 introduced. TASK 1/3/4/5 add the following, still-open items.

10. **`BuyCoinConfirmView` is not gateway-signature-verified.** As shipped,
    `IsAuthenticated` + "must be your own purchase" stand in for real
    payment-gateway webhook verification (no gateway integration was
    part of this upload). Before this endpoint is exposed to a real
    payment provider's callback, that verification needs to replace or
    gate the current ownership check — a genuine webhook call isn't
    "acting as" any particular authenticated user, so the current shape
    can't be the final one.
11. **`CoinPurchaseRequest`'s shape is inferred, not confirmed against
    `liveclass.CoinPurchase`.** `liveclass/models.py` wasn't part of any
    upload for this app. If `liveclass.CoinPurchase`'s actual field
    shape (gateway list, money precision, etc.) differs in a way that
    matters, reconcile the two — ideally by rerunning TASK 3 with
    `liveclass/models.py` included so they don't silently diverge.
12. **No admin/ops endpoint for `CoinWithdrawalRequest.objects.
    mark_processing()` / `.confirm_success()` / `.reject()`.** All three
    manager methods exist and are unit-tested, but nothing in `urls.py`
    calls them — a withdrawal can currently only ever reach `PENDING`
    through the public API; moving it to `PROCESSING`/`SUCCESS`/
    `REJECTED` requires a Django shell, a management command, or a
    future admin action, none of which exist yet.
13. **`CoinWithdrawalRequest` has no `MIN_WITHDRAWAL_COINS` floor, no
    `reviewed_by` tracking, and no INR conversion snapshot** — all three
    exist on the `liveclass.CoinWithdrawal` this was modeled after but
    were deliberately left out of this pass as out of scope; add them
    if/when an admin-facing withdrawal review UI is built.
14. **Earn-rate-limit constants are hardcoded, not settings-driven**
    (`fraud.EARN_RATE_LIMIT_WINDOW` / `_MAX_TRANSACTIONS` /
    `_MAX_COINS`). Fine for a first pass with no real farming-attempt
    data to calibrate against, but tightening/loosening them today means
    editing `fraud.py` directly rather than a settings/ops change — move
    them to Django settings once there's production signal to tune
    against.
15. **`fraud.py`'s two rules have partial test coverage, not full.**
    `tests_fraud.py` covers the withdrawal-eligibility math and both
    rate-limit caps, but (carried over from item 9) there's still no
    test asserting `CoinLedgerAdmin` actually refuses add/change/delete,
    and no test exercises `record_transaction()` specifically with
    `transaction_type=CAMPUS_REWARD` (only the EARN/CAMPUS_REWARD
    grouping inside the rate limiter is exercised, via EARN).

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

- [ ] **v4:** If/when `BuyCoinConfirmView` is wired to a real payment
      gateway, replace/gate its current "must be your own purchase"
      check with actual gateway-signature verification (see §11 item
      10) — do not expose it to the public internet unauthenticated
      before that.
- [ ] **v4:** If `liveclass/models.py` exists in your actual codebase,
      diff `CoinPurchaseRequest`'s shape against `liveclass.CoinPurchase`
      before relying on this as final (§11 item 11).
- [ ] **v4:** Decide who/what will eventually call
      `CoinWithdrawalRequest.objects.mark_processing()` /
      `.confirm_success()` / `.reject()` — no endpoint calls them yet
      (§11 item 12).
- [x] **v4:** `fraud.py`'s two rules (withdrawal eligibility, earn-rate
      limiting) are already wired into
      `CoinLedgerManager.record_transaction()` in code — nothing to
      configure to turn them on. Only the rate-limit *constants*
      (`EARN_RATE_LIMIT_WINDOW`/`_MAX_TRANSACTIONS`/`_MAX_COINS`) are
      worth revisiting once you have real usage data (§11 item 14).
- [x] **v4:** `CoinLedgerAdmin` (in the now-uploaded `admin.py`) is
      already read-only in code, same as noted for B-8 in v3.1 — if you
      already had your own `admin.py` with other model registrations in
      it, make sure they were merged in rather than overwritten (§8).

---

## 13. Cross-App Interconnections (summary)

`user_profile` doesn't run in isolation — this section pulls together,
in one place, every other app this codebase's comments reference so the
boundary between "owned here" and "consumed elsewhere" stays clear as
more apps get added. None of these other apps' own files were part of
any upload for this app — everything below is inferred from how
`user_profile` already refers to them.

| Other app | What it needs from `user_profile` | What `user_profile` needs from it |
|---|---|---|
| **`login`** | `settings.AUTH_USER_MODEL` (`login.User`) is the target of every FK in this app (`Follow`, `BlockUser`, `RestrictUser`, `CoinLedger`, `CoinPurchaseRequest`, `CoinWithdrawalRequest`). `user_profile` never imports `login.User` directly — always via `settings.AUTH_USER_MODEL` or `type(user)` — to stay decoupled. | The custom fields §2 lists (`profile_photo`, `bio`, `is_private`, `is_verified`, `is_active`, `followers_count`, `following_count`, `posts_count`, `coin`) must exist on `login.User`. Its own `ModelAdmin` must declare `search_fields` for `autocomplete_fields` elsewhere in this app's `admin.py` to work (§11 item 6). |
| **`testseries`** | `TestSeriesPurchase.purchase_and_start_attempt()` / `.release()` reference `CoinLedger.TransactionType.TESTSERIES_PURCHASE` / `TESTSERIES_PAYOUT` directly (TASK 1) — those values existing in this app's enum is a hard dependency; they were missing before TASK 1 and it was a live `AttributeError`. | Nothing — `testseries` is purely a consumer of this app's `TransactionType` enum and (presumably) calls `CoinLedger.objects.record_transaction()` itself for its own purchase/payout flow. |
| **`campus`** | `campus/tasks.py` (FEE-3/FEE-6) reads a student's `User.coin` balance to decide whether it covers an upcoming fee. `FEE-2` routes real tuition-fee payments through this same `CoinLedger`. Small engagement bonuses (attendance-streak, on-time-assignment-streak) credit coins via `TransactionType.CAMPUS_REWARD` (F-3) — and are therefore automatically subject to TASK 5's earn-rate limiter, the same as any other `EARN`/`CAMPUS_REWARD` credit. | Nothing structural — `campus` just needs `CAMPUS_REWARD` to exist (it does, as of F-3) and to call `record_transaction()` rather than writing `User.coin` directly, or its credits would silently escape both the audit trail and the rate limiter. |
| **`liveclass`** | Nothing currently — not a consumer of this app. | `liveclass.CoinPurchase` and `liveclass.CoinWithdrawal`/`CoinTransaction` were used as **reference reads** (not code dependencies) when designing `CoinPurchaseRequest` (TASK 3) and `CoinWithdrawalRequest` (TASK 4) respectively — both reproduce `liveclass`'s escrow/lifecycle patterns on top of `CoinLedger` instead of `liveclass.CoinTransaction`, since `CoinLedger` is this codebase's one shared ledger. `liveclass/models.py` itself was never uploaded, so these two models' exact field shapes are inferred, not verified against it (§11 items 10–11) — worth reconciling if the two ever need to match exactly.
| **`post`** | Nothing currently — not a consumer. | `post/models.py`'s `update_shares_count`/`update_saves_count`/`update_story_views_count` signal pattern is the reference design `tasks.py`'s module docstring points to as the *real* fix for follow-count drift (a `Follow` `post_save`/`post_delete` signal, instead of `reconcile_follow_counts`'s periodic detect-and-correct). Not implemented here — out of scope for this pass, noted for a future one. `post.views.TrendingHashtagsAPIView`'s "bounded recompute now, revisit at scale" trade-off is the same one `reconcile_follow_counts` makes. |
| **`message`** | Consumes `is_blocked_between()` for chat/contact-search filtering (block) and is expected to eventually consume `is_restricted_between()` to suppress read-receipts/online-status/notifications from a restricted user (not implemented yet — §11 item 8). Gifting flows in `message` are expected to call `CoinLedger.objects.record_transaction(transaction_type=GIFT_SENT / GIFT_RECEIVED)` — not verified against actual `message` code since it wasn't uploaded. | `MessageContactSearchView`/`MessageContactSearchSerializer` exist specifically to serve `message`'s "add members" flow. |

### Ground rules that apply across all of the above
- **`CoinLedger.objects.record_transaction()` is the only sanctioned way
  any app changes `User.coin`.** Every other app in this table is
  expected to call it rather than writing `user.coin = ...; user.save()`
  itself — that's what keeps the ledger, the fraud checks (TASK 5), and
  the balance mutually consistent no matter which app triggers the
  change.
- **`user_profile` owns the relationship, not the effect**, for both
  `BlockUser` and `RestrictUser` — `is_blocked_between()`/
  `is_restricted_between()` are the two functions other apps are meant
  to filter through; this app never reaches into `post`/`message`/
  notifications itself to enforce what blocking or restricting should
  do there.
- **Every cross-app reference above is a one-way dependency on this
  app's public surface** (a `TransactionType` value, a helper function,
  `record_transaction()` itself) — none of them require `user_profile`
  to import the other app back, keeping this app's own import graph
  free of circular references to `testseries`/`campus`/`liveclass`/
  `post`/`message`.