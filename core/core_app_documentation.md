# `core` App — Documentation

> Ye document `core` Django app ke andar jo bhi kaam hua hai (tasks 28, 42-48) uska single source of truth hai. Koi bhi is app pe kaam continue kare, sabse pehle ye file padhe — har file ka purpose, har function ka contract, aur sab `ASSUMPTION` markers ek jagah pe hain.

---

## 1. `core` app kyu bana?

Pehle notifications aur classroom↔chat coupling `liveclass` app ke andar bikhri hui thi. `core` app do cheezein centralize karta hai:

1. **Notification system** (task 42-48) — `Notification` + `NotificationPreference` models, unko banane ka service (`create_notification`), burst events ke liye batching (`create_batched_notification`), aur inhe expose karne wale REST endpoints.
2. **Classroom ↔ Chat bridge** (task 28) — `liveclass` app (classrooms) aur `message` app (chat groups) ke beech ka SAARA coupling isi ek file (`classroom_chat_bridge.py`) se guzarta hai, taaki dono apps ek dusre ke internal models seedhe import na karein.

**Design principle jo har jagah repeat hoti hai:** `core` ek neutral, app-agnostic layer hai — `liveclass`, `message`, aur future Phase 5 (Posts/Follow/Like) sab isi se hokar guzarte hain, ye kisi ek app ka internal detail nahi jaanta.

---

## 2. File-by-file map

