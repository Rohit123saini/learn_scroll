# `core` App — Documentation

> Ye document `core` Django app ke andar jo bhi kaam hua hai (tasks 28, 42-48, Task 18, F-1, F-4) uska single source of truth hai. Koi bhi is app pe kaam continue kare, sabse pehle ye file padhe — har file ka purpose, har function ka contract, aur sab `ASSUMPTION` markers ek jagah pe hain.

> **Reconciliation pass (latest — this update):** `models.py` aur nayi migration file (`0004_alter_notification_notif_type.py`) ke against check kiya — **`NotifType` enum ka documentation bahut peeche reh gaya tha**, fix kar diya:
> - **§3's `NotifType` list sirf 31 values document karti thi ("28 original liveclass + 3 message-app types") — real enum me ab 55 values hain.** Missing the: task 11 ka `post` app block (`POST_LIKED`/`POST_COMMENTED`, 2), pura campus block (9), testseries/assignment/campus-gamification block (8), aur is pass (TASK 1) ke 5 naye `FOLLOW_*`/`*_FROM_FOLLOWED` values (`user_profile`'s Follow feature). §3 ab poori 55-value list, grouped by source app, ke saath update hai.
> - **`CAMPUS_APP_TYPES`, `TESTSERIES_APP_TYPES`, aur naya `FOLLOW_APP_TYPES` — teeno frozensets is doc me pehle kabhi mention nahi hue the**, sirf `MESSAGE_APP_TYPES` document tha. `views.py`/`serializers.py` ke apne-apne app ka notification-routing in par depend karta hai (same "ek jagah se dono import karein" pattern jo `MESSAGE_APP_TYPES` ke liye already tha) — §3 me sab add kiye.
> - **Naya migration `core/migrations/0004_alter_notification_notif_type.py` — is doc me pehle mention nahi tha.** State-only `AlterField` (`choices=` par, koi DB column/constraint change nahi, isliye Postgres/SQLite par zero SQL issue hoti) — sirf Django ke migration-state ko model se sync rakhne ke liye, taaki `makemigrations --check` CI me drift na pakde. `TESTSERIES_CREATED_BY_FOLLOWED` (30 chars) ab `max_length=30` ke bilkul limit par hai, zero headroom bacha — agla naya value isse lamba hua to `max_length` bhi isi migration-shape me bump karna padega.

