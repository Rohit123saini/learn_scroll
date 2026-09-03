# LiveClass Module — Master Architecture & Logic Reference

> **This file is now the single source of truth for `lib/liveclass/`.**
> No further `.dart` source will be re-uploaded — every future task (new
> screen, bug fix, refactor, cross-screen change) must be reasoned about
> from what's written here. If something isn't in this doc, treat it as
> unknown/unverified rather than assumed.
>
> **This revision** promotes `classroom_detail_screen.dart` and
> `live_session_screen.dart` from 📋 SUMMARY-LEVEL to ✅ VERIFIED (full
> line-by-line read of both). These were the hub screen and the largest
> file in the module, and their read resolves several previously-open
> questions from the prior pass — see §5.3, §6.1, §6.4, §8.6, and the new
> §13 for what changed. `liveclass_models.dart`, `liveclass_api_service.dart`,
> and `liveclass_notification_handler.dart` were also re-uploaded this
> pass but are unchanged from the prior ✅ verified read — no new findings
> there, carried forward as-is.

---

## 0. Coverage Legend (read this first)

Every screen/file section below is tagged:

- **✅ VERIFIED** — full `.dart` source was read line-by-line. State
  variables, method bodies, API calls and UI structure below are accurate
  to that source.
- **📋 SUMMARY-LEVEL** — only known from cross-references, doc comments in
  other files, and an earlier partial architecture pass. API endpoints,
  purpose, and navigation edges are reliable; **exact state variables,
  private method names, and full UI structure are NOT verified** — don't
  assume a method signature or field name from this doc for these files
  without re-deriving it from the API service / model shapes that
  **are** verified, or asking for the specific detail needed.

**✅ VERIFIED files (32):** liveclass_theme.dart, liveclass_datetime.dart,
liveclass_models.dart, liveclass_api_service.dart,
liveclass_notification_handler.dart, liveclass_home_screen.dart,
banned_students_screen.dart, certificates_screen.dart,
chat_message_reports_screen.dart, classroom_purchases_screen.dart,
classroom_recordings_screen.dart, coin_wallet_screen.dart,
doubts_screen.dart, holidays_screen.dart, materials_screen.dart,
my_progress_screen.dart, my_reminders_screen.dart,
notification_preferences_screen.dart, notifications_screen.dart,
poll_templates_screen.dart, referral_screen.dart, request_join_screen.dart,
session_engagement_report_screen.dart, staff_management_screen.dart,
teacher_earnings_screen.dart, wishlist_screen.dart,
schedule_manager_screen.dart, sessions_list_screen.dart,
submission_grading_screen.dart, waitlist_screen.dart,
**classroom_detail_screen.dart, live_session_screen.dart**.

**📋 SUMMARY-LEVEL files (10):** assignments_screen.dart,
classroom_form_screen.dart, classroom_reports_screen.dart,
coupons_screen.dart, explore_screen.dart, join_requests_screen.dart,
my_passes_screen.dart, notice_board_screen.dart,
pass_gift_claim_screen.dart, pass_management_screen.dart.

If future work touches a 📋 file in a way that needs exact current state,
that gap should be surfaced explicitly. The two biggest, highest-uncertainty
files in the module (`classroom_detail_screen.dart`, the true hub, and
`live_session_screen.dart`, the largest file) are now both ✅ — remaining
gaps are private method names / exact widget trees inside the 10 smaller
screens left, not architecture-level unknowns.

---

## 1. Module Layout

```
lib/liveclass/
  models/liveclass_models.dart               ✅ — 55 confirmed classes/response-shapes + enums (see §4)
  services/liveclass_api_service.dart         ✅ — LiveClassApi facade, 26 sub-API classes, 2 sockets (see §5)
  services/liveclass_notification_handler.dart ✅ — FCM foreground/background + tap routing (see §9)
  theme/liveclass_theme.dart                  ✅ — design tokens, shared widgets, date fmt re-exports
  utils/liveclass_datetime.dart               ✅ — locale + IANA-timezone aware date/time formatting
  screens/                                    — 45 screen files total
```

External touchpoints (outside `lib/liveclass/`):
- `lib/services/auth_service.dart` — `AuthService.getToken()`, used both
  by any screen that streams an authenticated file download via `Dio`
  (Certificates ✅, Materials ✅, Submission Grading ✅, **Classroom Detail
  ✅ — confirmed this pass, see §6.1**) **and**, confirmed a prior pass, by
  `_Http.client()`'s own Dio interceptor in `liveclass_api_service.dart`
  (attaches `Authorization: Bearer <token>` to every REST call) and by
  both socket classes' `connect()` (JWT passed as a `?token=` query param
  on the WS URL).
- `lib/message/services/call_kit_service.dart` — `liveclass_notification_handler.dart`
  ✅ reuses `CallKitService.navigatorKey` (not its own key) to push routes
  from a notification tap, and does **not** import anything else from that
  file — no actual CallKit/ringing UI, just the shared navigator key.

---

## 2. Design System — `theme/liveclass_theme.dart` ✅

*(unchanged this pass.)*

### 2.1 Tokens

```dart
class LiveClassColors {
  static const navy = Color(0xFF030F27);          // primary text/icon/accent
  static const bg = Color(0xFFF0F2F5);             // screen background
  static const gradient = LinearGradient(colors: [Color(0xFFFF6A00), Color(0xFFEE0979)]); // orange→pink
  static const success = Color(0xFF2E7D32);
  static const successBg = Color(0xFFE8F5E9);
  static const warning = Color(0xFFED6C02);
  static const warningBg = Color(0xFFFFF3E0);
  static const danger = Color(0xFFC62828);
  static const dangerBg = Color(0xFFFDECEA);
  static const cardShadow = BoxShadow(color: Color(0x0A000000), blurRadius: 8, offset: Offset(0, 2));
}

class LiveClassSpacing { xs=4, sm=8, md=12, lg=16, xl=20, xxl=24 }
class LiveClassRadius  { card=14, chip=10, sheet=18 }
```

### 2.2 Shared functions/widgets (exact signatures — safe to rely on)

| Symbol | Signature | Behavior |
|---|---|---|
| `liveClassAppBar` | `AppBar liveClassAppBar(String title, {List<Widget>? actions, Widget? leading})` | White bg, navy fg, elevation 0.5, bold 16px single-line ellipsized title |
| `liveClassInputDecoration` | `InputDecoration liveClassInputDecoration(String hint, {String? label})` | filled grey50, chip-radius border, navy 1.4px focus border, dense |
| `LiveClassCard` | `LiveClassCard({child, padding = EdgeInsets.all(14), margin = EdgeInsets.only(bottom: 12), onTap})` | White rounded (14) container w/ cardShadow; wraps in `InkWell` only if `onTap` given |
| `LiveClassIconBadge` | `LiveClassIconBadge({icon, size=42, gradient, color})` | Rounded-square icon chip; default = the orange→pink gradient fill, white icon |
| `LiveClassStatusChip` | `LiveClassStatusChip({label, color, background})` | Small bold pill, 10.5px |
| `LiveClassEmptyState` | `{icon=Icons.inbox_outlined, title, subtitle, actionLabel, onAction}` | Returns a `ListView` (so pull-to-refresh still works over an empty list) |
| `LiveClassErrorState` | `{message, onRetry}` | Returns a `ListView` w/ error icon + retry button |
| `LiveClassLoading` | no args | Centered `CircularProgressIndicator(color: navy)` |

**Note (confirmed again this pass):** `classroom_detail_screen.dart` ✅ and
`live_session_screen.dart` ✅ both **hand-roll their own** loading/error/
app-bar UI (`_InlineMessage`, a bare `Scaffold`+`AppBar`) rather than using
`LiveClassErrorState`/`liveClassAppBar` — same structural drift already
tracked for the 5 screens in §8.5/§11.2, now confirmed to also apply to
the two biggest files in the module. Not re-litigated as a new finding,
just folded into that same existing gap.

### 2.3 Date/time formatting (re-exported from `utils/liveclass_datetime.dart`)

```dart
String liveClassFmtDate(DateTime d, [BuildContext? context])          // "Aug 29, 2026"
String liveClassFmtDateWeekday(DateTime d, [BuildContext? context])   // "Sat, 29 Aug 2026"
String liveClassFmtDateTime(DateTime d, [BuildContext? context])      // "Aug 29, 2026 · 6:00 PM"
```
All three call `.toLocal()` before formatting and use `intl`'s
`DateFormat` with the device/app locale (`Localizations.maybeLocaleOf`)
when `context` is passed. **Every screen must go through these — never
hand-format a raw `DateTime`.**

