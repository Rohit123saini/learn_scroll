# LiveClass Module — Technical Architecture Reference

> Maintainer note: Ye file poore `lib/liveclass/` module ka single source of
> truth hai — har screen, model, API method, aur unke relations. Future me
> jab bhi kisi screen/file me change chahiye hoga, is MD ko refer karke
> batana ki kaunsi file, kaunsa function/class, aur uska kya relation baaki
> module se hai — taaki bina poori codebase dobara bheje kaam ho sake.

---

## 0. Module Map (folder layout)

```
lib/liveclass/
├── models/
│   └── liveclass_models.dart          # all DTOs, fromJson/toJson
├── services/
│   ├── liveclass_api_service.dart     # LiveClassApi.* — every HTTP call
│   └── liveclass_notification_handler.dart  # FCM push -> local notif + tap routing
├── theme/
│   └── liveclass_theme.dart           # LiveClassColors/Spacing/Radius + shared widgets
├── utils/
│   └── liveclass_datetime.dart        # LiveClassDateTime — locale+tz aware formatting
└── screens/
    ├── explore_screen.dart                 (§1)  entry tab
    ├── classroom_detail_screen.dart         (§2)  HUB — everything routes through this
    ├── classroom_form_screen.dart           (§3)  create/edit classroom
    ├── schedule_manager_screen.dart         (§4)  recurring schedule CRUD
    ├── sessions_list_screen.dart            (§5)  session calendar/list + reminders
    ├── live_session_screen.dart             (§6)  the live room itself (LiveKit)
    ├── pass_management_screen.dart          (§7)  teacher: define passes
    ├── join_requests_screen.dart            (§8)  inbox (teacher) + mine (student)
    ├── my_passes_screen.dart                (§9)  student: purchase history
    ├── coin_wallet_screen.dart              (§10) student: coin ledger
    ├── materials_screen.dart                (§11) full-screen materials
    ├── notice_board_screen.dart             (§13) full-screen notice board
    ├── doubts_screen.dart                   (§14) full-screen doubts/Q&A
    ├── holidays_screen.dart                 (§15) off-day management
    ├── assignments_screen.dart              (§12 list) assignment list
    ├── submission_grading_screen.dart       (§12 grade) submit/grade one assignment
    ├── wishlist_screen.dart                 (§17) saved-for-later classrooms
    ├── coupons_screen.dart                  (§18) discount codes
    ├── staff_management_screen.dart         (§19) co-teacher/moderator/TA CRUD
    ├── waitlist_screen.dart                 (§20) my-waitlist + per-session roster
    ├── certificates_screen.dart             (§21) issue/view certificates
    ├── classroom_reports_screen.dart        (§22) platform-staff report queue
    ├── notifications_screen.dart            (§23) bell-icon notification list
    ├── classroom_purchases_screen.dart      (extra) teacher: per-classroom refund roster
    ├── my_reminders_screen.dart             (extra) student: manage session reminders
    ├── banned_students_screen.dart          (extra) owner/admin: ban/unban roster
    ├── classroom_recordings_screen.dart     (extra) browsable past-session recordings
    ├── teacher_earnings_screen.dart         (extra) teacher: earnings dashboard
    ├── referral_screen.dart                 (extra) account-wide: refer & earn
    ├── request_join_screen.dart             ⚠ DEAD/duplicate file — see §Known Issues
    └── liveclass_home_screen.dart           module shell (bottom nav: Explore/My Learning/Wallet)
```

Every screen imports the same three shared files: `models/liveclass_models.dart`,
`services/liveclass_api_service.dart`, `theme/liveclass_theme.dart` (plus
`utils/liveclass_datetime.dart` where dates/times need timezone conversion,
not just formatting).

---

## 1. Core Layer: `liveclass_models.dart`

Pure data layer — one Dart class per Django serializer, `fromJson` always
present, `toJson` only on writable models (omits server-controlled fields:
`teacher`, `student`, `sender`, `uploaded_by`, `created_by`, `status`, etc.,
mirroring DRF `read_only_fields`).

Key shared helpers: `_dt()`, `_int()`, `_num()` (+ nullable variants) for
tolerant JSON parsing; `_instantJson(DateTime d) => d.toUtc().toIso8601String()`
— **always used when serializing a full instant**, since locally-built
`DateTime`s (from date/time pickers) are local, not UTC, and Django's
`USE_TZ=True` interprets an offset-less timestamp in the *server's* zone.

