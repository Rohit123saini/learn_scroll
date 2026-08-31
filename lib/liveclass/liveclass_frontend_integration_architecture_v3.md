# LiveClass Frontend Integration Architecture
### Backend Pass 12–16 features → Flutter (`lib/liveclass/`) implementation plan

> **Scope basis:** backend doc (`liveclass_production_readiness_audit.md`) confirms
> at line ~1556 that Pass 12 onward has **"still no Flutter code in this upload"**.
> Cross-checked against the frontend doc (`LIVECLASS_PRODUCTION_AUDIT.md`) — none of
> `ChatReaction`, `SessionReadState`, `PollTemplate`, chat pinning, `PassGift`,
> `ChatMessageReport`, moderation, `NotificationPreference`, digest email, engagement
> report, or `auto_renew` exist anywhere in the current frontend. So **all six
> passes below (12–16) are 100% net-new frontend work**, not edits to half-built
> screens.
>
> ⚠️ **Caveat carried over from the backend doc itself:** Pass 14 & 15
> (`PassGift`, `ChatMessageReport`, `NotificationPreference`, engagement report,
> `auto_renew`) were **never written up in the backend doc's own §2–§6** — the
> backend doc says to read `models.py`/`views.py`/`serializers.py` source directly
> for exact field names/endpoints. Everything below for those two passes is
> therefore an **architecture skeleton** (screens, files, wiring, data flow) built
> from what the change-log entries do confirm — the exact JSON field names for
> `PassGift`/`ChatMessageReport`/`NotificationPreference` serializers must be
> confirmed against real backend source before writing `fromJson` code.
> Pass 12 & 13 are fully documented in the backend doc — that part below is exact.

---

## 0. Updated module map — additions in **bold**, removals struck through

```
lib/liveclass/
├── models/
│   └── liveclass_models.dart          # + 5 new classes (§2)
├── services/
│   ├── liveclass_api_service.dart     # + 5 new *Api classes / method groups (§3)
│   └── liveclass_notification_handler.dart   # + 3 NotifType cases (§4)
├── theme/
│   └── liveclass_theme.dart           # unchanged
├── utils/
│   └── liveclass_datetime.dart        # unchanged
└── screens/
    ├── live_session_screen.dart             MODIFIED — chat reactions, typing,
    │                                         pin/unpin, unread badges, quick-poll
    ├── ~~request_join_screen.dart~~          DELETE — confirmed dead duplicate
    │                                         (frontend doc §8) — do this cleanup
    │                                         in the same PR since you're already
    │                                         touching the module
    ├── pass_management_screen.dart          MODIFIED — surface "allow gifting"
    │                                         toggle per ClassPass (if backend
    │                                         gates gifting per-pass — confirm)
    ├── my_passes_screen.dart                MODIFIED — auto-renew toggle,
    │                                         "gift this pass" action, renewal
    │                                         chain display
    ├── notifications_screen.dart            MODIFIED — 3 new NotifType icons
    ├── classroom_reports_screen.dart        MODIFIED — add a second tab for
    │                                         message-level reports (or split,
    │                                         see §1.4)
    ├── **pass_gift_claim_screen.dart**      NEW (§1.3)
    ├── **notification_preferences_screen.dart**  NEW (§1.5)
    ├── **poll_templates_screen.dart**       NEW (§1.6)
    ├── **session_engagement_report_screen.dart** NEW (§1.7)
    └── (all other existing screens)          UNCHANGED
```

---

## 1. Feature-by-feature plan

### 1.1 Chat reactions (Pass 12) — `ChatReaction`
**Where:** `live_session_screen.dart`, Chat subsystem cluster only. No new screen.

- **Models (`liveclass_models.dart`):** add two fields to the existing
  `ChatMessage` class — `reactionCounts: Map<String, int>` (emoji → count) and
  `myReaction: String?`. Both come from `ChatMessageSerializer`'s new fields, so
  this is a field-add to `ChatMessage.fromJson`, not a new class.
- **API (`liveclass_api_service.dart`):** add to `ChatMessageApi`:
  `react(sessionId, messageId, emoji)` → `POST /chat/{id}/react/`,
  `unreact(sessionId, messageId)` → `DELETE /chat/{id}/react/`.