> **Reconciliation pass (latest):** source files firse check kiye gaye (campus app ke liye jaisi pass abhi-abhi hui thi, wahi tarika yahan bhi). Do real gaps mile:
> - **`search.py` (F-4) — poora naya file, is doc me ab tak bilkul mention hi nahi tha.** `core`'s unified cross-app "search everything" layer (Postgres FTS + trigram, `message/search_utils.py` ke strategy ka extension) — naya **§6.2** isko poora document karta hai; §2 aur §8 (dependency graph) bhi update kiye.
> - **`test_parent_bridge.py` test-count galat tha** — §2 aur §6.1 "10 tests" bolte the, real file me **11** hain (`test_second_students_parent_token_never_resolves_to_first_student` count me chhoot gaya tha). Fix kar diya.
>
> Baaki sab (`models.py`, `services.py`, `notification_batching.py`, `classroom_chat_bridge.py`'s 8+1 sync/parent functions, `admin.py`, `tests.py`) already is doc se match kar rahe the — in files me koi naya drift nahi mila.
>
> **Reconciliation pass (latest — this update):** phir se saare files check kiye gaye (campus app ke liye jaisi pass hui thi, wahi tarika) — **3 real gaps mile**, doc ab neeche diye changes ke saath update kar diya gaya hai:
> - **`SearchView`/`search/` endpoint ab BAN CHUKA HAI — pichli pass ka §6.2/§9-item-13 ka "koi view/url wired nahi" claim ab STALE tha.** `core/views.py` me ab `SearchView` (Task 18, plain `APIView`) hai jo `assignment`+`testseries` ke liye khud apne scoped querysets banata hai (`assignment` — `AssignmentViewSet.get_queryset()` ko mirror karta hai; `testseries` — individual/published + own-created + attempted + campus-enrolled, `campus.StudentEnrollment` se resolve karke) aur unhe `core.search.search_everything()` ko deta hai. `core/urls.py` me `path("search/", SearchView.as_view())` bhi wired hai. §6.2, §7, §8, §9 update kiye — koi test coverage abhi bhi nahi hai iske liye (naya open item).
> - **`search.py`'s `SOURCES` registry me `assignment`/`testseries` bhi ab WIRED hain (Task 18)** — pichli pass ke status-table me sirf `message`✅/`campus_notice`✅ the, `post`❌/`class_material`❌ stub — lekin `ASSIGNMENT_SOURCE`/`TESTSERIES_SOURCE` (dono `_search_generic_model` ke through, fields=`("title","description")`) us table me the hi nahi, jabki module docstring khud unhe ✅ bolta hai. §6.2 status table fix kiya.
> - **`check_config_drift.py` (F-1) — bilkul NAYI file, is doc me ab tak kahin mention nahi thi.** `core/management/commands/check_config_drift.py` — 4 automated drift checks (throttle-scope→`DEFAULT_THROTTLE_RATES`, `@shared_task`→`CELERY_BEAT_SCHEDULE`, model→admin registration, `APIView`→`urls.py` wiring), abhi `user_profile`+`core` par scoped (`settings.CONFIG_DRIFT_APPS`). Naya **§11** poora document karta hai; §2 aur §9 (settings.py wiring requirement) bhi update kiye.
>

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
| `models.py` | `Notification`, `NotificationPreference` — dono models `liveclass` se yahan move hue (task 42). `NotifType` enum ab **55 values** hai (§3) — pehle is doc me sirf 31 documented the. |
| `migrations/0004_alter_notification_notif_type.py` | **NEW row, is doc me pehle mention nahi thi.** State-only `AlterField` — `notif_type`'s `choices=` ko current 55-value list se sync karta hai, koi DB-level operation nahi (Postgres/SQLite pe zero SQL). Purpose sirf `makemigrations --check` (CI) ko drift-free rakhna hai. |
| `services.py` | `create_notification()` + `create_bulk_notifications()` — sirf bell-row(s) banate hain, push kabhi nahi bhejte |
| `notification_batching.py` | `create_batched_notification()` — burst events (5 likes ek saath) ko ek notification me collapse karta hai. ✅ **Real implementation ab uploaded hai — see §5** (pehle yahan broken-content warning thi). |
| `classroom_chat_bridge.py` | `liveclass` ↔ `message` app ke beech ka pura coupling — 8 sync functions + `get_groups_for_classrooms()` (bulk helper) + `resolve_parent_from_token()` (Task 5, parent-portal auth) — **poora file ab uploaded hai, body-not-available warning resolved — see §6/§6.1** |
| `search.py` | Unified cross-app "search everything" (Postgres FTS + trigram, `message/search_utils.py` ka extension). **4 sources ab WIRED hain**: `message`, `campus.Notice` (Task F-4), aur `assignment`/`testseries` (Task 18, is pass me confirm hue) — `post`/`liveclass.ClassMaterial` abhi bhi STUB hain (model uploads na hone ki wajah se). Pure ranking/merging layer hai — koi bhi permission-scoping khud nahi karta, caller (`core/views.py::SearchView`) ka pehle se scoped queryset leta hai. §6.2 dekho. |
| `serializers.py` | Real DRF `ModelSerializer`s (`NotificationSerializer`, `NotificationPreferenceSerializer`), replacing this doc's own original `to_dict` suggestion — see §7 |
| `views.py` | `NotificationViewSet` (list/retrieve/destroy + custom actions) + `NotificationPreferenceView` + **`SearchView`** (Task 18, unified search endpoint — is pass me "wired" confirm hua, see §6.2/§7) |
| `urls.py` | Router wiring (`notifications/`) + `notification-preferences/me/` + **`search/`** (Task 18, `SearchView.as_view()`) — root urlconf me `include("core.urls")` karna hai |
| `admin.py` | `NotificationAdmin` + `NotificationPreferenceAdmin` dono registered hain, see §3b |
| `apps.py` | Standard `CoreConfig` |
| `management/commands/check_config_drift.py` | **NEW row, F-1 — is doc me pehle bilkul mention nahi tha.** 4 automated config-drift checks (throttle-scope, celery-beat, admin-registration, urls-wiring), `settings.CONFIG_DRIFT_APPS` (default `["user_profile", "core"]`) par scoped. Naya **§11** poora document karta hai |
| `tests.py` | `Notification`/`NotificationPreference`/`create_notification`/viewset ke **25** tests — `SearchView` ke liye abhi koi test nahi hai (open item, §9) |
| `test_notification_batching.py` | `create_batched_notification` ke **5** tests (contract confirm karte hain — see §5) |
| `test_parent_bridge.py` | `resolve_parent_from_token()` (Task 5) ke **11** tests, `message.models.ParentAccessCode`/`ParentToken` ke against — see §6 |

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
| `is_read` | BooleanField | koi standalone `db_index=True` nahi — composite `Meta.indexes` cover karte hain (neeche, ab **do** hain) |
| `read_at` | DateTimeField | nullable, sirf `mark_read()` se set hota hai |
| `created_at` | auto (`auto_now_add`) | indexed (dono composite indexes ka part), `ordering = ["-created_at"]` |
| — | **no `updated_at`** | Original liveclass model me bhi nahi tha — `mark_read()` hi iski sirf mutation hai, alag `updated_at` redundant hoti (batching cache khud apna staleness track karta hai, is model pe nahi) |

**⚠️ NEW (production hardening pass, koi schema-breaking change nahi — sirf 2 additive index) — pehle iss doc me nahi tha:**

- `notif_type` ab `db_index=True` hai. Reasoning (module docstring se): recipient-scoped composite index "kitne SESSION_LIVE notifications aaj gaye, sab recipients milake" jaisi ops/admin-dashboard query ko cover nahi karta — wo ek bilkul alag query shape hai, upar wale kisi bhi index ka duplicate nahi.
- `Meta.indexes` ab **do** hain, ek nahi:
  1. `(recipient, is_read, -created_at)` — unread-badge/unread-list queries (pehle se documented).
  2. `(recipient, -created_at)` — **naya**. Reasoning: pehla index sirf un queries ko fully-sorted run deta hai jo `is_read` pe bhi filter karti hain. Bahut common "meri saari notifications, newest first" query (combined feed, `is_read` filter ke bina) us index se ORDER BY nahi le sakti across different `is_read` values. Ye dusra index seedha isी query-shape ke liye hai.
- **`Notification.objects` — naya custom manager/queryset**, ab `NotificationQuerySet` (`.for_user(user)`, `.unread()`) se banta hai (`objects = NotificationQuerySet.as_manager()`). Iska matlab: koi bhi jagah jo pehle `Notification.objects.filter(recipient=u, is_read=False).count()` inline likhti thi, ab `Notification.objects.for_user(u).unread().count()` likh sakti hai — aur agar is query-shape ko kabhi tune karna pade, sirf **ek** jagah (ye manager) badalni padegi. `views.py`/`tests.py` abhi bhi purane `.filter(...)` style se hi likhe hain (nayi manager methods ka istemal optional hai, backward-compatible) — ye ek available convenience hai, koi jagah force-migrate nahi ki gayi.

Do additive indexes + ek convenience manager ke alawa kuch aur nahi badla — field list, `db_table`, choices, sab wahi hai jo neeche/upar describe hai.

**`Meta.db_table` — DELIBERATELY pinned, ab implementation-confirmed:**
- `Notification` → `db_table = "liveclass_notification"`
- `NotificationPreference` → `db_table = "liveclass_notificationpreference"`

Ye task 42 ke migration (`core/migrations/0001_move_notification_models.py`) ko pure `SeparateDatabaseAndState` banata hai — sirf Django ORM state `liveclass`→`core` move hota hai, physical Postgres table na move hoti hai, na rename, na row-copy. **In values ko real ALTER/RENAME TABLE migration ke bina mat badlo.** (Real DB confirm karna abhi bhi bacha hai — §9.)

**`NotifType` enum — ✅ AB POORI CONFIRMED LIST HAI, 55 VALUES (pichli pass ne sirf 31 document kiye the):**

```
# --- Original liveclass block (28) ---
JOIN_REQUEST_RECEIVED, JOIN_REQUEST_ACCEPTED, JOIN_REQUEST_REJECTED,
PASS_REFUNDED, SESSION_REMINDER, ASSIGNMENT_GRADED, QUERY_ANSWERED,
CERTIFICATE_ISSUED, WAITLIST_PROMOTED, CLASSROOM_FLAGGED, NOTICE_POSTED,
SESSION_LIVE, SESSION_CANCELLED, ASSIGNMENT_POSTED, SUBMISSION_RECEIVED,
STAFF_ADDED, REVIEW_POSTED, REPORT_REVIEWED, WITHDRAWAL_APPROVED,
WITHDRAWAL_REJECTED, WITHDRAWAL_PAID, CLASSROOM_SHARED,
PASS_GIFT_RECEIVED, PASS_GIFT_CLAIMED, PASS_AUTO_RENEWED,
AUTO_RENEW_FAILED, PASS_GIFT_EXPIRED, GENERIC

# --- Task 44 — message app (3) ---
CHAT_MESSAGE, MENTION, INCOMING_CALL

# --- Task 11 — post app (2) — ⚠️ is doc me pehle bilkul mention nahi tha ---
POST_LIKED, POST_COMMENTED

# --- campus app, campus_app_design.md §10 (9) — ⚠️ pehle mention nahi tha ---
CAMPUS_SESSION_SCHEDULED, CAMPUS_SESSION_LIVE, LOW_ATTENDANCE_ALERT,
ASSIGNMENT_POSTED_CAMPUS, ASSIGNMENT_DUE_REMINDER, RESULT_PUBLISHED,
FEE_DUE_REMINDER, STAFF_ASSIGNMENT_APPROVED, STAFF_ASSIGNMENT_REJECTED

# --- testseries / assignment / campus-gamification (8) — ⚠️ pehle mention nahi tha ---
TESTSERIES_POSTED, TESTSERIES_CHECKED, TESTSERIES_PAYOUT_RELEASED,
ASSIGNMENT_DUE_SOON, CAMPUS_REWARD_EARNED, TESTSERIES_REVIEW_RECEIVED,
TESTSERIES_QUERY_RECEIVED, TESTSERIES_QUERY_ANSWERED

# --- TASK 1 (is pass) — user_profile's Follow feature (5) — NAYA ---
FOLLOW_REQUEST_RECEIVED, FOLLOW_REQUEST_ACCEPTED, NEW_POST_FROM_FOLLOWED,
CLASSROOM_CREATED_BY_FOLLOWED, TESTSERIES_CREATED_BY_FOLLOWED
```
**Total 55 values** (28 + 3 + 2 + 9 + 8 + 5). Pichli pass sirf pehle do block (31) document karti thi — baaki 24 values (post/campus/testseries-assignment/follow, sab already-existing PLUS is pass ke 5 naye follow-wale) is doc me kabhi nahi likhe gaye the.

**Notes:**
- `NOTICE_POSTED` campus ke liye reuse hota hai (`campus.bridge.NotifTypes.NOTICE_POSTED` isi value ko point karta hai) — campus ke liye alag duplicate choice nahi banaya gaya.
- `ASSIGNMENT_POSTED`/`ASSIGNMENT_GRADED` testseries/assignment block me bhi reuse hote hain (naya string nahi) — sirf `ASSIGNMENT_DUE_SOON` (assignment-app) naya hai, aur `ASSIGNMENT_DUE_REMINDER` (campus-app) se deliberately alag/separate value hai — do alag apps ke reminder events, aliases nahi.
- `FOLLOW_REQUEST_RECEIVED`/`FOLLOW_REQUEST_ACCEPTED` do alag values hain (ek "follow_status_changed" + status field ki jagah) — same pattern jo `JOIN_REQUEST_RECEIVED`/`JOIN_REQUEST_ACCEPTED` already follow karte hain.
- `NEW_POST_FROM_FOLLOWED`/`CLASSROOM_CREATED_BY_FOLLOWED`/`TESTSERIES_CREATED_BY_FOLLOWED` — "jisko follow karte ho usne X banaya" fan-out, ek value per content-type (generic `new_content_from_followed` nahi) — client `notif_type` se hi deep-link route kar sake, `data` inspect kiye bina.
- ⚠️ **`TESTSERIES_CREATED_BY_FOLLOWED` (`"testseries_created_by_followed"`) exactly 30 characters hai — `max_length=30` ki hard limit par, zero headroom.** Agla koi naya value 30 se lamba hua to usi migration me `max_length` bump bhi karna hoga.
- Har naya block choices-only addition hai (koi schema/DB-level migration nahi chahiye) — lekin ab is codebase ka convention hai ki phir bhi ek state-only `AlterField` migration record ki jaati hai taaki `makemigrations --check` CI me drift na pakde: yehi is pass ka `0004_alter_notification_notif_type.py` hai (see §2).

**`*_APP_TYPES` frozensets — "ye NotifType values kaunse app/feature se aaye" pattern, chaaro ab documented (pehle sirf `MESSAGE_APP_TYPES`):**

| Frozenset | Values | Notes |
|---|---|---|
| `MESSAGE_APP_TYPES` (task 46) | `CHAT_MESSAGE`, `MENTION`, `INCOMING_CALL` | `views.py`/`serializers.py` dono yahi ek jagah se import karte hain — naya message-app type add karte waqt sirf yahi ek jagah update karni hai. |
| `CAMPUS_APP_TYPES` | `CAMPUS_SESSION_SCHEDULED`, `CAMPUS_SESSION_LIVE`, `LOW_ATTENDANCE_ALERT`, `ASSIGNMENT_POSTED_CAMPUS`, `ASSIGNMENT_DUE_REMINDER`, `RESULT_PUBLISHED`, `FEE_DUE_REMINDER`, `STAFF_ASSIGNMENT_APPROVED`, `STAFF_ASSIGNMENT_REJECTED` | `NOTICE_POSTED` deliberately isme NAHI hai — wo campus se pehle ka hai, shared/generic value hai, campus-exclusive nahi (`MESSAGE_APP_TYPES` jo values claim nahi karta unke saath consistent reasoning). |
| `TESTSERIES_APP_TYPES` | `TESTSERIES_POSTED`, `TESTSERIES_CHECKED`, `TESTSERIES_PAYOUT_RELEASED`, `TESTSERIES_REVIEW_RECEIVED`, `TESTSERIES_QUERY_RECEIVED`, `TESTSERIES_QUERY_ANSWERED` | testseries ke apne notification-list routing ke liye. |
| `FOLLOW_APP_TYPES` (TASK 1, NAYA) | `FOLLOW_REQUEST_RECEIVED`, `FOLLOW_REQUEST_ACCEPTED`, `NEW_POST_FROM_FOLLOWED`, `CLASSROOM_CREATED_BY_FOLLOWED`, `TESTSERIES_CREATED_BY_FOLLOWED` | `POST_LIKED`/`POST_COMMENTED` deliberately isme NAHI hain — wo post-app types (task 11) hain, Follow-relationship-driven nahi, chahe ek "jisko follow karte ho unka feed" us dono ko dikha sakta ho. Isi tarah `CAMPUS_APP_TYPES` bhi `NOTICE_POSTED` tak nahi phailta. |

Sab chaar sets same jagah (`Notification` model par class attribute) define hain, `MESSAGE_APP_TYPES` (§ upar) jaisi jagah — do copies maintain karne ki zaroorat nahi.

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

### ✅ RESOLVED — real implementation ab uploaded hai (pehle yahan 🔴 CRITICAL warning thi)

Is doc ka pehla version yahan warn karta tha ki uploaded `notification_batching.py` me `create_batched_notification()` ki koi definition nahi thi — uski jagah `test_notification_batching.py` ka ek duplicate/earlier draft (khud apne hi module se self-import karta ek `TestCase`) galti se is path pe save ho gaya tha, jo module-load pe `ImportError` deta. **Latest upload me ye fix ho chuka hai** — `notification_batching.py` me ab asli `create_batched_notification()` implementation hai (bilkul upar wale "Kaise kaam karta hai" design contract ke mutabik), aur `NotificationBatchingTests` test-class content ab sirf `test_notification_batching.py` me hai, jahan uska hona chahiye. File ka apna docstring ye fix explicitly note karta hai.

**Naya, is pass me confirm hua real bug-fix — `_hydrate_actors()` helper:** Implementation se pehle `actors` list ka **type call-to-call inconsistent** raha hoga — fresh-batch branch me `actors = [actor]` (caller jo bhi de: `User` instance ya raw id, dono docstring allow karta hai), lekin folded-batch branch me `actors` hamesha hydrated `User` instances ki list thi (`User.objects.filter(id__in=actor_ids)`). Koi bhi `title_fn`/`message_fn` jo `actor.display_name`/`actor.username` jaisi attribute access karta (jaisa "X and 4 others liked your post" banane ke liye zaroori hai), **pehle event pe** silently crash ya galat output deta agar caller ne raw id pass kiya tha. Fix: dono branches (fresh aur folded) ab isi ek `_hydrate_actors(actor_ids)` helper se guzarte hain, taaki `actors` **hamesha** hydrated `User` list ho, chahe pehla event ho ya 50wa. Same fix `message_fn`'s teesre arg (`latest_actor`) ko bhi lagi — pehle raw `actor` param seedha pass ho raha tha (raw id ho sakta tha), ab hydrated actors list se lookup hota hai.

### ✅ RESOLVED — `message/push_utils.py` open item

Is doc ka §5 pehle bolta tha ki `push_utils.py` kabhi upload nahi hua aur uske real debounce se is module ko replace karna hai taaki do parallel implementations na rahen. **Latest `notification_batching.py`'s apna docstring confirm karta hai ki `push_utils.py` ab mil gaya hai aur check kar liya gaya hai** — aur nateeja "replace karo" nahi, balki **"dono jaanboojh kar alag hain, merge mat karo"** hai:

- `push_utils.py`'s debounce (`_DIGEST_COUNT_KEY`, `cache.add`/`incr`) ek plain per-`(user, conversation)` **counter** hai jo sirf push-**tray copy** decide karta hai ("1 message" vs "X sent N messages") — ye kabhi kisi `Notification` row ko merge/update nahi karta; har chat message ki apni alag bell-row banti rehti hai (`create_notification()` se, har baar).
- `notification_batching.py` fundamentally alag kaam karta hai — **same `Notification` row ko update** karta hai jab tak actor-set badhta rahe (5 logo ne post like kiya = 1 row).
- Dono mechanisms merge karna galat hota: chat ko per-message row chahiye (unread count, message-level tap-through), burst events (likes/reactions) ko collapsed-row chahiye. **Ye do jaanboojh kar alag implementations hain — merge nahi karna.** §9 ke pending-items list se ye item ab hata diya gaya hai (neeche dekho).

**Contract jo test files se confirm hota hai (ab implementation se bhi match karta hai):**
- Cache key = `(recipient_id, notif_type, target_id)`.
- Pehla event → `Notification` row + `send_push_fn` call (agar diya ho).
- Same window me doosra event (chahe naya actor ho ya repeat actor) → **wahi row update**, koi naya push nahi.
- Repeat actor → actor-count nahi badhta (title `_title_fn(count, actors)` se banta hai, "Someone liked" vs "N people liked").
- Alag `target_id` → alag, independent batch.
- Window expire ho jaye (ya cache key clear ho jaye) → agla event ek **fresh** row + fresh push deta hai.
- Agar batched row cache ke expect karne ke bawajood DB se delete ho chuki ho (user ne beech me clear kar diya) → gracefully naya batch start, `DoesNotExist` raise nahi karta.

**Test-count correction:** iss doc ke §2 me `test_notification_batching.py` ko "6 tests" bola gaya tha — real file me **5** hain (`test_first_event_creates_a_row_and_sends_push`, `test_second_event_within_window_updates_same_row_no_new_push`, `test_repeat_actor_does_not_inflate_count`, `test_different_targets_batch_separately`, `test_window_expiry_starts_a_fresh_batch`). §2 update kar diya gaya hai.

---

## 6. `classroom_chat_bridge.py` — Liveclass ↔ Message bridge

**Golden rule:** `liveclass/signals.py`, `liveclass/views.py`, aur `notify_session_live` task — koi bhi seedha `message.models` / `message.services` import NAHI karta. Sab is ek file se guzarta hai.

✅ **Poora `classroom_chat_bridge.py` ab uploaded hai** (pehle sirf indirect evidence se contract infer kiya gaya tha) — file me total **10 public/semi-public entry points** hain: 8 sync functions + `get_groups_for_classrooms()` (bulk helper) + `resolve_parent_from_token()` (Task 5).

✅ **FIXED (docstring bug):** module ka apna docstring pehle khud ko "9 functions" bolta tha bina clarify kiye ki `get_groups_for_classrooms()` us ginti me kyun nahi hai — koi functional bug nahi tha (code sahi kaam kar raha tha), sirf ambiguous/stale documentation thi jo naye reader ko confuse kar sakti thi. Ab docstring explicit hai: "9 functions" sirf un ko refer karta hai jinhe `liveclass` khud call karta hai (functions 1-9, neeche numbered); `get_groups_for_classrooms()` module ka **10wa** entry point hai — ek bulk/"GAP FIX" twin of `_get_group_for_classroom()`, parent dashboard ke liye add kiya gaya (neeche dekho), aur `liveclass` nahi balki `message/views_parent.py` seedha isko call karta hai, isliye wo "9 liveclass-facing functions" ki ginti se bahar tha. Dono counts (9 liveclass-facing + 1 message-facing = 10 total) ab module docstring me khud explicit hain.

### `get_groups_for_classrooms(classrooms)` — **NEW, iss doc me pehle mention nahi tha**

- **Kyu:** `message/views_parent.py`'s `StudentReportCardList._chat_group_by_classroom` ko ek poore page ke classrooms ke liye ek saath "is classroom ka linked chat Group hai kya" chahiye — `_get_group_for_classroom()` ko per-classroom loop me call karna N+1 hota (ek SELECT per classroom). Ye function wahi kaam **ek single bulk query** me karta hai, chahe kitne bhi classrooms pass ho.
- **Signature:** `classrooms` — Classroom instances ka koi bhi iterable (ya `.id`/`.chat_group_enabled`/`.linked_conversation_id` wala kuch bhi, jaisa caller `.only(...)` se already deta hai).
- **Return:** `{classroom_id: Group}` — sirf un classrooms ke liye jinke paas `chat_group_enabled=True`, non-null `linked_conversation_id`, **aur** ek Group row jo us conversation ke liye abhi bhi exist karti hai (`_get_group_for_classroom`'s apna DoesNotExist → skip-and-log behaviour, bas batched). Jis classroom ka eligible group nahi milta, wo result dict me simply absent hai — same "absent, not None" contract jo `_get_group_for_classroom` ke saare callers already rely karte hain.
- **Public** (no leading underscore) — `message/views_parent.py` isko seedha import karta hai: `from core.classroom_chat_bridge import get_groups_for_classrooms`. Isse `message` app par `core` ki ek public-function dependency ban gayi hai — abhi tak §8 ka dependency graph isko show nahi karta tha, update kar diya gaya hai.

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

## 6.1. `resolve_parent_from_token()` — Task 5, parent-portal auth

✅ **RESOLVED — implementation body ab uploaded hai.** Is doc ka pehla version yahan warn karta tha ki `classroom_chat_bridge.py` ke content me sirf 8 sync functions the, `resolve_parent_from_token` unme nahi tha, aur contract sirf indirectly (`liveclass/permissions.py`'s import + `test_parent_bridge.py`'s 11 tests se) infer kiya gaya tha. **Latest upload me poora function body maujood hai**, aur neeche ka pura "Confirmed contract" section ab function-body se line-by-line verify ho chuka hai — §9's corresponding open item (6) resolved kar diya gaya hai.

**Do naye details jo sirf real code se pata chale (tests se infer nahi ho sakte the):**
- **Exact check order:** (1) `ParentToken.DoesNotExist` / koi bhi unexpected lookup error → deny; (2) token ka apna rolling inactivity window (`last_seen_at is None` ya `> INACTIVITY_TTL_DAYS` purana) → deny; (3) `parent_access_code.is_active == False` (revocation) → deny; (4) `parent_access_code.expires_at` (absolute expiry) → deny. Doc ka pehla version revocation-check ko access-code-expiry ke baad describe karta tha — real code me revocation check pehle aata hai, expiry check baad me (dono hi pass hone zaroori hain, to result same hai, bas exact order ye hai).
- **`last_seen_at` touch bhi apne try/except me hai — agar wahi save fail ho jaye, to bhi access deny hota hai** (naya detail, doc me pehle nahi tha). Yani "success" sirf tab return hota hai jab lookup + saare checks + touch-write, sab pass ho jayein — touch-write khud ek aur fail-closed point hai, sirf ek "best-effort touch, ignore failure" nahi.
- **`ParentTokenResolution`** ek chhota result-class hai (`__slots__ = ("student", "parent_access_code")`) — `.student` aur `.parent_access_code` do attributes, jo `HasValidParentSessionToken` seedha `request.parent_student`/`request.parent_access_code` pe copy karta hai. `select_related` do fields pe hai: `"parent_access_code"` aur `"parent_access_code__student"` (poora chain ek hi query me).

**Confirmed contract (ab function-body se verified, sirf tests se infer nahi):**

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

~~**Open item:** function ka real code body abhi tak kisi upload me nahi mila...~~ ✅ **Resolved** — body ab uploaded aur verified hai (upar dekho); `select_related` exact fields = `"parent_access_code"`, `"parent_access_code__student"`.

---

## 6.2. `search.py` — F-4, unified cross-app search (NEW, is doc me pehle bilkul nahi tha)

`core` ka "search everything" layer — messages, campus notices, (future) posts, (future) classroom materials, sab ek hi endpoint se. `message/search_utils.py` ne jo Postgres FTS (tsvector) + trigram-similarity strategy `Message` ke liye already establish ki thi, usi ka extension hai, generalized har us model ke liye jiske paas apna stored `search_vector` column nahi hai.

**Kyu `core` me, `message` me nahi:** `core` already is project ka shared cross-app integration point hai (`Notification`'s seedhe `liveclass` FKs, `campus/bridge.py`'s "campus ka ONLY door core/message hai" golden rule — jo `core` khud ko kisi app me reach karne se restrict nahi karti). Yahan rakhne se `message`/`liveclass`/`campus`/`post` ko ek search-box ke liye ek-dusre ko seedha import nahi karna padta — sab sirf `core` se baat karte hain.

**🔒 GOLDEN RULE jo ye file follow karti hai (`message/search_utils.py` jaisa hi):** har function neeche ek **already permission-scoped queryset** input leta hai. Ye module KABHI decide nahi karta ki kaun kya dekh sakta hai — wo decision (kaun si conversations ka participant hai, kaun se notices kisi student ke enrollment/parent-link/staff-profile se entitled hain, kaun se posts kisi blocked user ke nahi hain) hamesha us app ke apne view/queryset-building code me hi rehta hai, jaisa `message` ke liye pehle se hai. Ye logic yahan bhi partially reimplement karna (sirf `Notice` ke liye bhi) ek DOOSRI, independently-maintained copy ban jaati — do copies drift karti hain, aur ek search endpoint jo ek row leak kar de jo us app ka apna view deny karta — ye is feature ke exist hi na karne se bhi bura outcome hai. Isliye ye file **pure ranking/merging layer** hai, kabhi permission layer nahi.

### Status (is pass tak)

| Source | Status |
|---|---|
| `message` (chat messages) | ✅ WIRED — `message.search_utils.search_messages` ko seedha reuse karta hai (`Message` ke paas already stored `search_vector` + trigger hai) |
| `campus_notice` (`campus.Notice`, `title`/`body`) | ✅ WIRED — `_search_generic_model()` ke through. Caller (`SearchView`) ko phir bhi khud ek properly-scoped `Notice` queryset pass karna hai (golden rule upar) — ye file campus/department/section visibility rules khud nahi jaanti/guess karti. |
| `assignment` (`assignment.Assignment`, `title`/`description`) | ✅ **WIRED (Task 18, naya is pass me confirm hua)** — `_search_generic_model()` ke through. `SearchView` (§7) scoped queryset `AssignmentViewSet.get_queryset()` ko VERBATIM mirror karta hai: staff sab kuch dekhte hain, baaki sirf jo unhone khud post kiya ya jispe unki personal submission hai. Campus/liveclass-sourced assignments non-staff ke liye deliberately excluded hain yahan bhi (wo viewset khud unhe bahar rakhta hai). |
| `testseries` (`testseries.TestSeries`, `title`/`description`) | ✅ **WIRED (Task 18, naya is pass me confirm hua)** — `_search_generic_model()` ke through. `SearchView` scoped queryset: individual/published (marketplace) + user ki khud-banayi + jo attempt ki + campus-context series un sections ke liye jinme user ACTIVE enrolled hai (`campus.StudentEnrollment` se — wahi roster-source jo `campus.bridge.create_testseries()` khud use karta hai). Liveclass-context series abhi included NAHI hain — liveclass side pe koi roster/entitlement resolver abhi nahi hai (`liveclass/bridge.py` me sirf assignment functions hain), isliye wo rows silently absent hain search results se, kabhi leak nahi hoti. |
| `post` (post app) | ❌ **STUB ONLY** — `post/models.py` kabhi kisi upload ka hissa nahi raha, isliye `Post`'s searchable field(s) ka naam pata nahi. Wire karne ke liye `SOURCES` me ek naya `SearchSource` add karna hai (`NOTICE_SOURCE` jaisi shape) jab wo model milega. |
| `class_material` (`liveclass.ClassMaterial`) | ❌ **STUB ONLY**, same reason — class exist karti hai (ek pehli `liveclass/models.py` upload me confirm hui thi) par uski field-list kabhi nahi dekhi gayi. |

**Finish karne ke liye chahiye:** `post/models.py` (searchable field(s) + confirm karna ki `post` visibility kaise scope karta hai — shayad `user_profile.BlockUser`/`RestrictUser` se, taaki ye us logic ko duplicate na kare) aur `ClassMaterial` model definition (same do sawaal). Dono milne pe bas `SOURCES` me ek-ek `SearchSource` entry aur view-side scoped-queryset builder add karna hai — is file me aur kuch badalna nahi hai.

### `_search_generic_model(qs, query, *, fields, order_field='created_at')`

`Message`-jaisa OR(ranked-FTS, trigram) strategy, generalize kiya kisi bhi model ke liye jiske paas — `Message` ke ulat — koi stored `search_vector` column nahi hai. `SearchVector(*fields)` **fresh, har query pe** compute karta hai (trigger-maintained column padhne ke bajaye) — ye strictly slower hai load ke neeche (Postgres har candidate row ka text query-time pe tokenize karta hai, write-time pe nahi) aur `Message.search_vector` jaisa GIN index use nahi kar sakta. Ek low-volume, already-narrowly-scoped table (jaise ek campus ki notices) ke liye theek hai — kisi large ya ungated table pe as-is point mat karo. Jis source ko scale chahiye, use apna stored `search_vector` column + trigger migration lena chahiye, `Message` jaisa, is generic path pe forever bharosa karne ke bajaye.

`fields` me searchable text column name(s) jaate hain (e.g. `("title", "body")`) — trigram similarity sirf **pehle** field ke against chalti hai (Postgres trigram similarity concatenated columns ke across utni cleanly compose nahi karti jitna ek tsvector karta hai), isliye sabse important searchable field pehle pass karo. Postgres na ho (sqlite/local dev) to unranked `icontains` fallback deta hai, saare fields OR ke saath.

### `SearchSource` (dataclass) aur `search_everything()`

`SearchSource(name, run, serialize)` — ek pluggable source. `run(qs, query)` caller ka scoped queryset leta hai, ranked queryset deta hai (same contract `message.search_utils.search_messages(qs, query)` ka). `serialize(obj)` ek result row ko common dict shape me convert karta hai, taaki heterogeneous models ek list me merge ho sakein: `{"source", "id", "title", "snippet", "created_at", "rank", "similarity", "extra"}`.

```python
search_everything(
    scoped_querysets: dict,   # {"message": <scoped Message qs>, "campus_notice": <scoped Notice qs>, ...}
    query: str,
    *, sources=None,          # limit to a subset of SOURCES keys
    limit_per_source: int = 10,
    total_limit: int = 30,
)
```

- Sirf wo keys jo `scoped_querysets` **aur** `SOURCES` (aur agar diya ho to `sources`) dono me hon, actually search hoti hain — ek unregistered ya unscoped source silently skip hota hai, error nahi. Isse caller hamesha apne saare scoped querysets pass kar sakta hai, chahe upar ka koi source abhi wired na ho.
- Result: ek flat list, sabhi sources se merged, `rank` (fallback `similarity`, phir `created_at`) se sorted — pehle har source apne `limit_per_source` tak capped hota hai (taaki ek noisy source baaki sabko crowd out na kare), phir poori list `total_limit` tak.
- **CAVEAT (bug nahi, inherent limit):** `rank`/`similarity` alag-alag models/content ke liye independently-computed Postgres scores hain — cross-source directly comparable guarantee nahi hai (ek notice ka rank aur ek message ka rank same "unit" nahi hain). Agar UI me ye matter karta hai (jaise ek "top result" callout), results ko source-wise group/section karna consider karo, ek single global sort order pe bharosa karne ke bajaye.
- `query` `MIN_QUERY_LENGTH` (message/search_utils.py se reused constant) se chhota ho to `ValueError` raise karta hai — same check jo `message/search_utils.py`'s callers `search_messages` tak pahunchne se pehle already karte hain, yahan ek hi jagah har source ke liye.
- Ek source ka query error hona (e.g. caller ne ek expected field-missing queryset pass kar diya) baaki sources ko down nahi le jaata — `logger.exception` + continue, wahi "degrade, crash mat karo" posture jo `campus/bridge.py` ek missing bridge function ke liye already leta hai.

**✅ RESOLVED — `SearchView`/`search/` URL ab wired hai (pehle yahan "abhi library-only hai" warning thi).** `core/views.py::SearchView` (plain `APIView`, Task 18) is file ke `search_everything()` ko `core/urls.py`'s `path("search/", SearchView.as_view())` ke through expose karta hai — poori detail §7 me. Scoped-queryset-building responsibility (golden rule ke mutabik) `SearchView` khud nibhata hai, `search.py` nahi — is file me is wajah se koi change nahi karna pada. **Abhi bhi open:** koi test coverage `SearchView`/`search_everything()` ke liye nahi hai (naya open item, §9), aur `post`/`class_material` sources upar wali table ke mutabik abhi bhi stub hain.

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
| GET | `search/` | **Task 18** — unified cross-app search, `?q=<query>` (required) + optional `?sources=assignment,testseries,...`. §6.2 me poori source-list; niche isi section me full behaviour. |

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

### `SearchView` (Task 18) — `GET /core/search/?q=...&sources=...`

Plain `APIView` (`IsAuthenticated`), `NotificationViewSet`/`NotificationPreferenceView` jaisa hi "own-scope only" spirit follow karta hai — bas yahan "own scope" ka matlab hai "jo bhi is user ko har source ki apni existing permission-rule allow karti hai", na ki sirf apne records.

- `q` required — nahi diya to `search_everything()` `ValueError` raise karta hai, jise ye view `400 {"detail": ...}` me convert karta hai.
- `sources` optional, comma-separated (`SOURCES` ke keys ka subset) — `?sources=assignment` jaisa; na diya jaye to har source jiska ye view khud queryset banata hai, search hoti hai.
- **Is view ki asli responsibility** — golden rule (§6.2) ke mutabik — HAR source ke liye ek already-permission-scoped queryset khud banana hai, `core.search` ko kabhi seedha model query nahi karne dena:
  - `assignment` — `assignment.models.Assignment` ko `AssignmentViewSet.get_queryset()` (`assignment/views.py`) jaisa hi scope karta hai: `user.is_staff` ho to sab, warna sirf `posted_by=user` YA (`source=PERSONAL` aur `submissions__student=user`).
  - `testseries` — `testseries.models.TestSeries` ko scope karta hai: `source=INDIVIDUAL, status=PUBLISHED` (marketplace) YA `creator=user` YA `attempts__student=user` YA (`source=CAMPUS`, `context_type="section"`, `context_id` un section-ids me jinme `campus.StudentEnrollment` ke through user `ACTIVE` enrolled hai).
- In dono ke local imports (`from assignment.models import ...`, `from testseries.models import ...`, `from campus.models import StudentEnrollment`) function-body ke andar hain, module-level nahi — same lazy-import posture jo poore project me circular-import se bachne ke liye use hoti hai.
- `message`/`campus_notice` (`Notice`) sources ke liye **is view me abhi koi scoped-queryset builder nahi hai** — `search.py`'s `SOURCES` registry me dono entries maujood hain, lekin `SearchView.get()` unke liye koi queryset nahi banata, isliye `?sources=message`/`?sources=campus_notice` aaj **khaali results dete hain, error nahi** (`search_everything()`'s "sirf jo dono jagah present ho, wahi search hoti hai" contract ke mutabik). Ye apna alag open item hai (§9) — `search.py`'s docstring khud is gap ko wired-vs-scoped do alag cheezein maankar treat karta hai.
- Response shape: `{"results": [...]}`, jahan har result `search_everything()`'s common dict shape follow karta hai (`source`, `id`, `title`, `snippet`, `created_at`, `rank`, `similarity`, `extra`).
- **⚠️ NO TEST COVERAGE** — `tests.py` me is view/endpoint ke liye ek bhi test nahi hai (naya open item, §9).

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
liveclass  ──uses──▶  core.classroom_chat_bridge (8 sync functions)
                            │
                            ├──local-import──▶ message.models (Group, Message, MessageType, GroupMember)
                            └──local-import──▶ message.services (create_group, add_members_to_group,
                                                                   remove_group_member, update_group_member_role)

liveclass.permissions.HasValidParentSessionToken ──uses──▶ core.classroom_chat_bridge.resolve_parent_from_token
                            └──local-import──▶ message.models (ParentAccessCode, ParentToken)

message    ──(pending, task 44)──▶  core.services.create_notification   [bell-row for FCM pushes]

message.views_parent.StudentReportCardList  ──uses──▶  core.classroom_chat_bridge.get_groups_for_classrooms  [NEW]
                            └──local-import──▶ message.models (Group)

core.models.Notification  ──FK (SET_NULL)──▶  liveclass.Classroom, liveclass.ClassSession

core.search.search_everything  ──uses──▶  message.search_utils.search_messages
                            └── caller ──must pass──▶ already permission-scoped querysets for each
                                 source (message/campus.Notice/assignment/testseries/...) — core.search
                                 itself never queries `campus`/`message`/`post`/`liveclass`/`assignment`/
                                 `testseries` models directly, only ranks/merges what the caller hands it.

core.views.SearchView  ──uses──▶  core.search.search_everything   [Task 18, NOW WIRED]
                            ├──local-import──▶ assignment.models (Assignment, AssignmentSource)
                            └──local-import──▶ campus.models (StudentEnrollment), testseries.models (TestSeries)
```

`liveclass` aur `message` **kabhi ek dusre ko seedha nahi jaante** — sab kuch `core.classroom_chat_bridge` se guzarta hai. Ye invariant future me bhi maintain karna hai.

---

## 9. Pending / Open items (agla kaam yahi se shuru hoga)

1. ✅ ~~`message/push_utils.py` chahiye, `notification_batching.py` ko usse align karna hai~~ — resolved: `push_utils.py` check ho chuka hai, aur nateeja "align/replace" nahi tha — dono jaanboojh kar alag mechanisms hain (§5 dekho), merge nahi karna.
2. **Task 44 abhi bhi incomplete:** `message/push_utils.py` ke `send_chat_message_push` / `send_incoming_call_push` / `send_mention_push` me `create_notification()` call add karni hai (bell-row currently missing for these) — ye item alag hai item 1 se (jo batching-vs-debounce ka tha), aur abhi bhi open hai.
3. **`liveclass/urls.py` cleanup** — purane `notifications` router + `notification-preferences/me/` path hatao jab `core.urls` wire ho jaye.
4. **Root urlconf** me `path("core/", include("core.urls"))` (ya jo prefix decide karo) add karna hai — abhi tak nahi hua.
5. **`db_table` names verify karo** DB me (`liveclass_notification`, `liveclass_notificationpreference`) migration chalane se pehle.
6. ✅ ~~`classroom_chat_bridge.py` ke field-name assumptions + `resolve_parent_from_token()` ka function body~~ — dono resolved. Poora file ab uploaded hai, field-names verified the pehle se, function body ab bhi verified hai (§6.1).
7. **`NotificationViewSetTests`** ka hardcoded URL path (`/core/notifications/...`) root urlconf wiring ke baad confirm karo.
8. ✅ ~~`NotifType` enum incomplete~~ — resolved, poori 31-value list §3 me confirmed hai.
9. ✅ ~~`NotificationSerializer` plain `to_dict`~~ — resolved, real `ModelSerializer` implementation ho chuki hai (§7 dekho).
10. ✅ ~~`notification_batching.py` uploaded content broken (self-import)~~ — resolved, real implementation ab hai (§5).
11. ✅ ~~`classroom_chat_bridge.py`'s apna module docstring khud ko "9 functions" bolta hai lekin `get_groups_for_classrooms()` (10wa entry point) us count me nahi hai~~ — **resolved.** Module docstring ab explicitly clarify karta hai ki "9 functions" sirf `liveclass`-facing count hai (functions 1-9); `get_groups_for_classrooms()` module ka 10wa entry point hai, `message/views_parent.py` se seedha call hota hai, isliye us 9 ki ginti me nahi tha. Koi functional bug nahi tha, sirf docstring stale/ambiguous tha — ab dono counts (9 liveclass-facing + 1 message-facing = 10 total) explicit hain.
12. **NEW open item:** `models.py`'s naye `NotificationQuerySet.for_user()`/`.unread()` manager methods abhi kahin bhi call-site pe use nahi ho rahe (`views.py`/`tests.py` purane `.filter(...)` style se hi likhe hain) — functional issue nahi (dono equivalent hain), bas ek available convenience hai jo abhi adopt nahi hui.
13. ✅ ~~`search.py` expose karne ke liye koi `views.py`/`urls.py` endpoint nahi tha~~ — **resolved, is pass me confirm hua.** `core/views.py::SearchView` + `core/urls.py`'s `path("search/", ...)` dono ab wired hain (§6.2, §7). `post`/`liveclass.ClassMaterial` sources abhi bhi stub hain (§6.2) un models ke upload hone tak — ye hissa khula hai.
14. **NEW open item:** `SearchView`/`search_everything()` ke liye `tests.py` me **koi test nahi hai** — na success path (`?q=`, `?sources=`), na 400-on-short-query, na "assignment/testseries scoping sahi hai" wali regression. Sabse pehle iske liye tests likhna agla natural kaam hai.
15. **NEW open item:** `SearchView` sirf `assignment`/`testseries` ke liye scoped queryset banata hai — `message`/`campus_notice` (`Notice`) `search.py`'s `SOURCES` me registered hain (§6.2) lekin `SearchView.get()` unke liye koi queryset nahi banata, isliye `?sources=message`/`?sources=campus_notice` aaj silently khaali result dete hain. Inke liye bhi scoped-queryset builder add karna hai (§7 me flag kiya) jab ye tasked ho.
16. **NEW open item (F-1, `check_config_drift.py`):** command khud apne docstring me `settings.py` me `CONFIG_DRIFT_APPS = ["user_profile", "core"]` set karne ko kehta hai — ye setting is upload me confirm nahi ho saki (settings.py iss pass me nahi aaya). Verify karo ye setting maujood hai, aur command ko CI me (`--strict` flag ke saath) wire karna hai taaki drift automatically catch ho — abhi sirf manually `python manage.py check_config_drift` chalane se hi kaam karta hai.

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

---

## 11. `management/commands/check_config_drift.py` — F-1, automated config-drift check (NEW, is doc me pehle bilkul nahi tha)

**Kyu bana:** is codebase ke audit-comments me baar-baar EK JAISE 4 bug-shapes milte rahe hain, alag-alag apps me — sab "compile to ho jaata hai, bug sirf pehli real request/tick pe pata chalta hai" type ke:

1. Koi `ScopedRateThrottle`/custom throttle `throttle_scope` (ya `scope`) set karta hai, lekin `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` me uski matching key nahi hoti — us endpoint ki pehli hi request `ImproperlyConfigured` deti hai. (Is codebase me 10+ baar ho chuka: `session_join`, `coupon_validate`, `coin_withdrawal`, `chat_reaction`, `classroom_share`, `chunked_upload_*`, ...)
2. Koi `@shared_task` likha jaata hai jo clearly ek schedule pe chalne ke liye bana hai (sweep/reconcile/cleanup/expire job), lekin kabhi `CELERY_BEAT_SCHEDULE` entry nahi milti — wo bas kabhi chalta hi nahi. (`refresh_stale_enrolled_counts`, `reconcile_stuck_coin_purchases`, `run_auto_renewals`, `expire_unclaimed_gifts`, `send_notification_digests` — sab kisi na kisi point pe ye bug rakh chuke hain, settings.py ke comments ke mutabik.)
3. Koi model banta hai lekin `admin.py` me kabhi register nahi hota — koi `/admin/` se inspect/support nahi kar sakta.
4. Koi `APIView` subclass likhi jaati hai lekin kabhi `urls.py` me wire nahi hoti — dead code jo koi reach hi nahi kar sakta.

Ye command in charo checks ko generic bana kar automate karta hai — Django ke app-registry + AST/regex source-scans par based, koi hardcoded per-app logic nahi.

### Scope (deliberate, limitation nahi)

Abhi sirf `user_profile` aur `core` check hote hain (`CONFIG_DRIFT_APPS` default) — sirf ye do apps is command likhte waqt available the. Command khud fully generic hai; baaki apps add karna sirf `CONFIG_DRIFT_APPS` list me ek line badalna hai — kuch aur nahi chhedna padta.

### Wiring (one-time, `settings.py` me — **is pass me verify nahi ho paaya, settings.py upload nahi hua**)

```python
CONFIG_DRIFT_APPS = ["user_profile", "core"]  # yahan aur apps add karo baad me

# Escape hatches — sirf tab add karo jab flag ki gayi cheez
# genuinely jaanboojh kar aisi hai, check galat nahi hai:
CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}
```

### Usage

```bash
python manage.py check_config_drift
python manage.py check_config_drift --apps user_profile core liveclass
python manage.py check_config_drift --strict   # CI ke liye — issue mile to nonzero exit
```

### 4 checks (as-built)

1. **`_check_throttle_scopes`** — har app ke saare `.py` files me `throttle_scope = "xxx"` (regex) aur `class XThrottle(...Throttle...):` ke andar bare `scope = "xxx"` (regex, sirf Throttle-base classes ke andar hi, taaki koi unrelated `scope` variable false-positive na de) dhoondta hai, phir har mile hue scope ko `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` ke against check karta hai.
2. **`_check_celery_beat`** — `tasks.py` ko AST-parse karke har `@shared_task` (bare ya `@shared_task(bind=True)` jaisa called-with-args, aur `@app.task` style bhi) decorated function ka naam nikalta hai, phir `CELERY_BEAT_SCHEDULE` ke against check karta hai — **sirf task ka function-name match hota hai** (poora dotted path nahi), kyunki khud ye codebase inconsistent hai `"app.tasks.func"` vs `"app.func"` convention me. `CONFIG_DRIFT_ONDEMAND_TASKS` whitelist un tasks ke liye jo genuinely sirf `.delay()`/`.apply_async()` se request-time pe chalte hain.
3. **`_check_admin_registration`** — `django_apps.get_app_config(app_label).get_models()` se har model ko `admin.site._registry` ke against check karta hai.
4. **`_check_urls_wiring`** — `views.py` ko AST-parse karke har aisi class dhoondta hai jo `APIView`/`GenericAPIView` (ya usi file me pehle se flag ki gayi ek aisi hi base) se inherit karti ho — **`ViewSet`s is check se bahar hain** (wo normally `router.register()` se wire hote hain, literal `.as_view()` reference se nahi) — phir check karta hai ki class-name `urls.py` ke text me kahin mention hai ya nahi.

Har check ek `(kind, app_label, message)` tuple deta hai; `_report()` inhe 4 headings ke neeche group-print karta hai. `--strict` diya ho aur koi bhi issue mila ho to `CommandError` raise hoti hai (CI-fail ke liye).

### Limitations (clean run ko blindly trust karne se pehle padho)

- Throttle-scope aur `APIView`-vs-`urls.py` checks **source-text ke regex/AST scans hain, running interpreter nahi** — dynamically-built scope strings (ek f-string, ek literal ke bajaye variable) nahi pakde jaate. Is codebase me ab tak har real usage plain string literal raha hai, isliye abhi non-issue hai.
- Ye ek drift **detector** hai, fixer nahi — kabhi `settings.py`/`admin.py`/`urls.py` khud edit nahi karta, sirf batata hai kahan dekhna hai.

### Is doc ke against status

- Command khud generic hai aur is upload me poora mil gaya — **implementation-level koi gap nahi**.
- **Open (§9, item 16):** `settings.py` me `CONFIG_DRIFT_APPS` set hai ya nahi, aur CI me `--strict` ke saath wired hai ya nahi — dono is pass me verify nahi ho paaye (settings.py upload nahi hua).