| File | Kya karta hai |
|---|---|
| `models.py` | `Notification`, `NotificationPreference` — dono models `liveclass` se yahan move hue (task 42) |
| `services.py` | `create_notification()` + `create_bulk_notifications()` — sirf bell-row(s) banate hain, push kabhi nahi bhejte |
| `notification_batching.py` | `create_batched_notification()` — burst events (5 likes ek saath) ko ek notification me collapse karta hai. **⚠️ Uploaded content is broken — see §5.** |
| `classroom_chat_bridge.py` | `liveclass` ↔ `message` app ke beech ka pura coupling — 8 documented sync functions + `resolve_parent_from_token()` (Task 5, parent-portal auth — see §6) |
| `serializers.py` | **NEW row (wasn't in this table before)** — real DRF `ModelSerializer`s (`NotificationSerializer`, `NotificationPreferenceSerializer`), replacing this doc's own original `to_dict` suggestion — see §7 |
| `views.py` | `NotificationViewSet` (list/retrieve/destroy + custom actions) + `NotificationPreferenceView` |
| `urls.py` | Router wiring — root urlconf me `include("core.urls")` karna hai |
| `admin.py` | **✅ NOT empty anymore** — `NotificationAdmin` + `NotificationPreferenceAdmin` dono registered hain, see §3b |
| `apps.py` | Standard `CoreConfig` |
| `tests.py` | `Notification`/`NotificationPreference`/`create_notification`/viewset ke tests |
| `test_notification_batching.py` | `create_batched_notification` ke 6 tests (contract confirm karte hain — see §5) |
| `test_parent_bridge.py` | **NEW row** — `resolve_parent_from_token()` (Task 5) ke 10 tests, `message.models.ParentAccessCode`/`ParentToken` ke against — see §6 |

---

## 3. `models.py` — `Notification` & `NotificationPreference`

### ⚠️ Sabse important warning — **RESOLVED, real implementation aa chuki hai**
Ye section pehle assume karta tha ki `core/models.py` khaali hai aur design suggestion deta tha. Real `models.py` ab exist karta hai (task 42 already applied) — neeche di gayi field table ab **implementation se confirmed** hai, suggestion nahi.

### `Notification`

| Field | Type | Notes |
|---|---|---|
| `id` | **AutoField (integer PK)** — ⚠️ **deviation from this doc's original UUID suggestion** | Real `models.py` ne deliberately original integer PK rakha, UUID pe convert nahi kiya — apna reasoning module docstring me deta hai: PK column type badalna apne aap me ek risky schema migration hai (har existing row ko backfilled UUID chahiye hoga PK drop/replace se pehle), aur ye task 42's "zero-data-copy" guarantee todta. Agar UUID PK abhi bhi chahiye, wo apna alag follow-up migration ho, staging DB pe test karke, is move se bilkul separate. |
| `recipient` | FK → `AUTH_USER_MODEL` (`login.User`) | `related_name="notifications"`, `on_delete=CASCADE` |
| `notif_type` | CharField(**max_length=30**, not 32) | `NotifType` choices (neeche, ab **poori confirmed list**) |
| `title` | CharField(**max_length=150**, not 255) | |
| `message` | TextField, blank ok | Task 44 me `CharField(255)` se widen kiya gaya — message-app chat text length-capped nahi hai, `CharField(255)` Postgres pe hard-fail karta longer message pe. Real `AlterField` migration (`0002_widen_message_and_add_data.py`) — varchar→text Postgres pe table-rewrite-free hai. |
| `classroom` | FK → `liveclass.Classroom` | nullable, `SET_NULL` — sirf liveclass-specific types ke liye |
| `session` | FK → `liveclass.ClassSession` | nullable, `SET_NULL` |
| `data` | JSONField (dict, `default=dict`) | task 44: generic free-form context (e.g. `conversation_id`, deep-linking) jo liveclass FKs me fit nahi hota — additive column, same 0002 migration |
| `is_read` | BooleanField | koi standalone `db_index=True` nahi — composite `Meta.indexes` (`recipient`, `is_read`, `-created_at`) hi per-user unread queries cover karta hai |
| `read_at` | DateTimeField | nullable, sirf `mark_read()` se set hota hai |
| `created_at` | auto (`auto_now_add`) | indexed (composite index ka part), `ordering = ["-created_at"]` |
| — | **no `updated_at`** | Original liveclass model me bhi nahi tha — `mark_read()` hi iski sirf mutation hai, alag `updated_at` redundant hoti (batching cache khud apna staleness track karta hai, is model pe nahi) |

**`Meta.db_table` — DELIBERATELY pinned, ab implementation-confirmed:**
- `Notification` → `db_table = "liveclass_notification"`
- `NotificationPreference` → `db_table = "liveclass_notificationpreference"`

Ye task 42 ke migration (`core/migrations/0001_move_notification_models.py`) ko pure `SeparateDatabaseAndState` banata hai — sirf Django ORM state `liveclass`→`core` move hota hai, physical Postgres table na move hoti hai, na rename, na row-copy. **In values ko real ALTER/RENAME TABLE migration ke bina mat badlo.** (Real DB confirm karna abhi bhi bacha hai — §9.)

**`NotifType` enum — ✅ AB POORI CONFIRMED LIST HAI (pehle sirf partial thi):**
```
JOIN_REQUEST_RECEIVED, JOIN_REQUEST_ACCEPTED, JOIN_REQUEST_REJECTED,
PASS_REFUNDED, SESSION_REMINDER, ASSIGNMENT_GRADED, QUERY_ANSWERED,
CERTIFICATE_ISSUED, WAITLIST_PROMOTED, CLASSROOM_FLAGGED, NOTICE_POSTED,
SESSION_LIVE, SESSION_CANCELLED, ASSIGNMENT_POSTED, SUBMISSION_RECEIVED,
STAFF_ADDED, REVIEW_POSTED, REPORT_REVIEWED, WITHDRAWAL_APPROVED,
WITHDRAWAL_REJECTED, WITHDRAWAL_PAID, CLASSROOM_SHARED,
PASS_GIFT_RECEIVED, PASS_GIFT_CLAIMED, PASS_AUTO_RENEWED,
AUTO_RENEW_FAILED, PASS_GIFT_EXPIRED, GENERIC,
CHAT_MESSAGE, MENTION, INCOMING_CALL   # task 44 — message app ke naye types
```
31 values total (28 original liveclass types + 3 message-app types). Purani list me `PASS_AUTO_RENEWED`/`AUTO_RENEW_FAILED`/`JOIN_REQUEST_RECEIVED`/`JOIN_REQUEST_REJECTED`/`QUERY_ANSWERED`/`SESSION_REMINDER`/`SESSION_CANCELLED`/`ASSIGNMENT_POSTED`/`SUBMISSION_RECEIVED`/`STAFF_ADDED`/`REVIEW_POSTED`/`REPORT_REVIEWED`/`WITHDRAWAL_*`/`GENERIC` missing the — ab sab yahan hai, ASSUMPTION marker hata diya gaya.

**`MESSAGE_APP_TYPES`** — `frozenset({CHAT_MESSAGE, MENTION, INCOMING_CALL})`, ek **class attribute directly `Notification` model pe** (⚠️ pehle iss doc ke §7 me `_MESSAGE_APP_TYPES` naam se `views.py` me hone ka zikr tha — real jagah ye hai, taaki `views.py` **aur** `serializers.py` dono same set import kar sakein, do copies maintain na karni pade — task 46). **Naya message-app type add karte waqt yahi ek jagah update karni hai.**

**Methods:**
- `mark_read()` — idempotent, `is_read=True` + `read_at=now()` set karta hai, sirf tab save karta hai jab already read na ho.

### `NotificationPreference`

| Field | Notes |
|---|---|
| `user` | OneToOne → `AUTH_USER_MODEL`, `related_name="notification_preference"` |
| `push_enabled` / `email_enabled` | default `True` |
| `sms_enabled` / `whatsapp_enabled` | default `False` |
| `muted_types` | JSONField list — NotifType values jo user kabhi nahi sunna chahta, **kisi bhi channel pe** (bell row bhi nahi) |
| `digest_frequency` | `off` / `daily` / `weekly` |
| `last_digest_sent_at` | nullable |

**Methods:**
- `for_user(cls, user)` — classmethod, `get_or_create` wrapper, sab jagah safe call karne ke liye.
- `allowed_channels_for(notif_type)` — agar type muted hai to `[]`, warna jo channels `True` hain unki list (`push`/`email`/`sms`/`whatsapp`).

---

## 3b. `admin.py` — ✅ ab register hai (pehle "khaali" documented tha)

Pehle iss doc me likha tha ki `admin.py` khaali hai, kuch register nahi hua. **Real file me dono model registered hain:**
- `NotificationAdmin` — `list_display = (recipient, notif_type, title, classroom, session, is_read, created_at)`, `list_filter = (notif_type, is_read)`, `search_fields = (recipient__username, title, message)`, `autocomplete_fields = [recipient, classroom, session]`, `created_at` readonly, `date_hierarchy = created_at`.
- `NotificationPreferenceAdmin` — `list_display = (user, push_enabled, email_enabled, sms_enabled, whatsapp_enabled, digest_frequency)`, filters on all 5 channel/frequency fields, `search_fields = (user__username,)`, `updated_at` readonly.

Update this file's own earlier claim: `admin.py` was **not** left empty.

---

## 4. `services.py` — `create_notification()` + `create_bulk_notifications()`

```python
create_notification(recipient, notif_type, title, message="", *, classroom=None, session=None, data=None)
```

- **Sirf** `Notification` row create karta hai — bell icon ka data.
- **Kabhi push nahi bhejta.** (Regression test `test_never_sends_a_push_itself` isi contract ko guard karta hai.)
- `recipient` User instance **ya** raw user id, dono accept karta hai — `message/push_utils.py` ke call-sites ke paas sirf raw ids hote hain, hydrated User objects nahi, to har push call pe ek extra query nahi lagti.
- Apni khud ki exceptions swallow + log karta hai, raise nahi karta — ek notification save fail hone se koi bhi real transaction (coin charge, grade, accept(), incoming chat) rollback/fail nahi hona chahiye. Fail hone pe `None` return karta hai.
- Har real call-site pe **do calls saath saath** hoti hain:
  ```python
  create_notification(student, "pass_auto_renewed", title, message, classroom=classroom)
  send_notification(student, title, message, channel="push", data={...})   # separate, liveclass.notifications
  ```
- **Task 44 ka pending kaam:** `message/push_utils.py` ke andar `send_chat_message_push` / `send_incoming_call_push` / `send_mention_push` abhi sirf push bhejte hain, bell-row nahi banate. In sab call-sites pe `create_notification(...)` ka matching call add karna hai (details `message/views_PATCH_bell_rows_for_push.md` me — agar wo file exist nahi karti, isse pehle bana lena).

### `create_bulk_notifications()` — **NEW, ab implement ho chuka hai (pehle iss doc me mention hi nahi tha)**

```python
create_bulk_notifications(recipients, notif_type, title, message="", *, classroom=None, session=None, data=None)
```

- Fan-out case ke liye (e.g. ek urgent Notice enrolled student sab ko) — `bulk_create(batch_size=500)` se ek hi INSERT, N alag calls ki jagah.
- `recipients` koi bhi iterable ho sakta hai — User instances **ya** user ids, mix bhi. Deduplicate karta hai (`{getattr(r, "id", r) for r in recipients}`) — ek teacher/co-teacher jo staff bhi hai aur enrolled student bhi, usko do baar notification nahi milegi.
- Empty/`None`-only recipient set pe silently no-op (`return` early, koi query nahi chalti).
- Yahan bhi exceptions swallow + log hote hain, `create_notification()` jaisa hi fail-safe contract.

---

## 5. `notification_batching.py` — `create_batched_notification()`

**Problem jo solve karta hai:** agar ek post ko 5 logo ne 10 second ke andar like kiya, to recipient ko "New like" x5 push nahi milne chahiye — sirf ek "5 people liked your post" milna chahiye.

### Kaise kaam karta hai (sliding window, Redis cache-based)

- **Cache key** = `(recipient_id, notif_type, target_id)` — har target (post/profile/etc) apna alag batch rakhta hai.
- **Pehla event** window me:
  - Real `Notification` row banta hai.
  - Push turant bhejta hai (`send_push_fn`, agar diya ho) — pehla like recipient ko turant pata chalna chahiye.
  - Cache window khulta hai (`window_seconds`, default 120s) jisme notification id + actor set store hota hai.
- **Baad ke events** usi window me:
  - Actor set me add hota hai (repeat actor se count nahi badhta — dobara like/unlike karne se inflate nahi hoga).
  - **Same** Notification row **update** hoti hai (naya row nahi, naya push nahi).
- **Sliding window:** har naya actor window ko `window_seconds` se **abhi se** extend karta hai (fixed window se nahi) — isliye ek steady trickle of likes hamesha ek hi notification me batch hote rehte hain, jab tak activity na ruke. Ye hi asli spam-prevention hai kisi popular cheez ke liye.

### Signature
```python
create_batched_notification(
    *, recipient, notif_type, actor, target_id,
    title_fn,                      # (actor_count, actors) -> str, HAR call pe chalta hai
    message_fn=None,               # (actor_count, actors, latest_actor) -> str, optional
    classroom=None, session=None,
    extra_data=None,
    window_seconds=120,
    send_push_fn=None,             # (recipient, title, message, data) -> None, sirf 1st event pe
)
```

- `target_id` **hamesha zaroori hai** — single-target types (jaise "follow") ke liye bhi recipient ka apna id pass karo (per-recipient ek batch).
- Deliberately **decoupled** kisi specific push function se — caller (liveclass/message/future Posts app) apna `send_push_fn` pass karta hai.
- Agar cache kehta hai row hai par DB me nahi milti (user ne beech me delete kar diya), to gracefully fresh batch start ho jata hai (exception nahi).

### ⚠️ Open item
`message/push_utils.py` (jisme already ek chat-push debounce mechanism hai jise "mirror" karna tha) **kabhi upload nahi hua**. Iska sirf evidence ek comment hai (`message/views.py` → `StudyRoomJoinView`: "same pattern as the debounce state in `push_utils.py`"). Is module ne wahi cache-based debounce shape **reimplement** kiya hai apne taur pe. **Agar `push_utils.py` mil jaye, isko uske real debounce helper se replace karna hai** taaki do parallel implementations na rahe.

### 🔴 CRITICAL — uploaded `notification_batching.py` me actual implementation missing hai

Jo `notification_batching.py` upload hui, uska content ye **nahi** hai jo upar describe kiya gaya — uske andar `create_batched_notification()` ki koi definition hi nahi hai. Uske bajaye us file ke andar ek Django `TestCase` class (`NotificationBatchingTests`) baithi hai, aur woh **khud apne hi module se import kar rahi hai**:

```python
# notification_batching.py ke andar, literally:
from .notification_batching import create_batched_notification
```

Ye ek self-import hai — jis module ke andar ye line hai, wahi module `create_batched_notification` ko khud se import karne ki koshish kar raha hai, jabki us function ki definition file me kahin hai hi nahi. Django app-load pe ye `ImportError`/circular-import crash dega, kyunki jab tak module ka poora load complete nahi hota, `create_batched_notification` uske apne namespace me exist nahi karta.

**Sabse zyada mumkin wajah:** `test_notification_batching.py` ka content (ya uska ek earlier/duplicate draft) galti se `notification_batching.py` ke path pe save ho gaya — dono files ka test-class shape bahut milta-julta hai (`NotificationBatchingTests` vs `test_notification_batching.py`'s tests), bas assertions/test-count thoda alag hai.

**Impact:**
- `create_batched_notification()` ka **real implementation is upload me kahin nahi hai** — na sliding-window Redis-cache logic, na `window_seconds` handling, na `send_push_fn` dispatch. Upar diya poora "Kaise kaam karta hai" section is doc ka **design contract** hai (test files se confirm hota hai — neeche dekho), implementation ka description nahi.
- `test_notification_batching.py` (jo real hai, alag file) `from .notification_batching import create_batched_notification` karti hai — agar `notification_batching.py` sach me is broken state me deploy hui, to **ye test suite bhi load-time pe crash karegi**, koi bhi test chalne se pehle.
- `test_parent_bridge.py`, `tests.py`, aur `services.py` is bug se affected nahi hain — unka apna import chain saaf hai.

**Fix:** `create_batched_notification()` ka asli implementation (jo iss section me design-level pe already describe hai) `notification_batching.py` me likho/restore karo; `NotificationBatchingTests` wala test-class content wahan se hata ke sirf `test_notification_batching.py` me rehne do.

**Contract jo test files confirm karte hain (dono `notification_batching.py`'s galat content aur `test_notification_batching.py` ke test-cases se ek jaisa nikalta hai) — implementation isi ko satisfy karni chahiye:**
- Cache key = `(recipient_id, notif_type, target_id)`.
- Pehla event → `Notification` row + `send_push_fn` call (agar diya ho).
- Same window me doosra event (chahe naya actor ho ya repeat actor) → **wahi row update**, koi naya push nahi.
- Repeat actor → actor-count nahi badhta (title `_title_fn(count, actors)` se banta hai, "Someone liked" vs "N people liked").
- Alag `target_id` → alag, independent batch.
- Window expire ho jaye (ya cache key clear ho jaye) → agla event ek **fresh** row + fresh push deta hai.
- Agar batched row cache ke expect karne ke bawajood DB se delete ho chuki ho (user ne beech me clear kar diya) → gracefully naya batch start, `DoesNotExist` raise nahi karta.

---

## 6. `classroom_chat_bridge.py` — Liveclass ↔ Message bridge

**Golden rule:** `liveclass/signals.py`, `liveclass/views.py`, aur `notify_session_live` task — koi bhi seedha `message.models` / `message.services` import NAHI karta. Sab is ek file ke **9 functions** se guzarta hai (pehle 8 the — Task 5 ne `resolve_parent_from_token()` add ki, neeche §6.1).

**Kyu:**
1. `liveclass` ko `message` app ke internal shape (Group ka structure, GroupMember role enum) se decouple rakhta hai.
2. Kal ko chat-backend badle (naya Group model / alag app) to sirf ye ek file badalni padegi.

**Error-handling pattern:** Har function **best-effort** hai — agar classroom ke paas `chat_group_enabled=False` hai (group kabhi bana hi nahi), sab sync functions chup-chaap **no-op** ho jaate hain, exception nahi throw karte (kyunki ye zyadatar signal handlers se call hote hain — inhe fail nahi hone dena). **Do exceptions is pattern se:** `create_classroom_group()` (explicit teacher action, signal nahi — REAL errors raise karta hai) aur `resolve_parent_from_token()` (§6.1 — ye ek live room ka access-gate hai, "fail open" yahan galat hoga, isliye kabhi exception nahi UGALTA lekin kabhi silently allow bhi nahi karta — har unexpected error `None` = deny).

### Internal helpers

- **`_get_group_for_classroom(classroom)`** — classroom se linked `Group` nikalta hai, ya `None`. Kabhi exception nahi (best-effort ka base). Agar `chat_group_enabled=True` hai par Group missing hai (kisi ne seedha delete kar diya), sirf `logger.warning` karta hai.
- **`_post_system_message(group, sender, text)`** — group ki conversation me system message post karta hai. Best-effort — fail hone pe sirf `logger.exception`.
- **`_classroom_cover_image_url(classroom)`** — ✅ **simplified, ab ImageField-only confirmed** (pehle dono `ImageField`/`URLField` handle karne ki koshish thi). `cover_image` falsy ho (koi file attached nahi, `null=True, blank=True`) to `None` return karta hai; warna `.url`. Dual-type handling ab zaroori nahi (§ ASSUMPTIONS neeche dekho — resolved).

### 8 Sync Functions (pehle se documented, ab field-names verified)

| # | Function | Kab call hoti hai | Behavior |
|---|---|---|---|
| 1 | `create_classroom_group(classroom, actor)` | Teacher ka explicit "Create Group" action (`POST /liveclass/classrooms/<id>/create_group/`) | **Idempotent** — group already hai to wahi return. `actor` teacher na ho to `ValueError`. Members = teacher + saare ACCEPTED join-requests wale students + saare `ClassroomStaff`. Staff ko `GroupMember.Role.MODERATOR` pe promote karta hai (`create_group()` sirf ADMIN/MEMBER deta hai). Poora kaam ek `transaction.atomic()` block me. Success pe `post_welcome_message()` bhi call hoti hai. **Sirf ye function real errors raise karta hai.** |
| 2 | `sync_membership_on_join_accept(classroom, student)` | Join request ACCEPT hone pe, **ya** session-waitlist se promote hone pe (dono call-sites yahi function reuse karte hain) | Student ko group me add karta hai (`add_members_to_group`, `actor=None` = system call). No-op agar group hi nahi. |
| 3 | `sync_membership_on_removal(classroom, student, reason="")` | Kick / ban / refund | Student ko group se remove karta hai. `reason` sirf logging ke liye. |
| 4 | `promote_to_moderator(classroom, user)` | Naya co-teacher/`ClassroomStaff` row bana | Pehle group me add karta hai (agar already member nahi), phir role `MODERATOR` set karta hai. |
| 5 | `sync_group_metadata(classroom)` | Classroom ka title/description/cover_image update hua | Group ka `name`/`description`/`photo_url` classroom ke saath sync karta hai. |
| 6 | `archive_group_on_classroom_close(classroom)` | Classroom close/soft-delete | System message post karta hai, WebSocket pe `group_deleted` broadcast karta hai (`chat_{conversation_id}` channel), phir group + conversation dono **soft_delete()** (hard-delete NAHI — recover ho sake grace period me). |
| 7 | `post_welcome_message(classroom)` | Group creation ke turant baad | "chat group ban gaya hai 🎉" wala system message |
| 8 | `post_session_live_announcement(session)` | `notify_session_live` task se | "🔴 Live session shuru ho gaya hai" wala system message |

### ✅ ASSUMPTIONS — RESOLVED (pehle 4 the, ab sab verified)

Ye doc pehle bolta tha `liveclass/models.py`/`liveclass/views.py` upload nahi hue the, isliye field-names best-guess hain. **Ab resolve ho chuka hai** — real `classroom_chat_bridge.py` ka apna module docstring confirm karta hai ("✅ VERIFIED, Task 2 gap-fix pass"):

1. ✅ `ClassJoinRequest(classroom, student, status)` — `ClassJoinRequest.Status.ACCEPTED` filter — **exact match, no change needed**.
2. ✅ `ClassroomStaff(classroom, user, role)` — **exact match, no change needed**.
3. ✅ `classroom.cover_image` — **confirmed always `ImageField`, kabhi `URLField` nahi** — `_classroom_cover_image_url()` isliye simplify ho gaya (upar dekho).
4. ✅ `classroom.description` (TextField, blank=True — **hamesha string, kabhi `None` nahi**, isliye `create_classroom_group()` me `getattr` fallback ki zaroorat nahi thi), `classroom.title`, `classroom.teacher`/`teacher_id`, `classroom.linked_conversation_id`, `classroom.chat_group_enabled` — sab confirmed.

**Koi field-name change nahi hui — jo assume kiya gaya tha wahi real model me nikla.**

### `message` app se dependency (assumed contract — abhi bhi unverified, `message` app iss round upload nahi hui)

`classroom_chat_bridge.py` in cheezon ko `message` app se import karta hai (local imports, module-load pe nahi — cross-app coupling avoid karne ke liye):
- `message.models.Group`, `Message`, `MessageType`, `GroupMember`
- `message.services.create_group`, `add_members_to_group`, `remove_group_member`, `update_group_member_role`
- **(§6.1, naya)** `message.models.ParentAccessCode`, `ParentToken`

Ye sab functions/models `message` app me already exist maane gaye hain — agar signature mismatch ho to yahi jagah check karo.

---

## 6.1. `resolve_parent_from_token()` — **NEW (Task 5), 9th function**

⚠️ **Implementation body iss upload me nahi hai** — `classroom_chat_bridge.py` ke content me sirf 8 sync functions hain, `resolve_parent_from_token` unme nahi. Iska existence aur poora behavioral contract sirf indirectly confirm hota hai:
- `liveclass/permissions.py`'s `HasValidParentSessionToken` isko import + call karta hai (`from core.classroom_chat_bridge import resolve_parent_from_token`) — **see LEARNSCROLL_LIVECLASS.md §6c**.
- `core/test_parent_bridge.py` — 10 dedicated unit tests, jo poora contract exercise karte hain.

**Confirmed contract (tests se, function-body-verify pending):**

```python
resolve_parent_from_token(token: str) -> resolution | None
```

- Input: `X-Parent-Token` header value (ek plain string, `message.models.ParentToken.token`).
- Lookup chain: `ParentToken` (FK → `ParentAccessCode`) dhoondta hai, phir do independent checks:
  1. **Token khud** expired na ho — `ParentToken` ka **rolling 30-day inactivity window** (`last_seen_at` se, `INACTIVITY_TTL_DAYS = 30`) — 31+ din inactive → reject.
  2. **Access code** khud expired na ho — `ParentAccessCode.expires_at`, ek **absolute, independent expiry** khud code pe — token abhi-abhi active hua ho tab bhi, agar code ki apni `expires_at` nikal chuki hai to reject.
- `ParentAccessCode.is_active = False` (revoke) → **saare uss code pe issued tokens ek saath invalid** ho jaate hain — single-code-revokes-all-devices guarantee (CHAT_APP_DOCUMENTATION.md §7.16 ke mutabik).
- Success pe `ParentToken.last_seen_at` **bump/touch** hota hai (rolling window ko accurate rakhne ke liye) — same jaisa `HasValidParentToken` (message app) already karta hai.
- Success pe return: ek resolution object jisme `.student` (linked `login.User`) aur `.parent_access_code` (`ParentAccessCode` instance) — `permissions.py`'s `HasValidParentSessionToken` inhe seedha `request.parent_student`/`request.parent_access_code` pe copy karta hai.
- **Fail-closed on everything** — unknown token, empty/`None` token, expired token, expired code, deactivated code, **ya koi bhi unexpected DB/lookup error** (e.g. ek patched `select_related` jo `RuntimeError` deta hai) — sab `None` return karte hain, kabhi exception raise nahi hota. Ye `create_classroom_group()`-jaisa "real errors raise karo" pattern se **deliberately alag** hai: ye function ek live audio/video room (aur report-card/query-thread data) ka access-gate hai, isliye "fail open, log kar do" wala best-effort pattern galat hoga — har unexpected condition **deny** honi chahiye, silently allow nahi.
- Do independent parent/student pairs ke tokens kabhi cross-resolve nahi karte — `test_second_students_parent_token_never_resolves_to_first_student` isko specifically guard karta hai.

**Open item:** function ka real code body abhi tak kisi upload me nahi mila — agla pass jab `classroom_chat_bridge.py` ka poora, latest version aaye, is contract ko line-by-line verify karo (khaas kar fail-closed try/except ka exact scope, aur `select_related` kis field pe hai).

---

## 7. `views.py` + `urls.py` + `serializers.py` — REST API

### Endpoints

| Method | Path | Kaam |
|---|---|---|
| GET | `notifications/` | List (paginated via `?limit=&offset=`, default 30, max 100). Response me `count`, `unread_count`, `results`. Optional `?source=message` / `?source=liveclass` filter. |
| GET | `notifications/{id}/` | Single notification |
| DELETE | `notifications/{id}/` | Delete |
| GET | `notifications/unread-count/` | `{"unread_count": N}` |
| POST | `notifications/{id}/mark-read/` | Mark ek read |
| POST | `notifications/mark-all-read/` | Sab read, `{"marked_read": N}` |
| GET/PATCH | `notification-preferences/me/` | Apni preference (get-or-create) |

**Root urlconf me wire karna hai** (abhi tak nahi kiya gaya):
```python
# project/urls.py
urlpatterns = [
    ...
    path("core/", include("core.urls")),   # prefix apni marzi se choose karo
]
```

**⚠️ IMPORTANT cleanup jab wire kar do:** `liveclass/urls.py` me agar `notifications` router aur `notification-preferences/me/` already registered hain, to unhe **hata do**. Warna do endpoints ek hi table serve karenge — exactly wo split jo ye poora task remove karna chahta tha.

### `NotificationSerializer` — ✅ **UPDATED: ab real DRF `ModelSerializer` hai, `to_dict` nahi**

Ye doc pehle bolta tha ki `NotificationSerializer` deliberately plain `to_dict` staticmethod hai (`ModelSerializer` nahi). **Ab aisa nahi hai** — real `serializers.py` project ke already-established `ModelSerializer` convention (`liveclass/serializers.py` jaisa) follow karta hai, jaisa iss doc ka §7 pehle hi suggest karta tha. Behavior same hai, bas implementation ab real serializer class hai:

- `classroom_id = PrimaryKeyRelatedField(source="classroom", read_only=True)`, `session_id = PrimaryKeyRelatedField(source="session", read_only=True)` — FK ko id ke roop me expose karte hain bina nested object serialize kiye.
- `source = SerializerMethodField()` — `get_source(obj)` return karta hai `"message"` agar `obj.notif_type` model ke `Notification.MESSAGE_APP_TYPES` (§3 dekho) me ho, warna `"liveclass"`. **Koi alag `_MESSAGE_APP_TYPES` set `views.py` ya `serializers.py` me nahi hai** — dono jagah `Notification.MESSAGE_APP_TYPES` seedha model se import hota hai, taaki naya message-app type add karte waqt sirf **ek** jagah (`models.py`) update karni pade.
- `Meta.fields = ["id", "notif_type", "title", "message", "classroom_id", "session_id", "data", "is_read", "read_at", "created_at", "source"]`, aur `read_only_fields = fields` — **poora serializer read-only hai**, kyunki rows sirf server-side (`create_notification()` / `create_batched_notification()`) se banti hain, koi bhi client-writable field nahi (mark-read actions bhi bina request body ke chalte hain, view-level actions se).

### `NotificationPreferenceSerializer` — **NEW subsection (pehle iss doc me detail nahi thi)**

`GET`/`PATCH` dono ke liye same serializer istemal hota hai:
- `Meta.fields = ["push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled", "muted_types", "digest_frequency", "last_digest_sent_at", "updated_at"]`.
- `read_only_fields = ["last_digest_sent_at", "updated_at"]` — baaki sab (`push_enabled` se lekar `digest_frequency` tak) client `PATCH` kar sakta hai.
- `validate_muted_types(value)` — pehle check karta hai `value` ek list hai (warna `ValidationError`), phir har entry ko `Notification.NotifType.values` ke against validate karta hai — koi bhi unknown type string ho to `ValidationError(f"Unknown notification type(s): ...")`. Isi validation ko `tests.py::test_patch_rejects_unknown_muted_type` guard karta hai.

### Pagination
Simple limit/offset — DRF ka `PageNumberPagination` istemal nahi kiya (project ka `REST_FRAMEWORK.DEFAULT_PAGINATION_CLASS` pata nahi tha). Agar project me standard pagination class hai, isse replace kar sakte ho.

### `tests.py` ke `NotificationViewSetTests` me ek path assumption hai:
```python
response = self.client.get("/core/notifications/unread-count/")
```
Ye path root urlconf me diye gaye prefix pe depend karta hai — **jaise hi `core.urls` ko wire karo, ye test path bhi confirm/update karo.**

---

## 8. Cross-app dependency graph

```
liveclass  ──uses──▶  core.services.create_notification
liveclass  ──uses──▶  core.classroom_chat_bridge (8 functions)
                            │
                            ├──local-import──▶ message.models (Group, Message, MessageType, GroupMember)
                            └──local-import──▶ message.services (create_group, add_members_to_group,
                                                                   remove_group_member, update_group_member_role)

message    ──(pending, task 44)──▶  core.services.create_notification   [bell-row for FCM pushes]

core.models.Notification  ──FK (SET_NULL)──▶  liveclass.Classroom, liveclass.ClassSession
```

`liveclass` aur `message` **kabhi ek dusre ko seedha nahi jaante** — sab kuch `core.classroom_chat_bridge` se guzarta hai. Ye invariant future me bhi maintain karna hai.

---

## 9. Pending / Open items (agla kaam yahi se shuru hoga)

1. **`message/push_utils.py` chahiye** — real debounce mechanism dekh ke `notification_batching.py` ko usse align karna hai (abhi parallel implementation hai).
2. **Task 44 incomplete:** `message/push_utils.py` ke `send_chat_message_push` / `send_incoming_call_push` / `send_mention_push` me `create_notification()` call add karni hai (bell-row currently missing for these).
3. **`liveclass/urls.py` cleanup** — purane `notifications` router + `notification-preferences/me/` path hatao jab `core.urls` wire ho jaye.
4. **Root urlconf** me `path("core/", include("core.urls"))` (ya jo prefix decide karo) add karna hai — abhi tak nahi hua.
5. **`db_table` names verify karo** DB me (`liveclass_notification`, `liveclass_notificationpreference`) migration chalane se pehle.
6. **`classroom_chat_bridge.py` ke field-name assumptions** — ✅ resolved (§6 dekho), lekin `resolve_parent_from_token()` (§6.1) ka function body abhi bhi kisi upload me nahi aaya — verify pending.
7. **`NotificationViewSetTests`** ka hardcoded URL path (`/core/notifications/...`) root urlconf wiring ke baad confirm karo.
8. ✅ ~~`NotifType` enum incomplete~~ — resolved, poori 31-value list §3 me confirmed hai.
9. ✅ ~~`NotificationSerializer` plain `to_dict`~~ — resolved, real `ModelSerializer` implementation ho chuki hai (§7 dekho).

---

## 10. Quick reference — kaun sa function kab call karo (liveclass signal handlers ke liye)

| Event | Call karo |
|---|---|
| Teacher "Create Group" button dabaye | `create_classroom_group(classroom, actor=request.user)` |
| Join request accept / waitlist promote | `sync_membership_on_join_accept(classroom, student)` |
| Student kick/ban/refund | `sync_membership_on_removal(classroom, student, reason="...")` |
| Naya co-teacher/moderator add | `promote_to_moderator(classroom, user)` |
| Classroom title/description/cover badla | `sync_group_metadata(classroom)` |
| Classroom close/soft-delete | `archive_group_on_classroom_close(classroom)` |
| Live session start (`notify_session_live` task) | `post_session_live_announcement(session)` |
| Burst-y event (likes/follows/comments) | `create_batched_notification(...)` bell + push dono ke liye, agar batching chahiye |
| Non-burst notification (single event) | `create_notification(...)` bell ke liye + apna separate push call |