- **Logic to add in `live_session_screen.dart`:**
  - `_sendReaction`/`_showReactionPicker` already exist for the *live-session
    floating reaction* feature (video-call emoji burst) — **do not merge these**.
    Chat-message reactions are a separate, per-message concept. Add new private
    methods `_reactToChat(messageId, emoji)` / `_removeChatReaction(messageId)`
    inside the **Chat** cluster, next to `_loadChat`/`_sendChat`/`_deleteChat`.
  - Long-press (or a small emoji-row under) each chat bubble → reaction picker →
    calls `_reactToChat`. Tapping an already-selected reaction calls
    `_removeChatReaction`.
  - Optimistic local update of `reactionCounts`/`myReaction` on tap, corrected
    on the next `chat.reaction` WebSocket event (see below) or next `_loadChat`.
- **Realtime:** the backend broadcasts a `chat.reaction` event
  (`message id` + full `reaction_counts`) over the existing session WebSocket —
  this module's WS handling isn't detailed in the frontend doc's current scope
  (no `consumers`/`realtime` equivalent listed there), so confirm where inbound
  WS session events are currently dispatched in `live_session_screen.dart`
  (likely the same place `chat.message`/`poll.created` events already land, if
  those are already wired) and add a `chat.reaction` case there. If the current
  frontend has **no WS listener wired at all yet** (worth confirming — the
  frontend doc doesn't describe one), chat reactions still work via polling
  (`_loadChat` refresh), just without live cross-device updates until that gap
  is closed separately.

### 1.2 Typing indicator (Pass 12)
**Where:** `live_session_screen.dart`, Chat cluster.

- No REST endpoint, no model, no DB — purely a WS message. Add outbound: fire
  `{"type": "typing"}` on the chat `TextField`'s `onChanged` (debounced, e.g.
  only once per 3–5s of continuous typing, not per keystroke). Add inbound: a
  `chat.typing` WS case → show "X is typing…" for ~3s, auto-clear client-side
  (matches backend's "no stopped-typing event" design — §11 Pass 12).
- Same WS-wiring dependency as §1.1 above.

### 1.3 Pass gifting (Pass 14) — `PassGift`
**Where:** new screen `pass_gift_claim_screen.dart` + entry points in two
existing screens. No frontend code exists for this today.

- **New model:** `PassGift` in `liveclass_models.dart` — fields to confirm
  against `PassGiftSerializer` source, but from the change-log's description
  expect at minimum: `id, classPassId/Title, gifter(UserMini), giftedTo(UserMini
  or email/username string — confirm), status(pending/claimed/expired/refunded),
  claimWindowExpiresAt, createdAt`. `refund_to_gifter()` and the 7-day
  `CLAIM_WINDOW_DAYS` are model-layer only — no frontend action needed for that
  part beyond displaying `status: expired/refunded` correctly once it happens.
- **New API group:** `PassGiftApi` in `liveclass_api_service.dart` —
  `send(classPassId, recipient)` → `POST`, `myGiftsSent()` → `GET`,
  `myGiftsReceived()` → `GET`, `claim(giftId)` → `POST .../claim/`. Exact paths
  need confirming against `urls.py`.
- **Entry point 1 — `pass_management_screen.dart` or `my_passes_screen.dart`:**
  add a "Gift this pass" action next to Buy — opens a small bottom sheet
  (recipient input, same "ID-only, no user-search" limitation flagged
  elsewhere in the frontend doc §8 — reuse that pattern, don't invent a search
  endpoint that doesn't exist) → calls `PassGiftApi.send`.
- **Entry point 2 — new `pass_gift_claim_screen.dart`:** shows a received gift
  (pass details, gifter name, countdown to `claimWindowExpiresAt`) with a Claim
  button → `PassGiftApi.claim` → on success, navigate into the normal
  `ClassroomDetailScreen` flow for that classroom (the claim call is what
  actually creates the `PassPurchase` on the backend, mirroring how
  `ClassJoinRequest.accept()` does it elsewhere in this app).
- **Notification tap routing:** `liveclass_notification_handler.dart` needs a
  new `NotifType` case (see §1.8) whose tap target is this new screen, passing
  the gift id from the push payload.
- **`my_passes_screen.dart` logic add:** show a "Gifted to you by X" badge on
  purchases that originated from a claimed gift (if the backend exposes a
  `PassPurchase.gift` back-reference — confirm) so the escrow/refund copy rule
  in §7 item 5 of the frontend doc still reads sensibly for a gifted pass.

### 1.4 Per-message chat reports (Pass 14) — `ChatMessageReport`
**Where:** `live_session_screen.dart` (report entry point) +
`classroom_reports_screen.dart` (review queue).

- **New model:** `ChatMessageReport` — expect `id, message(FK), reportedBy,
  reason, description, status, reviewedBy, adminNote, reviewedAt, createdAt` —
  i.e. the same shape as the existing `ClassroomReport`, just scoped to a
  message instead of a classroom. Confirm exact fields against source before
  coding `fromJson`.
- **New API:** add to (or alongside) the existing report API —
  `ChatMessageReportApi.create(messageId, reason, description)`,
  `.list()`, `.review(id, status, note)` for staff.
- **Logic to add in `live_session_screen.dart`:** long-press on a chat bubble →
  add "Report" alongside (or near) the reaction picker from §1.1 → small
  reason-picker sheet → `ChatMessageReportApi.create`. This is a **student/any
  participant** action, unlike moderation actions which are host-only.
- **Logic to add in `classroom_reports_screen.dart`:** this screen currently
  handles only `ClassroomReport` (classroom-level, platform-staff-only). Two
  options, pick one and note the decision in the file itself:
  1. **Add a second tab** ("Classrooms" / "Messages") inside the same screen,
     switching the list source and detail-card shape between
     `ClassroomReportApi` and `ChatMessageReportApi`.
  2. **New sibling screen** `chat_reports_screen.dart` reached from the same
     platform-staff entry point as a second icon.
  Given this screen is already platform-staff-only and role-gated identically,
  **option 1 (tabs)** is the smaller change and keeps one entry point — default
  to that unless the backend gives message reports meaningfully different
  staff-role gating.

### 1.5 Profanity/spam filter (Pass 14) — `moderation.py`
**Where:** `live_session_screen.dart`, `_sendChat` only. No new screen, no new
model — this is a backend-only enforcement point.

- **Logic to add:** `moderation.py` runs inside
  `ChatMessageViewSet.perform_create` — meaning a blocked message comes back as
  an error response, not a silent drop. `_sendChat` needs a specific
  error-handling branch: on whatever error `code` the backend returns for a
  filtered message (confirm exact code/status against `exceptions.py`'s
  normalized `{"detail","code",["errors"]}` shape), show an inline toast/snackbar
  like "Message blocked — please keep chat respectful" instead of the generic
  error handling `_sendChat` currently falls back to. **Do not retry the
  send** and do not add the message to the local optimistic list.
- No changes needed elsewhere — this is purely a new failure branch on an
  existing call.

### 1.6 Per-notification-type channel preferences + digest email (Pass 14)
**Where:** new screen `notification_preferences_screen.dart`. Digest email
itself is backend-only (no frontend surface — it's an email, not in-app).

- **New model:** `NotificationPreference` — expect one row per
  `(user, notifType)` or a single row per user with a JSON map of
  `{notifType: {push: bool, email: bool}}` (sms is a documented backend no-op
  per §1 of the backend doc, so likely omit an sms toggle entirely rather than
  showing a control that does nothing). Confirm shape against source — this
  materially changes whether the screen is "one row per type" (list of
  toggles) or "one form" (a settings object).
- **New API:** `NotificationPreferenceApi.get()` / `.update(prefs)`.
- **Screen content:** one row per existing `NotifType` (all 18 currently
  listed in `notifications_screen.dart`'s icon map, §23 of the frontend doc,
  plus the 3 new ones from §1.8 below = 21 rows) with push/email toggles per
  row. Group visually the same way `notifications_screen.dart` already groups
  notification categories, if it does, for consistency.
- **Entry point:** a settings/gear icon on `notifications_screen.dart`'s app
  bar, next to the existing unread-filter chip — pushes
  `NotificationPreferencesScreen`.
- **No changes needed to `liveclass_notification_handler.dart`** — preferences
  only affect whether the backend *sends* a push, not how the client *handles*
  one it receives.

### 1.7 Post-session engagement report (Pass 15)
**Where:** new screen `session_engagement_report_screen.dart`, teacher-facing.

- **New model:** `SessionEngagementReport` (name to confirm) — likely
  aggregates per-session: attendance count/list, average watch duration, chat
  message count, poll participation rate, hand-raise count. Exact shape
  unconfirmed — read `serializers.py` before coding.
- **New API:** `ClassSessionApi.engagementReport(sessionId)` → `GET`, probably
  `/sessions/{id}/engagement-report/` following this app's existing
  detail-action URL convention (`.../join/`, `.../token/`, etc.).
- **Entry point:** from `sessions_list_screen.dart`, a completed session's card
  gets a new "View report" action (host/co-teacher/moderator only, same
  `canManage` flag threading pattern as everywhere else in this module — §7
  item 6 of the frontend doc). Also reachable from `live_session_screen.dart`'s
  post-`_endSession` flow (offer to view the report right after ending).
- **Screen content:** simple stat cards/list (reuse existing card styling from
  `teacher_earnings_screen.dart` as the closest visual precedent already in
  the module) — no chart library currently listed as a dependency for this
  module, so start with plain stat rows unless the report data is genuinely
  time-series (then this needs a charting decision, not assumed here).

### 1.8 Auto-renew passes (Pass 15) — `PassPurchase.auto_renew`
**Where:** `my_passes_screen.dart` (toggle + status), `notifications_screen.dart`
+ `liveclass_notification_handler.dart` (3 new notif types).

- **Model change:** `PassPurchase` in `liveclass_models.dart` gains
  `autoRenew: bool`, `renewedFrom: int?` (purchase id), `renewedInto: int?`
  (confirm this field exists — change log mentions a "renewed_from/renewed_into
  chain"), `renewalFailedAt: DateTime?`.
- **API:** add to `PassPurchaseApi`: `setAutoRenew(purchaseId, bool)` →
  `PATCH`. Confirm whether this is its own endpoint or just a field on the
  existing purchase-update call.
- **Logic to add in `my_passes_screen.dart`:**
  - A toggle per active, unexpired purchase: "Auto-renew". Wire to
    `setAutoRenew`.
  - If `renewalFailedAt` is set, show a warning banner on that purchase card
    ("Auto-renew failed on {date} — renew manually" with a button that reuses
    the existing purchase flow) rather than silently showing it as expired.
  - If `renewedFrom` is set, this purchase is itself the *result* of a renewal
    — optionally show "Renewed from purchase #{renewedFrom}" as a small
    footnote for continuity, matching the module's general pattern of
    surfacing money-flow provenance (see §7 item 5 of the frontend doc on
    escrow copy).
- **New `NotifType` values (3, per backend change log Pass 16):**
  `PASS_AUTO_RENEWED`, `AUTO_RENEW_FAILED`, `PASS_GIFT_EXPIRED`. Add to:
  - `liveclass_models.dart`'s `NotifType` constants (currently 18 → 21).
  - `notifications_screen.dart`'s `_notifIcon` map (currently covers all 18 —
    this is the exact same kind of gap the frontend doc's §23 entry already
    describes being fixed once before for 7 other types; same fix shape here).
  - `liveclass_notification_handler.dart`'s `_handledTypes` — per the Quick
    Lookup table in the frontend doc (§9, "New notification type" row), this
    also may need entries in `_liveRoomTapTypes` if any of the three should
    deep-link somewhere specific (auto-renewed/failed → `my_passes_screen.dart`
    filtered to that purchase; gift expired → nowhere special, just the bell
    list, unless you want it to open `pass_gift_claim_screen.dart`'s sent-gifts
    view).

### 1.9 Chat pinning (Pass 13) — `ChatMessage.is_pinned`
**Where:** `live_session_screen.dart`, Chat cluster. Fully documented backend
side — no ambiguity here.

- **Model:** add `isPinned: bool`, `pinnedBy: UserMini?`, `pinnedAt: DateTime?`
  to existing `ChatMessage`.
- **API:** add to `ChatMessageApi`: `pin(sessionId, messageId)` →
  `POST .../pin/`, `unpin(sessionId, messageId)` → `POST .../unpin/`.
- **Logic:** host-only (`_can_moderate_session` boundary — same flag already
  threaded into this screen for other host actions). Add "Pin" to the same
  long-press menu as reactions (§1.1) and report (§1.4), host-visible only.
  Show the currently-pinned message as a persistent banner at the top of the
  chat panel (pattern: similar to how `notice_board_screen.dart` sorts pinned
  notices to top, but here it's a single dedicated slot, not a sort order,
  since backend enforces at-most-one-pinned-per-session). Handle
  `chat.pinned`/`chat.unpinned` WS events the same way as §1.1's `chat.reaction`
  (same WS-wiring dependency noted there).

### 1.10 Unread chat/poll counts (Pass 13) — `SessionReadState`
**Where:** `live_session_screen.dart` (mark-read) + `sessions_list_screen.dart`
(badge display).

- **New model:** `SessionUnreadCount` (or fold into `ClassSession` as optional
  fields `unreadChat: int?`, `unreadPolls: int?`, populated only where the
  backend includes them) — matches backend's `{"chat": N, "polls": N}` shape.
- **API:** add to `ClassSessionApi`: `unread(sessionId)` → `GET .../unread/`,
  `markRead(sessionId, {lastReadChatMessageId?, lastSeenPollId?})` →
  `POST .../mark-read/` (no body = mark everything current as read, per
  backend's documented default).
- **Logic to add in `sessions_list_screen.dart`:** fetch unread counts
  alongside the session list (or lazily per visible card) and show a small
  badge (chat icon + count, poll icon + count) on sessions with unread
  activity. Only relevant for sessions the user has already joined at least
  once — not upcoming/never-joined ones.
- **Logic to add in `live_session_screen.dart`:** call `markRead` on entering
  the Chat tab/panel and again on entering the Polls tab/panel (independently,
  since the model tracks them separately), and once more on screen dispose as
  a safety net.

### 1.11 Quick-poll templates (Pass 13) — `PollTemplate`
**Where:** new screen `poll_templates_screen.dart` (CRUD, classroom-scoped) +
modify the existing `_CreatePollSheet` inside `live_session_screen.dart`.

- **New model:** `PollTemplate` — `id, classroomId, createdBy, question,
  options: List<String>, createdAt`.
- **New API:** `PollTemplateApi` — `list(classroomId)`, `create(...)`,
  `update(...)`, `delete(id)` (standard CRUD, `_can_manage_classroom` gated —
  same boundary as `Assignment`/`Notice`/`ClassHoliday` per the backend doc).
  Plus on the existing poll API: `LivePollApi.quickCreate(templateId,
  sessionId)` → `POST /polls/quick-create/`.
- **New screen `poll_templates_screen.dart`:** simple list + create/edit/delete
  form (question + dynamic option list, same shape as the existing
  `_CreatePollSheet`'s option-list UI inside `live_session_screen.dart` — reuse
  that widget's option-input pattern rather than rebuilding it). Owner/admin
  only, same `canManage` threading convention as `coupons_screen.dart`/
  `staff_management_screen.dart`.
- **Entry point for the new screen:** `classroom_detail_screen.dart`'s
  `_openManageSheet`/`_manageTile` list — add a "Poll templates" tile, teacher
  tools section (per the Quick Lookup table pattern in frontend doc §9,
  "Manage-sheet entry point" row).
- **Logic to add in `live_session_screen.dart`'s `_CreatePollSheet`:** add a
  "Use a template" entry point (dropdown or a small "from template" button)
  that lists this classroom's templates and calls `quickCreate` instead of the
  manual create flow when one is picked. This fires `poll.created` on the
  backend exactly like a manual create, so no separate handling needed once
  the create call succeeds — the existing post-create refresh path applies.

---

## 2. `liveclass_models.dart` — full change summary

| Class | Change | Pass |
|---|---|---|
| `ChatMessage` | + `reactionCounts`, `myReaction`, `isPinned`, `pinnedBy`, `pinnedAt` | 12, 13 |
| `PassPurchase` | + `autoRenew`, `renewedFrom`, `renewedInto`(confirm), `renewalFailedAt` | 15 |
| `NotifType` | + `PASS_AUTO_RENEWED`, `AUTO_RENEW_FAILED`, `PASS_GIFT_EXPIRED` (18→21) | 16 |
| `PassGift` | **new class** | 14 |
| `ChatMessageReport` | **new class** | 14 |
| `NotificationPreference` | **new class** | 14 |
| `SessionEngagementReport` | **new class** (name TBC) | 15 |
| `SessionUnreadCount` (or `ClassSession` field-add) | **new class or field-add** | 13 |
| `PollTemplate` | **new class** | 13 |

Everything in this table needs its exact JSON field names confirmed against
real backend serializers before coding — the "Notes" column throughout §1
above flags exactly which ones are unconfirmed (Pass 14/15 items) vs. exact
(Pass 12/13 items, copied straight from the backend doc's own field lists).

## 3. `liveclass_api_service.dart` — full change summary

| API group | New/changed methods | Pass |
|---|---|---|
| `ChatMessageApi` | + `react`, `unreact`, `pin`, `unpin` | 12, 13 |
| `ClassSessionApi` | + `unread`, `markRead`, `engagementReport` | 13, 15 |
| `PassPurchaseApi` | + `setAutoRenew` | 15 |
| `LivePollApi` | + `quickCreate` | 13 |
| `PassGiftApi` | **new class**: `send`, `myGiftsSent`, `myGiftsReceived`, `claim` | 14 |
| `ChatMessageReportApi` | **new class**: `create`, `list`, `review` | 14 |
| `NotificationPreferenceApi` | **new class**: `get`, `update` | 14 |
| `PollTemplateApi` | **new class**: `list`, `create`, `update`, `delete` | 13 |

## 4. `liveclass_notification_handler.dart` change summary

- `_handledTypes` — add the 3 new `NotifType`s (§1.8).
- `_liveRoomTapTypes` — evaluate whether `PASS_AUTO_RENEWED`/
  `AUTO_RENEW_FAILED`/`PASS_GIFT_EXPIRED` need a tap target beyond the default
  bell-list behavior (§1.8 gives a suggested mapping — confirm with product
  before wiring).

## 5. Cleanup (do alongside, not blocking, the above)

- **Delete `request_join_screen.dart`.** Already flagged as a confirmed dead
  duplicate in the frontend doc's own §8 Known Issues — its header comment
  says it should be removed. Doing this in the same PR avoids a second
  "touch this module again just to delete one file" round-trip.
- **Resolve the `RequestJoinScreen` undefined-reference bug** (frontend doc
  §8, second bullet) — `classroom_detail_screen.dart._openRequestJoin()`
  references a class that doesn't exist anywhere in the module. This is
  unrelated to Pass 12–16 but sits in a file (`classroom_detail_screen.dart`)
  that §1.11 above already needs to touch (adding the poll-templates manage
  tile) — worth fixing in the same pass rather than opening a second PR into
  the same file.

## 6. Suggested build order

1. §1.9 pinning + §1.1 reactions + §1.2 typing (all three live inside the same
   Chat cluster of `live_session_screen.dart` — do them together, one PR).
2. §1.10 unread counts (touches `sessions_list_screen.dart` + the same Chat/
   Polls panels just modified in step 1 — natural follow-on).
3. §1.11 poll templates (new screen, moderate size, no cross-dependency on
   anything above).
4. §1.5 profanity-filter error handling (small, isolated, no new screen —
   quick win, do whenever convenient).
5. §1.6 notification preferences (new screen, isolated).
6. §1.8 auto-renew (touches `my_passes_screen.dart` + notification plumbing —
   do together with the 3 new `NotifType`s since they're the same feature's
   two halves).
7. §1.3 pass gifting (largest of the remaining items — new screen + two
   existing-screen entry points + notification wiring).
8. §1.4 chat message reports (depends on nothing above, but lowest priority
   per the backend doc's own stated priority order — "safety/moderation items
   ahead of monetization" was the *backend's* build order; for frontend,
   §1.5's filter and §1.9's pin are arguably higher-value quick wins first).
9. §1.7 engagement report (least urgent — teacher-facing analytics, no other
   feature depends on it).

Before starting #7–#9 specifically, confirm the exact backend field names
flagged throughout §1 as unconfirmed — those three passes are architecture
skeletons, not final specs, per the caveat at the top of this document.