`kLiveClassMonths` / `kLiveClassWeekdays` — `@Deprecated`, English-only,
kept only for source back-compat. Do not use in new code.

**Screens carrying their own thin local wrapper** (still correctly
delegating to `liveClassFmtDate`/`DateFormat.jm`/`DateFormat.E` with
`.toLocal()`, just not literally the same three function names) — updated
list, confirmed this pass: `holidays_screen.dart` ✅ (no wrapper),
`classroom_purchases_screen.dart` ✅, `schedule_manager_screen.dart` ✅,
`sessions_list_screen.dart` ✅, `waitlist_screen.dart` ✅, and now
`classroom_detail_screen.dart` ✅ (own `_fmtDate`/`_fmtTime`/`_fmtDateTime`
module-level functions, explicitly comment-flagged in its own header as
the timezone/i18n fix — see §6.1). `live_session_screen.dart` ✅ does
**not** define its own date wrapper — it calls `intl`'s `DateFormat`
directly in exactly one place (`_shortDate`, a waitlist-panel helper, see
§6.4) and otherwise has almost no absolute-date UI (a live room is
inherently "right now").

---

## 3. `utils/liveclass_datetime.dart` ✅ — `LiveClassDateTime`

*(unchanged this pass.)* Separate, richer formatter class (theme.dart's
free functions are a thin re-export/subset of this). Construct via
`LiveClassDateTime.of(context)` — lazily loads the IANA tz database once
process-wide (`tz_data.initializeTimeZones()`), locale bound to the
widget tree's current locale at construction time.

| Method | Purpose |
|---|---|
| `date(DateTime d)` | locale `yMMMd`, `.toLocal()` first |
| `dateWeekday(DateTime d)` | `EEE, d MMM y` |
| `time(DateTime d)` | locale `jm` (12h/24h per platform convention) |
| `dateTime(DateTime d)` | `date + ' · ' + time` |
| `weekdayShort(DateTime d)` | e.g. "Mon" — **no `.toLocal()`**, used for calendar-strip labels where the caller already controls the date |
| `resolveScheduleInstant(ClassSchedule s, {DateTime? onDate})` | Resolves a **recurring** schedule's wall-clock `startTime` ("HH:mm:ss") in its stored IANA `timezone` to a real instant using `package:timezone`, then returns `.toLocal()`. Falls back to treating the time as already-device-local if the zone name is missing/unrecognised (matches backend's `tasks.py` fallback) |
| `scheduleTimeLabel(ClassSchedule s)` | e.g. `"6:00 PM your time (set as 10:00 AM Asia/Kolkata)"` — parenthetical only shown if the converted clock time actually differs from the raw stored time |

**Confirmed live consumers (updated this pass):** `schedule_manager_screen.dart`
✅'s `_scheduleCard()` calls `.scheduleTimeLabel(s)` on every schedule
card's subtitle chips — as documented previously — and this pass confirms
`classroom_detail_screen.dart` ✅'s embedded `_ScheduleTab._scheduleCard()`
does the exact same thing (`LiveClassDateTime.of(context).scheduleTimeLabel(s)`),
which resolves the timezone-audit fix's own historical note (§8.3) that
this specific tab was the one place the fix had **previously been
missed** — it is confirmed present now.

---

## 4. Data Models — `models/liveclass_models.dart` ✅

*(Re-uploaded this pass; content unchanged from the prior ✅ verified
read — no new findings. Full field-by-field inventory, the `_instantJson()`
timezone-audit fix, the `NotifType`/`kAllNotifTypesForPreferences` gap,
and every other detail from the prior pass all stand as previously
documented. See the prior pass's full write-up — condensed pointers below
for anything this pass's two newly-verified screens directly touch.)*

Relevant to this pass's two newly-verified screens:
- `MyPassStatus.accessLevel` (`owner`/`admin`/`active`/`expired`/`pending`/`none`)
  and `.pendingRequestId` are exactly what drives `ClassroomDetailScreen`'s
  entire branching UI (§6.1) — confirmed the screen reads these fields
  precisely as documented, no drift.
- `SessionJoinResult.session`/`.waitlisted`/`.startedNew` are exactly what
  `ClassroomApi.startOrJoin()` returns and what both `ClassroomDetailScreen._enterClass()`
  and `LiveSessionScreen._join()` (§6.1, §6.4) branch on.
- `BreakoutRoom` (public, shared) is confirmed genuinely consumed by
  `LiveSessionScreen` this pass (§6.4) — the model doc's own note that it
  exists "so both the API service and `live_session_screen.dart` can
  share it" is now a confirmed-true statement, not a forward-looking one.
- `ChatMessage`'s Pass 12/13 fields (`reactionCounts`, `myReaction`,
  `isPinned`, `pinnedBy`, `pinnedAt`, and its `copyWith`) are confirmed
  genuinely exercised by `LiveSessionScreen`'s chat panel (§6.4) — not
  dead scaffolding.
- `Notice.isPinned` is what drives the pinned-notice banner in
  `LiveSessionScreen` (§6.4) and the pin toggle in
  `ClassroomDetailScreen`'s `_NoticesTab` (§6.1).

---

## 5. API Surface — `services/liveclass_api_service.dart` ✅

*(Re-uploaded this pass; content unchanged from the prior ✅ verified
read — full facade/sub-API/exception detail all stands as previously
documented. One prior open question is now resolved — see §5.3 below.)*

### 5.1–5.2 — unchanged this pass.

The malformed `LiveClassApi.myEarnings()` (§5.1 in the prior pass) is
still present as transcribed and still flagged, unresolved, in §11.

### 5.3 Sockets — `LiveClassSocket` / `LiveClassUserSocket`

*(Base mechanics — envelope shape, reconnect/backoff, ping — unchanged
from the prior pass.)*

⚠️ **Resolved this pass — prior wiring-status contradiction (§11.12 in the
prior pass) is now settled with a direct read of both consumer screens:**

- **`LiveClassSocket(sessionId)`** ✅ **is wired into `live_session_screen.dart`**,
  confirmed directly: `late final LiveClassSocket _liveSocket = LiveClassSocket(widget.sessionId);`
  is connected and listened to in `_afterJoined()` (`_liveSocket.connect();
  _liveSocketSub = _liveSocket.events.listen(_onLiveSocketEvent);`), right
  after a successful `sessions/{id}/join/`. See §6.4 for the full handler.
  The api-service file's own header comment (carried over from an earlier
  revision, claiming "NOT YET WIRED INTO ANY SCREEN") is now stale —
  `live_session_screen.dart` was re-uploaded and re-read this pass and
  the wiring is real and confirmed, not just claimed.
- **Scope of what's actually wired, confirmed exactly (narrower than "full
  realtime"):** only two event families are handled —
  `presence.snapshot`/`presence.joined`/`presence.left` (feeds
  `_onlinePresenceUserIds`, a "who's actually connected right now" set
  distinct from the DB-backed, 8s-polled `_participants` list) and
  `participant.kicked` (routes to `_handleIWasKicked()` if the kicked
  user is the caller, else prunes that user from `_participants`/presence
  locally). Chat, polls, hand-raise, and recording-state changes are
  **not** delivered over this socket in the current source — those panels
  stay on their existing REST + polling-timer pattern (`_loadChat`/
  `_loadPolls`/`_startPolling`, see §6.4). This is a deliberate, explicitly
  commented scope choice in the source ("this pass's scope"), not a gap.
- **`LiveClassUserSocket`** ✅ **is wired into `classroom_detail_screen.dart`**
  (confirmed this pass, not previously verified either way) — connected in
  `initState()`, torn down in `dispose()`, listened to via `_onUserSocketEvent`
  for a `join_request.created`/`join_request.decided`-style event pair
  that live-updates the teacher's pending-count badge and a student's own
  pending→active/none transition without reopening the screen. See §6.1.

---

## 6. Screen Catalog — Full Detail

### 6.1 Hub / Entry Screens

#### `LiveClassHomeScreen` — `liveclass_home_screen.dart` ✅
*(unchanged this pass — see the prior pass's full write-up: 3-tab
`IndexedStack` — Explore/My Learning/Wallet — `_MyLearningTab`'s grid of
6 tiles, unread-badge bell, staff-only flag icon.)*

---

#### `ExploreScreen` — `explore_screen.dart` 📋
*(unchanged this pass — still summary-level. Navigates to
`ClassroomDetailScreen`, `ClassroomFormScreen`, `MyProgressScreen`.)*

---