| Model | Key fields | Notes |
|---|---|---|
| `UserMini` | id, username, fullName, profilePicture | nested everywhere |
| `Classroom` | teacher, classroomType(individual/organisation), title, subject, language, coverImage, flags (whiteboard/screenShare/chat/recording enabled), ratingAvg/Count, isActive, isFlagged | `toJson()` for create/update (file upload handled separately as multipart) |
| `MyPassStatus` | accessLevel (owner/admin/active/expired/none), hasAccess, canViewInternals, canEnterClass, expiresAt | drives `ClassroomDetailScreen`'s entire UI branch |
| `ClassroomStats` | ratingAvg/Count, enrolledCount, weeklyTiming, upcomingHolidays[] | |
| `ClassSchedule` | recurrenceType (specific_date/daily/weekday/weekend/weekly/monthly/yearly), daysOfWeek[], dayOfMonth, startDate/endDate, startTime "HH:mm:ss", durationMinutes, **timezone** (IANA name) | wall-clock time in a *named* zone, not an instant — see `LiveClassDateTime.resolveScheduleInstant` |
| `ClassSession` | classroomId/Title, scheduleId, roomId, scheduledStart/End, actualStart/End, status (scheduled/live/completed/cancelled), recordingUrl, isRecording, isJoinable | concrete occurrence |
| `SessionJoinResult` | roomId, participantId?, role(host/student), livekitRole/Url/Token, waitlisted, session?, startedNew | shape of `/join/`, `/token/`, `/start-or-join/` |
| `ClassPass` | passType(free/daily/weekly/monthly/yearly), title, price(coins), validityDays, maxClasses? | |
| `PassPurchase` | student, classPassId/Title/Type, coinsSpent, status(success/refunded/failed/pending), purchasedAt/expiresAt, classesAttended, isValid, **remainingBalance, coinsReleased, perDayRate, lastChargeDate** | escrow model — `coinsReleased` = paid to teacher for days taught, `remainingBalance` = still refundable |
| `ClassJoinRequest` | classroomId/Title, classPassId/Title/Price, student, couponCode, message, status(pending/accepted/rejected/cancelled), decisionNote, decidedBy/At, passPurchaseId | |
| `SessionParticipant` | sessionId, user, role(host/student), joinedAt/leftAt, handRaised/handRaisedAt | |
| `BreakoutRoom` | roomNumber, participantIdentities[] (stringified user ids = LiveKit identity) | shared between api service + live_session_screen |
| `ClassMaterial` | classroomId, sessionId?, uploadedBy, title, materialType(pdf/ppt/doc/image/video/link), file?, externalLink | |
| `ChatMessage` | sessionId, sender, message, sentAt, isDeleted | |
| `LivePoll` / `PollResponse` | question, options[], resultCounts{optionIndex:count}, isActive/closedAt | live-session only |
| `Assignment` / `AssignmentSubmission` | dueDate, maxScore / score, feedback, gradedAt, isLate | |
| `ClassroomReview` | rating(1-5), comment | |
| `ClassroomWishlistItem` | wraps full `Classroom` | |
| `ClassroomReport` | reason(scam/not_delivering/inappropriate/other), status(pending/reviewed/action_taken/dismissed), reviewedById, adminNote | 3+ pending reports on same classroom auto-flags it (backend) |
| `Coupon` | classroomId? (null = usable across all of teacher's classrooms), discountPercent/Amount, validFrom/Until, maxUses/usedCount, isValid | |
| `CoinTransaction` | txnType(credit/debit), reason(pass_purchase/refund/referral_bonus/topup/admin), amount, balanceAfter, referenceId | read-only ledger |
| `ClassroomStaff` | user, role(co_teacher/moderator/ta), addedAt | `createBody()` static helper |
| `SessionWaitlistEntry` | student, joinedAt, notified | |
| `Certificate` | student, certificateId, certificateFile? | |
| `ClassReminder` | sessionId, user, remindAt, channel(push/sms/email), isSent | |
| `ClassHoliday` | scheduleId? (null = whole classroom), date, reason, createdBy | |
| `ClassroomBan` | classroomId, student, bannedBy?, reason, createdAt | `createBody()` static helper for `POST .../ban/` |
| `SessionRecording` | classroomId/Title, scheduledStart, actualEnd?, recordingUrl | narrow read-only shape for `GET .../recordings/` — deliberately **not** the full `ClassSession` (backend only sends 6 fields; reusing `ClassSession.fromJson` would crash on missing `scheduledEnd`/`status`) |
| `Referral` | referred, bonusAmount, createdAt | one row per successfully-referred person |
| `MyReferralCode` | code, referralCount, totalBonusEarned, bonusPerReferral | response shape of `GET referrals/my-code/`, not tied to a stored model — `code` is computed server-side |
| `EarningsByDay` | date, amount | one point in `TeacherEarnings.last30Days` |
| `EarningsByClassroom` | classroomId/Title, totalEarned, sessionsCharged | one row in `TeacherEarnings.byClassroom` |
| `TeacherEarnings` | totalEarned, totalSessionsCharged, thisMonthEarned, last30Days[], byClassroom[] | response shape of `GET my-earnings/` (optional `?classroom=`) |
| `Notice` | priority(low/normal/urgent), isPinned, expiresAt/isExpired | |
| `ClassQuery` | classroomId, sessionId?, askedBy, question, status(open/answered), answer, answeredBy/At | = "Doubt" in UI |
| `AppNotification` | notifType (18 values — see NotifType below), classroomId/Title, sessionId, isRead, readAt | read-only |
| `LiveClassDashboard` | upcomingSessions[], teachingClassroomsCount, enrolledClassroomsCount, certificatesCount, wishlistCount, pendingJoinRequestsCount, unreadNotificationsCount | `GET dashboard/` |
| `PaginatedList<T>` | count, next, previous, results[] | generic DRF pagination wrapper used by almost every list call |

### `NotifType` constants (18 total)
`join_request_received/accepted/rejected`, `pass_refunded`, `session_reminder`,
`assignment_graded`, `query_answered`, `certificate_issued`,
`waitlist_promoted`, `classroom_flagged`, `notice_posted`, `session_live`,
`session_cancelled`, `assignment_posted`, `submission_received`,
`staff_added`, `review_posted`, `report_reviewed`, `generic`.

---

## 2. Core Layer: `liveclass_api_service.dart`

Single `LiveClassApi` static-locator class exposing 26 sub-API instances,
each wrapping one DRF ViewSet. `_Http.client()` builds the shared Dio
instance; `LiveClassApiException` wraps error responses with `.message`.

```
LiveClassApi.classrooms        -> ClassroomApi
LiveClassApi.classroomReports  -> ClassroomReportApi
LiveClassApi.schedules         -> ScheduleApi
LiveClassApi.sessions          -> SessionApi
LiveClassApi.breakoutRooms     -> BreakoutRoomApi
LiveClassApi.passes            -> ClassPassApi
LiveClassApi.joinRequests      -> JoinRequestApi
LiveClassApi.passPurchases     -> PassPurchaseApi
LiveClassApi.participants      -> ParticipantApi
LiveClassApi.materials         -> MaterialApi
LiveClassApi.chatMessages      -> ChatMessageApi
LiveClassApi.polls             -> PollApi
LiveClassApi.assignments       -> AssignmentApi
LiveClassApi.submissions       -> SubmissionApi
LiveClassApi.reviews           -> ReviewApi
LiveClassApi.wishlist          -> WishlistApi
LiveClassApi.coupons           -> CouponApi
LiveClassApi.coinTransactions  -> CoinTransactionApi
LiveClassApi.staff             -> ClassroomStaffApi
LiveClassApi.waitlist          -> WaitlistApi
LiveClassApi.certificates      -> CertificateApi
LiveClassApi.reminders         -> ReminderApi
LiveClassApi.holidays          -> HolidayApi
LiveClassApi.notices           -> NoticeApi
LiveClassApi.queries           -> QueryApi
LiveClassApi.notifications     -> NotificationApi
LiveClassApi.referrals         -> ReferralApi
LiveClassApi.dashboard()       -> GET liveclass/dashboard/
LiveClassApi.myEarnings({classroomId})  -> GET my-earnings/
```

### Full method surface (used by which screen)

**ClassroomApi** — `explore({search,language,mine,page})` [ExploreScreen] ·
`detail(id)` [ClassroomDetailScreen] · `create/update(...,coverImagePath)`
[ClassroomFormScreen] · `delete(id)` [ClassroomFormScreen — 30+ days old & no
active pass, else 400] · `startOrJoin(classroomId)` [ClassroomDetailScreen
"Enter Class"] · `close(id)` [ClassroomFormScreen — refunds every active pass
then deactivates] · `hasAccess(id)` · `myPass(id)` → `MyPassStatus`
[ClassroomDetailScreen — UI branch driver] · `stats(id)` → `ClassroomStats` ·
`ban({classroomId,studentId,reason})` → `ClassroomBan` (idempotent — banning
an already-banned student returns the existing ban, 200, not an error) /
`bans(classroomId)` → `List<ClassroomBan>` (⚠ **not** paginated — plain JSON
array, unlike almost every other list call in this file) / `unban({classroomId,
studentId})` [all three — `BannedStudentsScreen`] · `recordings(classroomId,
{page})` → `PaginatedList<SessionRecording>` (normally paginated, unlike
`bans`) [`ClassroomRecordingsScreen`].

**ClassroomReportApi** — `list({classroomId,status})` /
`file({classroomId,reason,description})` [ClassroomDetailScreen's
`_openReportDialog`] / `review(id,{status,adminNote})`
[ClassroomReportsScreen — platform staff only].

**ScheduleApi** — `list({classroomId})` / `create` / `update` / `delete` —
[ScheduleManagerScreen, ClassroomDetailScreen's embedded `_ScheduleTab`,
HolidaysScreen's dropdown source (read-only there)].

**SessionApi** — `list({classroomId,status})` / `detail(id)` / `create` /
`update` / `delete` [SessionsListScreen ad-hoc sessions] · `join(id)` /
`freshToken(id)` [LiveSessionScreen] · `end(id)` [LiveSessionScreen host] ·
`kick(sessionId,userId)` / `muteParticipant(...,muted)` [LiveSessionScreen
host controls] · `setHandRaised({raised})` / `lowerHand(sessionId,userId)` ·
`startRecording` / `stopRecording`.

**BreakoutRoomApi** — `list(sessionId)` / `create({sessionId,roomCount})` /
`assign({sessionId,participantId,roomNumber})` / `close(sessionId)` —
[LiveSessionScreen's `_openBreakoutSheet` flow].

**ClassPassApi** — `list({classroomId})` / `create` / `update` / `delete`
(server refuses delete once ever-purchased) — [PassManagementScreen].

**JoinRequestApi** — `list({classroomId,status})` / `detail(id)` /
`request({classroomId,classPassId,couponCode,message})`
[RequestJoinScreen — ⚠ see Known Issues] · `accept(id,{note})` (charges
coins, creates PassPurchase) / `reject(id,{note})` / `cancel(id)` —
[JoinRequestsScreen inbox+mine].

**PassPurchaseApi** — `myPurchases()` [MyPassesScreen] ·
`forClassroom(classroomId)` [ClassroomPurchasesScreen — teacher-scoped, added
alongside a backend fix since the ViewSet was previously hard-scoped to
"own purchases only"] · `detail(id)` · `refund(id)` [teacher, via
ClassroomPurchasesScreen — only `remainingBalance` returns to student] ·
`cancel(id)` [student self-service, via MyPassesScreen — same math].

**ParticipantApi** — `list({sessionId})` / `leave(id)`.

**MaterialApi** — `list({classroomId,sessionId})` / `upload({classroomId,
title,materialType,filePath,externalLink})` (multipart) / `update` /
`delete` — [MaterialsScreen, ClassroomDetailScreen's `_MaterialsTab`].

**ChatMessageApi** — `list(sessionId)` / `send({sessionId,message})` /
`delete(id)` — [LiveSessionScreen chat panel].

**PollApi** — `list(sessionId)` / `create({sessionId,question,options})` /
`vote(pollId,optionIndex)` / `close(pollId)` — [LiveSessionScreen].

**AssignmentApi** — `list(classroomId)` / `create(...,attachmentPath)` /
`delete(id)` — [AssignmentsScreen].

**SubmissionApi** — `list(assignmentId)` / `submit({assignmentId,filePath})`
/ `grade(id,{score,feedback})` — [SubmissionGradingScreen].

**ReviewApi** — `list(classroomId)` / `create({classroomId,rating,comment})`
/ `update(id,{rating,comment})` / `delete(id)` — [ClassroomDetailScreen About
tab reviews section].

**WishlistApi** — `list()` / `add(classroomId)` / `remove(wishlistEntryId)`
— [WishlistScreen; add happens from ClassroomDetailScreen's heart toggle].

**CouponApi** — `list({classroomId})` / `create` / `update` / `delete` /
`validate(code)` — [CouponsScreen; validate used wherever a student redeems
a code at purchase/join time].

**CoinTransactionApi** — `myLedger()` / `balance()` — [CoinWalletScreen].

**ClassroomStaffApi** — `list(classroomId)` / `add({classroomId,userId,
role})` / `updateRole(id,role)` / `remove(id)` — [StaffManagementScreen].

**WaitlistApi** — `myEntries({sessionId})` / `forSession(sessionId)` (=
`myEntries(sessionId:)` alias) / `leave(id)` / `promote(id)` —
[WaitlistScreen both modes; `promote` teacher-only].

**CertificateApi** — `list({classroomId})` / `issue({classroomId,
studentId,certificateFilePath})` — [CertificatesScreen].

**ReminderApi** — `list()` / `create(ClassReminder)` / `delete(id)` —
[MyRemindersScreen list/cancel; SessionsListScreen's bell creates].

**HolidayApi** — `list(classroomId)` / `create` / `delete` —
[HolidaysScreen].

**NoticeApi** — `list(classroomId)` / `create` / `delete` / `pin(id)` —
[NoticeBoardScreen, ClassroomDetailScreen's `_NoticesTab`].

**QueryApi** — `list(classroomId)` / `ask(ClassQuery)` /
`answer(id,answer)` — [DoubtsScreen].

**NotificationApi** — `list({isRead})` / `unreadCount()` / `delete(id)` /
`markRead(id)` / `markAllRead()` — [NotificationsScreen,
LiveClassHomeScreen's bell badge].

**ReferralApi** — `list()` → `PaginatedList<Referral>` (own ledger — people
successfully referred) / `myCode()` → `MyReferralCode` (own code +
redemption tally; `code` is computed server-side, not stored) /
`redeem(code)` (`POST referrals/redeem/` — only works once, only inside the
new-account signup window; surface `LiveClassApiException.message` verbatim
on 400) — [ReferralScreen].

**`LiveClassApi.myEarnings({classroomId})`** (static, not a sub-API
instance) — `GET my-earnings/`, optional `?classroom=` to scope to one
classroom the caller teaches (403 if they don't teach it). Teacher-only —
under the escrow design a co-teacher/moderator earns nothing themselves, so
this is never staff-visible — [TeacherEarningsScreen].

---

## 3. Core Layer: `liveclass_theme.dart`

Design-system source of truth. Every screen should import this instead of
hand-rolling colors/spacing.

- **`LiveClassColors`**: `navy (#030F27)`, `bg (#F0F2F5)`,
  `gradient (orange→pink #FF6A00→#EE0979)`, `success/warning/danger` (+ Bg
  variants), `cardShadow`.
- **`LiveClassSpacing`** / **`LiveClassRadius`**: xs–xxl / card,chip,sheet.
- **`liveClassAppBar(title,{actions,leading})`** — shared white/navy AppBar.
- **`liveClassInputDecoration(hint,{label})`** — shared form-field style.
- **Widgets**: `LiveClassCard`, `LiveClassIconBadge`, `LiveClassStatusChip`,
  `LiveClassEmptyState`, `LiveClassErrorState`, `LiveClassLoading`.
- **Date formatting free functions** (thin wrappers, no tz-database, just
  `.toLocal()` + `intl` `DateFormat`, locale from optional `BuildContext`):
  `liveClassFmtDate`, `liveClassFmtDateWeekday`, `liveClassFmtDateTime`.
  `kLiveClassMonths`/`kLiveClassWeekdays` are `@Deprecated` English-only
  leftovers kept only for source back-compat.

**Known drift history (fixed)**: explore_screen, pass_management_screen,
wishlist_screen, waitlist_screen, join_requests_screen,
classroom_purchases_screen previously hand-duplicated hex literals instead
of importing `LiveClassColors` — all now alias `const _kNavy =
LiveClassColors.navy` etc. If the palette ever changes, grep for `_kNavy =`
/ `_kBg =` / `_kGradient =` across screens to confirm every alias still
points at the shared token.

---

## 4. Core Layer: `liveclass_datetime.dart` (`LiveClassDateTime`)

Heavier sibling of the plain formatters in `liveclass_theme.dart` — adds
real **IANA timezone** conversion via `package:timezone`, needed because
`ClassSchedule` stores a wall-clock time (`startTime "HH:mm:ss"`) *in a
named zone* (`schedule.timezone`, e.g. `"Asia/Kolkata"`), not a UTC instant.

Instantiate per-build: `LiveClassDateTime.of(context)` (lazily loads the
tz database once, process-wide, via `_ensureTzData()`).

- `date(d)` / `dateWeekday(d)` / `time(d)` / `dateTime(d)` /
  `weekdayShort(d)` — for absolute instants (session times, createdAt, etc.)
  — same pattern as the theme file's free functions but instance-based.
- **`resolveScheduleInstant(ClassSchedule s, {onDate})`** — the core method:
  parses `s.startTime`, builds a `tz.TZDateTime` in `s.timezone`, converts
  `.toLocal()`. Falls back to treating the time as already-local if the
  zone name is bad/missing (mirrors the backend's own fallback).
- **`scheduleTimeLabel(s)`** — human string: `"6:00 PM your time"`, with a
  `"(set as 10:00 AM Asia/Kolkata)"` suffix only when the conversion
  actually changes the displayed clock time.

Used by: `ScheduleManagerScreen`, `ClassroomDetailScreen`'s `_ScheduleTab`
(any screen that shows a *recurring schedule's* time, not just a concrete
session instant).

**Historical bug this fixed (module-wide)**: `classroom_detail_screen.dart`,
`classroom_purchases_screen.dart`, `join_requests_screen.dart`,
`sessions_list_screen.dart`, `liveclass_theme.dart` itself, and (last one
fixed) `classroom_reports_screen.dart` all used to hand-roll an
English-only month array and never call `.toLocal()` on API DateTimes
(which are UTC per `_dt()`/`DateTime.parse()` on the 'Z'-suffixed strings
Django returns) — so every date/time rendered in the *server's* clock and
in English only, regardless of viewer locale/timezone. All now route
through either `liveClassFmtDate*` (theme) or `LiveClassDateTime` (this
file) depending on whether zone-conversion is actually needed.

---

## 5. Core Layer: `liveclass_notification_handler.dart`

`LiveClassNotificationHandler` (singleton `.instance`) — push-notification
display + tap-routing for the module, parallel to
`message/services/call_kit_service.dart` but lighter (no CallKit ring).

- **`_handledTypes`** (18, mirrors `NotifType`): the set this handler will
  process at all.
- **`_liveRoomTapTypes = {class_reminder, session_live}`** — the only two
  types that get the special "jump straight into `LiveSessionScreen`"
  treatment on tap; everything else opens `ClassroomDetailScreen`.
- **`registerChannel()`** — call once at app start; registers the
  `class_reminders` Android notification channel (High importance).
- **`showForeground(RemoteMessage)`** — only fires for `_liveRoomTapTypes`;
  everything else is assumed already shown by the app's generic
  `push_notification_service.dart` path.
- **`showBackground(Map data)`** — static, background-isolate-safe version
  (builds its own plugin instance — background isolates can't share
  instance fields).
- **`handleTap(Map data)`** — static; branches on `data['type']`:
  - `class_reminder` / `session_live` → `Navigator.push(LiveSessionScreen(sessionId))`
  - everything else → `Navigator.push(ClassroomDetailScreen(classroomId))`,
    or a no-op log if `classroom_id` is missing from the payload.

**Backend payload contract** (data keys per type) — documented in full in
the file header; the two "special" types carry `session_id`; every other
type carries `classroom_id` (recipient varies: teacher for
`join_request_received`/`classroom_flagged`/`submission_received`/
`review_posted`, student for `join_request_accepted/rejected`/
`waitlist_seat_open`/`report_reviewed`, enrolled students for
`session_cancelled`/`assignment_posted`, added staff for `staff_added`).

**Wiring required** (not in this file — lives in
`push_notification_service.dart`): 3 hooks — background handler, foreground
`onMessage` listener, and notification-tap handler — each needs to check
`data['type'] is in _handledTypes` and delegate here.

---

## 6. Screen-by-Screen Reference

### §1 `explore_screen.dart` — `ExploreScreen`
- **Purpose**: public classroom discovery/search feed. Entry tab of
  `LiveClassHomeScreen`.
- **API**: `ClassroomApi.explore({search,language,mine,page})`.
- **Navigates to**: `ClassroomDetailScreen` (tap card), `ClassroomFormScreen`
  (create new, presumably a teacher-only FAB).
- **State**: `_ExploreScreenState`; sub-widgets `_MineToggle`,
  `_LanguageChip`, `_ClassroomCard`, `_MessageState`.

### §2 `classroom_detail_screen.dart` — `ClassroomDetailScreen` (THE HUB)
- **Purpose**: central router for a single classroom — every other
  classroom-scoped screen is reached from here.
- **On open**, 4 parallel calls: `classrooms/{id}/`,
  `classrooms/{id}/my-pass/` (→ `MyPassStatus.accessLevel` drives the whole
  UI), `classrooms/{id}/stats/`, `wishlist-classrooms/` (pre-check heart).
- **UI branches on `accessLevel`**:
  - `owner`/`admin` → settings icon opens the **manage sheet** (see below),
    full tabs, no bottom CTA.
  - `active` → full tabs + "Enter Class" bottom bar → `startOrJoin()` →
    `LiveSessionScreen`.
  - `expired` → full tabs + "Renew Pass" bottom bar.
  - `none` → About + Reviews tabs only + "Request to Join" bottom bar →
    `RequestJoinScreen` (⚠ see Known Issues — this class is referenced but
    not defined in any provided file).
- **Manage sheet tiles** (`_openManageSheet`, owner/admin only) →
  `ClassroomFormScreen` (Edit) · `ScheduleManagerScreen` · `SessionsListScreen`
  · `HolidaysScreen` · `PassManagementScreen` · `ClassroomPurchasesScreen`
  (Purchases) · `CouponsScreen` · `TeacherEarningsScreen` (My Earnings,
  `classroomId` pre-scoped) · `StaffManagementScreen` ·
  `JoinRequestsScreen.inbox()` (badge = pending count) · `CertificatesScreen`
  (`canIssue: true`) · `ClassroomRecordingsScreen` (Recordings) ·
  `BannedStudentsScreen` · `MaterialsScreen`/`NoticeBoardScreen`/`DoubtsScreen`/
  `AssignmentsScreen` full-screen variants.
- **Embedded tabs** (`TabBarView`, each its own `StatefulWidget` +
  `AutomaticKeepAliveClientMixin`): About, Schedule (`_ScheduleTab`),
  Materials (`_MaterialsTab`), Notices (`_NoticesTab`), Doubts
  (`_DoubtsTab`), Assignments (`_AssignmentsTab`), Reviews — all pass
  `canManage`/`canAsk`/`canSubmit` down based on `accessLevel`.
- **Also owns**: flag/report icon → `_openReportDialog()` →
  `ClassroomReportApi.file()`; Close Classroom lifecycle action lives in
  `ClassroomFormScreen` (edit mode), not here.
- **`_ScheduleTab`** also opens `WaitlistScreen(sessionId:...)` when a
  session is full, and a full `SessionsListScreen` via
  `_openFullSessionsList`.

### §3 `classroom_form_screen.dart` — `ClassroomFormScreen`
- **Purpose**: single screen, two modes via `existing` param — Create
  (`POST classrooms/`) vs Edit (`PATCH classrooms/{id}/`).
- **Cover image**: multipart only if a new file was actually picked.
- **Lifecycle actions (edit mode only)**: Close Classroom (`POST
  classrooms/{id}/close/` — refunds every active paid pass, deactivates) ·
  Delete Classroom (`DELETE classrooms/{id}/` — backend 400s unless 30+
  days old AND no active paid pass; error surfaced verbatim).
- **Navigates to**: `PassManagementScreen`, `ScheduleManagerScreen` (likely
  quick-links after create).

### §4 `schedule_manager_screen.dart` — `ScheduleManagerScreen`
- **Purpose**: teacher CRUD over recurring `ClassSchedule`s.
- **Form behavior**: recurrence type drives extra required fields — `weekly`
  needs `daysOfWeek`, `monthly` needs `dayOfMonth`; others need neither.
- **`canManage`** param (default true) — same read-only-viewer pattern as
  `_ScheduleTab`.
- Uses `LiveClassDateTime` for the tz-aware start-time display.

### §5 `sessions_list_screen.dart` — `SessionsListScreen`
- **Purpose**: full session history + upcoming queue for one classroom.
  Distinct from the "next session" summary shown inline on
  ClassroomDetail's About tab.
- **Layout**: 14-day horizontal date strip (poor-man's calendar, dotted on
  days with a session) above a status-filtered list.
- **`canManage`**: true → create/edit/delete ad-hoc sessions, end session;
  false → read-only + "Enter Class" only.
- **Reminder bell** (fix, screen-architecture audit): every upcoming
  session card has a bell → `_ReminderSheet` (offset + channel picker) →
  `ReminderApi.create()`. Companion read/cancel UI is `MyRemindersScreen`.
- **Navigates to**: `LiveSessionScreen`, `WaitlistScreen` (when full).

### §6 `live_session_screen.dart` — `LiveSessionScreen` (LARGEST FILE, ~4800 lines)
- **Purpose**: the actual live classroom room — LiveKit-based video/audio,
  self-contained (just pass `sessionId`; does the `join()` dance itself:
  200→render room, 403→"pass required", 202→"waitlisted"). Pass
  `initialResult` to skip straight to the room if caller already joined.
- **Major subsystems** (each a cluster of private methods on
  `_LiveSessionScreenState`):
  - **Green room / pre-join**: `_initGreenRoom`, `_toggleGreenRoomCam/Mic`,
    `_joinFromGreenRoom`, `_flipGreenRoomCamera`.
  - **LiveKit connection**: `_connectLiveKit`, `_reconnectLiveKit` (auto +
    manual, with `_scheduleAutoReconnect`), `_wireLiveKitEvents`,
    `_disconnectLiveKit`, mic/cam/screen-share toggles.
  - **Data-channel signaling**: `_sendSignal`/`_handleDataReceived` — custom
    payloads for spotlight, unmute-request/approve, reactions, whiteboard
    strokes, captions.
  - **Recording**: `_startRecording`/`_stopRecording`/
    `_refreshSessionRecordingState`.
  - **Hand-raise**: `_toggleHandRaise`, `_lowerHand`, `_syncOwnHandState`.
  - **Reactions**: `_sendReaction`, `_addFloatingReaction`,
    `_showReactionPicker` → `_FloatingReactionWidget`.
  - **Live captions**: `_toggleCaptions`, `_startCaptionListenBurst`,
    `_addCaptionLine` → `_CaptionOverlay`.
  - **Breakout rooms**: `_loadBreakoutRooms`, `_createBreakoutRooms`,
    `_assignToBreakoutRoom`, `_closeBreakoutRooms`, `_openBreakoutSheet`.
  - **Whiteboard**: `_openWhiteboard`, `_wbStartStroke/_wbAppendPoint/
    _wbEndStroke/_wbHandlePoint/_wbClear/_wbUndo`,
    `_exportWhiteboardPdf`, `_saveWhiteboardToGallery` (uses `gal` package)
    → `_WhiteboardStroke`/`_WhiteboardPainter`.
  - **Chat**: `_loadChat`, `_sendChat`, `_deleteChat` → uses
    `ChatMessageApi`.
  - **Polls**: `_loadPolls`, `_vote`, `_closePoll`, `_createPoll` →
    `_CreatePollSheet`, uses `PollApi`.
  - **Materials/Doubts/Notices/Waitlist panels**: inline mini-versions of
    the full-screen equivalents, all with `{silent:false}` refresh pattern
    — `_loadMaterials/_copyMaterialLink`, `_loadQueries/_askQuery/
    _answerQuery`, `_loadNotices`, `_loadWaitlist/_promoteFromWaitlist`.
  - **Participants panel**: `_loadParticipants`, `_toggleParticipantMute`,
    `_muteAllParticipants`, `_kick`.
  - **Session lifecycle**: `_endSession` (host), `_leave` (everyone).
  - **Device/network resilience**: `_flipCamera`,
    `_checkBatteryForCameraSuggestion` (uses `battery_plus`),
    `_toggleAudioOnly` (bandwidth saver), reconnect-on-network-restore (via
    `connectivity_plus`), `wakelock_plus` keeps screen on for the session.
- **Setup dependencies** (see file header): `livekit_client`,
  `permission_handler`, `wakelock_plus`, `connectivity_plus`,
  `battery_plus`, `gal`. Also needs Camera/Mic platform permissions.
- **Private support classes**: `_WhiteboardStroke/_WhiteboardPainter`,
  `_ElapsedTimerText(State)`, `_FloatingReaction(Widget)`,
  `_CaptionLine`/`_CaptionOverlay`, `_MiniViewTile`, `_CreatePollSheet(State)`.
- **Reached from**: `ClassroomDetailScreen` ("Enter Class"),
  `SessionsListScreen`, `MyRemindersScreen`, `WaitlistScreen` (retry-join),
  `LiveClassNotificationHandler.handleTap` for `class_reminder`/
  `session_live` pushes.

### §7 `pass_management_screen.dart` — `PassManagementScreen`
- **Purpose**: teacher defines `ClassPass` offerings for a classroom.
- **Delete** refused server-side once ever-purchased → offer Pause
  (`PATCH is_active=false`) instead; delete only offered while
  never-purchased is plausible. **Update** also refused if it would
  retroactively shrink what active paid holders already bought (price↑,
  validity/maxClasses/type↓) — surfaced verbatim.
- Sub-widget: `_PassEditorSheet`.

### §8 `join_requests_screen.dart` — `JoinRequestsScreen` (two named ctors)
- **`JoinRequestsScreen.inbox({classroomId, classroomTitle})`** — teacher/
  co-teacher/moderator inbox for one classroom. Accept (`POST .../accept/`
  — charges coins, creates `PassPurchase`) / Reject (`POST .../reject/`),
  both with optional note.
- **`JoinRequestsScreen.mine()`** — student's own requests across every
  classroom (backend scopes with no `classroom` param). Pending → Cancel
  (`POST .../cancel/`).
- Shared status-tab UI, card content/actions differ per mode.
- Reached from: `ClassroomDetailScreen` manage sheet (inbox),
  `LiveClassHomeScreen`'s My Learning tab (mine, as "My Requests").

### §9 `my_passes_screen.dart` — `MyPassesScreen`
- **Purpose**: student's own purchase history, read-only + self-service
  Cancel.
- **API**: `PassPurchaseApi.myPurchases()`.
- **Cancel** (fix — previously missing UI for an existing backend/API
  method): `passPurchases.cancel(id)`, only offered while `status==success`
  and unexpired (expired ones are swept automatically by a backend job).
  Confirmation dialog always quotes `remainingBalance`, never `coinsSpent`.
- Shows escrow split (`coinsReleased` vs `remainingBalance`) only once
  something's actually been released or the pass has expired with money
  stuck — avoids redundant display on a fresh purchase.
- Reached from `LiveClassHomeScreen`'s My Learning tab.

### §10 `coin_wallet_screen.dart` — `CoinWalletScreen`
- **Purpose**: read-only coin ledger + current balance. Coins only ever
  change as a side effect of other actions elsewhere (never directly here).
- **API**: `coinTransactions.myLedger()` + `.balance()` in parallel.
- Bottom-nav tab #2 (`LiveClassHomeScreen`'s "Wallet").
- Icon/label mapping per `reason` (pass_purchase/refund/referral_bonus/
  topup/admin) via `_reasonLabel`/`_reasonIcon`.

### §11 `materials_screen.dart` — `MaterialsScreen`
- **Purpose**: full-screen materials list (teacher upload/delete via
  `canManage`, everyone downloads/opens).
- **Download pattern** (shared with `certificates_screen.dart`): Dio
  streams to a temp file with auth header; **corrupt-cache fix** — a
  failed download now deletes its own partial file in a try/catch before
  rethrowing, so the next tap retries fresh instead of replaying a
  permanently-broken cached file forever.
- External links: copied to clipboard (no in-app browser wired up yet).
- Note: hides Flutter's own `MaterialType` (Material widget enum) via
  `hide MaterialType` import — this module's `MaterialType` (pdf/ppt/doc/
  image/video/link) comes from `liveclass_models.dart`.

### §13 `notice_board_screen.dart` — `NoticeBoardScreen`
- Priority-coded cards (low/normal/urgent), pinned sorted to top.
- `canManage` → post/pin/delete; everyone else reads.

### §14 `doubts_screen.dart` — `DoubtsScreen`
- Ask (any user with access) / Answer (`canManage`) — flips
  `status: open → answered`.
- Sorted: open first, then newest.

### §15 `holidays_screen.dart` — `HolidaysScreen`
- **Purpose**: off-day CRUD, owner/admin only.
- `scheduleId: null` → off across every schedule in the classroom;
  specific schedule → scopes the off-day to just that recurring slot.
- Backend's session-generation job auto-skips these dates — this screen
  only manages the list.
- Schedule dropdown sourced from `ScheduleApi.list()` (read-only here).

### §12 `assignments_screen.dart` — `AssignmentsScreen`
- List + create (teacher: title/description/attachment/dueDate/maxScore) +
  delete. Tapping a card → `SubmissionGradingScreen`.

### §12 (grade half) `submission_grading_screen.dart` — `SubmissionGradingScreen`
- Teacher: sees every student's submission, grades (score capped at
  `maxScore` + feedback). Student: sees brief + submit button or own
  status/grade. Assumes `submissions/` list is pre-scoped to "own" for
  non-managers server-side (same pattern as join-requests).

### §17 `wishlist_screen.dart` — `WishlistScreen`
- Saved-for-later classrooms, grid layout. `GET wishlist-classrooms/`
  (self-scoped by backend) / `DELETE .../{id}/`. **Adding** happens from
  `ClassroomDetailScreen`'s heart toggle, not here.

### §18 `coupons_screen.dart` — `CouponsScreen`
- Owner/admin only. Backend always scopes to "coupons I created"; narrowed
  here by `?classroom=`. At least one of discountPercent/discountAmount
  required; `validUntil > validFrom` — both server-enforced.
- `classroomId` is fixed to the current classroom on create (the
  "all-classrooms" coupon variant with `classroom=null` isn't exposed
  here since this screen is per-classroom).
- Sub-widget: `_CouponEditorSheet`.

### §19 `staff_management_screen.dart` — `StaffManagementScreen`
- Owner/admin only. Add by numeric **User ID** (no user-search endpoint
  exists yet — same limitation as certificates/join-requests). Change role
  via bottom-sheet picker. Remove.

### §20 `waitlist_screen.dart` — `WaitlistScreen` (two modes via `sessionId`)
- **`sessionId` omitted** → "My Waitlist" (student, own view across
  classrooms) — each bare-session-id entry enriched via `sessions/{id}/`.
  Leave frees the spot; tapping a card retries `sessions/{id}/join/`
  directly if a spot opened up.
- **`sessionId` provided** (+ `classroomTitle`, `canManage`) → teacher
  "who's waiting" view for that one session, oldest-first, with Promote
  (`POST waitlist/{id}/promote/`).
- Reached from `SessionsListScreen`, `ClassroomDetailScreen`'s
  `_ScheduleTab`, `LiveClassHomeScreen`'s My Learning tab ("My Waitlist").

### §21 `certificates_screen.dart` — `CertificatesScreen`
- **`classroomId` omitted** → "My Certificates" (student, own list,
  read-only). **Provided + `canIssue`** → teacher's issued-list + Issue
  action (student id + optional file).
- Same download-with-corrupt-cache-retry pattern as `materials_screen.dart`.
- Memory-leak fix pattern (see §7 below) applied to the issue-sheet's
  `TextEditingController`.

### §22 `classroom_reports_screen.dart` — `ClassroomReportsScreen`
- **Platform-staff-only** review queue (pending by default + status
  filter). Student-side "file a report" lives in `ClassroomDetailScreen`.
- 3+ pending reports on the same classroom auto-flags it (backend signal,
  not enforced by this screen).
- Takes **no role param itself** — gating is entirely the caller's
  responsibility (`LiveClassHomeScreen.isPlatformStaff` gates the entry
  point icon).
- Was the last screen in the module to get the timezone/locale fix (see §4).

### §23 `notifications_screen.dart` — `NotificationsScreen`
- Bell-icon target, not part of the classroom tab flow — pushed from
  wherever the app's bell icon lives (`LiveClassHomeScreen`'s My Learning
  app bar, in this module's own case).
- Unread-only filter chip, swipe-to-delete (`Dismissible`), mark-all-read,
  tap unread → optimistic mark-read + navigate.
- **Icon mapping** (`_notifIcon`) covers all 18 `NotifType`s including the
  7 added in the "production notification coverage audit" second pass
  (`sessionLive`, `sessionCancelled`, `assignmentPosted`,
  `submissionReceived`, `staffAdded`, `reviewPosted`, `reportReviewed`) —
  these previously silently fell back to the default bell icon.
- Tap routing: `classroomId != null` → `ClassroomDetailScreen`. (Note:
  session-scoped "jump to room" tap behavior for `class_reminder`/
  `session_live` lives in `liveclass_notification_handler.dart`'s
  `handleTap`, not here — this screen's own tap handler only knows about
  `classroomId`.)

### (extra) `classroom_purchases_screen.dart` — `ClassroomPurchasesScreen`
- Teacher-side, single-classroom pass-purchase roster. Filled a gap: both
  the backend `refund` action and `PassPurchaseApi.refund()` existed with
  no screen calling them, and `forClassroom()` required a backend fix
  (ViewSet was hard-scoped to "own purchases only").
- Use for a **single student's** refund (e.g. complaint resolution) —
  refunding everyone at once is "Close Classroom" instead (via
  `ClassroomFormScreen`).
- Same escrow-aware refund-amount dialog pattern as `MyPassesScreen`'s
  cancel (always quotes `remainingBalance`, never `coinsSpent`).

### (extra) `my_reminders_screen.dart` — `MyRemindersScreen`
- Read/cancel half of the `ClassReminder` gap (create half is
  `SessionsListScreen`'s bell). `GET/DELETE reminders/`, self-scoped by
  backend. Resolves each reminder's session via `SessionApi.detail()`,
  cached locally, best-effort (a deleted session just falls back to
  "Session #id" label instead of blocking the list).
- Reached from `LiveClassHomeScreen`'s My Learning tab.

### (extra) `banned_students_screen.dart` — `BannedStudentsScreen`
- **Purpose**: classroom-wide student ban roster. Owner/admin only,
  reached from `ClassroomDetailScreen`'s manage sheet. Filled a gap: the
  backend (`ClassroomViewSet.ban/bans/unban`) was fully implemented with no
  screen anywhere calling it.
- **Ban** (`POST .../ban/`, `{student_id, reason}`): same numeric-User-ID
  limitation as Staff/Join-Requests/Certificates (no user-search endpoint
  exists). Confirmation copy in the sheet spells out the side effects —
  kicks from any live session, rejects pending join requests, refunds the
  active pass — before submit. Idempotent server-side.
- **List** (`GET .../bans/`) — ⚠ **not paginated**, unlike almost every
  other list call in this module; parses a plain JSON array.
- **Unban** (`POST .../unban/{studentId}/`) — lifts the ban but does
  **not** restore the refunded pass; the student would need to raise a
  fresh join request and pay again.
- Memory-leak fix pattern (see §7 below) applied to the ban-sheet's two
  `TextEditingController`s (`userIdCtrl`, `reasonCtrl`).

### (extra) `classroom_recordings_screen.dart` — `ClassroomRecordingsScreen`
- **Purpose**: browsable, paginated library of a classroom's past recorded
  sessions. Reached from `ClassroomDetailScreen`'s manage sheet (same entry
  pattern as Certificates). Filled a gap: `recording_url` has existed
  per-session since the LiveKit egress wiring, and the backend
  (`ClassroomViewSet.recordings`) was already implemented, but no screen
  ever listed them.
- **API**: `classrooms/{id}/recordings/` — normal DRF pagination (unlike
  `BannedStudentsScreen`'s `bans` call), infinite-scroll via
  `NotificationListener<ScrollNotification>` near the bottom.
- Access tier matches Materials/Notices (teacher/staff/anyone who has ever
  held a pass, active or expired) — **not** owner-only — but is currently
  only reached the same way `CertificatesScreen` is, via the manage sheet.
- A recorded session with an egress upload still in flight simply doesn't
  appear yet — no separate "processing" state is shown.
- Opens a recording via `url_launcher` (`LaunchMode.externalApplication`) —
  hands off to the device's video player/browser, no in-app player.
  Requires the `url_launcher` package as a pubspec dependency.

### (extra) `teacher_earnings_screen.dart` — `TeacherEarningsScreen`
- **Purpose**: teacher earnings dashboard — total earned, this-month
  earned, sessions charged, a 30-day daily bar chart, and a per-classroom
  breakdown. Filled a gap: the backend (`TeacherEarningsView`, `GET
  my-earnings/`) was fully implemented (real `PassDailyCharge` aggregates)
  but wasn't even reachable at any URL until a routing fix, and no screen
  ever called it once it was.
- **`classroomId`** optional — provided (from the manage sheet, "My
  Earnings" tile) scopes to one classroom the caller teaches (403 if they
  don't teach it); omitted gives the teacher's earnings across every
  classroom they teach.
- **API**: `LiveClassApi.myEarnings({classroomId})` (static method on
  `LiveClassApi` itself, not a sub-API instance).
- Daily bars (`_dailyBars`) are a hand-rolled `FractionallySizedBox` row,
  not a charting package — `heightFactor` clamped to a `[0.03, 1.0]` floor
  so a zero-earning day still renders a visible sliver instead of
  disappearing.
- Read-only — same as `CoinWalletScreen`, no user actions besides pull to
  refresh.

### (extra) `referral_screen.dart` — `ReferralScreen`
- **Purpose**: "Refer & Earn" — own referral code, redemption tally, and
  redeeming someone else's code. Filled a gap: the backend (`ReferralViewSet`
  — `referrals/`, `referrals/my-code/`, `referrals/redeem/`) was fully
  implemented with no screen anywhere calling any of the three endpoints.
- **Account-wide, not classroom-scoped** — unlike the other "extra" screens
  in this pass, this is **not** reached from `ClassroomDetailScreen`'s
  manage sheet or from `LiveClassHomeScreen`'s My Learning tab. It has no
  entry point wired anywhere in the currently-uploaded files — wire it in
  from wherever the app's main menu/profile/wallet section lives.
- Loads `myCode()` + `list()` in parallel on open. Copy-code button
  (`Clipboard.setData`). Redeem field validates non-empty client-side, then
  surfaces the backend's 400 message verbatim (own code / already redeemed
  / window expired / invalid code all come back as text from
  `LiveClassApiException.message`).
- `_redeemCtrl` is a class-level `TextEditingController`, disposed in
  `dispose()` — **not** the outer-method-plus-try/finally pattern used by
  every bottom-sheet controller elsewhere in the module (see §7), because
  it's a persistent field on the screen itself, not a sheet that opens and
  closes repeatedly.

### `liveclass_home_screen.dart` — `LiveClassHomeScreen` (module shell)
- **Purpose**: LiveClass module's own `Scaffold` + bottom nav (separate
  from the app's main social-feed nav). Push this instead of pushing
  `ExploreScreen` directly.
- **3 tabs**: 0 Explore (`ExploreScreen`), 1 My Learning
  (`_MyLearningTab`), 2 Wallet (`CoinWalletScreen`).
- **`_MyLearningTab`**: 2×N grid of "my stuff" screens that previously had
  no entry point anywhere — `MyPassesScreen`, `JoinRequestsScreen.mine()`,
  `WishlistScreen`, `WaitlistScreen`, `CertificatesScreen()` (no
  classroomId = own list), `MyRemindersScreen`. App bar has a
  notifications bell (unread badge via `notifications.unreadCount()`) and,
  if `isPlatformStaff`, a flag icon → `ClassroomReportsScreen`.
- **`isPlatformStaff`** param: module has no session/user concept of its
  own, so this is threaded in from the caller (same pattern as
  `canManage`/`canIssue` elsewhere) — defaults `false` so old callers don't
  accidentally expose the staff queue.

---

## 7. Cross-Cutting Conventions (apply these when editing ANY screen)

1. **`TextEditingController` memory-leak pattern**: every bottom-sheet/
   dialog that owns a controller creates it in an outer method, then calls
   an inner `_show*` method wrapped in `try { ... } finally { ctrl.dispose(); }`
   — guarantees disposal on every exit path (submit/cancel/dismiss).
   Applied across `certificates_screen`, `doubts_screen`,
   `holidays_screen`, `staff_management_screen`, `materials_screen`,
   `banned_students_screen`, etc. (`referral_screen`'s redeem field is the
   one exception — see its §6 entry.)
2. **Corrupt-cache download fix**: any screen that downloads-then-opens a
   file (`certificates_screen`, `materials_screen`) checks
   `File(savePath).exists()` before downloading, but wraps the actual
   `Dio().download()` in try/catch that **deletes the partial file** before
   rethrowing — otherwise a failed download leaves a corrupt file that
   `exists()` treats as cached forever, permanently bricking that item.
3. **Timezone/locale formatting**: never hand-format a `DateTime` or
   hardcode English month names. Use `liveClassFmtDate*` (theme.dart) for
   absolute instants, or `LiveClassDateTime` (datetime.dart) when a
   `ClassSchedule`'s named-zone wall-clock time needs resolving. Every
   `DateTime` from the API is UTC (`_dt()`/`DateTime.parse()` on 'Z'-suffixed
   strings) — always `.toLocal()` before reading clock fields.
4. **Design tokens**: import `LiveClassColors`/`LiveClassSpacing`/
   `LiveClassRadius` from `theme/liveclass_theme.dart` directly, or alias
   local `_kNavy`/`_kBg`/`_kGradient` consts to them — never re-declare hex
   literals.
5. **Escrow-aware refund/cancel copy**: any UI quoting a refund amount
   (`MyPassesScreen.cancel`, `ClassroomPurchasesScreen.refund`) must quote
   `PassPurchase.remainingBalance`, **never** `coinsSpent` — days already
   taught (`coinsReleased`) have already paid the teacher and are not
   clawed back.
6. **Role gating via caller-supplied flags**: this module has no
   session/user concept of its own. `canManage`/`canIssue`/`canAsk`/
   `canSubmit`/`isPlatformStaff` are always passed in by whoever pushes the
   screen (ultimately from `ClassroomDetailScreen`'s `MyPassStatus.accessLevel`
   or the app's own auth state for `isPlatformStaff`). Never assume a role
   — always thread the flag through.
7. **`{silent: false}` refresh pattern**: inline panels inside
   `LiveSessionScreen` (chat, polls, materials, doubts, notices, waitlist,
   participants) all take a `silent` param on their `_load*` methods so a
   background poll/refresh doesn't flash a loading spinner over live
   content.

---

## 8. Known Issues / Gaps / Dead Code

- **⚠ `request_join_screen.dart` is a stale duplicate.** It contains
  `class JoinRequestsScreen` — an exact copy of `join_requests_screen.dart`
  (same class name, same two named constructors `.inbox()`/`.mine()`).
  Nothing in the module imports this file. The file's own header comment
  flags it as leftover from a fixed "ambiguous import" compile error and
  says it should be deleted from the repo.
- **⚠ `RequestJoinScreen` class is referenced but never defined.**
  `classroom_detail_screen.dart`'s `_openRequestJoin()` does:
  `Navigator.push(... RequestJoinScreen(classroomId: widget.classroomId,
  classroom: _classroom))` — but no file provided so far defines a class
  named `RequestJoinScreen` (not in `request_join_screen.dart`, which
  actually defines `JoinRequestsScreen`; not anywhere else). This is either
  (a) a genuine missing file not yet uploaded, or (b) a naming bug where
  `classroom_detail_screen.dart` should be calling
  `JoinRequestsScreen.request(...)`-style constructor instead. **Flag this
  before doing any work on the "Request to Join" flow.**
- **User lookup is ID-only everywhere.** Certificates (issue), Staff (add)
  both require typing a raw numeric User ID — no user-search endpoint
  exists yet in this API. A future improvement would add one search
  endpoint and reuse it in both places.
- **No in-app browser for external material links** — `materials_screen.dart`
  copies the link to clipboard instead of opening it.
- **`referral_screen.dart` has no entry point.** Unlike every other "extra"
  screen in this pass, it isn't pushed from `ClassroomDetailScreen`'s
  manage sheet or `LiveClassHomeScreen`'s My Learning tab — it's
  account-wide, meant to be wired in from the app's main menu/profile
  section, which isn't part of this module's uploaded files. It will not
  be reachable in-app until that wiring is added elsewhere.
- **No "processing" state for recordings.** `classroom_recordings_screen.dart`
  simply won't list a session whose LiveKit egress upload hasn't finished
  yet — there's no placeholder telling the viewer a recording exists but
  isn't ready.

---

## 9. Quick Lookup — "I need to change X, which file(s)?"

| Change | Primary file(s) | Also touches |
|---|---|---|
| A new field on Classroom | `liveclass_models.dart` (`Classroom`) | `classroom_form_screen.dart`, `explore_screen.dart`, `classroom_detail_screen.dart` |
| New notification type | `liveclass_models.dart` (`NotifType`) | `notifications_screen.dart` (`_notifIcon`), `liveclass_notification_handler.dart` (`_handledTypes`, payload docs, possibly `_liveRoomTapTypes`) |
| Refund/cancel amount logic or copy | `classroom_purchases_screen.dart`, `my_passes_screen.dart` | `liveclass_models.dart` (`PassPurchase`), `liveclass_api_service.dart` (`PassPurchaseApi`) |
| Date/time formatting bug | `liveclass_theme.dart` (simple) or `liveclass_datetime.dart` (schedule/tz) | any screen displaying dates |
| New API endpoint | `liveclass_api_service.dart` (add method to the right `*Api` class) | `liveclass_models.dart` (response/request model), the screen that will call it |
| Design token / color change | `liveclass_theme.dart` (`LiveClassColors`) | grep `_kNavy =`/`_kBg =`/`_kGradient =` aliases across screens |
| Live session feature (new panel, new control) | `live_session_screen.dart` (huge — locate the right subsystem cluster first, see §6 §6-breakdown) | `liveclass_models.dart` if new data shape needed |
| Manage-sheet entry point (new teacher tool) | `classroom_detail_screen.dart` (`_openManageSheet`, `_manageTile` list) | the new screen file itself |
| My Learning tile (new "my stuff" screen) | `liveclass_home_screen.dart` (`_MyLearningTab.build`'s `tiles` list) | the new screen file itself |
| Ban/unban logic or copy | `banned_students_screen.dart` | `liveclass_models.dart` (`ClassroomBan`), `liveclass_api_service.dart` (`ClassroomApi.ban/bans/unban`) |
| Recordings library (list/open) | `classroom_recordings_screen.dart` | `liveclass_models.dart` (`SessionRecording`), `liveclass_api_service.dart` (`ClassroomApi.recordings`) |
| Teacher earnings figures/chart | `teacher_earnings_screen.dart` | `liveclass_models.dart` (`TeacherEarnings`/`EarningsByDay`/`EarningsByClassroom`), `liveclass_api_service.dart` (`LiveClassApi.myEarnings`) |
| Referral code/redemption logic | `referral_screen.dart` | `liveclass_models.dart` (`Referral`/`MyReferralCode`), `liveclass_api_service.dart` (`ReferralApi`) |

---

*End of reference. When requesting work, name the screen/file + function
from this doc — full source re-upload won't be needed.*