#### `ClassroomDetailScreen` — `classroom_detail_screen.dart` ✅ (3,187 lines — promoted from 📋 this pass)

**The true hub — almost every management screen routes through here.**
Full line-by-line read confirms the prior pass's summary-level shape was
directionally accurate; below is the exact confirmed detail.

**Constructor:** `{required classroomId, Classroom? initial}` — `initial`
is an optional pre-fetched `Classroom` (e.g. the card tapped from Explore)
for instant first paint; the screen always refetches fresh data on open
regardless.

**On open, `_load()` fires 3 calls in parallel via `Future.wait`**
(`classrooms/{id}/`, `classrooms/{id}/my-pass/`, `classrooms/{id}/stats/`),
then a **4th, separate, best-effort** wishlist lookup (`wishlist.list()`,
scanned client-side for a matching `classroom.id` to pre-check the heart
icon — swallows its own errors, never blocks the other three). If the
resolved `MyPassStatus.accessLevel` is `owner`/`admin`, fires a 5th,
independent, best-effort call — `_loadPendingRequestCount()`
(`joinRequests.list(classroomId:, status: JoinRequestStatus.pending)`,
badges the manage-sheet's "Join Requests" tile and the app-bar settings
icon with `.count`).

**Realtime (confirmed this pass — resolves §5.3's open wiring question
for this socket):** `LiveClassUserSocket` is connected in `initState()`
and disposed in `dispose()`, alongside a **30-second `Timer.periodic`**
(`_statsTimer`) that silently re-fetches just `classrooms/{id}/stats/`
(no loading spinner, no touch of `_classroom`/`_myPass`) — the file's own
comment frames this as a deliberate, low-urgency polling fallback for
"enrolled count/rating going stale while this screen sits open in the
background," since there's no realtime push for those two numbers the way
there is for join requests. `_onUserSocketEvent(e)` switches on `e.type`:
`'join_request.created'` (teacher-side — bumps `_pendingRequestCount`
live) and (per the header comment) a `'join_request.decided'`-shaped
event that flips a pending student straight to active/none without
reopening the screen. Degrades silently to the existing load-on-open
behavior if the socket/channel layer is ever unreachable.

**Access-level branching** — confirmed exactly as the prior pass's
table, via 4 derived getters: `_accessLevel` (`_myPass?.accessLevel ??
'none'`), `_canManage` (`owner`/`admin`), `_hasFullAccess` (`_canManage
|| active || expired`), `_everHadAccess` (`!= 'none'`), `_isPending`
(`== 'pending'`):

| access_level | Tabs shown | Bottom bar |
|---|---|---|
| `owner`/`admin` | full 7 (About/Schedule/Materials/Notices/Doubts/Reviews/Assignments) | **"Enter Class"** (gradient) |
| `active` | full 7 | "Enter Class" (gradient) |
| `expired` | full 7 | "Renew Pass" (gradient) → `RequestJoinScreen` |
| `pending` | About + Reviews only | disabled "Request Sent — Waiting for Approval"; tap → `_confirmCancelJoinRequest()` |
| `none` | About + Reviews only | "Request to Join" → `RequestJoinScreen` |

⚠️ **Confirmed fix, matches the prior pass's summary exactly:** `owner`/
`admin` used to fall into a `default` branch and see "Request to Join" on
their **own** classroom — now explicitly its own switch case alongside
`active`, both mapped to "Enter Class" → `_enterClass()`.

**Tab set is keyed** (`ValueKey('tabs-${tabs.length}')`) on the
`DefaultTabController` — confirmed fix, code comment explains: `_load()`
can be re-invoked after several in-place actions (accepting a join
request, buying a pass, etc.), which can flip the tab count between 2 and
7 on a later build, not just once in `initState`; without the key, Flutter
tries to update the same controller element in place mid-detach, which
throws the framework's `_dependents.isEmpty` assertion.

**`_enterClass()` — confirmed to call the single unified endpoint, not a
multi-step client-side dance:** `ClassroomApi.startOrJoin(classroomId)`.
Branches on the result: `waitlisted` → snack; else pushes
`LiveSessionScreen(sessionId: session.id, session: session, initialResult:
result)` (this is the confirmed 3-arg call site referenced from §6.4's
constructor entry) and, if `result.startedNew`, an extra "Class started."
snack. On `LiveClassApiException`: `403` → `_showPassRequiredDialog()`
(offers "Request to Join"); `404` with `body['no_session'] == true` →
`_showNoSessionMessage()`, which best-effort fetches the classroom's
active schedules and composes a specific "no live class right now, next
class per schedule: `_stats.weeklyTiming`" message rather than a bare
generic string. The code comment explicitly notes this used to be up to
4 sequential client-side calls (list live → list scheduled → decide →
maybe-confirm → create → join) collapsed into this one server-side call,
so a flaky network now only has one request to retry.

**Wishlist toggle (`_toggleWishlist`)** — optimistic, with a confirmed
**double-tap re-entrancy guard** (`_wishlistBusy`) explicitly flagged in
a fix comment: without it, a second tap landing mid-flight read the
already-optimistically-flipped state and fired its own add()/remove()
against the server on top of the first, and the two responses could race
back in either order and silently leave the heart in the wrong final
state. Reverts to the pre-toggle id on failure + snacks.

**Share / Refer & Earn (both confirmed real, wired features, not
skeletons):**
- **Share sheet** (`_openShareSheet`) — `classrooms.share(classroomId)`
  (no `toUserId` = outside-app share). Shows the share URL/text in a
  copyable box, a "Copy Link" button, and a **"Share" button that opens
  the real OS share sheet via `share_plus`** (`SharePlus.instance.share(ShareParams(...))`),
  falling back to a clipboard copy if the native sheet fails to launch —
  confirmed as a genuine fix over an older clipboard-only version, per
  the code's own comment. If `classroom.referralEnabled == true && !_canManage`,
  the sheet also shows a "Refer & Earn `N`% commission" row with a button
  into `_openReferLink()`.
- **`_openReferLink()`** — `classrooms.referLink(classroomId)` →
  `ReferLinkResult`, shown in its own bottom sheet (commission %, URL,
  copy button).
- **`_openShareStats()`** (manage-sheet only) — `classrooms.shareStats(classroomId)`
  → `ClassroomShareStats`, shown in a `DraggableScrollableSheet`: total
  count, a `Wrap` of per-channel chips (`byChannel`), and a "Recent
  Shares" list (`recent`, sharer name + channel + datetime).

**Report classroom (`_openReportDialog`)** — visible only if `!_canManage
&& _everHadAccess` (i.e. any user who has ever held a pass, matching the
backend's own gate documented in §5.2's `ClassroomReportApi.file`).
Reason dropdown (`ReportReason` constants) + optional description →
`classroomReports.file(classroomId:, reason:, description:)`. Its
`TextEditingController` is disposed via the standard outer-method
try/finally pattern (§8.4) — confirmed a real fix here too, per an
explicit "memory leak" comment in the source.

**Manage sheet (`_openManageSheet`, owner/admin only)** — confirmed
`DraggableScrollableSheet` + `isScrollControlled: true`, with an explicit
fix comment: without `isScrollControlled`, the sheet capped at a screen
fraction and (being a plain scrolling `ListView` inside a non-scroll-
controlled sheet) everything past that cap — down to "Close Classroom" —
was clipped and unreachable, given 14+ tiles. Confirmed exact tile list,
in order (identical to the prior pass's table, now source-confirmed):
Edit Classroom → `ClassroomFormScreen`; Schedule Manager →
`ScheduleManagerScreen(canManage:true)`; Sessions → `SessionsListScreen(canManage:true)`;
Holidays → `HolidaysScreen`; Passes → `PassManagementScreen`; Purchases →
`ClassroomPurchasesScreen`; Coupons → `CouponsScreen`; My Earnings →
`TeacherEarningsScreen` (scoped); Staff → `StaffManagementScreen`; Join
Requests (badged w/ `_pendingRequestCount`) → `JoinRequestsScreen.inbox()`;
Certificates → `CertificatesScreen(canIssue:true)`; Poll Templates →
`PollTemplatesScreen`; Recordings → `ClassroomRecordingsScreen`; Banned
Students → `BannedStudentsScreen`; Reported Messages →
`ChatMessageReportsScreen`; Share Insights → in-screen (`_openShareStats`,
not a route); then a divider and 4 "full screen" variants — Materials,
Notice Board, Doubts, Assignments (`canManage: _canManage` passed to
each, letting the same full-screen widget serve read-only students
elsewhere); then a divider and, danger-styled, **Close Classroom**
(`_confirmCloseClassroom`).

**`_confirmCloseClassroom()`** — confirm dialog ("all active students will
be refunded... cannot be undone") → `classrooms.close(classroomId)` →
snacks the returned `passes_refunded` count → `_load()`.

**Every manage-sheet push confirmed to `.then((_) => _load())`** for
Schedule Manager, Sessions, and Join Requests Inbox specifically (these
three can change tab-relevant or badge-relevant state); the rest push
without a reload callback. `_openEditClassroom()` is a special case: on
return, if the result is the sentinel string `'closed'` or `'deleted'`,
it pops this screen entirely (the classroom is no longer manageable the
same way) rather than reloading in place.

**`RequestJoinScreen` build-break bug (§8.6) — confirmed fixed, straight
from this file's own header comment**, which documents the history in
detail: `_openRequestJoin()` always constructed
`RequestJoinScreen(classroomId:, classroom:)`, but the class didn't exist
— the file on disk under that name was a stale duplicate of
`JoinRequestsScreen` (same `.inbox()`/`.mine()` shape), never imported,
and would have been an ambiguous-import error if it had been. Confirmed
now fixed by giving `request_join_screen.dart` the actual, distinct
`RequestJoinScreen` class (§6.3's student pass-selection form) — the
import `join_requests_screen.dart` (for `JoinRequestsScreen.inbox()`,
used elsewhere in this same manage sheet) coexists safely with it since
they're now genuinely different class names.

**`MaterialType` collision (§8.7) — reconfirmed:** this file does
`import 'package:flutter/material.dart' hide MaterialType;`.

**Corrupt-cache-on-failed-download pattern (§8.1) — reconfirmed present**
in this file, listed in the prior pass as one of the 4 screens carrying
it; not independently re-quoted here since the shape is identical to
Certificates/Materials/Submission Grading's.

**Tabs (`_buildTabBody`)** — `About` (`_AboutTab`, stateless, renders
description/language/feature-toggle chips/policies from the `Classroom` +
`ClassroomStats`), `Schedule` (`_ScheduleTab`, see below), `Materials`
(`_MaterialsTab`), `Notices` (`_NoticesTab`), `Doubts` (`_DoubtsTab`,
`canAsk: _hasFullAccess && !_canManage`), `Reviews` (`_ReviewsTab`,
`canReview: accessLevel is active or expired`), `Assignments`
(`_AssignmentsTab`, `canSubmit: _hasFullAccess && !_canManage`). Each tab
is its own private `StatefulWidget` with `AutomaticKeepAliveClientMixin`
(`wantKeepAlive = true`) so switching tabs doesn't re-fetch/re-scroll —
confirmed uniform across all 6 stateful tabs.

**`_ScheduleTab`** — `Future.wait([schedules.list(classroomId:),
sessions.list(classroomId:)])` in parallel. Shows the recurring-pattern
list (`_scheduleCard`, uses `LiveClassDateTime.of(context).scheduleTimeLabel(s)`
— confirmed present, resolving §3/§8.3's "was this tab missed by the
timezone fix" open question: it was **not** missed, it's here) and the
next 3 upcoming sessions inline (`_sessions.take(3)`), with a "View All"
link to the full `SessionsListScreen` (now confirmed reachable by **every**
user with access, not just a manager — `canManage: widget.canManage` is
forwarded through, letting a student browse the full session
history/calendar too, which the tab's own comment calls out as a fix over
an earlier teacher-only version). `_enterSession(s)` pushes
`LiveSessionScreen(sessionId: s.id, session: s)` (2-arg call site — no
`initialResult`, since entering from a specific session card in this tab
hasn't already called join/token the way the bottom bar's `_enterClass()`
has). `_openWaitlist(s)` pushes `WaitlistScreen(sessionId:, classroomTitle:,
canManage: true)` — the confirmed teacher-view entry point already
documented in §6.3, now with a second confirmed call site alongside
`SessionsListScreen`'s.

**`_MaterialsTab`/`_NoticesTab`/`_DoubtsTab`/`_ReviewsTab`/`_AssignmentsTab`**
— each is a lighter, embedded-in-tab counterpart to the matching full-screen
manage-sheet variant (same API calls, `canManage`-gated create/edit/delete
affordances, own dialog-based create flows with the standard outer-method
`try/finally` controller-disposal pattern per §8.4). `_AssignmentsTab`'s
"View Submissions" action opens `_SubmissionsSheet` (a private, in-file
bottom-sheet counterpart to the full `SubmissionGradingScreen`, not a
navigation to that screen) — its own small grade dialog reuses the same
disposal pattern.

**`_confirmCancelJoinRequest()`** — reads `_myPass?.pendingRequestId`;
confirm dialog ("Keep Waiting" / "Cancel Request") → `joinRequests.cancel(requestId)`
→ `_load()`. Reuses the same `JoinRequestApi.cancel` already documented
for `RequestJoinScreen`/`JoinRequestsScreen`'s own "mine" tab — this is
just a third, newly-reachable call site for it, per the header comment.

⚠️ **New dependency confirmed this pass, not previously listed anywhere
in this module:** `package:share_plus` (for the real native OS share
sheet in `_openShareSheet`). Worth adding to the pubspec.yaml dependency
list alongside `live_session_screen.dart`'s own new dependencies below
if this doc is ever used to audit `pubspec.yaml` completeness.

---

### 6.2 Classroom Setup / Management Screens
*(unchanged this pass — `ClassroomFormScreen` 📋, `ScheduleManagerScreen` ✅,
`PassManagementScreen` 📋, `CouponsScreen` 📋, `StaffManagementScreen` ✅,
`BannedStudentsScreen` ✅, `PollTemplatesScreen` ✅, `NoticeBoardScreen` 📋
all carry forward exactly as the prior pass documented them. See the
prior pass's write-up for full detail on each — none of it is touched by
this pass's two newly-verified files beyond the cross-references already
folded into §6.1 above.)*

---

### 6.3 Enrollment / Access Flow
*(unchanged this pass — `RequestJoinScreen` ✅, `JoinRequestsScreen` 📋,
`ClassroomPurchasesScreen` ✅, `MyPassesScreen` 📋, `PassGiftClaimScreen`
📋 orphan, `WaitlistScreen` ✅ all carry forward exactly as documented
previously. This pass adds two new confirmed call sites into
`WaitlistScreen`'s teacher-view entry point — from `ClassroomDetailScreen`'s
`_ScheduleTab` (§6.1) in addition to the already-documented `SessionsListScreen`
one — but the screen's own internals are unchanged.)*

---

### 6.4 Sessions / Live Class Flow

#### `SessionsListScreen` — `sessions_list_screen.dart` ✅
*(unchanged this pass — full write-up carries forward from the prior
pass exactly, including the Hindi-string leaks (§8.10) and the
`isJoinable` host-bypass fix. One cross-reference updated: `_enterClass(s)`
pushes `LiveSessionScreen(sessionId:, session: s)` — confirmed this is
indeed a **2-arg** call site of the single constructor documented below,
not a hint of a second constructor as the prior pass's phrasing left
open.)*

⚠️ **§5.2's `SessionApi.list()` `?status=` tension — still unresolved.**
This screen's own client-side-filter comment wasn't re-verified this
pass (the screen file itself wasn't re-uploaded); the tension noted
previously stands exactly as before.

---

#### `LiveSessionScreen` — `live_session_screen.dart` ✅ (5,494 lines — promoted from 📋 this pass; still the largest file in the module)

**The actual live-classroom room UI.** Full line-by-line read this pass —
the prior pass's 📋 summary undersold how much is actually built here;
this is a substantially larger feature set than "video, chat, polls,
hand-raise, breakout rooms, whiteboard" implied.

**Confirmed single constructor (resolves the prior pass's open
question — there is exactly one, not two):**
```dart
LiveSessionScreen({
  super.key,
  required int sessionId,
  ClassSession? session,      // optional — instant header info (title, scheduled time)
  SessionJoinResult? initialResult,  // optional — skip this screen's own join() if the caller already joined
})
```
`session` alone (2-arg, no `initialResult`) is the `SessionsListScreen`/
`ClassroomDetailScreen._ScheduleTab`/`MyRemindersScreen` shape (screen
still does its own `sessions/{id}/join/` on open). `session` +
`initialResult` (3-arg) is `ClassroomDetailScreen._enterClass()`'s and
`WaitlistScreen._tryRejoin()`'s shape (skips straight to the room, no
extra join call) — both confirmed exact call sites this pass and the
prior one respectively.

**Room state machine (`_RoomState` enum):** `greenRoom → joining → inRoom
→ (passRequired | waitlisted | error | ended)`. `initState` skips
`greenRoom` entirely and goes straight to `joining` **only if**
`widget.initialResult != null` (the caller already completed their own
join) — otherwise the pre-join green room always runs first.

**Pre-join green room (`_initGreenRoom`/`_buildGreenRoom`)** — genuinely
new relative to the prior pass's summary, which didn't mention this at
all. Requests camera/mic OS permission up front (`permission_handler`),
starts a **local-only** LiveKit preview track (`_previewTrack`, entirely
separate from the real in-room `_lkRoom` — never touches the actual
session join or LiveKit connect), lets the user pre-toggle mic/cam and
flip front/back camera before ever entering, and tracks 4 distinct
permission states (`_DevicePermState`: unknown/granted/denied/
permanentlyDenied) so a denial renders its own explanatory tile
(`_greenRoomPlaceholder`) instead of a raw crash or a silent black
rectangle. Only on "Join Class" does it call the real `sessions/{id}/join/`
+ LiveKit connect (`_joinFromGreenRoom`) and dispose the preview track.

**`_join()`** — `sessions.join(sessionId)`. `waitlisted` → `_RoomState.waitlisted`.
Success → `_RoomState.inRoom` + fires `_afterJoined()` (`unawaited`).
`LiveClassApiException` with `statusCode == 403` → `_RoomState.passRequired`;
anything else → `_RoomState.error`.

**`_afterJoined()`** — the real "everything turns on" sequence, confirmed
in this exact order: connects & subscribes `_liveSocket` (§5.3); loads
chat, polls, participants eagerly (not lazily on first panel-open); seeds
`_isRecording` from whatever `ClassSession` is already known, then either
awaits or fires-and-forgets a fresh `sessions/{id}/` detail fetch
depending on whether `widget.session` was null (materials/doubts/notices
below need `classroomId`, which only exists once that fetch resolves, so
the awaited path blocks briefly only when there was no pre-fetched
session); loads materials, queries (doubts), and notices eagerly too (so
the "more" sheet's badge counts are accurate from first frame, not 0 until
tapped); loads the waitlist if host; loads breakout-room state for
**everyone** (silent), reasoning explicitly noted in-source: a student
joining mid-class while a breakout is already running should land
straight in their assigned room's banner, not only discover it by opening
the panel; starts the REST poll timers (`_startPolling`); and finally
kicks off the real LiveKit connect (`unawaited(_connectLiveKit())`).

**Realtime socket handling (`_onLiveSocketEvent`, confirmed exact scope —
see §5.3 for the cross-reference):**
- `presence.snapshot`/`.joined`/`.left` → maintains `_onlinePresenceUserIds`.
- `participant.kicked` → if the kicked `user_id` matches the caller's own
  (compared against the caller's LiveKit identity, `int.tryParse(_localIdentity())`),
  calls `_handleIWasKicked()`; otherwise prunes that user from
  `_participants`/presence immediately rather than waiting up to 8s for
  the next participant-list poll.
- `_handleIWasKicked()` sets a `_kickedBySocket` latch (confirmed
  deliberately checked by both `_scheduleAutoReconnect()` and the
  connectivity-restored listener, so a kicked user's network coming back
  doesn't trigger a quiet LiveKit reconnect attempt that would only hit
  the same dead end ~14s later), tears down LiveKit, and shows a
  non-dismissible "Removed from Session" dialog that pops both itself and
  this whole screen on OK.

**LiveKit integration — genuinely fully wired**, not stubbed:
`_connectLiveKit`/`_reconnectLiveKit`/`_wireLiveKitEvents`/`_disconnectLiveKit`
manage a real `lk.Room`. Confirmed features on top of basic connect:
- **Auto-reconnect with capped exponential backoff** (`_scheduleAutoReconnect`,
  `_maxAutoRetries = 3`), short-circuited the instant `connectivity_plus`
  reports the network is back (skips the rest of the backoff wait) —
  guarded off entirely once `_kickedBySocket` is set.
- **Active speaker highlighting**, **per-connection connection-quality
  icon** (own connection only) with a **one-time** "switch to audio-only"
  suggestion nudge on poor quality (`_suggestedAudioOnly` latch).
- **Battery-aware camera-off suggestion** (`battery_plus`, `_checkBatteryForCameraSuggestion`,
  also a one-time nudge).
- **Front/back camera flip**, independently tracked for the green room
  preview and the real in-room track (`_isFrontCameraGreenRoom` vs.
  `_isFrontCameraInRoom`).
- **Screen share** toggle (`_toggleScreenShare`).
- **Audio-only mode** (`_toggleAudioOnly`) — remembers the pre-toggle cam
  state (`_camOnBeforeAudioOnly`) to restore on exit.
- **App-lifecycle camera pause**: `WidgetsBindingObserver` turns the
  camera off on backgrounding (remembers prior state in
  `_camOnBeforeBackground`) and restores it on return; mic is deliberately
  left alone so audio-only listening keeps working backgrounded.
- **Grid view** (`_gridView`, everyone, not host-only) alongside a
  smaller default thumbnail-strip layout, and a **spotlight/pin** feature
  (`_setSpotlight`) — host can pin one participant's video large for
  everyone. Confirmed **not** backed by any model field — it's a pure
  LiveKit data-channel broadcast (`_sendSignal`/`_handleDataReceived`,
  topic `_kSignalTopic`), so it's session-local only: invisible to anyone
  not already in the room when it was set, and resets to nothing on a
  fresh join.
- **"Ask to unmute"** (`_requestUnmute`/`_approveUnmute`) — same
  data-channel signaling mechanism as spotlight.
- **Emoji reactions** (`_sendReaction`/`_addFloatingReaction`,
  👍❤️😂👏🎉🙌) — same data-channel mechanism, purely ephemeral,
  floating tiles that self-clear after a few seconds; a running
  session-local `_reactionTotalCount` badges the reaction button.
- **Live captions** (`speech_to_text`, on-device STT on the caller's
  **own mic only** — confirmed the file's header explains why remote
  participants' audio can't be captioned the same way: no per-frame tap
  into decoded remote audio in Flutter/LiveKit). Each device recognizes
  its own speaker locally and broadcasts finished lines over the same
  data channel as reactions/spotlight, tagged with the speaker's name —
  nothing is sent to any server, nothing persists. `_captionsUnavailable`
  gracefully covers devices with no OS STT engine rather than crashing.
- **In-app "mini view"** — explicitly **not** true OS-level Picture-in-
  Picture (that needs native platform code this file can't add); this is
  a draggable floating video tile that stays visible while a side panel
  (chat/whiteboard/materials) would otherwise cover the whole video area.
  Tapping it closes the open panel and returns to the full room view.
- **Whiteboard** (`_WhiteboardStroke`/`_WhiteboardPainter`, session-local
  drawing state keyed by stroke id) — draw/undo/clear/color/width, plus
  **export to PDF** (`_exportWhiteboardPdf`, via `pdf`/`printing`,
  rasterizing the `CustomPaint` through a `RepaintBoundary`) and **save
  to photo gallery** (`_saveWhiteboardToGallery`, via `gal`). Points are
  broadcast over the data channel too (`_wbHandlePoint`), so it's a
  shared live whiteboard, not a per-device local scratch pad, but — same
  as spotlight/reactions/captions — it's ephemeral: the file's own
  comment confirms there's no backend persistence, so the board is empty
  again for everyone once every participant has left.
- **Breakout rooms** — confirmed genuinely wired to the real backend
  (`LiveClassApi.breakoutRooms`, §5.2), **not** signaled peer-to-peer like
  spotlight/reactions/whiteboard: the file's own comment explains this
  deliberate choice — a student who briefly drops connection and rejoins
  should see their assigned room from the server, not lose it because a
  peer-to-peer signal was missed. Host creates (`_createBreakoutRooms`,
  `{room_count}`), assigns (`_assignToBreakoutRoom`, `{participant_id,
  room}`, `room: null` = back to main), and closes
  (`_closeBreakoutRooms`) via REST; a `Timer` (`_breakoutPollTimer`)
  keeps state fresh for everyone. `_myBreakoutRoom` getter resolves the
  caller's own assignment by matching their LiveKit identity string
  against each room's `participantIdentities`.
- **Session elapsed timer** (`_ElapsedTimerText`, a small dedicated
  `StatefulWidget` so a once-a-second tick doesn't rebuild the whole
  video-tile tree), set from `_lkFirstConnectedAt` on first successful
  connect.
- **Pinned notice banner** — the classroom's pinned `Notice` (loaded via
  `_loadNotices()`), shown as a dismissible strip above the video;
  dismissal is confirmed **local-only, per device, per open of this
  screen** — it does not unpin/delete the notice for anyone else.

**Recording** — `_startRecording`/`_stopRecording` call
`sessions/{id}/start-recording,stop-recording/`; `_isRecording` (mirrors
`ClassSession.isRecording`) is kept fresh for **every** participant (not
just whoever started it) via `_refreshSessionRecordingState()`, a silent
background poll — so a teacher starting recording from a different device
still shows "REC" correctly on this device.

**Hand raise** — `_toggleHandRaise` (own hand only, `sessions/{id}/hand/`)
and `_lowerHand` (host lowering someone else's, `sessions/{id}/hand/{uid}/lower/`).
`_syncOwnHandState`, called from every `_loadParticipants` refresh,
re-derives `_handRaised` from the server-truth `SessionParticipant.handRaisedAt`
so a stale optimistic tap from a flaky network never leaves the button
wrong for long.

**Side panels (`_openPanel`, one of 7 via `_PanelTab`):** chat, polls,
participants, materials (read-only — uploading stays on Classroom
Detail), queries/doubts (anyone asks, host answers), waitlist (host
only), breakout (host management UI). Each panel is backed by the
matching REST sub-API from §5.2, largely already documented there.

**Chat panel** — confirmed the Pass 12/13 features (`react`/`unreact`/
`pin`/`unpin`) are genuinely wired here, not dead: `_reactToChat`/
`_removeChatReaction`/`_showChatReactionPicker`, `_pinChat`/`_unpinChat`
(host-only, at most one pinned message per session, pinning a new one
auto-unpins the prior — matches §5.2's `ChatMessageApi` doc exactly).

**Polls panel** — `_CreatePollSheet` (its own private `StatefulWidget`)
supports both a manual create flow and a **"use a saved template"** flow
(`_useTemplate`/`_PollTemplatePickerSheet`) that calls
`PollApi.quickCreate({templateId, sessionId})` — this is the confirmed
real call site for `quickCreate` that `poll_templates_screen.dart`'s own
doc comment (§6.2, prior pass) said existed "in live_session_screen.dart,
not here."

**Waitlist panel (host only)** — `_loadWaitlist`/`_promoteFromWaitlist`.
`_shortDate(dt)` is this screen's one and only local date-formatting
helper (a small relative/short label for waitlist entries) — everywhere
else in this file that needs a date either doesn't (a live room has very
little absolute-date UI) or the one place it does isn't independently
duplicating `liveClassFmtDate` logic in a way worth flagging as drift.

**Muting / kicking (host)** — `_toggleParticipantMute` (per-participant),
`_muteAllParticipants` (bulk, own `_muteAllBusy` guard), `_kick`.

**`_endSession()`** (host) / **`_leave()`** (anyone) — both confirmed to
tear down LiveKit (`_disconnectLiveKit`) and, for `_leave()`, call
`participants/{id}/leave/` **only when `initialResult.participantId` is
non-null** — this is the confirmed mechanic behind the file header's own
claim that a `token()`-sourced reconnect (`participantId` null) skips the
"leave" cleanup call, since a reconnect never created a fresh participant
row to begin with.

**Setup / new dependencies this screen introduces (confirmed from its own
header comment, not previously listed anywhere else in this module):**
`livekit_client`, `permission_handler`, `wakelock_plus` (keeps the screen
awake for the session duration), `connectivity_plus`, `battery_plus`,
`gal`, `pdf` + `printing`, `speech_to_text`. Permissions: camera + mic
(iOS `NSCameraUsageDescription`/`NSMicrophoneUsageDescription`, Android
`CAMERA`/`RECORD_AUDIO`, optionally `BLUETOOTH_CONNECT` on Android 12+),
plus `NSSpeechRecognitionUsageDescription` on iOS for captions. Android
`minSdkVersion 24+` (flutter_webrtc requirement). The file's own comment
flags `lk.VideoTrackRenderer` and other track-publication field names as
matching `livekit_client` ~2.3.x specifically — worth checking the
CHANGELOG if `flutter pub get` ever resolves a different major version.

⚠️ **Explicitly out of scope, confirmed from the file's own header, not a
gap to "fix":** virtual/blurred background. The screen already depends on
`google_mlkit_selfie_segmentation`, but that package only segments single
still frames, while LiveKit's `LocalVideoTrack` publishes camera frames
straight from the native WebRTC capturer with no per-frame Dart hook to
intercept/blur/re-inject — doing this for real needs a custom
VideoProcessor at the platform-channel level, which this single Dart file
can't safely add. The comment is explicit that this is flagged honestly
rather than shipping a blur toggle that silently no-ops.

⚠️ **Confirmed still-open backend dependency, from the file's own
comment:** a Django migration for the `BreakoutRoom` model +
`SessionParticipant.breakout_room` field still needs to be generated and
applied before breakout rooms work against a real database — the Dart
side and the API surface are both ready, but this is a backend-side
prerequisite this file can't itself satisfy.

**`_PanelTab` enum:** `{chat, polls, participants, materials, queries,
waitlist, breakout}`.

**Private support classes confirmed in-file (not shared elsewhere):**
`_WhiteboardStroke`, `_WhiteboardPainter`, `_ElapsedTimerText`(+State),
`_FloatingReaction`(+Widget), `_CaptionLine`, `_CaptionOverlay`,
`_MiniViewTile`, `_CreatePollSheet`(+State), `_PollTemplatePickerSheet`(+State),
`_CenteredState` (shared small helper for the passRequired/waitlisted/
error/ended full-screen states).

---

#### `SessionEngagementReportScreen` — `session_engagement_report_screen.dart` ✅
*(unchanged this pass — see prior pass's write-up.)*

#### `ChatMessageReportsScreen` — `chat_message_reports_screen.dart` ✅
*(unchanged this pass.)*

#### `SubmissionGradingScreen` — `submission_grading_screen.dart` ✅
*(unchanged this pass. Note: `ClassroomDetailScreen`'s embedded
`_AssignmentsTab` has its own lighter, in-sheet `_SubmissionsSheet`
rather than navigating to this screen — see §6.1 — the two are separate,
parallel implementations of similar grading UI, not one reusing the
other.)*

---

### 6.5 Coursework / Content Screens
*(unchanged this pass — `MaterialsScreen` ✅, `NoticeBoardScreen` 📋,
`DoubtsScreen` ✅, `AssignmentsScreen` 📋, `CertificatesScreen` ✅ all carry
forward exactly as previously documented.)*

### 6.6 Account-wide "My Stuff" Screens
*(unchanged this pass — `CoinWalletScreen` ✅, `MyRemindersScreen` ✅,
`MyProgressScreen` ✅, `ReferralScreen` ✅, `TeacherEarningsScreen` ✅,
`NotificationsScreen` ✅, `NotificationPreferencesScreen` ✅,
`ClassroomReportsScreen` 📋 all carry forward exactly as previously
documented.)*

---

## 7. Cross-Screen Connection Graph

```
LiveClassHomeScreen
 ├─ ExploreScreen
 │   ├─ ClassroomDetailScreen ─────────────────┐
 │   ├─ ClassroomFormScreen                    │
 │   └─ MyProgressScreen                       │
 ├─ _MyLearningTab                             │
 │   ├─ MyPassesScreen                         │
 │   ├─ JoinRequestsScreen.mine()               │
 │   ├─ WishlistScreen ─────► ClassroomDetailScreen
 │   ├─ WaitlistScreen (My Waitlist, sessionId omitted) ─► LiveSessionScreen
 │   ├─ CertificatesScreen (my mode)             │
 │   ├─ MyRemindersScreen ──► LiveSessionScreen │
 │   ├─ NotificationsScreen ─► ClassroomDetailScreen, NotificationPreferencesScreen
 │   └─ ClassroomReportsScreen  (staff only)     │
 └─ CoinWalletScreen                             │
                                                  │
ClassroomDetailScreen ✅ ◄────────────────────────┘   (the hub — fully verified this pass)
 ├─ ClassroomFormScreen (edit)
 ├─ ScheduleManagerScreen (canManage:true)
 ├─ SessionsListScreen (canManage:true) ─┬─ LiveSessionScreen (2-arg: sessionId+session)
 │                                        ├─ WaitlistScreen (teacher view, sessionId set, canManage:true)
 │                                        └─ SessionEngagementReportScreen
 ├─ HolidaysScreen
 ├─ PassManagementScreen
 ├─ ClassroomPurchasesScreen
 ├─ CouponsScreen
 ├─ TeacherEarningsScreen (scoped)
 ├─ StaffManagementScreen
 ├─ JoinRequestsScreen.inbox()
 ├─ CertificatesScreen (teacher mode, canIssue)
 ├─ PollTemplatesScreen
 ├─ ClassroomRecordingsScreen
 ├─ BannedStudentsScreen
 ├─ ChatMessageReportsScreen
 ├─ MaterialsScreen (full screen, canManage)
 ├─ NoticeBoardScreen (full screen, canManage)
 ├─ DoubtsScreen (full screen, canManage)
 ├─ AssignmentsScreen (full screen, canManage) ──► SubmissionGradingScreen
 ├─ RequestJoinScreen (join/renew, now a genuinely-existing class — §8.6 fix confirmed)
 ├─ _ScheduleTab (embedded) ──┬─ SessionsListScreen (view all)
 │                             ├─ LiveSessionScreen (2-arg, per-session)
 │                             └─ WaitlistScreen (teacher view, per-session)
 ├─ _MaterialsTab / _NoticesTab / _DoubtsTab / _ReviewsTab / _AssignmentsTab (embedded, lighter dupes of the full-screen variants above)
 └─ LiveSessionScreen (3-arg: sessionId+session+initialResult, "Enter Class" via startOrJoin())

liveclass_notification_handler.dart ✅ (push tap routing — full detail in §9)
 ├─ class_reminder / session_live  ──► LiveSessionScreen  (deep room join)
 ├─ pass_auto_renewed / auto_renew_failed / pass_gift_expired ──► MyPassesScreen
 └─ everything else (classroom-scoped, i.e. any type with classroom_id) ──► ClassroomDetailScreen
```

**Orphans (not currently wired from anywhere in the module):**
- **`PassGiftClaimScreen`** — built, backend-ready, no push call site.
  Unchanged this pass (its own file and the notification handler's own
  file weren't both re-touched together this pass — status carried
  forward as still-orphaned).

---

## 8. Cross-Cutting Fixes & Patterns Already Applied

*(All entries below carry forward from the prior pass. Only §8.1, §8.6,
and §8.7 have new confirming evidence this pass — noted inline. §8.2–§8.5,
§8.8–§8.10 are otherwise unchanged and not re-typed in full; see the
prior pass for their complete text if needed. Condensed list below for
orientation.)*

### 8.1 Corrupt-cache-on-failed-download bug
Affects: Certificates ✅, Materials ✅, Submission Grading ✅, **Classroom
Detail ✅ — confirmed this pass** (previously listed but unverified;
`classroom_detail_screen.dart`'s own download helper follows the exact
try/catch-delete-rethrow shape documented here). Fix pattern unchanged
from the prior pass — see there for the full code shape.

### 8.2 Timezone / i18n bug — unchanged, carried forward.
### 8.3 `ClassSchedule` zone resolution — unchanged, carried forward, with
one resolved sub-note: `classroom_detail_screen.dart`'s embedded
`_ScheduleTab` — previously flagged in its own header comment as having
been **missed** by this fix — is now confirmed to actually use
`scheduleTimeLabel()` correctly (§3, §6.1). The historical "was missed"
note in this doc referred to an earlier version of the file; the version
read this pass has it.
### 8.4 `TextEditingController` leaks in bottom sheets — unchanged
pattern; `classroom_detail_screen.dart`'s `_openReportDialog` (§6.1)
confirmed as another real instance of this exact fix.
### 8.5 Design-system drift — unchanged; `classroom_detail_screen.dart`
and `live_session_screen.dart` both confirmed to independently hand-roll
their own loading/error/app-bar UI too (§2.2), same structural pattern as
the 5 screens already tracked here — not re-counted as a new finding,
just confirmed to also apply to these two.

### 8.6 Missing-class build break
`request_join_screen.dart` used to be a stale duplicate of
`JoinRequestsScreen`. **Confirmed fixed this pass, directly from
`classroom_detail_screen.dart`'s own header comment** (§6.1), which
documents the exact history and confirms `RequestJoinScreen(classroomId:,
classroom:)` now resolves correctly and coexists safely with the
`JoinRequestsScreen` import used elsewhere in the same file.

### 8.7 Flutter's built-in `MaterialType` collision
Both `materials_screen.dart` ✅ and `classroom_detail_screen.dart` ✅ — now
**both confirmed directly** (the latter was only inferred/expected in the
prior pass) — `import 'package:flutter/material.dart' hide MaterialType;`.
**New this pass:** `live_session_screen.dart` ✅ carries the exact same
`hide MaterialType` import, for the identical reason (the module's own
`MaterialType` enum vs. Flutter's widget-internal one) — a third
confirmed instance of this pattern, not previously known to apply to
this file.

### 8.8 Overstated refund dialog — unchanged, carried forward.
### 8.9 Previously-orphaned screens now wired in — unchanged, carried
forward; `classroom_detail_screen.dart`'s manage sheet (§6.1) is
confirmed as the actual wiring point for several of these
(`ChatMessageReportsScreen`, `ClassroomRecordingsScreen`,
`BannedStudentsScreen`, `TeacherEarningsScreen`, `PollTemplatesScreen`,
`ClassroomPurchasesScreen` all reachable from its manage sheet, confirmed
this pass).
### 8.10 Mixed-language (Hindi) string leaks — unchanged, carried
forward from the prior pass (`sessions_list_screen.dart`,
`schedule_manager_screen.dart`, and `liveclass_notification_handler.dart`'s
code comments). **Confirmed this pass: neither `classroom_detail_screen.dart`
nor `live_session_screen.dart` carries any of this pattern** — both are
fully English throughout, in UI copy and in code comments.

---

## 9. Notification System — `liveclass_notification_handler.dart` ✅

*(Re-uploaded this pass; content unchanged from the prior ✅ verified
read — no new findings. Full push-type table, `_handledTypes`,
`_liveRoomTapTypes`/`_passLifecycleTapTypes`, and wiring-expectations
detail all stand exactly as previously documented — see the prior pass's
§9 for the complete text.)*

One cross-reference newly confirmed this pass: `_liveRoomTapTypes`
(`{class_reminder, session_live}`) routes to `LiveSessionScreen(sessionId:)`
— now confirmed a valid, real call against the single constructor
documented in §6.4 (2-arg equivalent: `sessionId` only, no `session`/
`initialResult` — the screen does its own green room + join from a cold
push-tap start, exactly as the constructor's optionality is designed to
support).

---

## 10. Working Conventions to Preserve

*(Unchanged this pass — full text carries forward from the prior pass.
Two conventions get a fresh confirming data point from this pass's two
newly-verified files, noted inline rather than duplicating the whole
section:)*

- **Role/permission gating is always caller-supplied:** `classroom_detail_screen.dart`
  ✅ is itself the *source* of `canManage`/`canIssue`/etc. for nearly every
  other screen in the module (§6.1's manage-sheet tile list) — it derives
  `_canManage` once, locally, from `MyPassStatus.accessLevel`, and passes
  it down as a constructor bool to everything it pushes, matching the
  convention exactly. `live_session_screen.dart` ✅ derives its own
  `_isHost` locally from `_joinResult.role`/`.livekitRole` rather than
  taking it as a constructor param — a legitimate variant of the same
  convention, since a live-session role can only really be known from the
  join response itself, not decided by the pushing screen ahead of time.
- **Optimistic UI + revert-on-failure:** `classroom_detail_screen.dart`'s
  wishlist toggle (§6.1) is a confirmed instance, complete with the
  double-tap re-entrancy guard pattern also worth generalizing (see the
  new note below).

**New convention worth naming, first clearly observed this pass:**
optimistic-update methods that can be re-triggered before their first
call resolves (rapid double-taps) should guard re-entrancy with a
dedicated `bool _xBusy` flag checked at the top of the method — confirmed
in `classroom_detail_screen.dart`'s `_wishlistBusy` (§6.1), and effectively
the same shape already used elsewhere in the module for non-optimistic
busy-guards (`_actionBusy`, `_handBusy`, `_recordingBusy`, `_breakoutBusy`,
`_muteAllBusy` in `live_session_screen.dart` alone). Worth treating as the
standard guard for any new mutating action, optimistic or not.

---

## 11. Known Gaps / Follow-ups (as of this doc)

1. **`PassGiftClaimScreen`** — still orphaned (§7). Unchanged this pass.
2. **Design-system structural drift** — now confirmed to span **7**
   screens, not 5: the previously-tracked `ClassroomPurchasesScreen`,
   `WishlistScreen`, `WaitlistScreen`, `ScheduleManagerScreen`,
   `SessionsListScreen`, plus **`ClassroomDetailScreen`** and
   **`LiveSessionScreen`**, confirmed this pass (§2.2, §8.5). Same
   low-risk, low-priority cleanup as before — porting to
   `liveClassAppBar`/`LiveClassLoading`/`LiveClassErrorState` — just a
   larger list now that the two biggest files are known to share the gap.
3. **`NotificationPreference` model shape** — unresolved, unchanged.
4. **`SessionEngagementReport` fields** — unresolved, unchanged.
5. **3-way `NotifType` sync rule** — unresolved, unchanged from the prior
   pass (still confirmed broken for `kAllNotifTypesForPreferences`; still
   unconfirmed either way for `notifications_screen.dart`'s icon switch
   and `notification_preferences_screen.dart`'s label map, since neither
   was re-uploaded this pass).
6. **Mixed-language (Hindi) string leaks** — unchanged count. **Newly
   confirmed this pass: neither of the two newly-verified files
   (`classroom_detail_screen.dart`, `live_session_screen.dart`) adds to
   this list** — both are clean (§8.10).
7. **Recording playback inconsistency** (`SessionsListScreen`'s
   snack-only "Recording" button vs. `ClassroomRecordingsScreen`'s real
   `url_launcher` hand-off) — unresolved, unchanged; `sessions_list_screen.dart`
   wasn't re-uploaded this pass.
8. **`_fmtRelative` duplicated, not shared** — unresolved, unchanged.
9. **10 files remain 📋 SUMMARY-LEVEL** (§0, down from 12):
   `assignments_screen.dart`, `classroom_form_screen.dart`,
   `classroom_reports_screen.dart`, `coupons_screen.dart`,
   `explore_screen.dart`, `join_requests_screen.dart`,
   `my_passes_screen.dart`, `notice_board_screen.dart`,
   `pass_gift_claim_screen.dart`, `pass_management_screen.dart`. All
   remaining gaps in these are private method names / exact widget trees
   / state variable names — no more architecture-level (model/API-shape/
   hub-navigation) uncertainty compounds with them, since §4/§5/§6.1 now
   cover the pieces that used to be the biggest source of cross-file
   uncertainty.
10. **`LiveClassApi.myEarnings()` non-compiling method** (§5.1, prior
    pass) — unresolved, unchanged. Not touched by this pass's two screen
    reads (neither calls it directly in a way this pass's read could
    confirm/deny `TeacherEarningsScreen`'s exact behavior against it —
    that screen wasn't re-uploaded either).
11. **Push `type` vs. `NotifType` vocabulary mismatch** (§4/§9.1, prior
    pass) — unresolved, unchanged.
12. **RESOLVED this pass — `LiveClassSocket` wiring status.** Previously
    flagged as contradictory across sources (§5.3, prior pass). **Now
    confirmed definitively: it IS wired into `live_session_screen.dart`**,
    scoped exactly to presence + kick events (chat/polls/hand/recording
    stay on REST+polling) — see §5.3 and §6.4 for the full confirmed
    detail. No longer an open question.
13. **NEW this pass — `LiveSessionScreen`'s true feature scope was
    significantly undersold by the prior pass's 📋 summary.** The prior
    "video, chat, polls, hand-raise, breakout rooms, whiteboard" one-line
    description covers maybe a third of what's actually built: green
    room, full LiveKit lifecycle management (auto-reconnect, active
    speaker, connection quality, battery-aware suggestions), spotlight,
    ask-to-unmute, emoji reactions, live captions, in-app mini-view,
    whiteboard export (PDF + gallery save), and a pinned-notice banner
    are all real, wired, non-trivial features that weren't previously
    documented at all. Anyone doing UI/feature work against this screen
    should read §6.4 in full rather than relying on any older summary.
14. **NEW this pass — `ClassroomDetailScreen` confirmed as the concrete
    fix point for several previously-abstract "was fixed" claims.** The
    doc's §8.6 (missing-class build break), §8.1 (corrupt-cache download,
    4th confirmed screen), and several §8.9 orphan-wiring entries were
    previously sourced from cross-references or other files' comments
    about this screen; this pass reads the fixes directly at their source
    in `classroom_detail_screen.dart` itself. No behavior changed by this
    — just moved from "claimed" to "directly confirmed."
15. **NEW this pass — two new pubspec.yaml dependencies surfaced that
    weren't previously listed anywhere in this doc:** `share_plus`
    (`classroom_detail_screen.dart`'s native share sheet, §6.1) and the
    full list in §6.4 for `live_session_screen.dart` (`livekit_client`,
    `permission_handler`, `wakelock_plus`, `connectivity_plus`,
    `battery_plus`, `gal`, `pdf`+`printing`, `speech_to_text` —
    `google_mlkit_selfie_segmentation` is also referenced but explicitly
    unused for its originally-intended purpose, see §6.4's background-blur
    note). Worth a dependency audit against the real `pubspec.yaml` if
    one hasn't been done since these were added.
16. **NEW this pass — confirmed backend prerequisite still outstanding for
    breakout rooms:** a Django migration for `BreakoutRoom` +
    `SessionParticipant.breakout_room` still needs to be generated/applied
    (§6.4) — the Dart/API side is fully ready, but this is a genuine
    blocker for breakout rooms working against a real database, confirmed
    straight from `live_session_screen.dart`'s own header comment.

---

## 12. Prior-Pass Summary (models/API-service/notification-handler pass — for orientation)

Files re-verified that pass: `liveclass_models.dart` (2,423 lines),
`liveclass_api_service.dart` (2,602 lines),
`liveclass_notification_handler.dart` (327 lines). Net effect: §0
coverage went 27✅/14📋 → 30✅/12📋; §4/§5/§9 went from cross-reference
summaries to full inventories; found the malformed `myEarnings()`, the
push-type/`NotifType` vocabulary mismatch, the confirmed-current
`kAllNotifTypesForPreferences` gap, and (at the time) an unresolved
`LiveClassSocket` wiring-status contradiction. See §11 above for which of
those are still open.

## 13. This-Pass Summary (for quick orientation on what changed)

Files re-verified/newly-verified: `classroom_detail_screen.dart` (3,187
lines, **newly** ✅), `live_session_screen.dart` (5,494 lines, **newly**
✅). `liveclass_models.dart`, `liveclass_api_service.dart`, and
`liveclass_notification_handler.dart` were also re-uploaded this pass but
read as unchanged from their prior verified state — no new findings
there.

**Net effect on the doc:**
- §0 coverage legend: 30✅/12📋 → **32✅/10📋**.
- §6.1 (`ClassroomDetailScreen`) and §6.4 (`LiveSessionScreen`) went from
  short cross-reference-derived summaries to full method-by-method /
  feature-by-feature inventories.
- **Resolved:** the `LiveClassSocket` wiring-status contradiction
  (§5.3/§11.12) — confirmed genuinely wired, with exact scope (presence +
  kick only).
- **Resolved:** `LiveSessionScreen`'s constructor ambiguity — confirmed
  exactly one constructor, `{required sessionId, session, initialResult}`,
  both optional params independently meaningful.
- **Resolved:** the `RequestJoinScreen` missing-class bug (§8.6) — now
  confirmed fixed directly at its source-of-truth file, not just inferred.
- **New, not previously known:** `LiveSessionScreen`'s true feature scope
  (green room, full LiveKit lifecycle features, spotlight, captions,
  mini-view, whiteboard export, breakout rooms) — see §11 item 13.
- **New, not previously known:** two new pubspec.yaml dependencies
  (`share_plus`, and the fuller LiveKit-adjacent list already known to be
  needed but not previously enumerated in one place) — see §11 item 15.
- **New, not previously known:** a real, outstanding backend migration
  prerequisite for breakout rooms — see §11 item 16.
- Confirmed (not new, but newly verified rather than assumed): the
  `MaterialType` Flutter-collision workaround also applies to
  `live_session_screen.dart`; the corrupt-cache-download pattern also
  applies to `classroom_detail_screen.dart`; the design-system structural
  drift (hand-rolled AppBar/loading/error) also applies to both of this
  pass's files; neither file carries the Hindi-string-leak pattern found
  elsewhere in the module.

**Still needed for full coverage:** the 10 files listed in §11 item 9 —
all now purely private-implementation-detail gaps (method names, widget
trees), not architecture-level unknowns, since the hub screen and the
biggest file in the module are both now ✅.