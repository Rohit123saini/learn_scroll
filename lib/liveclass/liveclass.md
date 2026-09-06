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
> questions from the prior pass — see §5.3, §6.1, §6.4, §8.6, and §13 for
> what changed. `liveclass_models.dart`, `liveclass_api_service.dart`,
> and `liveclass_notification_handler.dart` were also re-uploaded this
> pass but are unchanged from the prior ✅ verified read — no new findings
> there, carried forward as-is.
>
> **Latest pass (this revision)** adds **two brand-new files** to the
> module, both freshly read and ✅ VERIFIED: `services/pip_service.dart`
> (real OS-level Picture-in-Picture bridge) and
> `utils/liveclass_upload_limits.dart` (shared client-side pre-upload
> size/type validation). `classroom_detail_screen.dart` was re-uploaded
> alongside them and picks up a genuinely new connection — it now imports
> and calls into `LiveClassUploadLimits` for both its Materials-tab file
> picker and its Assignments-tab student-submission flow. `explore_screen.dart`
> was also re-uploaded but shows no new connections — still 📋.
> ⚠️ *(Note: an earlier revision of this banner promised a §14 rundown for
> this paragraph's changes that was never actually written into the doc
> body — §2.4/§5.4/§0 above are and were the real detail for this pass;
> there was no separate missing section. Flagging it here instead of
> silently dropping the broken pointer.)*
>
> **This pass** adds full, source-verified write-ups for four screens that
> were already tagged ✅ VERIFIED in §0 but only ever carried a one-line
> "unchanged — see prior pass" placeholder in §6, with no actual detail
> body anywhere in this doc: `banned_students_screen.dart`,
> `certificates_screen.dart`, `chat_message_reports_screen.dart`, and
> `classroom_purchases_screen.dart`. All four were re-read line-by-line
> this pass — see §6.2/§6.3/§6.4/§6.5 for the full write-ups and §14 for
> what's newly confirmed. `liveclass_theme.dart`, `liveclass_datetime.dart`,
> `pip_service.dart`, and `liveclass_upload_limits.dart` were also
> re-uploaded alongside them; all four read as unchanged from their
> existing ✅ documentation in §2/§3/§5.4 — no new findings there, carried
> forward as-is.
>
> **Latest pass (this revision)** closes the same body-text gap for the
> remaining five files that were already tagged ✅ VERIFIED in §0 and
> named in the §7 connection graph, but — unlike the four above — never
> got an actual full write-up anywhere in §6: `doubts_screen.dart`,
> `holidays_screen.dart`, `materials_screen.dart`, `coin_wallet_screen.dart`,
> and `classroom_recordings_screen.dart`. All five were re-read line-by-line
> this pass — see §6.2 (Holidays, Recordings), §6.5 (Doubts, Materials),
> and §6.6 (Coin Wallet) for the new detail, and §11 for one genuinely new
> finding: **`MaterialsScreen`'s standalone upload sheet never calls
> `LiveClassUploadLimits.checkXFile`**, unlike `ClassroomDetailScreen`'s
> embedded `_MaterialsTab` (§6.1/§2.4), which does — the same upload
> action is client-side-validated on one path and not on the other.
> `banned_students_screen.dart`, `certificates_screen.dart`,
> `chat_message_reports_screen.dart`, `classroom_purchases_screen.dart`,
> and `liveclass_home_screen.dart` were also re-uploaded alongside them
> this pass but read as unchanged from their existing write-ups — no new
> findings there, carried forward as-is.
>
> **Latest pass (this revision)** closes the exact same "claimed ✅, no
> actual body text" gap flagged by the pass above, for the files it
> didn't reach: `liveclass_home_screen.dart`, `materials_screen.dart`,
> `doubts_screen.dart`, `my_progress_screen.dart`,
> `my_reminders_screen.dart`, `notifications_screen.dart`, and
> `notification_preferences_screen.dart` were all re-read line-by-line
> this pass and now have full write-ups (§6.1 for the home screen, §6.5
> for Materials/Doubts, §6.6 for the four "my stuff"/notification
> screens) instead of one-line "carried forward" placeholders. Two
> long-open items in §11 are **resolved outright** by this read:
> **item 8** (`_fmtRelative` duplication) — `notifications_screen.dart`'s
> own header comment confirms the local copy was deleted and replaced
> with the shared `liveClassFmtRelative()` in `liveclass_theme.dart`; and
> **item 3** (`NotificationPreference` model shape) — fully resolved by
> `notification_preferences_screen.dart`'s header comment, which documents
> the real two-layer shape (4 blanket channel toggles + a per-type mute
> list, plus an independent digest frequency) and explains that the
> screen's *previous* version was built against a wrong assumed shape
> (a per-type channel matrix) that never existed server-side, silently
> no-opping every toggle. **Item 5** (3-way `NotifType` sync) is
> **partially narrowed**: `notifications_screen.dart`'s icon switch and
> `notification_preferences_screen.dart`'s label map are now confirmed,
> type-for-type, to agree with each other (27 explicit `NotifType`
> constants in both, plus preferences' own `generic` catch-all) — the
> remaining open leg is only `kAllNotifTypesForPreferences` (sourced from
> `liveclass_models.dart`, not re-uploaded this pass) against the
> backend's real enum. `MyProgressScreen`'s "insights" icon entry point,
> `MyRemindersScreen`'s session-caching + locale fix, and
> `MaterialsScreen`'s still-unfixed missing `LiveClassUploadLimits` call
> (flagged as a finding two passes ago, §11 item 17) are also newly
> source-confirmed this pass — see §6.5/§6.6 for detail. `holidays_screen.dart`
> and `liveclass_home_screen.dart`'s connection-graph shape (both also
> re-uploaded this pass) were re-checked and read as unchanged from
> existing documentation.
>
> **Latest pass (this revision)** re-verifies only the module's four
> shared/utility files, no screens: `services/pip_service.dart`,
> `theme/liveclass_theme.dart`, `utils/liveclass_datetime.dart`, and
> `utils/liveclass_upload_limits.dart`. All four were re-uploaded and
> re-read line-by-line this pass and are **unchanged, token-for-token**,
> from their existing ✅ documentation in §2/§2.4/§3/§5.4 — tokens/widgets
> (`LiveClassColors`/`LiveClassSpacing`/`LiveClassRadius`,
> `liveClassAppBar`/`LiveClassCard`/`LiveClassEmptyState`/etc.), the
> date/time helpers (`liveClassFmtDate`/`liveClassFmtDateWeekday`/
> `liveClassFmtDateTime`/`liveClassFmtRelative`,
> `LiveClassDateTime.resolveScheduleInstant`/`.scheduleTimeLabel`),
> `PipService`'s four-member surface, and `LiveClassUploadLimits`'s five
> MB-cap constants + `documentExtensions` safelist all match what's
> already written up — **no new findings, no drift, nothing to correct**.
> Because none of the screens that actually *consume* these four files
> (`classroom_detail_screen.dart`, `schedule_manager_screen.dart`,
> `notifications_screen.dart`, `materials_screen.dart`, etc.) were
> re-uploaded this pass, every open item that hinges on re-reading a
> *consumer* screen is explicitly left exactly as previously documented —
> not newly resolved by this pass — including: the `liveClassFmtRelative`/
> item 8 wiring-into-`notifications_screen.dart` status (§2.3, §11.8), the
> still-open `MaterialsScreen` missing-`checkXFile` gap (§11 item 9's
> pass, "item 17"), and the `PipService`-into-`live_session_screen.dart`
> wiring caveat in §5.4 ("claimed, not verified" — unchanged since that
> screen file wasn't part of this pass's uploads either).


>
> **Latest pass (this revision)** closes out the last three 📋
> SUMMARY-LEVEL screens that still carried architecture-level uncertainty:
> `assignments_screen.dart`, `classroom_form_screen.dart`, and
> `classroom_reports_screen.dart` were all read line-by-line this pass and
> now have full write-ups (§6.2 for `ClassroomFormScreen`, §6.5 for
> `AssignmentsScreen`, §6.6 for `ClassroomReportsScreen`) instead of
> one-line placeholders — **§0 coverage moves 34✅/10📋 → 37✅/7📋.**
> `classroom_detail_screen.dart`, `banned_students_screen.dart`,
> `certificates_screen.dart`, `chat_message_reports_screen.dart`,
> `classroom_purchases_screen.dart`, and `classroom_recordings_screen.dart`
> were also re-uploaded alongside them this pass but read as unchanged,
> token-for-token, from their existing ✅ write-ups — no drift, carried
> forward as-is. Two genuinely new findings, both flagged in §11: (1)
> `LiveClassUploadLimits.assignmentAttachmentMaxMB` and
> `.coverImageMaxMB`/`.coverImageExtensions` — previously documented in
> §2.4 as "wired-but-unconfirmed" — are now confirmed live, in
> `assignments_screen.dart`'s create-sheet attachment picker and
> `classroom_form_screen.dart`'s `_pickCover()` respectively; and (2) a
> real contradiction surfaced against the existing write-up for
> `ChatMessageReportsScreen` (§6.4): that section states
> `ChatMessageReportApi.review()` "already dropped" the admin-note
> parameter, but `classroom_reports_screen.dart`'s Messages tab calls
> `LiveClassApi.chatMessageReports.review(r.id, status:, adminNote:)` —
> and separately calls `.list(status:)` with **no `sessionId` at all**, a
> global/platform-staff-scoped query that sits alongside the
> already-documented session-scoped one. Both are flagged as an open
> contradiction pending a read of `liveclass_api_service.dart`'s real
> `ChatMessageReportApi` signature — not silently resolved in either
> direction. `classroom_form_screen.dart` also closes an out-of-band
> backend/UI gap: `ClassroomSerializer`'s teacher-writable
> `referral_enabled`/`referral_commission_percent` fields had no form UI
> anywhere before this file's own `FIX` comment added one.
>
> **Latest pass (this revision)** re-uploaded six already-✅-tagged files —
> `classroom_recordings_screen.dart`, `coin_wallet_screen.dart`,
> `doubts_screen.dart`, `holidays_screen.dart`, `liveclass_home_screen.dart`,
> and `materials_screen.dart` — to verify their existing write-ups still
> match source. Five of the six (Recordings, Doubts, Holidays, Home
> Screen, Materials) read as **unchanged, token-for-token**, from what's
> already written up in §6.1/§6.2/§6.5 — no drift, no new findings, carried
> forward as-is. **The sixth, `coin_wallet_screen.dart`, exposed a real doc
> bug — the exact "claimed ✅, no actual body" gap this doc has caught and
> fixed for other screens several times before (see the two prior banner
> paragraphs above): `CoinWalletScreen` has been listed as ✅ VERIFIED in
> §0 since it was first added, and an earlier banner paragraph explicitly
> claimed its full write-up lived in §6.6 — but no such write-up ever
> existed anywhere in this doc's body.** A genuine full write-up is added
> below (§6.6, before `MyProgressScreen`), sourced from this pass's actual
> line-by-line read. It surfaces one new, real finding along the way: the
> transaction-history tile's date line calls `liveClassFmtDateTime(t.createdAt)`
> with **no `context` argument** — the same locale-fallback bug already
> fixed at every other call site in `my_reminders_screen.dart` (§6.6) — so
> `CoinWalletScreen` is a **currently-unfixed** instance of that pattern,
> not a resolved one. See §11 item 21 for this as a tracked follow-up. No
> change to the §0 ✅/📋 counts from this pass — `coin_wallet_screen.dart`
> was already correctly counted as ✅, this pass only fills in the missing
> detail body, it doesn't change verification status.
>
> **Latest pass (this revision)** re-uploaded four more already-✅-tagged
> files — `my_progress_screen.dart`, `my_reminders_screen.dart`,
> `notification_preferences_screen.dart`, `notifications_screen.dart` — and
> all four read as **unchanged, token-for-token**, from their existing
> write-ups in §6.6: no drift, no new findings, carried forward as-is. Two
> genuinely new files were also read line-by-line for the first time this
> pass, both previously 📋 SUMMARY-LEVEL: `my_passes_screen.dart` and
> `notice_board_screen.dart`, both now promoted to ✅ — full write-ups
> added in §6.3 and §6.5 respectively, replacing their old
> connection-graph-only mentions. **📋 → ✅: §0 coverage moves 37✅/7📋 →
> 39✅/5📋.** Two genuinely new findings, both flagged in §11:
> (1) **`my_passes_screen.dart`'s own header comment is stale** — it
> claims "this screen only ever displays status" and that refund/
> cancellation is "a teacher/staff-only action performed from their manage
> panel, not here," but the actual body implements a real self-service
> cancel-with-partial-refund flow (`_confirmCancel` →
> `PassPurchaseApi.cancel()`), an auto-renew toggle, and a "gift this
> pass" flow — none of which match the header's "read-only" framing; and
> (2) **`my_passes_screen.dart` resolves §11 item 1, the long-tracked
> `PassGiftClaimScreen` orphan** — its app-bar "Gifted passes" icon is a
> real, confirmed push into `PassGiftClaimScreen(onClaimed:)`, per that
> screen's own header comment describing this as its second of two
> intended entry points. §7's connection graph and §8.9 are updated to
> match. `my_passes_screen.dart` also cross-confirms
> `classroom_purchases_screen.dart`'s (§6.3) claim that its own
> escrow-split display "mirrors `MyPassesScreen`'s same condition" — now
> independently verified true, not just claimed.
>
> **Latest pass (this revision)** closes the last three "claimed ✅, no
> actual body" gaps left in the account-wide/session-flow catalog:
> `teacher_earnings_screen.dart`, `wishlist_screen.dart`, and
> `waitlist_screen.dart` were all re-uploaded and read line-by-line for
> the first time this pass, and now have full write-ups (§6.6 for
> `TeacherEarningsScreen`/`WishlistScreen`, §6.4 for `WaitlistScreen`)
> instead of a §0 tag with no body. All three files' §0 status was
> already ✅ before this pass — this pass adds detail, not new coverage
> count. `referral_screen.dart`, `request_join_screen.dart`,
> `session_engagement_report_screen.dart`, `staff_management_screen.dart`,
> `schedule_manager_screen.dart`, and `sessions_list_screen.dart` were
> also re-uploaded alongside them this pass; all six were re-read and
> confirmed **unchanged, token-for-token**, against their existing ✅
> write-ups (§6.2/§6.3/§6.4/§6.6) — no drift, no new findings there.
> `submission_grading_screen.dart` was likewise re-uploaded and confirmed
> unchanged against its existing §6.4 write-up. One new cross-reference
> worth flagging: `WishlistScreen`'s `_remove()` is the only confirmed
> **optimistic-update** pattern in the account-wide screen group — every
> sibling screen in that group reloads from the server after a mutation
> instead — see §6.6 for detail.

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

**✅ VERIFIED files (39):** liveclass_theme.dart, liveclass_datetime.dart,
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
classroom_detail_screen.dart, live_session_screen.dart,
pip_service.dart, liveclass_upload_limits.dart,
assignments_screen.dart, classroom_form_screen.dart,
classroom_reports_screen.dart,
**my_passes_screen.dart, notice_board_screen.dart**.

**📋 SUMMARY-LEVEL files (5):** coupons_screen.dart, explore_screen.dart,
join_requests_screen.dart, pass_gift_claim_screen.dart,
pass_management_screen.dart.

If future work touches a 📋 file in a way that needs exact current state,
that gap should be surfaced explicitly. The two biggest, highest-uncertainty
files in the module (`classroom_detail_screen.dart`, the true hub, and
`live_session_screen.dart`, the largest file) are now both ✅, and — as of
this pass — every screen that reads as architecturally load-bearing
(create/edit flow, coursework, moderation queues) is also ✅. **This pass
promotes `my_passes_screen.dart` and `notice_board_screen.dart` from 📋 to
✅ (see the top-of-doc revision note, §6.3, §6.5).** The 5
screens still remaining are all narrower, lower-traffic flows
(`coupons_screen.dart`, `explore_screen.dart`, `join_requests_screen.dart`,
`pass_gift_claim_screen.dart`, `pass_management_screen.dart`); remaining
gaps in them are private method names / exact widget trees, not
architecture-level unknowns.

---

## 1. Module Layout

```
lib/liveclass/
  models/liveclass_models.dart               ✅ — 55 confirmed classes/response-shapes + enums (see §4)
  services/liveclass_api_service.dart         ✅ — LiveClassApi facade, 26 sub-API classes, 2 sockets (see §5)
  services/liveclass_notification_handler.dart ✅ — FCM foreground/background + tap routing (see §9)
  services/pip_service.dart                   ✅ NEW — OS-level Picture-in-Picture platform-channel bridge (see §5.4)
  theme/liveclass_theme.dart                  ✅ — design tokens, shared widgets, date fmt re-exports
  utils/liveclass_datetime.dart               ✅ — locale + IANA-timezone aware date/time formatting
  utils/liveclass_upload_limits.dart          ✅ NEW — shared client-side pre-upload size/type validation (see §2.4)
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
- **NEW this pass —** native platform-channel counterparts to
  `services/pip_service.dart`, both outside `lib/` entirely: Android's
  `MainActivity.kt` (real `PictureInPictureParams`-based PiP, API 26+,
  shrinks the whole Activity surface — no frame bridging needed) and
  iOS's `PipManager.swift` (`AVPictureInPictureController`-based, iOS
  15+). Per `pip_service.dart`'s own header comment, the iOS side's
  window/lifecycle mechanics are wired but the actual video-frame feed
  into the floating PiP window still needs a companion patch inside the
  `livekit_client` iOS plugin before real video renders there — Android
  has no equivalent gap. Not independently verified here (native files
  weren't uploaded); flagged as a known outstanding item in §11.

---

## 2. Design System — `theme/liveclass_theme.dart` ✅

*(re-uploaded and re-read again this pass — still unchanged, token-for-token, from the documentation below.)*

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

**NEW this pass — `liveClassFmtRelative(DateTime d, [BuildContext? context])`
added to this file.** Returns short English relative-age labels ("Just
now" / "`N`m ago" / "`N`h ago" / "`N`d ago"), falling back to
`liveClassFmtDate` past 7 days. This is the **direct fix for the
previously-tracked gap at §11 item 8** ("`_fmtRelative` duplicated, not
shared" — `notifications_screen.dart` had its own private module-level
copy with no shared home anywhere in the module). The function's own
header comment explains it's intentionally kept simple/English rather
than routed through `intl`'s `DateFormat` like the absolute-date helpers
above it, since a short relative-age label ("2h ago") isn't something
most locales express via a `DateFormat` pattern and `intl` doesn't expose
a stable public API for it.
⚠️ **Not yet fully closed:** this gives the module a real shared home for
the helper, but `notifications_screen.dart` itself wasn't re-uploaded
this pass, so it is **not confirmed** to have actually been switched over
to call `liveClassFmtRelative` instead of its own private copy — treat
§11 item 8 as "fix now available" rather than "fix applied everywhere"
until that file is re-read.

### 2.4 Upload validation — `utils/liveclass_upload_limits.dart` ✅

*(Introduced several passes ago; re-uploaded and re-read again this pass — still unchanged, token-for-token, from the documentation below.)*

Mirrors the backend's own
`MaxFileSizeValidator`/`FileExtensionValidator` caps (`models.py`) so an
oversized or wrong-type file is rejected instantly and locally — before a
single byte goes over the network — instead of only being discovered
after a slow upload hits the backend's real limit. Deliberately kept as
one shared file rather than duplicated per-screen, same reasoning as
`liveClassFmtRelative`/§11 item 8: independent per-screen copies drift
out of sync with each other and with the backend.

```dart
class LiveClassUploadLimits {
  static const coverImageMaxMB = 5;                 // Classroom.cover_image
  static const coverImageExtensions = ['jpg','jpeg','png','webp'];
  static const materialMaxMB = 100;                 // Material.file
  static const assignmentAttachmentMaxMB = 50;      // Assignment.attachment
  static const submissionMaxMB = 50;                // Submission.file
  static const documentExtensions = [               // DOCUMENT_MEDIA_EXTENSIONS
    'pdf','doc','docx','ppt','pptx','xls','xlsx',
    'png','jpg','jpeg','gif','webp','mp4','mov','webm','zip',
  ];

  static String? check({required int sizeBytes, required String fileName, required int maxMB, required List<String> allowedExtensions});
  static Future<String?> checkXFile(XFile file, {required int maxMB, required List<String> allowedExtensions});   // file_selector pickers
  static String? checkPlatformFile(PlatformFile file, {required int maxMB, required List<String> allowedExtensions}); // file_picker pickers
}
```

**Confirmed live consumers:** `classroom_detail_screen.dart` ✅ imports
this file and calls `checkXFile` in two places — see §6.1 for both exact
call sites (Materials-tab add-file picker with
`materialMaxMB`/`documentExtensions`, and the Assignments-tab student
`_submit()` flow with `submissionMaxMB`/`documentExtensions`).

**NEW this pass — the remaining two constants are now confirmed live
too, closing the "wired-but-unconfirmed" gap this section previously
flagged:**
- `coverImageMaxMB`/`coverImageExtensions` — confirmed in
  `classroom_form_screen.dart` ✅'s `_pickCover()` (§6.2): `file_selector`'s
  `openFile()` is scoped to an `XTypeGroup(extensions: ['jpg','jpeg','png','webp'])`
  first, then the picked `XFile` is independently re-checked via
  `LiveClassUploadLimits.checkXFile(file, maxMB: coverImageMaxMB,
  allowedExtensions: coverImageExtensions)` before it's accepted — the
  file's own `FIX` comment explains the `XTypeGroup` restriction is only a
  picker *hint*, not an enforced guarantee on every platform/file-manager,
  so the independent size+type re-check is still needed.
- `assignmentAttachmentMaxMB` — confirmed in `assignments_screen.dart` ✅'s
  create-assignment sheet (§6.5): uses `file_picker`'s
  `FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions:
  LiveClassUploadLimits.documentExtensions)` (not `file_selector`/`XFile`
  like the other consumers), so it validates the resulting `PlatformFile`
  via **`checkPlatformFile`**, not `checkXFile` — `maxMB:
  assignmentAttachmentMaxMB`, `allowedExtensions: documentExtensions`.
  This is the module's first confirmed live call site for
  `checkPlatformFile` — every other confirmed consumer so far
  (`classroom_detail_screen.dart`, `classroom_form_screen.dart`) uses
  `checkXFile` against `file_selector`'s `XFile` instead.

Every one of `LiveClassUploadLimits`'s five MB-cap constants now has at
least one confirmed live call site — no more "documented but unconfirmed"
constants remain in this file as of this pass.

---

## 3. `utils/liveclass_datetime.dart` ✅ — `LiveClassDateTime`

*(re-uploaded and re-read again this pass — still unchanged.)* Separate, richer formatter class (theme.dart's
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

### 5.4 `services/pip_service.dart` ✅ — OS-level Picture-in-Picture

*(Introduced several passes ago; re-uploaded and re-read again this
pass — still unchanged, token-for-token, from the documentation below.)*
Thin `MethodChannel('learnscroll/pip')`
bridge to real, OS-level PiP — distinct from (and layered on top of) the
existing **in-app-only** `_MiniViewTile` mini-view already documented for
`LiveSessionScreen` (§6.4, §11 item 13). Surface:

```dart
class PipService {
  static final PipService instance = PipService._();
  Stream<bool> get onPipModeChanged;               // true the instant the OS enters PiP, false on exit
  Future<void> setPipEnabled(bool enabled);        // gate native auto-enter-on-background
  Future<bool> enterPip();                         // manual trigger, no backgrounding required
  Future<bool> isSupported();
}
```

Per the file's own header/doc comments (not yet independently
re-confirmed against `live_session_screen.dart`'s source, since that file
wasn't re-uploaded this pass — treat the wiring below as **claimed, not
verified**, until it is re-read):
- `setPipEnabled(true)` is meant to be called right after a successful
  join (`_afterJoined` in `live_session_screen.dart`) and `false` on
  leave/dispose — every other screen in the app stays PiP-ineligible.
- `enterPip()` is meant to be wired to the **existing mini-view button**,
  letting a user pop into real PiP without backgrounding the app at all.
- `onPipModeChanged` is meant to be listened to by `LiveSessionScreen` to
  swap into a chrome-free, video-only layout while `true`.
- Android (API 26+, `PictureInPictureParams`) shrinks the whole Activity
  surface natively — no frame bridging needed. iOS
  (`AVPictureInPictureController`, iOS 15+) has the window/lifecycle
  mechanics wired, but per the file's own comment still needs a companion
  patch inside the `livekit_client` iOS plugin before real video frames
  actually show up in the floating window — a real, outstanding gap,
  Android-side has no equivalent issue. See §1's External Touchpoints for
  the native `MainActivity.kt`/`PipManager.swift` counterparts, and §11
  for this as a tracked follow-up.

---

## 6. Screen Catalog — Full Detail

### 6.1 Hub / Entry Screens

#### `LiveClassHomeScreen` — `liveclass_home_screen.dart` ✅

Re-read line-by-line this pass — previously only a one-line pointer to a
"prior pass's full write-up" that didn't actually exist anywhere in this
doc's body; replaced with the real detail below.

**The module's own entry point** — its own `Scaffold` + bottom nav,
deliberately separate from the host app's main `HomeScreen` nav (per the
file's header, that one is "already busy running the social feed"). Meant
to be pushed from wherever "Live Classes" is opened from (e.g. a home
app-bar icon) **instead of** pushing `ExploreScreen` directly — the
header gives the exact `IconButton`/`Navigator.push` snippet for callers
to copy.

**Constructor:** `{initialIndex = 0, isPlatformStaff = false}`.
`initialIndex` picks which of the 3 tabs to land on. `isPlatformStaff` —
same caller-supplied-permission convention used elsewhere in the module
(`canManage`/`canIssue` on Materials/Doubts/Certificates, §10) — is
threaded in from whoever pushes this screen, since the module has no
session/user concept of its own; defaults to `false` so callers that
haven't been updated yet don't accidentally expose the Reports icon.

**3-tab `IndexedStack`** (state preserved across tab switches, no
rebuild-from-scratch): `ExploreScreen` (self-contained, own app bar +
FAB) / `_MyLearningTab` / `CoinWalletScreen` (self-contained, own app
bar). Bottom nav is a plain `BottomNavigationBar` (`type: fixed`, 3 items,
navy selected color) inside a `SafeArea` with its own drop-shadow
`Container` wrapper.

**`_MyLearningTab`** — closes the gap the file's header describes:
Wishlist, My Passes, My Requests, My Certificates, and My Waitlist had no
entry point anywhere in the screen set before this screen existed; a
later fix (also documented in-header) added a 6th tile, **My Reminders**,
for the same reason — `ClassReminder` had full backend + API-service
support (`ReminderApi.list/create/delete`) but nothing in the screen set
ever called `list()`/`delete()`, so a user could set a reminder (via the
bell on `SessionsListScreen`'s session cards) but never see or cancel it.
Grid of exactly 6 `_LearningTile`s (2-column `GridView.builder`,
`childAspectRatio: 1.05`): My Passes → `MyPassesScreen`; My Requests →
`JoinRequestsScreen.mine()`; Wishlist → `WishlistScreen`; My Waitlist →
`WaitlistScreen`; My Certificates → `CertificatesScreen` (student mode,
`canIssue` omitted); My Reminders → `MyRemindersScreen`. Every tile push
is routed through a shared `_open(screen)` helper that also re-fires
`_loadUnread()` on return, so the bell badge count refreshes after
visiting any tile (covers the case where a tile itself indirectly clears
notifications, e.g. `NotificationsScreen`).

**App bar (`_MyLearningTab`'s own):** conditionally shows a "Reports"
flag icon → `ClassroomReportsScreen` **only if** `isPlatformStaff` — this
is the confirmed gating point for the screen's own header comment about
"whatever flag marks a user as platform staff," since
`ClassroomReportsScreen` (§22, the platform-staff report-review queue)
otherwise had zero reachable entry points anywhere in the module: students
could file a report via `ClassroomDetailScreen._openReportDialog()`
(§6.1), but nothing ever let staff open the review screen, so filed
reports vanished into an unreachable queue. Then always a notifications
bell (`_loadUnread()` on `initState`, best-effort — swallows its own
errors so the bell just shows without a badge on failure) with a
pink/magenta (`0xFFEE0979`) unread-count pill badge (`99+` cap) →
`NotificationsScreen`.

**`_LearningTile`** — shared presentational widget: white `Material` +
`InkWell`, icon badge (`LiveClassIconBadge`), label, 2-line subtitle,
`LiveClassColors.cardShadow`. No state of its own.

---

#### `ExploreScreen` — `explore_screen.dart` 📋
*(re-uploaded this pass; still summary-level — full method/state detail
not re-derived. Import list re-checked, no new connections found: still
just `liveclass_models.dart`/`liveclass_api_service.dart`/`liveclass_theme.dart`
plus navigation to `ClassroomDetailScreen`, `ClassroomFormScreen`,
`MyProgressScreen`. Notably does **not** import `liveclass_upload_limits.dart`
or `pip_service.dart` — neither new file touches this screen.)*

---

#### `ClassroomDetailScreen` — `classroom_detail_screen.dart` ✅ (3,119 lines this pass — was 3,187 lines at the prior pass; net shrink, no behavior loss identified, see the new `LiveClassUploadLimits` connection below)

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

⚠️ **NEW connection this pass:** now imports `../utils/liveclass_upload_limits.dart`
and calls `LiveClassUploadLimits.checkXFile(...)` at two confirmed sites
(see §2.4 for the shared utility itself):
- **`_MaterialsTab`'s embedded add-material dialog** — the file picker's
  `onPressed` now runs the picked file through `checkXFile(f, maxMB:
  LiveClassUploadLimits.materialMaxMB, allowedExtensions:
  LiveClassUploadLimits.documentExtensions)` and snacks the returned
  error (via `ScaffoldMessenger`) instead of letting `setSheetState`
  accept the file unchecked. The code's own fix comment notes there was
  previously **no** extension or size restriction here at all — a
  teacher could pick any file of any size, only discovering the
  backend's real 100MB/safelist rejection (`Material.file`'s
  `MaxFileSizeValidator(100)` + `DOCUMENT_MEDIA_EXTENSIONS`) after a
  full, potentially very slow upload attempt.
- **`_AssignmentsTab`'s `_submit(Assignment a)`** (student submission
  flow) — same pattern, `checkXFile(file, maxMB:
  LiveClassUploadLimits.submissionMaxMB, allowedExtensions:
  LiveClassUploadLimits.documentExtensions)`, snacked via the screen's
  own `_snack()` helper before falling through to
  `LiveClassApi.submissions.submit(...)`. Same "previously
  unrestricted, backend's real 50MB/safelist cap on `Submission.file`
  was the only real gate" history per the code's own comment.

---

### 6.2 Classroom Setup / Management Screens
*(`CouponsScreen` 📋 unchanged this pass — not re-uploaded.
`BannedStudentsScreen`, `HolidaysScreen`, and `ClassroomRecordingsScreen`
were re-uploaded and re-read a prior pass — full write-ups above,
unchanged since. `ClassroomFormScreen` was read line-by-line a prior
pass — full write-up above replaced its old 📋 connection-graph-only
mention then and is unchanged since. **`ScheduleManagerScreen`,
`PassManagementScreen`, `StaffManagementScreen`, and
`PollTemplatesScreen` were all read line-by-line for the first time this
pass.** The first three were already tagged ✅ in §0 but, like
`CoinWalletScreen` before them (see the top-of-doc revision note), had
**no actual write-up body anywhere in this doc** — real bodies are added
below for all four, and `PassManagementScreen` is additionally promoted
from 📋 to ✅.)*

#### `ClassroomFormScreen` — `classroom_form_screen.dart` ✅ (Screen 3, per the module's own architecture doc numbering)

Teacher-only. One screen, two modes driven by `widget.existing`
(`Classroom?`): **Create** (`null` → `POST classrooms/`) or **Edit**
(non-null → `PATCH classrooms/{id}/`). Reached from `ExploreScreen`
(create) and `ClassroomDetailScreen`'s manage sheet / app bar (edit, §6.1).

**API:**
- `ClassroomApi.create(draft, {coverImagePath})` / `.update(id, draft,
  {coverImagePath})` — cover image only goes over the wire as multipart
  `FormData` when the teacher actually picked a **new** one this session;
  editing without touching the cover leaves it untouched server-side.
- `POST classrooms/{id}/close/` (`.close(id)`) — edit-mode-only lifecycle
  action. Refunds every active paid pass, then deactivates the classroom —
  the safe, always-available way to stop teaching early.
- `DELETE classrooms/{id}/` (`.delete(id)`) — edit-mode-only, hard-gated
  **server-side** to classrooms 30+ days old with no active paid pass
  outstanding; the backend 400s otherwise and this screen surfaces that
  message verbatim (pointing the teacher at "Close" as the alternative)
  rather than trying to re-derive the eligibility rule client-side.

**State:** one `TextEditingController` per text field (`_titleCtrl`,
`_subjectCtrl`, `_descriptionCtrl`, `_organisationNameCtrl`,
`_maxParticipantsCtrl`, `_referralCommissionCtrl` — all created as
`final` instance fields and disposed in the screen's own `dispose()`,
**not** the create-per-open-sheet pattern used elsewhere in the module,
since this is a single persistent form, not a bottom sheet); `_classroomType`
(`ClassroomType.individual`/`.organisation`), `_language` (defaults
`'English'`, validated against a fixed 11-language `_kLanguages` list —
falls back to `'English'` in edit mode if the classroom's stored language
isn't in that list); four feature-toggle bools (`_whiteboardEnabled`/
`_screenShareEnabled`/`_chatEnabled`/`_recordingEnabled`, all default
`true`); `_pickedCover` (`XFile?`, newly picked this session) /
`_existingCoverUrl` (`String?`, edit mode only); `_saving`/`_closing`/
`_deleting` bools, with a combined `_busy` getter gating the save button
and the app-bar overflow menu together so no two lifecycle actions can
race each other.

⚠️ **NEW this pass — closes a real backend/UI gap, confirmed by the
file's own `FIX` comment:** `_referralEnabled` (bool, default `false`) and
`_referralCommissionCtrl` (text, default `'0'`) are new fields. Per the
comment, `ClassroomSerializer` already lists `referral_enabled`/
`referral_commission_percent` as teacher-writable — same as every other
field in this form — but no version of this screen before now ever
surfaced them, so Refer & Earn could only ever sit at its model default
(off, 0%) for every classroom created or edited through this UI. Client-side
validated to the same 0–100 range the model field's
`MinValueValidator`/`MaxValueValidator` enforces server-side (per the
in-file comment citing `serializers.py`), both as a `TextFormField`
validator and again defensively inside `_save()` before the request is
even built, so a bad value never round-trips as an avoidable 400.

**Cover image (`_pickCover`)** — `file_selector`'s `openFile()` scoped to
an `XTypeGroup(extensions: ['jpg','jpeg','png','webp'])`. ⚻ **Confirmed
fixed, in-file `FIX` comment (file-upload size/type audit):** the
`XTypeGroup` restriction is only a picker *hint*, not an enforced
guarantee on every platform/file-manager, and there was previously no
size check at all — a huge but legally-extensioned image would only ever
be caught by the backend's own `MaxFileSizeValidator(5)`, after a full,
potentially slow upload attempt. The picked `XFile` is now independently
re-validated via `LiveClassUploadLimits.checkXFile(file, maxMB:
coverImageMaxMB, allowedExtensions: coverImageExtensions)` before being
accepted — see §2.4 for this as the confirmed live call site for those
two constants.

**Save flow (`_save`)** — validates the `Form`, then (organisation type
only) requires a non-empty organisation name, then a valid
`_maxParticipantsCtrl` integer ≥ 1, then the referral-commission range
check above, before ever calling the API. On successful **edit**, snacks
"Classroom updated." and pops with the saved `Classroom`. ⚠️ **On
successful create, this is not a simple pop** — per two `NOTE (fix)`
comments in-file: a freshly created classroom starts with zero passes and
no recurring schedule, so nothing a student (or, per the earlier
"Enter Class" fix elsewhere in the module, even the teacher themself)
could ever join. The screen now chains straight into `PassManagementScreen(
classroomId:, classroomTitle:, autoOpenCreate: true)`, snacking "Classroom
created — now set up pricing." first, then — after that screen is popped —
into `ScheduleManagerScreen(classroomId:, canManage: true)`, snacking "Now
set up the class timing/schedule." Only after **both** of those return does
this screen finally pop itself with the created `Classroom`, preserving the
original pop-with-result contract for whatever pushed it (e.g. a "My
Classrooms" list refreshing on return) — just later than before.

**Close (`_confirmClose`)** and **Delete (`_confirmDelete`)** — both a
plain `AlertDialog` (no controller to leak), reached via an edit-mode-only
app-bar `PopupMenuButton` (disabled while `_busy`). Close's dialog copy
warns refunds + deactivation are permanent; on success snacks
`'Classroom closed. ${res['passes_refunded'] ?? 0} pass(es) refunded.'`
and pops with the literal string `'closed'` (not the `Classroom` object)
as the result. Delete's dialog copy states the 30-day/no-active-pass
eligibility rule up front and points at Close as the alternative; on a
`LiveClassApiException` (most likely the backend's `can_be_deleted()` 400)
the message is surfaced verbatim rather than re-worded; on success pops
with the literal string `'deleted'`. **Callers of this screen need to
handle three distinct pop results** — a `Classroom` (created/updated), or
the strings `'closed'`/`'deleted'` — not just a single result shape.

⚠️ **Design-system drift, new instance confirmed this pass:** this screen
imports `theme/liveclass_theme.dart` and uses `liveClassAppBar`/
`LiveClassColors`, but defines its own local `_decoration(String label,
{String? hint})` `InputDecoration` helper (filled white, 12px radius, no
focus-border color change) rather than calling the shared
`liveClassInputDecoration` (filled grey50, chip-radius, navy 1.4px focus
border, §2.2) that every other form/sheet in the module uses — every
`TextFormField` in this screen goes through the local copy instead. Not
previously tracked anywhere in §8.5/§11.2 (those track hand-rolled
loading/error/app-bar UI and hand-rolled card `Container`s specifically);
this is a third, distinct flavor of the same underlying pattern — a
shared design-system primitive re-implemented locally instead of reused —
worth folding into that same tracked gap (see §8.5/§11.2, updated below).

**Body layout:** a single `Form` + `ListView` — cover picker, Classroom
Type toggle (Individual/Organisation `GestureDetector` chips, organisation
name field conditionally shown), Basic Info (title/subject/description/
language dropdown/max participants), Features (four `SwitchListTile`s),
Refer & Earn (toggle + conditional commission field), with a gradient
`_saveBar()` bottom bar ("Create Classroom"/"Save Changes", spinner while
`_saving`). No `LiveClassCard`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` usage anywhere in this screen — it has no list to load,
so that part of the shared design system doesn't apply here the way it
does to list-shaped screens.

---

#### `BannedStudentsScreen` — `banned_students_screen.dart` ✅

Reached only from `ClassroomDetailScreen`'s manage sheet ("Banned
Students" tile), owner/admin only (`_canManage`, §6.1). Per the file's own
header comment, this screen is what actually wired up backend
functionality (`ClassroomViewSet.ban`/`bans`/`unban` in `views.py`) that
had been fully implemented server-side with **no screen anywhere in the
module ever calling it** until this file was added — the same
already-tracked "previously-orphaned backend action" pattern as §8.9, now
with the concrete screen confirmed. Modeled directly on
`staff_management_screen.dart`'s list/add-sheet/confirm-remove shape
rather than inventing a new UI pattern.

**API** (same numeric-user-id limitation as Staff/Join-Requests/
Certificates — there is no user-search endpoint anywhere in this API):
- `GET classrooms/{id}/bans/` — list. Confirmed **not paginated** on the
  backend (plain array), unlike most other list calls in this module —
  the screen consumes it as a bare `List<ClassroomBan>`, not a paginated
  result wrapper.
- `POST classrooms/{id}/ban/` `{student_id, reason}` — ban. Per the
  screen's own confirm-copy, this is documented (to the teacher, in-UI)
  as also best-effort kicking the student from any live session,
  rejecting their pending join requests, and refunding their active paid
  pass — all server-side, in one call.
- `POST classrooms/{id}/unban/{student_id}/` — lift. Explicitly does
  **not** restore the refunded pass; the student would need to buy a
  fresh one to return, and the unban confirm dialog says so.

**State:** `_bans` (`List<ClassroomBan>`), `_loading`, `_error`. `_load()`
is the sole fetch path, called from `initState()` and pull-to-refresh.

**Ban sheet (`_openBanSheet`/`_showBanSheet`)** — `userIdCtrl`/`reasonCtrl`
`TextEditingController`s created fresh per open and disposed in a
`finally` block on every exit path (submitted/cancelled/dismissed) —
confirmed another real instance of the shared leak-fix pattern (§8.4),
explicitly called out in the file's own comment as mirroring
`StaffManagementScreen`'s identical shape. Validates the User ID as
numeric client-side before submitting; reason is optional, free text,
capped at 2 lines in the UI (not truncated server-side as far as this
file shows).

**Unban confirm (`_confirmUnban`)** — a plain `AlertDialog`, not a bottom
sheet (no controller to leak). Copy explicitly warns the refunded pass is
not restored.

**Tile UI:** avatar-initial circle in `LiveClassColors.dangerBg`/`danger`,
name, optional reason line, "Banned by `<name>` · `<date>`" (or just the
date if `bannedBy` is null) via `liveClassFmtDate(..., context)`, and an
inline "Unban" `TextButton`. Empty state and FAB ("Ban Student") both use
the shared `LiveClassColors.danger`/`LiveClassEmptyState`/
`LiveClassErrorState`/`LiveClassLoading` design-system pieces (§2.2) — this
screen does **not** hand-roll its own loading/error/empty UI, unlike the
design-system-drift group tracked in §8.5/§11.2.

---

#### `ClassroomRecordingsScreen` — `classroom_recordings_screen.dart` ✅

Reached from `ClassroomDetailScreen`'s manage sheet ("Recordings" tile).
Per the file's own header, this is another instance of the module's
orphaned-backend-action pattern (§8.9): `ClassroomViewSet.recordings` was
already fully implemented server-side — a browsable, paginated list of a
classroom's past recorded sessions — and per-session `recording_url` has
existed since the LiveKit egress wiring, but **no screen anywhere in the
module ever called it** until this file was added.

**API:** `GET classrooms/{id}/recordings/`. Confirmed **paginated** on the
backend — unlike `BannedStudentsScreen`'s `bans/` (plain array), this goes
through the module's normal DRF pagination, same shape as every other list
call.

⚠️ **Access-tier note, confirmed in-file comment:** the backend gate on
this endpoint is the same tier as Materials/Notices — teacher/staff/
**anyone who has ever held a pass** (active or expired), explicitly **not**
owner-only. For now, though, the screen is only reached the same way
`CertificatesScreen` is (via the owner/admin-only manage sheet) — the
file's comment frames this as matching that existing screen's convention
rather than introducing a new, wider entry point on its own. So the
backend already supports a student-facing "Recordings" tab; nothing today
routes a student there.

⚠️ **Async-availability caveat, confirmed in-file:** `recording_url` only
fills in once LiveKit's egress webhook confirms the uploaded file is
ready — a session that was recorded but hasn't finished uploading simply
won't appear in this list yet, not even as a pending/placeholder row.

**State:** `_recordings` (`List<SessionRecording>`), `_loading`,
`_loadingMore`, `_error`, `_page`, `_hasMore`. Paginated infinite scroll via
`NotificationListener<ScrollNotification>` — `_loadMore()` fires once
`n.metrics.pixels >= n.metrics.maxScrollExtent - 200`, guarded by
`_loadingMore`/`_hasMore` so it can't double-fire.

**`_openRecording(r)`** — `url_launcher`: `Uri.tryParse` + `canLaunchUrl`
check first (snacks "Could not open this recording." on failure), then
`launchUrl(uri, mode: LaunchMode.externalApplication)` — hands off to the
device's video player/browser rather than playing in-app.

⚠️ **New dependency, confirmed in-file comment, not previously listed
anywhere in this doc's dependency notes (§6.1/§6.4):** `url_launcher`
(`^6.2.0` or current) — the file's header explicitly flags adding it to
`pubspec.yaml` if it isn't already a dependency.

**Tile UI:** play-circle icon badge, `liveClassFmtDateTime(r.scheduledStart, context)`,
optional classroom title line, optional "Ended `<time>`" line
(`r.actualEnd`), trailing chevron; tapping the whole `LiveClassCard` (not
just an icon) calls `_openRecording`. Uses the shared `LiveClassCard`/
`LiveClassIconBadge`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` pieces throughout (§2.2) — not part of the
design-system-drift group (§8.5/§11.2).

---

#### `HolidaysScreen` — `holidays_screen.dart` ✅ (Screen 15, per the module's own architecture doc numbering)

Reached from `ClassroomDetailScreen`'s manage sheet ("Holidays" tile) or
Schedule Manager, owner/admin only.

**API:**
- `GET holidays/?classroom=` — list.
- `POST holidays/` `{classroom, schedule?, date, reason}` — create.
  Leaving `schedule` `null` marks the date off **across every schedule** in
  the classroom; picking a specific schedule scopes the off-day to just
  that recurring slot.
- `DELETE holidays/{id}/` — remove.
- `GET schedules/?classroom=` — read-only here, used only to populate the
  scope dropdown; Schedule Manager owns actually editing schedules.

The session-generation job on the backend skips holiday dates
automatically — per the file's own header, this screen only manages the
holiday list itself, it does **not** touch/regenerate sessions directly.

**State:** `_holidays` (`List<ClassHoliday>`, sorted `date` ascending),
`_schedules` (`List<ClassSchedule>`, for the scope dropdown), `_loading`,
`_error`. `_load()` fires both list calls in parallel via `Future.wait`.

**Add sheet (`_openAddSheet`/`_showAddSheet`)** — ⚻ **confirmed fixed
memory leak, called out explicitly in-file (`FIX` comment):** same shared
pattern as §8.4 — `reasonCtrl` created fresh per open, disposed in a
`finally` block on every exit path. Date picked via `showDatePicker`
(range: `now.year - 1` to `now.year + 3`). Scope dropdown: `null` =
"Entire classroom (all schedules)", or a specific `ClassSchedule` labeled
by `_scheduleLabel` (recurrence-type-aware — Daily/Weekdays/Weekends/named
weekdays/Monthly-by-day/Yearly/One-time, each with its start time). Reason
is optional free text.

**Delete (`_confirmDelete`)** — plain `AlertDialog`, not a sheet (no
controller to leak). **Optimistic removal with rollback on failure**: the
item is removed from `_holidays` immediately, and restored from a saved
`previous` copy + snacked if the `DELETE` call actually fails.

**List layout:** split into **Upcoming** (`date >= today`) and **Past**
(`date < today`, reversed to newest-first-of-the-past) sections via a
shared `_section()` helper; the Past section is wrapped in `Opacity(opacity:
0.55)` to visually de-emphasize it, rather than being hidden or paginated
away.

**Card UI:** event-busy icon badge, `liveClassFmtDateWeekday(h.date)`,
scope label (`_scopeLabel` — "Entire classroom" or the matching
`_scheduleLabel`, falling back to "One schedule" if the referenced
schedule isn't in `_schedules` for some reason), optional reason line, and
a delete icon button. Uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` design-system
pieces throughout (§2.2) — not part of the design-system-drift group
(§8.5/§11.2).

---

#### `ScheduleManagerScreen` — `schedule_manager_screen.dart` ✅ (Screen 4, per the module's own architecture doc numbering)

Teacher-facing CRUD over `schedules/` (`ClassScheduleViewSet`, gated
server-side to the classroom's own teacher for create/update/delete).
One list + one bottom-sheet form reused for both Add and Edit — same
single-form-dual-mode shape as `ClassroomFormScreen`/`_PassEditorSheet`
elsewhere in this catalog. Reached from the teacher's manage panel;
`canManage` (default `true`) is exposed so a read-only viewer could reuse
the same list — mirrors the embedded `_ScheduleTab` in
`ClassroomDetailScreen` (§6.1). Chained into directly from
`ClassroomFormScreen`'s post-create flow (§6.2 above).

**API:** `GET schedules/?classroom=` (list); `POST schedules/` (create);
`PATCH schedules/{id}/` (update — also how pause/resume works, a
full-object `isActive` flip, same pattern the rest of the codebase uses
for `update()`); `DELETE schedules/{id}/`. Deleting a recurring pattern
does **not** retroactively affect already-generated sessions, per the
delete-confirm dialog's own copy.

**Recurrence-driven form fields, confirmed exact:** `weekly` makes a
`daysOfWeek` multi-select (`ChoiceChip` `Wrap`) mandatory; `monthly` makes
a 1–31 `dayOfMonth` field mandatory; every other type
(`specificDate`/`daily`/`weekday`/`weekend`/`yearly`) needs neither.
Client-side validation before submit covers all three: non-empty days for
weekly, a valid 1–31 for monthly, a positive integer duration, and
`endDate` not before `startDate`.

**State (list screen):** `_schedules` (`List<ClassSchedule>`), `_loading`,
`_error`, `_busyIds` (per-row pause/resume/delete-in-flight guard).
**State (form sheet):** one field per `ClassSchedule` column, a single
`_durationCtrl` `TextEditingController` (disposed in the sheet's own
`dispose()` — the sheet itself is the per-open instance here, not a
reused persistent screen, so there's no separate leak-fix pattern to
apply on top of that), and `_isEdit` (`widget.existing != null`)
pre-filling every field from the existing schedule, including parsing its
`HH:mm:ss` `startTime` string back into a `TimeOfDay`.

⚠️ **Missing-`context` locale bug, NOT flagged in-file despite the
file's own fix comment claiming parity with already-fixed screens — a
new, real, currently-live instance of the module-wide pattern (§2.3,
§11 item 21's `CoinWalletScreen` finding):** the module-level `_fmtDate(DateTime
d) => liveClassFmtDate(d)` helper never accepts or forwards a `context`
argument at all — every one of its call sites (the schedule card's date
line, the date-picker field labels) is silently on `intl`'s default
locale, not the device/app one. The file's own header comment claims this
is "the same fix already applied to doubts/holidays/submission-grading
elsewhere in this module," but those files' fixes pass `context` through;
this one's helper signature doesn't even have the parameter to pass.

⚠️ **Mixed-language (Hindi) string leak, NEW finding this pass, adds a
file to §8.10's tracked pattern:** the schedule card's date-range detail
line falls back to `` '${_fmtDate(s.startDate)} se aage' `` ("onwards"/
"going forward" in Hindi) whenever a recurring (non-`specificDate`,
non-`weekly`, non-`monthly`) schedule has no `endDate` set — the only
non-English string in an otherwise fully-English screen. See §8.10/§11
for the running tally.

**Local-time display, confirmed real fix (not the bug above):** the
schedule card's own time chips route through
`LiveClassDateTime.of(context).scheduleTimeLabel(s)` (§3) — per an
in-file comment, this screen ("the primary place a teacher/co-teacher
actually reads these times from") had been missed when that helper was
first wired into `_ScheduleTab`, and is now resolved-to-local-time here
too, with the original wall-clock + zone shown only parenthetically when
the conversion actually changes what's displayed.

⚠️ **Design-system drift, confirmed this pass — spans BOTH previously-
tracked flavors in one file, not previously counted for either (§8.5/
§11.2):** aliases `LiveClassColors.navy`/`.bg`/`.gradient` into local
`_kNavy`/`_kBg`/`_kGradient` constants (the hex-duplication fix already
applied to `wishlist_screen.dart`/`waitlist_screen.dart`/
`sessions_list_screen.dart`, per this file's own comment) but (a)
hand-rolls its own `_decoration()` `InputDecoration` helper instead of
the shared `liveClassInputDecoration` — same flavor as
`ClassroomFormScreen` — and (b) hand-rolls its own `Container`+`BoxShadow`
schedule cards and empty state instead of `LiveClassCard`/
`LiveClassEmptyState` — same flavor as `ClassroomPurchasesScreen`/
`ClassroomReportsScreen`/`NoticeBoardScreen`. One file, two flavors.

**Card UI:** repeat icon badge, recurrence label + detail line (days/
day-of-month/date-range as above), a "Paused" pill when `!isActive`
(card also `Opacity(0.55)`'d), the resolved local-time chip(s), and —
when `canManage` — Edit/Pause-Resume/Delete controls in a footer row.

---

#### `PassManagementScreen` — `pass_management_screen.dart` ✅ (Screen 7 (teacher side), per the module's own architecture doc numbering)

⚻ **Promoted from 📋 to ✅ this pass.** Teacher-only pass/pricing CRUD
for one classroom, reached from `ClassroomDetailScreen`'s manage sheet,
owner/admin only. Also pushed directly, `autoOpenCreate: true`, from
`ClassroomFormScreen`'s post-create chain (§6.2 above) — confirmed real:
`initState` fires `_openEditor()` via `addPostFrameCallback` when that
flag is set, auto-opening the New Pass sheet on first frame so a fresh
classroom is never left with zero pricing (and therefore unjoinable).

**API:** `GET passes/?classroom=` (list); `POST passes/` (create:
`pass_type`, `title`, `price` in coins, `validity_days`, optional
`max_classes` cap, `allow_gifting`); `PATCH passes/{id}/` (update — also
how pause/resume works, a full-object `isActive` flip); `DELETE
passes/{id}/`. Two backend-enforced business rules, both surfaced
**verbatim** from the API error rather than re-derived client-side: (1)
delete is refused once a pass has ever been purchased — pause
(`is_active=false`) is always offered as the alternative, delete only
while never-purchased is plausible; (2) an update that would
retroactively shrink what an active paid holder already bought (price up,
or validity/max-classes/type down) while purchases are active is also
refused.

⚻ **Confirmed real, in-file `FIX` comment — `allowGifting` field-loss
bug already caught and fixed at introduction:** `_togglePause` rebuilds a
whole new `ClassPass` rather than patching one field (same pattern as
`ScheduleManagerScreen`'s pause/resume) — the fix explicitly threads
`pass.allowGifting` through that rebuild, since forgetting it would
silently reset a pass's gifting flag to the model default on every single
pause/resume toggle.

**State:** `_passes` (`List<ClassPass>`), `_loading`, `_error`.

**First-pass UX nudge, confirmed in-file `NOTE`:** after the very first
pass is created for a classroom (`wasEmpty && existing == null`), snacks
"Pass created. You can add more tiers like daily/weekly/monthly if you
like" — nothing else in the UI hinted that a classroom could have
multiple simultaneous pass tiers.

**Gifting toggle (`_allowGifting`, Pass 14 frontend catch-up §1.3)** —
per-pass "Allow gifting" `SwitchListTile` in the editor sheet (default
`true`), the field `MyPassesScreen`'s/`PassGiftClaimScreen`'s gift flows
ultimately depend on (§6.3).

⚠️ **Mixed-language (Hindi) string leak, NEW finding this pass, adds a
second file to §8.10's tracked pattern:** the pass card's stat row shows
validity as `` '${p.validityDays} din' `` ("days" in Hindi) instead of an
English unit — the only non-English string in this screen.

⚠️ **Design-system drift, confirmed this pass — a THIRD distinct flavor
of the hand-rolled-loading/error/app-bar pattern, not previously
catalogued as spanning this many files (§8.5/§11.2):** unlike
`ScheduleManagerScreen` above (which at least uses `liveClassAppBar`/
`LiveClassLoading`/`LiveClassErrorState`), this screen hand-rolls a raw
`AppBar` (not `liveClassAppBar`), a raw `Center(CircularProgressIndicator)`
for loading, and a raw `Center(Text(...) + Retry button)` for its error
state — joining `WishlistScreen`/`WaitlistScreen`/`ScheduleManagerScreen`/
`SessionsListScreen`/`ClassroomDetailScreen`/`LiveSessionScreen` in that
specific flavor group, and also hand-rolls its own `Container`+`BoxShadow`
pass cards instead of `LiveClassCard` (the `ClassroomPurchasesScreen`/
`ClassroomReportsScreen`/`NoticeBoardScreen`/`ScheduleManagerScreen`
flavor) **and** its own local `_inputDecoration()` helper (the
`ClassroomFormScreen`/`ScheduleManagerScreen` flavor). All three flavors
in one file. Also aliases `_kNavy`/`_kBg`/`_kGradient` locally, same
hex-duplication fix already applied elsewhere — per an in-file comment
citing the identical gap in `explore_screen.dart`.

**Card UI:** confirmation-number gradient icon badge, title/type,
ACTIVE/PAUSED status pill, a 3-up stat row (Price in coins / Validity /
Max Classes, "Unlimited" if `maxClasses` is null), and an
Edit/Pause-Resume/Delete `TextButton` row.

---

#### `StaffManagementScreen` — `staff_management_screen.dart` ✅ (Screen 19, per the module's own architecture doc numbering)

Reached from `ClassroomDetailScreen`'s manage sheet, owner/admin only.

**API:** `GET staff/?classroom=` (list); `POST staff/`
`{classroom, user_id, role}` (add); `PATCH staff/{id}/ {role}` (change
role); `DELETE staff/{id}/` (remove). **Same numeric-ID-only limitation
as `BannedStudentsScreen`/`CertificatesScreen`'s student lookups** — no
user-search endpoint exists anywhere in this API today, so the add form
asks for the User ID directly and tells the teacher to get it from the
target user's own app profile.

**Roles (`StaffRole`):** Co-Teacher / Moderator / Teaching Assistant,
via `_roleLabel`.

**State:** `_staff` (`List<ClassroomStaff>`), `_loading`, `_error`.

**Add sheet (`_openAddSheet`/`_showAddSheet`)** — ⚻ **confirmed fixed
memory leak, called out explicitly in-file (`FIX` comment):** same shared
§8.4 pattern — `userIdCtrl` created fresh per open, disposed in a
`finally` block on every exit path. Numeric User ID validated client-side
(`int.tryParse`) before submit; role picked from a fixed 3-item dropdown
(default Teaching Assistant). This is the shape `BannedStudentsScreen`'s
own ban-sheet comment (§6.2 above) explicitly cites as identical.

**Change role (`_changeRole`)** — a bottom-sheet `ListTile` picker (not
a dropdown-in-place), one row per role; a no-op if the picked role
matches the current one.

**Remove (`_confirmRemove`)** — plain `AlertDialog` (no controller to
leak), copy names the staff member being removed.

**Tile UI:** gradient-circle avatar-initial, name, role label, and a
`PopupMenuButton` (Change Role / Remove). Uses the shared `LiveClassCard`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
throughout (§2.2) — not part of the design-system-drift group
(§8.5/§11.2). Fully English throughout — no Hindi-string-leak instance
in this file.

---

#### `PollTemplatesScreen` — `poll_templates_screen.dart` ✅

NEW screen (frontend integration architecture v3, §1.11, Pass 13) —
classroom-scoped CRUD for `PollTemplate`, owner/admin only, same
`canManage` threading convention as `CouponsScreen`/`StaffManagementScreen`
per the file's own header. Reached from `ClassroomDetailScreen`'s manage
sheet ("Poll Templates" tile, right after Certificates per the header's
own wiring snippet). Per the header, the backend contract
(`LiveClassApi.pollTemplates`, `PollTemplate`) already existed with
confirmed Pass 13 fields — **not** a skeleton, unlike `PassGiftClaimScreen`
below.

**API:** `GET poll-templates/?classroom=` (`.list(classroomId)`); `POST`
(`.create(draft)`); presumably `PATCH`/`PUT` (`.update(id, draft)`);
`DELETE` (`.delete(id)`).

**State:** `_templates` (`List<PollTemplate>`), `_loading`, `_error`.

**Editor sheet (`_PollTemplateEditorSheet`)** — a separate `StatefulWidget`
(not an inline method-built sheet like most of this module's forms),
explicitly commented as mirroring `_CreatePollSheet`'s option-list UI
pattern inside `live_session_screen.dart` "so both flows feel the same,
without depending on that file directly" — confirmed **no import** of
`live_session_screen.dart` here, just a matching UI convention.
`_questionCtrl` + a `List<TextEditingController> _optionCtrls` (starts at
the existing template's options, or `['', '']` for a new one, padded up
to a 2-controller minimum) — **all** disposed in the sheet's own
`dispose()`, including looping over `_optionCtrls`, since this is a
per-open `StatefulWidget` instance (its own `dispose()` is the natural
leak-fix point, not the outer-method-plus-`finally` pattern used for
inline-built sheets elsewhere). Options: 2–6 range enforced by
`_addOption`/`_removeOption` (no-op past either bound); validated on save
to require a non-empty question and ≥ 2 non-empty options (blank options
are filtered out, not just rejected).

**Delete (`_delete`)** — plain `AlertDialog`. **Optimistic removal with
rollback on failure**, same shape as Holidays/Materials/Assignments/
NoticeBoard: removed from `_templates` immediately, restored from a saved
`previous` copy + snacked if the `DELETE` call actually fails.

**Card UI:** quiz icon badge, question (2-line max), "`N` options ·
`first-3-options-joined`…" summary line, and a `PopupMenuButton`
(Edit/Delete). Uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
throughout (§2.2) — not part of the design-system-drift group
(§8.5/§11.2). Fully English throughout.

---

### 6.3 Enrollment / Access Flow
*(`JoinRequestsScreen` 📋 unchanged this pass — carries forward exactly
as documented previously; not re-uploaded. `ClassroomPurchasesScreen`
was re-uploaded and re-read a prior pass — full write-up below is
unchanged since. `MyPassesScreen` was read line-by-line a prior pass —
full write-up below replaced its old connection-graph-only mention then
and is unchanged since. `RequestJoinScreen` and `PassGiftClaimScreen`
were both read line-by-line a prior pass — full write-ups below are
unchanged since. **Note:** `WaitlistScreen` is cross-referenced
throughout this subsection (it's `RequestJoinScreen`'s and
`ClassroomDetailScreen`'s sibling in the enrollment/access flow) but its
own full write-up now lives in §6.4, alongside `SessionsListScreen`/
`LiveSessionScreen` — the screens that actually push it — since this
pass gave it a real body for the first time.)*

#### `MyPassesScreen` — `my_passes_screen.dart` ✅ (Screen 9, per the module's own architecture doc numbering)

Student-facing purchase history for the caller's **own** passes, across
every classroom. Reached from `LiveClassHomeScreen`'s My Learning tab
(§6.1/§7).

⚠️ **Stale header comment, confirmed by direct read — a real
contradiction between what the file's own top-of-file comment claims and
what the body actually does.** The header states: "Refund is a
teacher/staff-only action performed from their manage panel, not here —
this screen only ever displays status." That framing describes an older
version of this screen. The actual body implements **three** real,
non-display actions: a self-service cancel-with-partial-refund flow
(`_confirmCancel`, below), an auto-renew toggle (`_toggleAutoRenew`), and
a "gift this pass" flow (`_openGiftSheet`) — each with its own in-file
`NOTE`/`NEW` comment describing exactly when it was added (a self-service
cancel fix, then Pass 14's gift entry point, then Pass 15's auto-renew
toggle). The header was evidently never updated as those three features
landed. Treat the header as historical context for *why* this screen was
first built, not as a current description of what it does — the API/
state/UI detail below reflects the real, current body.

**API:**
- `GET pass-purchases/` (`PassPurchaseApi.myPurchases()`) — own
  purchases only, scoped server-side; paginated, client re-sorted newest-
  `purchasedAt`-first.
- `POST pass-purchases/{id}/cancel/` (`.cancel(id)`) — self-service
  cancel. Per the in-file `NOTE`, only `remainingBalance` (coins still in
  escrow for un-taught days) is refundable; whatever's already released
  to the teacher for classes actually held is not clawed back — the
  confirm dialog quotes that exact number (or, if it's `0`, says plainly
  there's nothing left to refund) so a student is never promised a
  bigger refund than they'll actually get. **Same escrow-split logic as
  `ClassroomPurchasesScreen`'s teacher-side refund (§6.3 above) — now
  confirmed a real, matching pair on both sides of the same action, not
  just `ClassroomPurchasesScreen`'s one-sided claim.**
- auto-renew toggle (`PassPurchaseApi.setAutoRenew(id, bool)`) — returns
  the updated `PassPurchase`, which replaces the single item in `_all` by
  index rather than triggering a full `_load()` — per the in-file
  comment, because no per-item `copyWith` is exposed here, a whole-object
  replace from the server's response is the simplest correct approach for
  a single-field flip.
- `PassGiftApi.send(classPassId:, recipient:)` — gift flow, recipient
  identified by username or email (free text, no user-search endpoint —
  same no-search limitation already documented for `BannedStudentsScreen`/
  `CertificatesScreen`'s numeric-ID-only student lookups elsewhere in this
  catalog); per the sheet's own copy, the recipient has 7 days to claim it.

**State:** `_all` (`List<PassPurchase>`, sorted `purchasedAt` descending),
`_loading`, `_error`, `_activeOnly` (`FilterChip`, client-side filters to
`p.isValid` via a `_visible` getter), `_busyIds` (cancel-in-flight per
purchase id), `_autoRenewBusyIds` (auto-renew-toggle-in-flight per
purchase id, tracked separately from `_busyIds` so the two actions don't
block each other's spinners on the same card).

⚠️ **RESOLVES §11 item 1 — `PassGiftClaimScreen` orphan, confirmed wired
here.** App bar carries a "Gifted passes" icon button that pushes
`PassGiftClaimScreen(onClaimed: (claimed) => _load())` — per the in-file
`FIX` comment, this is explicitly entry point #2 of the two `PassGiftClaimScreen`'s
own header comment calls for ("general browsing — a 'My gifts' entry
from `my_passes_screen.dart`"), opened on its Received/Sent tabs (no
`giftId`, matching that screen's no-id constructor). Claiming a gift
grants classroom access, so the callback re-fires `_load()` on return so
the newly-claimed pass shows up without a manual pull-to-refresh. See
§7/§8.9 for this as no-longer-orphaned module-wide.

**Reason/status/type label helpers (`_passTypeLabel`/`_statusLabel`/
`_statusColor`, module-level functions)** — `_passTypeLabel` maps
`PassType.free/daily/weekly/monthly/yearly` to display labels, falling
back to the raw type string for anything unrecognized. `_statusLabel`/
`_statusColor` map the raw purchase status string: `'success'` →
"Active"/green, `'refunded'` → "Refunded"/grey, `'failed'` →
"Failed"/red, anything else (including `'pending'`) → "Pending"/
`LiveClassColors.warning`.

**Card UI (`_passCard`):** icon badge, classroom title + pass title/type
line, a status `LiveClassStatusChip`; a pink "Gifted to you by
`giftedBy.fullName`" row shown only when both `giftId` and `giftedBy` are
non-null (a normal, non-gifted purchase shows nothing extra); a divider;
Purchased/Expires date stats (Expires in red if `expired && status ==
'success'`); a Coins Spent/Coupon stats row; a conditional
Released-to-Teacher/Held-in-escrow stats row, shown only when `status ==
'success'` **and** (`coinsReleased > 0` **or** `remainingBalance <
coinsSpent`) — per the in-file `NOTE`, a fresh fully-unreleased purchase
would otherwise show `remainingBalance == coinsSpent`, redundant with the
row above it; an optional classes-attended progress bar (only if
`maxClasses` is set); an expired-pass info banner; an auto-renew-failure
banner (shown whenever `renewalFailedAt != null`, regardless of current
status — explains *why* a pass reads as expired instead of leaving it
unexplained); a "Renewed from purchase #`renewedFrom`" footnote when
present; an auto-renew `Switch` (only on an active (`status ==
'success'`), unexpired purchase — nothing to auto-renew on a free or
already-lapsed pass); and, also only while active-and-unexpired, a
Gift/Cancel Pass button row — per the in-file `NOTE`, self-service cancel
is deliberately withheld once a pass is already expired, since the
backend's own `expire_and_refund_passes` sweep already handles that
leftover escrow automatically and a manual cancel at that point would
just race the same sweep for no benefit.

Uses the shared `LiveClassCard`/`LiveClassIconBadge`/`LiveClassStatusChip`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
(§2.2) throughout — not part of the design-system-drift group (§8.5/§11.2).

---

#### `ClassroomPurchasesScreen` — `classroom_purchases_screen.dart` ✅

Teacher-side pass-purchase roster for **one** classroom, reached from
`ClassroomDetailScreen`'s manage sheet ("Purchases" tile), owner/admin
only. Per the file's own header, this screen is the missing UI for a
backend `refund` action and Dart `PassPurchaseApi.refund()`/`forClassroom()`
pair that already existed with **no screen anywhere calling them** — same
orphaned-backend-action pattern as `BannedStudentsScreen` (§6.2, §8.9).
Getting a teacher-scoped list at all required a **backend** fix too
(`PassPurchaseViewSet.get_queryset()` was hard-scoped to "own purchases
only"; per the header comment it now also accepts `?classroom=<id>` for
that classroom's teacher/co-teacher/moderator).

Use case is explicitly narrower than it might look: **single-student**
refunds (e.g. resolving one complaint), not bulk refunding — refunding
everyone at once is "Close Classroom" elsewhere in the module, not this
screen.

**API:**
- `GET pass-purchases/?classroom=<id>` (`.forClassroom(classroomId)`) —
  teacher-scoped list, paginated result (`res.results`).
- `POST pass-purchases/{id}/refund/` — refund. **Only `remainingBalance`**
  (coins still sitting in escrow for un-taught days) goes back to the
  student; `coinsReleased` (days already actually taught) has already
  paid the teacher and is **not** clawed back. Access to the classroom
  ends immediately either way. This cannot be undone.

**State:** `_all` (`List<PassPurchase>`, sorted newest-`purchasedAt`-first),
`_loading`, `_error`, `_activeOnly` (client-side `FilterChip` filtering to
`p.isValid`), `_busyIds` (per-purchase refund-in-flight guard).

**Refund confirm dialog (`_confirmRefund`)** — ⚻ **fixed dialog copy**
(the file's own `NOTE` comment flags this as a correction of a real prior
bug): the dialog used to quote the purchase's full `coinsSpent` as what
comes back to the student, which overstated the refund on any purchase
that had already had at least one day charged against it. Now correctly
branches on `p.remainingBalance`: if `> 0`, quotes exactly that amount as
what returns, explicitly notes the already-released `coinsReleased`
portion stays with the teacher; if `0`, tells the teacher there's nothing
left to refund and only access will end. This is the concrete screen
behind the module-wide "Overstated refund dialog" fix already tracked in
§8.8 — confirmed at its source this pass, not just inferred.

`canRefund` gates the button to `p.status == 'success'` only — mirrors a
real backend-side rejection of any other status, just avoiding a
pointless round-trip / confusing enabled-but-fails button for an
already-refunded/failed/pending purchase.

**Card UI:** avatar, name/`@username`, status pill
(`_statusLabel`/`_statusColor`: success→"Active" green, refunded→grey,
failed→red, else→"Pending" orange), a `Divider`, then a 3-up `_stat()` row
(Pass title / Coins spent / Purchased date via `_fmtDate` →
`liveClassFmtDate(d, context)`), an **escrow-split row** (`You received` /
`Held in escrow`, shown only when `status == 'success'` **and**
(`coinsReleased > 0` or `remainingBalance < coinsSpent`) — mirrors
`MyPassesScreen`'s same condition per the file's own comment, and is
itself a fix: a flat "Coins" total previously gave the teacher no
visibility into how much of a purchase was already theirs vs. still
refundable), optional coupon code, and the Refund button when `canRefund`.

⚠️ **Design-system drift, confirmed and self-flagged in-file:** the
screen aliases `LiveClassColors.navy`/`.bg` into local `_kNavy`/`_kBg`
constants (fixing an earlier hex-literal-duplication drift risk the
file's own comment says was already called out for `wishlist_screen.dart`
and `waitlist_screen.dart`) but still **hand-rolls its own card
`Container`+`BoxShadow`** rather than using the shared `LiveClassCard`,
and its empty state is a bare `ListView`+`Center(Text(...))` rather than
`LiveClassEmptyState` — this screen is a confirmed **8th** instance of the
structural drift tracked in §8.5/§11.2 (that gap's count should be updated
from 7 to 8, see §14).

---

#### `RequestJoinScreen` — `request_join_screen.dart` ✅

⚻ **Resolves the build-break bug at its actual source — confirmed
in-file, cross-references §8.6:** `ClassroomDetailScreen._openRequestJoin()`
has always pushed `RequestJoinScreen(classroomId:, classroom:)`, but per
this file's own header, no file in the module ever defined that class
before now — this file used to be a **stale duplicate** of
`join_requests_screen.dart`'s `JoinRequestsScreen` (same class name, same
two named constructors, nothing importing it), left over from a fixed
ambiguous-import compile error. Replaced with the actual missing screen
rather than deleted, since the filename already matched what
`ClassroomDetailScreen` expects to import.

**Used for BOTH entry points routed through `_openRequestJoin()`,
confirmed in-file:** `accessLevel == 'none'` → fresh "Request to Join";
`accessLevel == 'expired'` → "Renew Pass" (same flow — renewing is just a
new join request against a pass, same as joining fresh). Does **not**
charge coins or grant access itself — that only happens when the teacher
(or co-teacher/moderator) accepts the request from
`JoinRequestsScreen.inbox()`.

**Flow, confirmed exact order:** pick one active `ClassPass` → optional
coupon code (validated **live** via `coupons/validate/` before submit,
never blindly trusted — the submit path only ever sends a coupon code
that's already been through this validation, per an explicit in-file
comment: passing through unvalidated text would just surface as a
confusing 400 on submit instead of the inline validation error the Apply
button already showed) → optional message to the teacher → `POST
join-requests/`.

**API:** `ClassPassApi.list(classroomId:)` (active passes, sorted
cheapest-first); `CouponApi.validate(code)`; `JoinRequestApi.request({classroomId,
classPassId, couponCode, message})`.

**State:** `_passes` (`List<ClassPass>`, filtered to `isActive`,
price-ascending), `_selectedPassId` (defaults to the cheapest active
pass), `_appliedCoupon`/`_validatingCoupon`/`_couponError`, `_submitting`.
`_couponCtrl`/`_messageCtrl` are **class-level, disposed once in the
screen's own `dispose()`** — explicitly commented as deliberate: this is
a persistent full screen, not a repeatedly-opened bottom sheet, so it
doesn't need the outer-method-plus-`finally` leak-fix pattern used for
sheet controllers elsewhere (the comment cites `referral_screen.dart` for
the same reasoning).

**UI:** radio-style pass-selection tiles (title/type + validity/max-classes
summary + price, "Free" if `price == 0`); an applied-coupon chip
(discount-percent-or-amount summary via `_couponSummary`, with a clear/
remove button) or an entry field + Apply button when none is applied yet;
an optional message field; a bottom-docked "Send Request" CTA (disabled
while `_submitting` or with no passes available). Uses the shared
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading`/
`LiveClassCard` pieces (§2.2) for the coupon-applied chip, but the
pass-selection tiles themselves are a hand-rolled `Container`+`BoxShadow`,
not `LiveClassCard` — **another confirmed instance** of the
hand-rolled-card-chrome flavor tracked in §8.5/§11.2 (alongside
`ClassroomPurchasesScreen`/`ClassroomReportsScreen`/`NoticeBoardScreen`/
`ScheduleManagerScreen`/`PassManagementScreen`). Fully English throughout.

---

#### `PassGiftClaimScreen` — `pass_gift_claim_screen.dart` ✅

⚻ **Promoted from 📋 to ✅ this pass.** New screen (frontend
integration architecture v3, §1.3, Pass 14). ⚠️ **The file's own header
self-flags as an "ARCHITECTURE SKELETON"** — `PassGift`/`PassGiftApi`
were a best guess from a change-log description only, since "Pass 14 was
never written up in the backend doc's own §2–§6"; the header explicitly
asks for exact field names/endpoint paths to be confirmed against real
backend source before shipping. This doc's line-by-line read this pass
confirms the file **compiles consistently against `liveclass_models.dart`'s
current `PassGift`/`PassGiftStatus`** (see the two in-file self-corrections
below) — it does not independently confirm those model shapes are
themselves correct against the real Django backend, which is a separate,
still-open question the header itself raises.

**Two entry points, both now confirmed real (§6.3 above, §7, §8.9, §11
item 1):** (1) a notification tap — `liveclass_notification_handler.dart`
opens this screen directly on a specific gift via `PassGiftClaimScreen(giftId:)`,
per §9's `pass_gift_expired`/etc. routing (not independently re-verified
this pass, since the handler file wasn't re-uploaded); (2) general
browsing — `MyPassesScreen`'s "Gifted passes" app-bar icon,
`PassGiftClaimScreen()` with no id, opening on Received/Sent tabs.

**Constructor:** `{giftId?, onClaimed?}` — `giftId` set routes to a
single-gift detail view (`_SingleGiftView`); unset routes to a
`DefaultTabController`-based Received/Sent `TabBarView`
(`_GiftListView` × 2). `onClaimed` fires with the claimed `PassGift` on a
successful claim from either path — per the header, meant to be wired by
the caller to push into `ClassroomDetailScreen` using the classroom id
the gift unlocked (this screen deliberately does **not** import
`classroom_detail_screen.dart` directly, to avoid an import cycle, since
that screen is the one pushing this one).

**API:** `PassGiftApi.myGiftsReceived()`/`.myGiftsSent()` (list, `sent`
excludes the claim button); `.claim(id)`; `.cancel(id)` (gifter-only,
while still `PENDING`).

⚠️ **Confirmed real gap, in-file comment:** there is **no single-gift GET**
in the documented `PassGiftApi` group — `_SingleGiftView` (the `giftId:`
path) works around this by fetching the **entire** received-gifts list
and filtering client-side for the matching id, with an explicit comment
flagging this as a stopgap: "add a dedicated `PassGiftApi.detail(id)`
once the real endpoint is confirmed, rather than paging through
everything." Works correctly today but doesn't scale past a small
received-gifts list.

⚻ **Two confirmed in-file self-corrections against `liveclass_models.dart`'s
real `PassGift`/`PassGiftStatus` — evidence this skeleton has already
been reconciled against the model once:** (1) `PassGift`'s real recipient
field is `recipient` (non-nullable `UserMini`) — an earlier version
referenced `giftedTo`/`giftedToRaw`, which never existed and wouldn't
compile; (2) `PassGiftStatus` has no `refunded` value on the real
backend — cancelling refunds the gifter as a side effect, but the gift
row itself lands on `cancelled`, not a separate `refunded` status; an
earlier version's `switch` referenced a constant that no longer exists.

**Cancel (gifter-side, `_cancel` in `_GiftListView`)** — per an in-file
comment tying this to a specific tracked backend item ("item 10"), the
gifter can cancel their own still-`PENDING` gift; refund happens
server-side (`PassGift.refund_to_gifter()`) and the client just swaps in
the returned (now-cancelled) gift.

**Live countdown (`_SingleGiftView`)** — a `Timer.periodic(30s)` just
calls `setState(() {})` with no other side effect, so the "Claim within
`N` days/hours" label recomputes off `gift.timeLeft` on each tick;
cancelled in `dispose()`.

**UI:** a shared `_GiftCard` (pass title, classroom, "From `<gifter>`",
a claim-window countdown or status label, and a "Claim Gift" button when
still pending-and-unexpired) reused both for the single-gift view and
(in a `compact` variant) for pending rows in the Received tab list;
non-pending / non-received rows instead show a plain `LiveClassCard` row
with a status chip (or a Cancel button for the gifter's own pending Sent
rows). Uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassStatusChip`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` pieces throughout (§2.2) — not part of the
design-system-drift group (§8.5/§11.2). Fully English throughout.

---

### 6.4 Sessions / Live Class Flow

#### `SessionsListScreen` — `sessions_list_screen.dart` ✅ (Screen 5, per the module's own architecture doc numbering)

⚻ **Read line-by-line in full this pass — the placeholder from the two
prior passes ("unchanged, carries forward") never actually had a real
body anywhere in this doc; a genuine one is written below for the first
time, replacing both placeholders.** The classroom's full session
history + upcoming queue — distinct from the "next session" summary
already shown inline on `ClassroomDetailScreen`. Reached from Classroom
Detail's manage panel (teacher, `canManage: true`) or "Schedule" area
(student, view-only). Layout: a horizontal 14-day date strip (today-3 to
today+10, dots mark days with a session) above a status-filtered list —
avoids pulling in a calendar package while still giving day-picking.

**API:** `GET sessions/?classroom=` — ⚠️ **confirmed real, still
unresolved tension with §5.2:** an in-file `NOTE` states
`ClassSessionViewSet.get_queryset()` only reads `?classroom=` and never
reads `?status=`, so a server-side status filter would silently be a
no-op; the screen fetches once per classroom and filters status
**client-side** instead (same approach already used for the date-strip
filter). Also: `POST`/`PATCH`/`DELETE sessions/` for teacher ad-hoc
sessions; `POST sessions/{id}/end/`; `GET sessions/{id}/unread/`
(Pass 13 §1.10, chat/poll unread counts).

**Reminders — confirmed live, resolving half of the `ClassReminder` gap
(the other half is `MyRemindersScreen`, §6.6):** a bell `IconButton` on
every upcoming (`scheduled`) session card opens `_ReminderSheet` to pick
an offset + channel and `POST`s a `ClassReminder`. `_loadReminders()` is
best-effort (no classroom filter exists on `reminders/`, so the full list
is pulled and matched client-side by session id; a failure here just
leaves the bell showing "no reminder set"). `_loadUnread()` (Pass 13
§1.10) is likewise best-effort, one call per live/completed session
currently shown, no bulk endpoint.

⚻ **RESOLVES §11 item 7 — "Recording playback inconsistency," confirmed
fixed, not still open as previously tracked.** The completed-session
card's "Recording" button calls `_openRecording(s.recordingUrl)`, which
does a real `url_launcher` hand-off (`Uri.tryParse` + `canLaunchUrl` +
`launchUrl(..., mode: LaunchMode.externalApplication)`, snacking on
failure) — **the same pattern as `ClassroomRecordingsScreen` (§6.2)**,
not the snack-only stub the known-gaps list previously described. That
gap entry was carried forward across passes without re-verification;
this pass's actual read shows it was already fixed.

**`isJoinable` host-bypass fix, confirmed in-file `NOTE`:** the
server-computed `isJoinable` window is meant to stop **students** joining
before the teacher starts class — but the host is the one who starts it,
so gating them on the same flag left a teacher with a just-created
scheduled session and no way to ever enter it. The Enter/Start Class
button now shows whenever `status == live` **or** (`status == scheduled`
**and** (`isJoinable` **or** `canManage`)) — a manager can always attempt
entry, with the backend keeping final say (errors surface inside
`LiveSessionScreen`).

⚠️ **Mixed-language (Hindi) string leaks, confirmed this pass —
substantially more extensive than the one-line mention previously
carried forward, and concentrated almost entirely in the reminder
sheet:** "Wapas" (Back) as the Cancel-side button label in **both** the
delete-session and end-session confirm dialogs; and, in `_ReminderSheet`,
`_offsetLabel`'s three unit suffixes are entirely Hindi ("`N` din pehle"/
"`N` ghante pehle"/"`N` min pehle" — "days/hours/minutes before"), the
remove-reminder button reads "Reminder Hataayein," and both sheet
section headers are Hindi ("Kitni der pehle?" / "Kaise batayein?" —
"how long before?" / "how to notify?"). Every other string in the file,
including the rest of the reminder sheet's own body copy, is English —
this reads as one specific author's pass through the reminder feature,
not a module-wide habit. See §8.10/§11 for the running tally.

**Timezone/i18n, confirmed real and correctly done** (unlike the
`_fmtDate` gap found in `ScheduleManagerScreen`, §6.2): `_fmtDate`/
`_fmtTime`/`_fmtWeekdayShort` all take and forward an optional `context`,
and `.toLocal()` is applied before reading any clock field — an in-file
comment describes the pre-fix state explicitly (hardcoded English
month/weekday arrays, no `.toLocal()`, every session time on this screen
wrong for a non-UTC, non-English viewer) and this file's current state
resolves it correctly everywhere, including the ad-hoc session form's
edit-mode date-picker pre-fill (a separate, explicitly-noted **wrong-
calendar-day** risk near midnight, not just a wrong-hour one).

**State:** `_all` (`List<ClassSession>`), `_reminders`
(`Map<int, ClassReminder>`, active-only), `_unread`
(`Map<int, SessionUnreadCount>`), `_statusFilter`/`_selectedDate`
(client-side filters via a `_visible` getter), `_dateStrip` (14 fixed
`DateTime`s computed once in `initState`).

⚠️ **Design-system drift, confirmed — the hand-rolled-loading/error/
app-bar flavor (§8.5/§11.2), already counted for this file in that
tracked group:** hand-rolled `Container`+`BoxShadow` session cards and
date-strip chips (not `LiveClassCard`), a bare `ListView`+`Center(Text)`
empty state (not `LiveClassEmptyState`), though it does use
`liveClassAppBar`/`LiveClassLoading`/`LiveClassErrorState`. Also aliases
`_kNavy`/`_kBg`/`_kGradient` locally (the same hex-duplication fix
applied to `wishlist_screen.dart`/`waitlist_screen.dart`, per an in-file
comment) — confirmed as the file the `ScheduleManagerScreen`/
`PassManagementScreen` write-ups (§6.2) cite when describing their own
identical alias pattern.

**Card UI:** status pill (live pulse dot for `live`), `canManage`-only
edit/waitlist/delete `PopupMenuButton` on scheduled sessions, date/time
range, optional room-id line, unread chat/poll badges, the reminder bell,
and a context-dependent action row (Enter/Start Class gradient button,
disabled "Not Joinable Yet", Recording playback, `canManage`-only View
Report/Waitlist/End buttons per status).

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

⚻ **Read line-by-line in full this pass — the one-line "see prior pass's
write-up" placeholder never pointed at an actual body anywhere in this
doc; a genuine write-up is added here for the first time.** NEW screen
(Pass 15 frontend catch-up §1.7) — "View Report" entry point on a
**completed** session, host/co-teacher/moderator only, pushed from
`SessionsListScreen`'s per-card "View Report" button (§6.4 above).
Read-only summary of a `SessionEngagementReport`.

**API:** `GET sessions/{id}/engagement-report/`
(`LiveClassApi.sessions.engagementReport(sessionId)`).

⚠️ **Confirmed genuine unconfirmed-field caveat, straight from the file's
own header — not just a generic disclaimer:** the `SessionEngagementReport`/
`SessionAttendanceRow` model and this endpoint already existed in
`liveclass_models.dart`/`liveclass_api_service.dart` before this screen
was written (Pass 15 architecture skeleton); this file is **the first
thing that actually calls `engagementReport()`** anywhere in the module.
The header explicitly warns: if a field comes back null/0 for every
session, check the field names against the real serializer before
assuming attendance was actually zero.

**State:** `_report` (`SessionEngagementReport?`), `_loading`, `_error`.
Single fetch, no pagination.

**Metrics shown:** a 2×2 stat grid (attendance count, average watch
duration via a local `_fmtDuration()` helper — `Xm Ys`, or bare `Ys`
under a minute — chat message count, hand-raise count), a poll-
participation-rate progress bar + percentage (`.clamp(0, 1)` guards
against a malformed >100%/negative value from the unconfirmed field
above), and a per-student attendance list (`SessionAttendanceRow`: avatar
or initial, name, a hand-raised icon when `raisedHand`, and that
student's own watch duration).

**UI:** uses the shared `LiveClassCard`/`LiveClassEmptyState`/
`LiveClassErrorState`/`LiveClassLoading` pieces throughout (§2.2) — not
part of the design-system-drift group (§8.5/§11.2). Fully English
throughout. No FAB, no write actions — purely read-only, matching
`MyProgressScreen`'s (§6.6) read-only-dashboard shape.

---

#### `WaitlistScreen` — `waitlist_screen.dart` ✅ (Screen 20, per the module's own architecture doc numbering)

⚻ **Read line-by-line in full this pass — tagged ✅ in §0 and referenced
several times in the connection graph (§7) but never actually given a
write-up body anywhere in this doc; a real one is added here for the
first time.** **Two modes in one screen, driven entirely by whether
`sessionId` is passed, confirmed exact:**
- **Omitted → "My Waitlist"** (student, own view): every session the
  caller is currently waitlisted on, across classrooms. Each
  `SessionWaitlistEntry` only carries a bare session id, so the screen
  separately enriches each one with its classroom/time via
  `sessions/{id}/` (`_sessionCache`, best-effort — a missing detail just
  falls back to "Session #`id`", non-fatal per an in-file comment).
- **Provided (+ `classroomTitle`, `canManage: true`) → teacher/staff
  "who's waiting" view**, scoped to that one session, oldest-first.
  Reached from `SessionsListScreen`'s per-card "Waitlist" action/menu
  item (§6.4 above, both the scheduled-session popup menu and the
  live-session action row) and from `LiveSessionScreen`'s waitlist panel
  (§6.4 above) when a session is full.

**API:** `LiveClassApi.waitlist.myEntries(sessionId:)` (list — doubles as
both modes' fetch, the `sessionId` argument is what actually
differentiates them server-side); `DELETE waitlist/{id}/` (`.leave(id)`,
student-only); `POST waitlist/{id}/promote/` (`.promote(id)`,
teacher/staff-only).

**State:** `_entries` (`List<SessionWaitlistEntry>`, sorted by
`joinedAt`), `_sessionCache` (`Map<int, ClassSession>`, student-view
only), `_loading`, `_error`, `_busy` (`Set<int>`, per-entry
leave/promote/rejoin-in-flight guard).

**Leave (`_leave`, student)** — removes the entry from the local list on
success; a failure just snacks the error, no rollback needed since
nothing was optimistically removed before the call completed.

**Promote (`_promote`, teacher/staff)** — same shape, snacks the promoted
student's name on success; on failure the entry is **not** removed (the
`_busy` guard is cleared but the entry stays), unlike `_leave`'s
always-remove-on-success path.

**Try Enter / rejoin (`_tryRejoin`, student)** — calls
`sessions/{id}/join/` directly rather than re-navigating through
`SessionsListScreen`; if the result still comes back `waitlisted` (no
seat has actually opened), it snacks "No seat has opened up yet" and
leaves the entry in place; otherwise it pushes straight into
`LiveSessionScreen(sessionId:, initialResult:)` — the confirmed 3-arg
constructor shape documented for `LiveSessionScreen` above, skipping that
screen's own join call since this screen already completed it.

**Card UI:** **manage view** — a numbered position circle, student
name, a relative "Waiting since `Xm/h/d` ago" line (`_fmtRelative`, a
local helper — not `liveClassFmtRelative` from the shared theme file,
worth a spot-check against the §11 item 8 "duplicated relative-time
helper" tracking if this file is ever re-audited for that specific
pattern), a green "Notified" pill when `e.notified`, and a Promote
button. **Student view** — a gradient icon tile, classroom title (or
"Session #`id`" fallback) + scheduled time from `_sessionCache`, a
"Seat opened!" pill when `e.notified` (with a green card border to match),
the same relative-wait line, and side-by-side Leave/Try Enter buttons.

⚠️ **Design-system drift, confirmed — the hand-rolled-card flavor
(§8.5/§11.2):** both card variants hand-roll their own
`Container`+`BoxShadow` (not `LiveClassCard`), and the empty state is a
bare `ListView`+`Center(Text)` block, not `LiveClassEmptyState` — though
the screen does use `liveClassAppBar`/`LiveClassLoading`/
`LiveClassErrorState`. Also aliases `LiveClassColors.navy`/`.bg`/`.gradient`
into local `_kNavy`/`_kBg`/`_kGradient` constants, per an in-file comment
explicitly citing this as the same hex-duplication fix already called
out for `wishlist_screen.dart` (§6.6) — confirmed as one of the files the
`ScheduleManagerScreen`/`SessionsListScreen` write-ups (§6.2/§6.4) point
to when describing their own identical alias pattern.

**Timezone/i18n, confirmed real and correctly done:** the student-view
scheduled-time line routes through the shared, `.toLocal()`-aware
`liveClassFmtDateTime()` helper (`_fmtDateTime` local wrapper) — an
in-file comment explicitly frames this as the same fix already applied
to doubts/holidays/submission-grading elsewhere in the module, replacing
a prior hardcoded-English-month-array-with-no-`.toLocal()` implementation.
Fully English throughout otherwise — no Hindi-string-leak instance in
this file.

---

#### `ChatMessageReportsScreen` — `chat_message_reports_screen.dart` ✅

*(re-uploaded and re-read this pass — full write-up below replaces the
old one-line placeholder.)* The moderation queue for reported chat
messages — "Phase 2, item 7" per the file's own header. Backend
(`ChatMessageReportViewSet.review` in `views.py`) and a Dart caller
(`ChatMessageReportApi.review`) both already existed with **no screen
anywhere in the module surfacing the queue at all** — `ClassroomDetailScreen`'s
manage sheet had no tile for it — until this screen; same
orphaned-backend-action pattern as `BannedStudentsScreen`/
`ClassroomPurchasesScreen` (§6.2/§6.3, §8.9).

⚠️ **Architecturally important, confirmed directly against
`ChatMessageReportViewSet.get_queryset()`:** this is a **session-scoped**
queue, **not** classroom-scoped — there is no "all reports for this
classroom" endpoint anywhere in the API. So the screen itself is a
classroom-scoped *shell* around a session picker: pick one of this
classroom's sessions, then review only that session's chat reports. A
future "show me every report across the classroom" request would need a
new backend endpoint, not just a Dart-side change.

Review actions are exactly two — confirmed against
`ChatMessageReportViewSet.review` (everything except `'pending'` is a
valid target status, but only two are ever surfaced in this UI):
`'actioned'` (also soft-deletes the reported message server-side) and
`'dismissed'`. There is deliberately **no** separate admin-note field on
this action — `ChatMessageReportApi.review()` itself already dropped that
parameter, and this screen doesn't try to reintroduce it.

**State:** `_sessions`/`_selectedSessionId`/`_sessionsLoading`/
`_sessionsError` (session picker, sorted most-recent-`scheduledStart`-first
— "almost always where a fresh report will be sitting", per the file's
own comment); `_statusFilter` (defaults `'pending'`) /`_reports`/
`_reportsLoading`/`_reportsError` (report list for the selected session +
filter); `_busyIds` (per-report review-in-flight guard).

**Flow:** `_loadSessions()` on `initState`, auto-selects the first
(newest) session and immediately calls `_loadReports()` for it.
`_loadReports()` calls `chatMessageReports.list(sessionId:, status:)`.
Switching the session dropdown or the status `ChoiceChip`
(Pending/Actioned/Dismissed) both re-trigger `_loadReports()`.
`_review(r, status)` calls `.review(r.id, status:)`, then optimistically
removes the report from `_reports` on success rather than reloading the
whole list.

⚻ **Fixed locale bug, confirmed in-file (`FIX` comment):** the session
dropdown's label used to call `liveClassFmtDate(s.scheduledStart)` without
the `context` param, silently falling back to `intl`'s default locale
instead of the device/app one — the same class of gap already fixed
elsewhere in the module (the file's own comment cites
`doubts_screen.dart`'s matching call sites). Now passes `context`
correctly.

**Report tile UI:** reason (underscore-stripped) + relative timestamp via
`liveClassFmtDateTime(r.createdAt, context)`, an italic message-preview
box (`r.messagePreview`, shown only if non-empty), an optional longer
`r.description`, "Reported by `<name>`", and — only when
`_statusFilter == 'pending'` — a Dismiss/`Action (remove)` button pair
(the latter in `Colors.red.shade600`); otherwise a status
`LiveClassStatusChip` (danger-colored for `'actioned'`, grey for anything
else).

#### `SubmissionGradingScreen` — `submission_grading_screen.dart` ✅ (Screen 12, submission+grading half, per the module's own architecture doc numbering)

⚻ **Read line-by-line in full this pass — the one-line placeholder
never pointed at an actual body anywhere in this doc; a genuine write-up
is added here for the first time.** `ClassroomDetailScreen`'s embedded
`_AssignmentsTab` has its own **separate, lighter, in-sheet**
`_SubmissionsSheet` (§6.1) rather than navigating to this screen — the
two are parallel implementations of similar grading UI, not one reusing
the other. Constructor takes an `Assignment` object directly (not just an
id) plus `canManage` — same param shape convention as `DoubtsScreen`/
`NoticeBoardScreen`.

**API:** `GET submissions/?assignment=` (`.list(assignmentId)` — the
endpoint is expected to scope itself to "own" for non-manager callers,
same pattern as `join-requests/` elsewhere, per an in-file comment — so
this screen never needs to know the signed-in user's own id); `POST
submissions/` (student submit, multipart file); `POST
submissions/{id}/grade/` (`{score, feedback}`, score capped at
`assignment.maxScore`).

**Two bodies, one screen:** `widget.canManage` switches between
`_teacherBody()` (every submission, tap-to-open-file, a Grade/Regrade
button each) and `_studentBody()` (the assignment brief plus either a
Submit button or the caller's own single submission's status/grade —
`_submissions.first`, since a student's own scoped list is expected to be
at most one item).

⚻ **Confirmed real, in-file `FIX` comment — upload size/type validation
gap, same class already fixed in `classroom_detail_screen.dart`/
`assignments_screen.dart`'s pickers:** file submission used to have **no**
extension restriction and **no size check at all** client-side — a
student could pick any file of any size, only discovering the backend's
real 50MB/safelist rejection (`Submission.file`'s
`MaxFileSizeValidator(50)` + `DOCUMENT_MEDIA_EXTENSIONS`) after a full,
potentially very slow upload attempt. Now pre-filtered via
`FilePicker.platform.pickFiles(allowedExtensions:
LiveClassUploadLimits.documentExtensions)` and independently re-checked
via `LiveClassUploadLimits.checkPlatformFile(maxMB: submissionMaxMB,
allowedExtensions:)` before the upload ever starts.

**File open/download (`_openFile`)** — Dio + auth-token header, cached to
a temp path, opened via `OpenFilex.open()`. ⚻ **Confirmed fixed corrupt-
cache bug, same shared pattern as `certificates_screen.dart`/
`materials_screen.dart` (§6.5/§8):** a download that failed partway used
to leave a partial/corrupt file at the cache path, which the `exists()`
check then treated as permanently-already-cached; a failed download now
deletes its own partial file in a `catch`/`rethrow` before the outer
handler snacks "Couldn't open file," so the next tap retries cleanly.

**Grade dialog (`_openGradeDialog`/`_showGradeDialog`)** — ⚻ **confirmed
fixed memory leak, called out explicitly in-file (`FIX` comment):** same
shared §8.4 pattern — `scoreCtrl`/`feedbackCtrl` created fresh per open
(pre-filled from any existing grade for a "Regrade"), disposed in a
`finally` block on every exit path. Score validated client-side to
`0–maxScore` before submit.

⚻ **Timezone fix, confirmed real and consistent with the rest of the
module:** the assignment brief's due-date line and every submission's
submitted-at line both route through `liveClassFmtDateTime(..., context)`
— in-file comments explicitly cite the risk this closes (a UTC-vs-IST
student could otherwise see a shifted deadline and be marked late
incorrectly) and cross-reference this as the same fix already applied
elsewhere in the module.

**Card/brief UI:** the assignment brief is a `LiveClassCard` (icon,
title, description, due-date row reddened+bolded when overdue, max
score, optional "View attachment" button); teacher rows are
`LiveClassCard`-wrapped `ListTile`s with an avatar-initial, submitted-at
+ late/graded status line, and a trailing Grade/Regrade button; the
student's own status tile swaps its leading icon between an hourglass
(ungraded) and a gold grade icon (graded), with a two-line subtitle when
feedback is present. Uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
throughout (§2.2) — per the file's own header, this pass's (the one that
introduced these fixes) restyle onto the shared design system; not part
of the design-system-drift group (§8.5/§11.2). Fully English throughout.

---

### 6.5 Coursework / Content Screens
*(`MaterialsScreen` and `DoubtsScreen` were re-uploaded and re-read a
prior pass — full write-ups below, unchanged since. `CertificatesScreen`'s
full write-up (added a prior pass) is unchanged. `AssignmentsScreen` was
read line-by-line a prior pass — full write-up below (after
`CertificatesScreen`) replaced its old 📋 connection-graph-only mention
then and is unchanged since. **`NoticeBoardScreen` was read line-by-line
for the first time this pass — promoted from 📋 to ✅, full write-up
below (last in this subsection) replaces its old connection-graph-only
mention (see the top-of-doc revision note).**)*

#### `MaterialsScreen` — `materials_screen.dart` ✅ (Screen 11, per the module's own architecture doc numbering)

Reached from `ClassroomDetailScreen`'s manage sheet (`canManage: true`,
teacher/staff) or the embedded `_MaterialsTab` shortcut (student,
read-only, `canManage: false`).

**API:** `GET materials/?classroom=`; `POST materials/` (multipart —
either a file **or** `external_link`); `PATCH materials/{id}/`;
`DELETE materials/{id}/`. Upload/edit/delete are restricted **server-side**
to teacher/co-teacher/moderator (`ClassMaterialViewSet.perform_update`/
`destroy`), independent of whatever `canManage` the caller passes in.

**Naming collision, confirmed in-file:** Flutter's own `material.dart`
exports a `MaterialType` enum (the `Material` widget's internal
canvas/card/circle/button/transparency `type:` property). This file's
`MaterialType` (`pdf`/`ppt`/`doc`/`image`/`video`/`link`) comes from
`liveclass_models.dart` — the two collide, so the import is
`package:flutter/material.dart' hide MaterialType` (§8.7's third
confirmed instance, alongside `classroom_detail_screen.dart` and
`live_session_screen.dart`).

**State:** `_items` (`List<ClassMaterial>`, sorted `uploadedAt`
descending), `_loading`, `_error`, `_uploading` (drives the FAB's own
spinner independent of the list's loading state).

**Open/download (`_openMaterial`/`_downloadAndOpen`)** — external links
are copied to the clipboard (snacked "paste it in your browser") since
there's no in-app browser wired up; files download via `Dio` (Bearer
token from `AuthService.getToken()`) to the temp directory, then open via
`OpenFilex.open()`. ⚻ **Confirmed instance of the module's
corrupt-cache-on-failed-download fix (§8.1):** a download that used to
die partway left a permanently-unopenable partial file at the cache path
(the `exists()` check treated it as already-cached forever); a failed
download now deletes its own partial file in the `catch` before
rethrowing, so the next tap retries fresh instead of replaying the
corrupt file.

**Upload sheet (`_openUploadSheet`/`_runUploadFlow`)** — ⚻ **confirmed
fixed memory leak, called out explicitly in-file (`FIX` comment):** both
`titleCtrl` and `linkCtrl` `TextEditingController`s used to be created
without ever being disposed; both are now created fresh per open and
released in a `finally` block that covers every exit path, since their
values are still read *after* the sheet closes (for the upload call) —
the comment explicitly notes disposal has to happen at the very end of
the outer method, not inside the sheet builder, for exactly that reason.
Type dropdown (PDF/Presentation/Document/Image/Video/External Link) via
`file_selector`'s `openFile()` for the 5 file types, or a plain link
`TextField` for External Link. Client-side validation before the sheet
even closes: title required, file required (unless link type), link
required (if link type).

⚠️ **Confirmed, unresolved finding (already flagged as new two passes
ago, carried forward and now directly source-verified):** this upload
flow does **not** call `LiveClassUploadLimits.checkXFile()` anywhere —
unlike `ClassroomDetailScreen`'s embedded `_MaterialsTab` (§2.4/§6.1),
which does, with `materialMaxMB`. The same logical upload action
(materials, materialMaxMB) is client-side pre-validated on the embedded
tab's path and **not** on this full-screen path's — a real, live
inconsistency, not a resolved/carried-forward note.

**Delete (`_confirmDelete`)** — plain `AlertDialog`. **Optimistic removal
with rollback on failure**, same shape as Holidays: item removed from
`_items` immediately, restored from a saved `previous` copy + snacked if
the `DELETE` actually fails.

**Card UI (`_materialCard`):** icon keyed off `materialType` via
`_iconFor` (pdf/ppt/doc/image/video, else generic link icon), title,
"`uploadedBy.fullName` · `date`" subtitle, trailing delete button
(`canManage`) or a plain chevron (read-only). Whole `ListTile` is
tappable → `_openMaterial`. FAB ("Upload") only renders when
`widget.canManage`. Uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
throughout (§2.2) — not part of the design-system-drift group (§8.5/§11.2).

---

#### `DoubtsScreen` — `doubts_screen.dart` ✅ (Screen 14, per the module's own architecture doc numbering)

**API:** `GET queries/?classroom=`; `POST queries/` (ask); `POST
queries/{id}/answer/` (answer, flips `status` `Open` → `Answered`). Any
user with classroom access can ask; only teacher/co-teacher/moderator
(`canManage`, caller-supplied per the module-wide convention, §10) can
answer.

**Constructor:** `{required classroomId, required canManage}` — unlike
most other screens in this catalog, `canManage` here has no default and
must always be supplied explicitly by the caller.

**State:** `_queries` (`List<ClassQuery>`), `_loading`, `_error`. `_load()`
client-side sorts **Open before Answered**, and within each status group,
newest (`createdAt`) first — not the server's raw order.

**Ask dialog (`_openAskDialog`/`_showAskDialog`) and Answer dialog
(`_openAnswerDialog`/`_showAnswerDialog`)** — ⚻ **both confirmed fixed
memory leaks, called out explicitly in-file (`FIX` comment) as the same
issue twice over:** each dialog's `TextEditingController` (`ctrl` for
Ask, `ctrl` pre-filled with `q.answer` for Answer) used to be created
without disposal; both now created fresh per open and released in a
`finally` block on every exit path — same shared §8.4 pattern, explicitly
cross-referenced to each other in-file (the Answer dialog's fix comment
calls out "same issue as `_openAskDialog` above").

⚻ **Confirmed instance of the module's timezone/i18n fix (§8.2):** the
query-list date line used to call `DateFormat.format()` directly on
`q.createdAt`, which — since that field parses as a UTC `DateTime`
(per `liveclass_models.dart`'s own parsing note) — read the server's raw
UTC clock fields instead of the viewer's local time. Switched to the
shared, locale- **and** timezone-safe `liveClassFmtDate(q.createdAt,
context)` helper, matching every other date display in the module.

**Card UI:** question text + an ANSWERED/OPEN `LiveClassStatusChip`,
"`askedBy.fullName` · `date`" line, and — only if answered — a divider
then the answer text with a `subdirectory_arrow_right` icon and an
optional "— `answeredBy.fullName`" attribution line. If **not** answered
and `canManage`, an inline "Answer" `FilledButton.tonal` opens the Answer
dialog instead. FAB ("Ask") is unconditional — any user with access can
ask, so it's not gated on `canManage`. Uses the shared `LiveClassCard`/
`LiveClassStatusChip`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` pieces throughout (§2.2) — per the file's own header,
this screen was explicitly restyled onto the shared design system to
match Certificates/Holidays/Coin Wallet rather than falling back to plain
Material defaults; not part of the design-system-drift group (§8.5/§11.2).

---

#### `CertificatesScreen` — `certificates_screen.dart` ✅ (Screen 21, per the module's own architecture doc numbering)

Two modes, both in one file, driven entirely by the caller-supplied
`canIssue` bool (matching the module-wide caller-supplied-permission
convention, §10):
- **Teacher/staff** (`classroomId` set, from `ClassroomDetailScreen`'s
  manage sheet, "Certificates" tile, `canIssue: true`) — sees every
  certificate issued for that classroom (`GET certificates/?classroom=`)
  plus an "Issue Certificate" FAB.
- **Student** ("My Certificates", `classroomId` omitted) —
  `GET certificates/` scoped to the caller's own certificates **by the
  backend**, read-only, no FAB. Reached from `_MyLearningTab` (§7).

**API:**
- `certificates.list({classroomId})` — paginated; screen re-sorts client-side
  newest-`issuedAt`-first regardless of server order.
- `certificates.issue({classroomId, studentId, certificateFilePath})` —
  teacher/co-teacher/moderator only per the file's header; student ID is
  numeric-only (same no-user-search-endpoint limitation as
  `BannedStudentsScreen`); certificate file is optional.

**Issue sheet (`_openIssueSheet`/`_showIssueSheet`)** — ⚻ **confirmed fixed
memory leak, called out explicitly in-file (`FIX` comment):** the
`studentIdCtrl` `TextEditingController` used to be created without ever
being disposed, leaking one controller (plus its internal listeners) per
open+close of the sheet for the lifetime of the app. Now created fresh
per open and released in a `finally` block on every exit path — another
confirmed instance of the shared §8.4 pattern. File picking
(`file_selector`'s `openFile`, accepts pdf/png/jpg/jpeg) is optional and
independent of that fix.

**File download/open (`_openFile`)** — downloads via `Dio().download()`
to the temp directory (Bearer-token header from `AuthService.getToken()`),
opens via `OpenFilex.open()`. ⚻ **Confirmed instance of the module's
corrupt-cache-on-failed-download fix, already tracked in §8.1:** a failed
download that dies partway (dropped connection, mid-stream server error,
app backgrounded and killed) used to leave a partial/corrupt file sitting
at the cache path; because the method's `exists()` check then returned
true forever, the certificate became **permanently unopenable in-app**
(short of clearing app storage) even though it was perfectly fine on the
server. Now a failed download deletes its own partial file before the
error reaches the outer catch, so the next tap retries a fresh download
instead of replaying the same corrupt one.

**Card UI:** teacher mode shows the student's name; student mode shows
the classroom title. Both show certificate ID and
`liveClassFmtDate(c.issuedAt)`; a download icon button appears only when
`certificateFile` is non-empty. Uses the shared `LiveClassCard`/
`LiveClassIconBadge`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` design-system pieces throughout (§2.2) — per the
file's own header comment this screen's local color/date constants and
hand-rolled loading/error blocks were previously replaced to match the
rest of the module (Holidays/Coin Wallet/etc.), so it is **not** part of
the design-system-drift group tracked in §8.5/§11.2.

---

#### `AssignmentsScreen` — `assignments_screen.dart` ✅ (Screen 12, list half — per the module's own architecture doc numbering)

Full-screen assignment list for a classroom (or, if `sessionId` is passed,
scoped further to one session). Reached from `ClassroomDetailScreen`'s
manage sheet ("Assignments" tile, `canManage: true`) as well as a
student-facing read-only entry point (`canManage: false`); the embedded
`_AssignmentsTab` on `ClassroomDetailScreen` itself (§6.1) is a separate,
lighter parallel implementation, not this screen reused. Per the file's
own header, this pass **restyled the screen onto the shared LiveClass
design system** (matching Certificates/Holidays/Coin Wallet) — logic and
API calls are explicitly unchanged from before that restyle.

**Constructor:** `{required classroomId, sessionId, required canManage}` —
`sessionId` is optional (session-scoped assignment), `canManage` has no
default and must always be supplied by the caller, same
caller-supplied-permission convention as `DoubtsScreen` (§6.5, §10).

**API:** `LiveClassApi.assignments.list(classroomId)` (paginated);
`.create(Assignment(...), {attachmentPath})`; `.delete(id)`. Tapping any
card — teacher or student — pushes `SubmissionGradingScreen(assignment:,
canManage:)`, which itself branches into either the submit flow (student)
or the grading queue (teacher); this screen does not duplicate that logic.

**State:** `_assignments` (`List<Assignment>`, server order, not
client-resorted), `_loading`, `_error`.

**Create sheet (`_openCreateSheet`/`_showCreateSheet`)** — ⚻ **confirmed
fixed memory leak, called out explicitly in-file (`FIX` comment):** all
three `TextEditingController`s (`titleCtrl`, `descCtrl`,
`scoreCtrl` — the latter pre-filled `'100'`) used to be created without
ever being disposed, leaking three controllers per open+close of the
sheet for the app's lifetime. Now created fresh per open and released in
a `finally` block on every exit path — another confirmed instance of the
shared §8.4 pattern. Due date is picked via `showDatePicker` (range: now
to `+365` days) immediately followed by `showTimePicker` (default `23:59`
if the teacher dismisses the time picker without choosing) and combined
into one `DateTime`; client-side required alongside a non-empty title
before submit is allowed.

⚠️ **NEW this pass — first confirmed live consumer of
`LiveClassUploadLimits.checkPlatformFile` in the module (§2.4).** The
attachment picker uses `file_picker`'s
`FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions:
LiveClassUploadLimits.documentExtensions)` — scoping the OS picker itself
to the allowed extensions — then independently re-validates the resulting
`PlatformFile` via `LiveClassUploadLimits.checkPlatformFile(picked, maxMB:
assignmentAttachmentMaxMB, allowedExtensions: documentExtensions)` before
accepting it. Per the file's own `FIX` comment (file upload size/type
audit), there was previously **no extension restriction and no size check
at all** — a teacher could attach anything of any size and only discover
the backend's real `MaxFileSizeValidator(50)`/`DOCUMENT_MEDIA_EXTENSIONS`
rejection (`Assignment.attachment`, `models.py`) after a full, potentially
very slow upload attempt. Note this screen goes through `file_picker`
(`PlatformFile`), not `file_selector` (`XFile`) like
`classroom_detail_screen.dart`'s and `classroom_form_screen.dart`'s
upload flows — hence `checkPlatformFile` rather than `checkXFile`.

**Delete (`_confirmDelete`)** — plain `AlertDialog` (no controller to
leak). **Optimistic removal with rollback on failure**, same shape as
Holidays/Materials: the assignment is removed from `_assignments`
immediately, restored from a saved `previous` copy + snacked if the
`DELETE` call actually fails.

⚻ **Confirmed instance of the module's timezone/i18n fix (§8.2), called
out explicitly in-file (`FIX` comment) with an unusually long
cross-reference list:** every due-date display used to hand-format
`a.dueDate` (a UTC-parsed `DateTime` per `liveclass_models.dart`) by
reading `.day`/`.month`/`.hour` straight off it with no `.toLocal()` call,
against the deprecated English-only `kLiveClassMonths` array — a due date
set for 11:59 PM IST rendered hours off, and only in English, for every
viewer. The file's own comment cites this as the same class of gap
already fixed in `doubts_screen.dart`, `holidays_screen.dart`, and
`submission_grading_screen.dart`; every call site here now goes through
the shared `liveClassFmtDateTime()` helper instead.

**Card UI:** icon badge, title, a due-date row (`Due <liveClassFmtDateTime(a.dueDate,
context)>`, colored/bolded `LiveClassColors.danger` when `DateTime.now().isAfter(a.dueDate)`)
with a red event icon to match, a small "PAST DUE" `LiveClassStatusChip`
shown only when overdue, and a grey "Max `<maxScore>`" pill. Trailing: a
delete icon button when `canManage`, else a plain chevron. FAB ("New")
only renders when `canManage`. Uses the shared `LiveClassCard`/
`LiveClassIconBadge`/`LiveClassStatusChip`/`LiveClassEmptyState`/
`LiveClassErrorState`/`LiveClassLoading` design-system pieces throughout
(§2.2) — per the file's own header, this is the explicit result of this
pass's restyle onto the shared system; not part of the design-system-drift
group (§8.5/§11.2).

---

#### `NoticeBoardScreen` — `notice_board_screen.dart` ✅ (Screen 13, per the module's own architecture doc numbering)

Reached from `ClassroomDetailScreen`'s manage sheet ("Notice Board" full-
screen tile, `canManage: true`) or the embedded `_NoticesTab` shortcut
(read-only, `canManage: false`) — same manage-sheet-plus-embedded-tab
pairing as Materials/Doubts/Assignments (§6.1/§6.5). Per the file's own
header, this pass **restyled the screen onto the shared LiveClass design
system** (matching Certificates/Holidays/Materials) — logic and API
calls are explicitly unchanged from before that restyle.

**Constructor:** `{required classroomId, required canManage}` — no
default, same caller-supplied-permission convention with no fallback as
`DoubtsScreen`/`AssignmentsScreen` (§6.5, §10).

**API:** `GET notices/` (`.list(classroomId)`); `POST notices/` (create);
`POST notices/{id}/pin/` (`.pin(id)` — toggles, returns the updated
`Notice`); `DELETE notices/{id}/`. Teacher/staff post and pin/delete;
everyone with classroom access reads.

**State:** `_notices` (`List<Notice>`), `_loading`, `_error`. Both
`_load()` and `_togglePin()` apply the **same client-side sort** —
pinned notices first, then newest (`createdAt`) first within each
pinned/unpinned group — re-applied after every pin toggle (not just on
initial load), so a newly-pinned notice jumps to the top immediately
without waiting for a full `_load()`.

**Pin (`_togglePin`)** — calls `.pin(id)`, splices the returned `Notice`
back into `_notices` (filters out the stale copy by id, appends the
fresh one, re-sorts) rather than a full reload; on failure, snacks the
error and leaves the list as-is (not optimistic — waits for the server
response before touching state either way).

**Compose sheet (`_openComposeSheet`/`_showComposeSheet`)** — ⚻
**confirmed fixed memory leak, called out explicitly in-file (`FIX`
comment):** both `titleCtrl` and `messageCtrl` `TextEditingController`s
used to be created without disposal, leaking two controllers per
open+close of the sheet for the app's lifetime; now created fresh per
open and released in a `finally` block on every exit path — the same
shared §8.4 pattern. Priority dropdown (Low/Normal/Urgent via
`NoticePriority`, defaults Normal); an optional auto-hide expiry date
(`showDatePicker`, range: now to `+365` days). Client-side required:
non-empty title and message before submit is allowed.

⚻ **Confirmed instance of the module's timezone/i18n fix (§8.2), with an
in-file comment worth flagging as unusually precise about scope:** the
expiry-date label in the compose sheet is explicitly commented as **not**
needing the fix — `expiresAt` there is a locally-picked `DateTime` from
`showDatePicker`, already in local time, not one parsed from the API —
but it's still routed through the shared `liveClassFmtDate(expiresAt,
context)` helper anyway, for consistent locale-aware formatting rather
than because it was buggy. The **card list's** own date line, by
contrast, genuinely was buggy: `n.createdAt` is UTC-parsed per
`liveclass_models.dart`, and the file's `FIX` comment cross-references
the identical bug/fix already documented for `doubts_screen.dart`
(§6.5) — now also on `liveClassFmtDate(n.createdAt, context)`.

**Delete (`_confirmDelete`)** — plain `AlertDialog` (no controller to
leak). **Optimistic removal with rollback on failure**, same shape as
Holidays/Materials/Assignments: the notice is removed from `_notices`
immediately, restored from a saved `previous` copy + snacked if the
`DELETE` call actually fails.

**Card UI:** a hand-rolled `Container` (white, rounded, colored border at
35% alpha keyed to `_priorityColor`, `LiveClassColors.cardShadow`) —
**not** `LiveClassCard` — wrapped in `Opacity(opacity: 0.55)` when
`n.isExpired`, same de-emphasize-rather-than-hide pattern as
`HolidaysScreen`'s Past section (§6.2). Inside: an optional pin icon +
title row with a priority `LiveClassStatusChip` (urgent → red, low →
grey, normal → navy, via `_priorityColor`), the message body, and a
footer row — "`postedBy.fullName` · `date`" (plus " · Expired" appended
when `n.isExpired`), and, only when `canManage`, inline pin-toggle and
delete icon buttons. FAB ("Post") only renders when `canManage`.

⚠️ **Design-system drift, confirmed this pass — a new, not-previously-
counted instance of the hand-rolled-card-chrome flavor already tracked in
§8.5/§11.2:** despite otherwise using the shared
`liveClassAppBar`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading`/`LiveClassStatusChip` pieces (§2.2), the notice card
itself is a hand-rolled `Container`+`BoxShadow`, not `LiveClassCard` —
the same flavor already documented for `ClassroomPurchasesScreen` (§6.3).
Worth folding into that count — see §11 for the updated tally.

### 6.6 Account-wide "My Stuff" Screens
*(**Latest pass** closes the "claimed ✅, no body" gap for the two files
flagged as still outstanding in that state at the end of the prior pass:
`TeacherEarningsScreen` and `WishlistScreen` were both read line-by-line
for the first time this pass — full write-ups are added below, replacing
their §0-tag-only status with real detail (see the top-of-doc revision
note). `ClassroomReportsScreen` was read line-by-line a prior pass — its
full write-up (last in this subsection) replaced its old 📋
connection-graph-only mention then and is unchanged since. `CoinWalletScreen`
was read line-by-line a prior pass — its full write-up (below) is
unchanged since. `MyProgressScreen`, `MyRemindersScreen`,
`NotificationsScreen`, and `NotificationPreferencesScreen` were
re-uploaded and re-read a prior pass, specifically to re-verify their
existing write-ups against source; all four confirmed unchanged then and
are not re-touched this pass. `ReferralScreen` was read line-by-line a
prior pass — its full write-up (below) is unchanged since.)*

#### `ReferralScreen` — `referral_screen.dart` ✅

⚻ **Read line-by-line in full this pass — tagged ✅ in §0 with no
actual write-up anywhere in this doc; a real one is added here for the
first time.** The backend (`ReferralViewSet`: `referrals/`,
`referrals/my-code/`, `referrals/redeem/`) was fully implemented — own
referral code, redemption tally, redeeming someone else's code — but per
the file's header, **no screen anywhere in the module ever called any of
these three endpoints** before this file. **Account-wide, not
classroom-scoped**, same situation as `MyProgressScreen` — not reached
from `ClassroomDetailScreen`'s manage sheet; wired in from wherever the
host app's main menu/profile/wallet section lives (not part of this
upload, per the header).

**API:** `GET referrals/my-code/` (`.myCode()`); `GET referrals/`
(`.list()`); `POST referrals/redeem/` (`.redeem(code)`).

⚠️ **Two distinct referral systems live in one screen, confirmed
in-file — easy to conflate, worth keeping separate:** (1) the sign-up
code system above (`referrals/my-code` — referring **people** to the
app, this screen's original purpose); and (2) `PassPurchaseApi.referralEarnings()`
(Phase 2 item 9) — the caller's own commission ledger from referring
specific **classrooms** via each classroom's own `ClassroomApi.referLink`
(`ClassroomDetailScreen`'s "Refer & Earn" entry point, §6.1/§7's
connection graph). The backend for (2) was ready with no frontend caller
anywhere in the module before this file. Fetched **separately and
best-effort** (`_loadEarnings()`) — a failure there shouldn't block the
sign-up referral section, which is this screen's original purpose; the
"Classroom Referral Earnings" section simply doesn't render
(`!_earningsLoading && _earnings != null` guards it) if that fetch fails.

**State:** `_myCode` (`MyReferralCode?`), `_referrals`
(`List<Referral>`), `_loading`/`_error` (sign-up section);
`_earnings` (`ReferralEarnings?`), `_earningsLoading` (classroom-earnings
section, fetched in parallel via a separate `_loadEarnings()` call in
`initState`, not `Future.wait`-joined with `_load()`); `_redeemCtrl`
(class-level, disposed in the screen's own `dispose()` — same
persistent-screen reasoning as `RequestJoinScreen`, §6.3, which
explicitly cites this file); `_redeeming`.

**Redeem (`_redeem`)** — client-side non-empty check only; the real
validation (whether the code exists, whether the caller is still inside
the short post-signup redemption window per the UI's own copy) is
entirely server-side, surfaced via the `LiveClassApiException` message.
On success, clears the field, snacks "Referral code redeemed!", and
calls `_load()` to refresh the referral list/tally.

**Copy code (`_copyCode`)** — `Clipboard.setData`, no `url_launcher`/
share-sheet integration.

**UI:** a gradient code card (large code text + copy button, "Referred"/
"Coins Earned" stat pair below) sourced from `MyReferralCode`; a redeem
row (text field + button, with a short-window-warning caption); a
People-You-Referred list (avatar-initial circle, name, `+bonusAmount`);
and, conditionally, the separate Classroom Referral Earnings section
(total-earned summary card + a per-purchase list showing each referred
student, classroom, purchase date via `liveClassFmtDate(p.purchasedAt)`
— ⚠️ **called with no `context` argument**, the same missing-`context`
locale-fallback bug already tracked for `CoinWalletScreen`/
`ScheduleManagerScreen` (§11 item 21 and above) — a **third** confirmed
live instance of this exact pattern, and `+referralCoinsReleased`).

Uses the shared `LiveClassCard`/`LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading`/`liveClassInputDecoration` pieces throughout (§2.2) —
not part of the design-system-drift group (§8.5/§11.2). Fully English
throughout.

---

#### `CoinWalletScreen` — `coin_wallet_screen.dart` ✅ (Screen 10, per the module's own architecture doc numbering)

Per the file's own header, this screen closed a real gap: its API
(`CoinTransactionApi` in `liveclass_api_service.dart`) already existed and
the screen is part of the `LIVECLASS_SCREEN_ARCHITECTURE.md` folder
layout, but no screen anywhere in the module ever called it before this
file was added.

**Not part of the classroom flow — account-wide, same situation as
`MyProgressScreen`/`ReferralScreen`.** Like Notifications/My Passes/
Wishlist, it's meant to be pushed from wherever the host app's own coin
balance/wallet icon lives (home app-bar, profile menu, etc.) — the
file's header gives the exact `IconButton`/`Navigator.push` snippet for
callers to copy, same convention as `LiveClassHomeScreen`'s own header
(§6.1). It is also embedded directly as the 3rd tab of
`LiveClassHomeScreen`'s `IndexedStack` (self-contained, own app bar) —
confirmed the screen's constructor takes no arguments, so both the
standalone-push and embedded-tab call sites use it identically.

**API:** `GET coin-transactions/` (`CoinTransactionApi.myLedger()` —
own ledger only, newest-first) and `GET coin-transactions/balance/`
(`.balance()`, returns a bare `int`). Both fetched **in parallel** via a
single `Future.wait` on open. **Read-only — no create/edit/delete
action anywhere in this screen:** per the file's header, coins are only
ever earned/spent as a **side effect** of other actions elsewhere in the
module (pass purchase, refund, referral bonus, admin top-up), never
directly from this screen — confirmed there is no FAB, no dialog, no
write call anywhere in the source.

**State:** `_ledger` (`List<CoinTransaction>`), `_balance` (`int`),
`_loading`, `_error`. `_load()` casts `results[0]` from the `Future.wait`
to `PaginatedList<CoinTransaction>` and **re-sorts client-side**
`createdAt` descending (server order not trusted, same pattern as
Materials'/Certificates' own client-side re-sorts elsewhere in this
catalog) before assigning `_ledger`; `results[1]` casts straight to `int`
for `_balance`.

**Balance card (`_balanceCard`)** — a gradient `Container`
(`LiveClassColors.gradient`) with a tinted pink/magenta drop shadow
(`0xFFEE0979` at `alpha: 0.25`), "Current Balance" label, a coin icon +
large `_balance` number + "coins" sub-label. Purely presentational, no
interaction.

**Reason mapping (`_reasonLabel`/`_reasonIcon`, module-level functions,
not methods)** — switches on `CoinTransaction.reason` (a raw string, not
an enum): `pass_purchase`/`refund`/`referral_bonus`/`topup`/`admin`, each
with its own label + icon (confirmation-number/replay/card-gift/add-card/
admin-panel respectively). An unrecognized reason string falls back to a
generic `swap_horiz` icon and either the raw `reason` text or the literal
word "Transaction" if `reason` is empty — so a backend-added reason
value this screen doesn't yet know about still renders something
legible rather than crashing or showing a blank label.

**Transaction tile (`_txnTile`)** — a colored icon circle keyed off
`CoinTxnType.credit` vs. debit (`LiveClassColors.success` green /
`.danger` red, both the circle fill at 10% alpha and the icon/amount
text itself), the mapped reason label, a date line, and a trailing
`+`/`-`-prefixed amount plus a small "Bal: `balanceAfter`" sub-line —
the running balance **as of that transaction**, not the current balance,
pulled straight from the `CoinTransaction` record rather than recomputed
client-side.

⚠️ **NEW finding this pass, currently unfixed — a real instance of the
module's missing-`context` locale bug (§2.3/§8.2/§8.3), not previously
documented anywhere:** the tile's date line calls
`liveClassFmtDateTime(t.createdAt)` with **no `context` argument** —
since `context` is optional (`[BuildContext? context]`, §2.3),
this compiles and runs, but silently falls back to `intl`'s default
locale instead of the device/app one, exactly the same class of gap
already confirmed fixed at every call site in `my_reminders_screen.dart`
(§6.6). Unlike that screen, `CoinWalletScreen` still has this bug live —
not a resolved instance, a genuinely open one. See §11 item 21.

**Empty/loading/error states and list layout:** a plain `ListView`
(not `ListView.builder` — the balance card and the whole transaction
list are built as one children list), with the shared
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
(§2.2) for their respective states, and a manually hand-rolled empty-ledger
block (icon + "No transactions yet." text) used **only** when the
balance card has already loaded but `_ledger` is empty — the shared
`LiveClassEmptyState` widget isn't reused for that inner case since the
balance card still needs to render above it. Transaction tiles themselves
use `LiveClassCard` (§2.2) — not part of the design-system-drift group
(§8.5/§11.2).

---

#### `TeacherEarningsScreen` — `teacher_earnings_screen.dart` ✅

⚻ **Read line-by-line in full this pass — tagged ✅ in §0 with no
actual write-up anywhere in this doc; a real one is added here for the
first time.** New screen. The backend (`TeacherEarningsView`, `GET
my-earnings/`) was fully implemented — total earned, this-month earned, a
30-day daily breakdown, and a per-classroom breakdown, all backed by real
`PassDailyCharge` aggregates — but per the file's header the view **was
never even reachable at any URL until a routing fix**, and no screen
anywhere in the module ever called it once it was.

**Constructor:** `{classroomId?, classroomTitle}` — `classroomId`
provided scopes to one classroom the caller teaches (`?classroom=<id>`
server-side, 403 if they don't teach it, per the header); omitted gives
the teacher's overall earnings across every classroom they teach. App-bar
title switches between "Earnings — `<classroomTitle>`" and "My Earnings"
accordingly.

**API:** `GET my-earnings/` (optional `?classroom=`) via
`LiveClassApi.myEarnings(classroomId:)` — a **module-level** function,
not a nested sub-API group like most other endpoints in this catalog
(worth noting since every other screen's calls in this doc read as
`LiveClassApi.<group>.<method>()`).

**State:** `_earnings` (`TeacherEarnings?`), `_loading`, `_error`. Single
fetch, no pagination.

**Metrics shown:** two top stat cards (Total Earned, This Month Earned)
plus a full-width Sessions Charged card; a 30-day daily bar chart
(`_dailyBars`, hand-rolled `FractionallySizedBox` bars scaled to that
period's own max — not a charting package — each bar's `Tooltip` shows
the exact date + amount on long-press/hover, `heightFactor` clamped to a
`0.03` floor so a zero-earning day still renders a visible sliver rather
than vanishing); and a by-classroom breakdown list (`EarningsByClassroom`:
title, sessions-charged count, total earned).

**UI:** uses the shared `LiveClassCard`/`LiveClassIconBadge`/
`LiveClassLoading`/`LiveClassErrorState`/`liveClassAppBar` pieces
throughout (§2.2) for every stat card and list row — **not** part of the
design-system-drift group (§8.5/§11.2), unlike several of its sibling
account-wide screens. Fully English throughout. No FAB, no write
actions — purely a read-only dashboard, matching `MyProgressScreen`'s
(below) and `SessionEngagementReportScreen`'s (§6.4) read-only shape.

⚠️ **Not independently re-verified this pass:** the malformed
`myEarnings()` signature flagged against `liveclass_api_service.dart` in
the prior models/API-service pass (§12) — this screen's own call site
reads as a straightforward `LiveClassApi.myEarnings(classroomId:
widget.classroomId)`, consistent with what this write-up describes, but
`liveclass_api_service.dart` itself wasn't re-uploaded this pass, so
whether that malformation is still live server-response-shape-wise is
left exactly as previously tracked, not newly resolved.

---

#### `WishlistScreen` — `wishlist_screen.dart` ✅ (Screen 17, per the module's own architecture doc numbering)

⚻ **Read line-by-line in full this pass — tagged ✅ in §0 with no
actual write-up anywhere in this doc beyond the connection-graph mention
(§7); a real one is added here for the first time.** "Save for later" —
bookmarked classrooms with no pass/commitment attached. Account-wide, not
classroom-scoped, same situation as `ReferralScreen`/`CoinWalletScreen`/
`MyProgressScreen` — reached from wherever the host app's own "My
Learning" area lives, not from `ClassroomDetailScreen`'s manage sheet.
**Adding happens elsewhere** (Classroom Detail's heart-icon toggle, per
the file's own header) — this screen only lists and removes.

**API:** `GET wishlist-classrooms/` (`.list()` — no classroom filter
param; per the header the backend already scopes the list to the caller)
and `DELETE wishlist-classrooms/{id}/` (`.remove(id)`).

**State:** `_items` (`List<ClassroomWishlistItem>`, sorted
newest-`createdAt`-first client-side), `_loading`, `_error`, `_removing`
(`Set<int>`, per-card in-flight-removal guard).

**Remove (`_remove`)** — **optimistic**: the item is pulled from `_items`
immediately, then the `DELETE` fires; on failure the previous list is
restored and a snack shows the error. This is the confirmed **only**
optimistic-update pattern independently found in this account-wide screen
group — every sibling screen in this subsection (`ReferralScreen`,
`CoinWalletScreen`, `TeacherEarningsScreen` above) instead reloads via a
full `_load()` after a successful mutation, not an optimistic-then-revert
one.

**Open classroom (`_openClassroom`)** — pushes `ClassroomDetailScreen`,
and its `.then((_) => _load())` continuation explicitly re-fetches on
return, per an in-file comment, because "pass/enrollment state might've
changed" while the user was on the detail screen — the wishlist list
itself doesn't change from that visit, but this is a deliberate
freshness-over-efficiency choice, not a bug.

**Card UI (`_WishlistCard`, private):** a 2-column `GridView`, each card a
16:10 cover-image tile (`CachedNetworkImage`, gradient-icon fallback for a
missing/failed image) with a heart-icon remove button overlaid
top-right (spinner while `removing`) and a "Closed" badge overlaid
bottom-left when `!classroom.isActive`, below which sits the title
(2-line ellipsis), teacher name, a star-rating-or-"New" line, and the
classroom's language.

⚠️ **Design-system drift, confirmed — the hand-rolled-card flavor
(§8.5/§11.2):** `_WishlistCard`'s own `Container`+`BoxShadow` (not
`LiveClassCard`), and the empty state is a bare `ListView`+`Center` block
(icon + two lines of text), not `LiveClassEmptyState` — though the screen
does use `liveClassAppBar`/`LiveClassLoading`/`LiveClassErrorState`. Also
aliases `LiveClassColors.navy`/`.bg`/`.gradient` into local
`_kNavy`/`_kBg`/`_kGradient` constants per an in-file comment citing this
as the same hex-duplication fix already applied elsewhere in the module —
this is the file the `SessionsListScreen`/`ScheduleManagerScreen`/
`WaitlistScreen` write-ups (§6.2/§6.4) cite when describing their own
identical alias pattern. Fully English throughout — no Hindi-string-leak
instance in this file.

---

#### `MyProgressScreen` — `my_progress_screen.dart` ✅

Per the file's own header, this closed a real gap: the backend
(`StudentProgressView`, `GET my-progress/`) was fully ready with a
confirmed serializer shape, but `LiveClassApi.myProgress()` was **never
called from anywhere** — the Insta-style "activity/insights" card the
frontend spec asked for didn't exist as a screen yet.

**Account-wide, not classroom-scoped** — own activity across every
classroom the caller has ever had access to, same situation as
`ReferralScreen`; that's explicitly why it's **not** reached from
`ClassroomDetailScreen`'s manage sheet. Wired in from `ExploreScreen`'s
app bar (an "insights" icon next to the create-classroom button) per the
header comment, since this module's upload doesn't include the host
app's own main menu/profile section — if one exists elsewhere, it should
also point at this screen.

**API:** `GET my-progress/` (`LiveClassApi.myProgress()`) — single call,
no pagination, no filters.

**State:** `_progress` (`StudentProgress?`), `_loading`, `_error`.

**UI:** a gradient "streak hero" card (`LiveClassColors.gradient`) —
fire icon, `currentStreakDays` large, "Current attendance streak" label,
"Best: `longestStreakDays`" sub-line — followed by a 2×2
`GridView.count` of stat cards (`classesAttended`, `classroomsEnrolled`,
`assignmentsSubmitted`, `certificatesEarned`), each a plain icon + big
number + label `LiveClassCard`. No FAB, no destructive/write actions —
purely read-only. Uses the shared `LiveClassCard`/`LiveClassEmptyState`/
`LiveClassErrorState`/`LiveClassLoading` pieces (§2.2) — not part of the
design-system-drift group (§8.5/§11.2).

---

#### `MyRemindersScreen` — `my_reminders_screen.dart` ✅

The "manage" half of the same `ClassReminder` gap `LiveClassHomeScreen`'s
header describes (§6.1): full backend + `ReminderApi.list/create/delete`
existed, but nothing ever called `list()`/`delete()`, so a set reminder
could never be seen or cancelled. The "create" half is the bell icon on
`SessionsListScreen`'s session cards (not itself re-uploaded this pass).
Reached from `LiveClassHomeScreen`'s My Learning tab (§6.1/§7).

**API:** `GET reminders/` — scoped to `request.user` **server-side**
(`ClassReminderViewSet`), no client-side filter needed; `DELETE
reminders/{id}/`.

**Session resolution, confirmed in-file:** the `reminders/` endpoint only
returns each reminder's `session_id`, **not** a nested session object
(per `ClassReminderSerializer`'s own field list) — so each reminder's
session is separately resolved via `SessionApi.detail()` and cached
locally in a `Map<int, ClassSession> _sessions` keyed by `sessionId`, to
avoid re-fetching the same session twice when several reminders share one
session. Resolution is **best-effort and parallel** (`Future.wait` over
every unresolved reminder): a session that fails to load (e.g. deleted)
just falls back to a plain "Session #`id`" label at render time instead
of blocking the whole list.

**State:** `_reminders` (`List<ClassReminder>`, sorted `remindAt`
ascending), `_sessions` (the cache above), `_loading`, `_error`.

**Cancel (`_cancel`)** — plain `AlertDialog`, not optimistic (waits for
the `DELETE` to succeed before removing from `_reminders`). ⚻ **Confirmed
instance of the module's locale/i18n fix, called out explicitly in-file:**
the confirm-dialog's date line used to call `liveClassFmtDateTime(r.remindAt)`
**without** its `context` argument, silently falling back to `intl`'s
default locale instead of the device/app one — the same class of gap
already fixed at every other call site in this file (and, per §8.2/§8.3,
elsewhere in the module); now passes `context` here too.

**`_openSession`** — pushes `LiveSessionScreen(sessionId: r.sessionId,
session: _sessions[r.sessionId])` (2-arg constructor, confirmed valid
against §6.4's documented shape — `session` may be `null` if resolution
failed, the screen handles that itself).

**Card UI:** channel icon (`_channelIcon` — SMS/Email/Push via
`ReminderChannel`), classroom title (or "Session #`id`" fallback),
session's own scheduled-start line (if resolved), "Alert: `remindAt` ·
`channel`" line, and a SENT/DUE/UPCOMING `LiveClassStatusChip` (`isSent`
→ SENT; else past `remindAt` → DUE; else UPCOMING). Whole card tappable
→ `_openSession`; a trailing close/cancel icon button appears only when
`!r.isSent` (a sent reminder can no longer be meaningfully cancelled).
Uses the shared `LiveClassCard`/`LiveClassIconBadge`/`LiveClassStatusChip`/
`LiveClassEmptyState`/`LiveClassErrorState`/`LiveClassLoading` pieces
(§2.2) — not part of the design-system-drift group (§8.5/§11.2).

---

#### `NotificationsScreen` — `notifications_screen.dart` ✅ (Screen 23, per the module's own architecture doc numbering)

Not part of the tab flow itself — meant to be pushed from the host app's
own home app-bar bell icon (badge count from
`LiveClassApi.dashboard()`'s `unread_notifications_count`), **and** from
`LiveClassHomeScreen`'s My Learning tab bell (§6.1/§7). Wiring snippet
given in the file's own header for the former.

**API:** `GET notifications/` (own, newest-first, optional `?is_read=`
filter); `GET unread-count/`; `POST {id}/mark-read/`; `POST
mark-all-read/`; `DELETE {id}/`.

⚻ **Confirmed resolves §11 item 8 (`_fmtRelative` duplication):** per the
file's own dedup-audit comment, a local `_fmtRelative()` used to live
only in this file with no shared home; it was deleted and replaced with
`liveClassFmtRelative()` in `liveclass_theme.dart` (§2.2) specifically so
a *future* second screen needing the same "2h ago" label would reuse it
instead of re-writing its own copy — which, per the comment, is exactly
how the original duplication happened. No other screen currently reads
this doc as still carrying a duplicate `_fmtRelative`.

**Icon map (`_notifIcon`)** — a `switch` on `NotifType`, 27 explicit
cases (see below) + a default bell icon fallback. In-file comments
document this map's own history as a repeated "constant added,
icon-mapping missed" gap, fixed in at least 4 separate batches: the
original set (join-request/pass-refunded/session-reminder/
assignment-graded/query-answered/certificate-issued/waitlist-promoted/
classroom-flagged/notice-posted), then 7 more (session-live,
session-cancelled, assignment-posted, submission-received, staff-added,
review-posted, report-reviewed), then 3 more (pass-auto-renewed,
auto-renew-failed, pass-gift-expired), then a final 3 that didn't even
have a `NotifType` constant yet (classroom-shared, pass-gift-received,
pass-gift-claimed), then the 3 coin-withdrawal types
(withdrawal-approved/rejected/paid).

**State:** `_all` (`List<AppNotification>`), `_loading`, `_error`,
`_unreadOnly` (drives a `FilterChip`, re-fetches with `is_read: false`
when toggled on).

**Tap handling (`_onTapNotification`)** — optimistic mark-read: flips the
notification's `isRead`/`readAt` locally first (rebuilding a whole new
`AppNotification` since the model is presumably immutable — no
`copyWith` used here), fires `mark-read/` in the background, swallows
its own failure ("worst case it shows read locally but unread on
reload — not worth interrupting the tap for," per the code's own
reasoning). If the notification carries a `classroomId`, pushes
`ClassroomDetailScreen(classroomId:)` — a `sessionId` alone still routes
through Classroom Detail too, since (per the file's header) that's the
only stable route this screen has into a session's classroom.

**Delete (`_deleteOne`)** — swipe-to-dismiss (`Dismissible`,
end-to-start, red delete background), optimistic removal; on failure,
snacks and calls `_load()` to resync rather than trying to re-insert the
item at its old position.

**App bar actions:** a settings-gear icon → `NotificationPreferencesScreen`
(placed **before** "mark all read" since it's a settings action, not a
list action, per an explicit in-file comment), then "mark all read"
(`done_all` icon).

**Tile UI:** icon badge (navy circle, `_notifIcon`), bold-if-unread
title, optional 2-line message, `liveClassFmtRelative(createdAt,
context)` relative-time line, and — if unread — a small navy dot on the
trailing edge (in addition to the tile's own subtly-tinted/bordered
background). Uses the shared `LiveClassEmptyState`/`LiveClassErrorState`/
`LiveClassLoading` pieces (§2.2) but otherwise **hand-rolls its own tile
container** (rounded `Container` + `InkWell`, not `LiveClassCard`) — a
minor, low-priority structural variant, not flagged as part of the
design-system-drift group tracked in §8.5/§11.2 since the base tokens
(colors/radii/shadow) still come from the shared theme.

---

#### `NotificationPreferencesScreen` — `notification_preferences_screen.dart` ✅

⚻ **Resolves §11 item 3 (`NotificationPreference` model shape) —
confirmed straight from the file's own extensive header comment,** which
documents a full rebuild against the real `NotificationPreferenceSerializer`.
The **previous** version of this screen rendered a push/email switch per
`NotifType` — i.e. it assumed `NotificationPreference` was a per-type
channel matrix. That shape **never existed** on the backend: every toggle
in that old version was a silent no-op, since `PATCH`ing it sent fields
the serializer doesn't have, and `GET`ting it back never found the matrix
it expected, so every row stayed at its default forever with no visible
error.

**The real model** (`models.py`'s `NotificationPreference.allowed_channels_for`,
confirmed via `liveclass_models.dart`'s `NotificationPreference` class) is
two independent layers:
1. **Four blanket channel toggles** — `pushEnabled`/`emailEnabled`/
   `smsEnabled`/`whatsappEnabled` — global, **not** per notification type.
2. **A per-type mute list** — a muted type gets **no channel at all**,
   not even the in-app bell/notification-history row's alert (the row is
   still written server-side, just silent).

Plus an independent `digestFrequency` (`off`/`daily`/`weekly`) for the
roundup email, unrelated to the two layers above.

**API:** `GET`/`update` via `LiveClassApi.notificationPreferences` — one
row per user, fetched/saved whole (no per-field PATCH endpoint used by
this screen).

**Label map (`_kNotifTypeLabels`)** — 28 entries (27 concrete `NotifType`
constants + `generic`), kept local to this screen per an explicit
comment rather than added to `liveclass_models.dart`, since it's
presentation-only. ⚻ **Newly cross-checked this pass against
`notifications_screen.dart`'s `_notifIcon` switch (§6.6 above,
independently re-uploaded the same pass):** the two are now confirmed to
agree exactly — the same 27 concrete `NotifType` constants appear in
both, with `generic` present only here (icon map falls through to its
default bell instead, which is the intended behavior for "other
updates"). This narrows §11 item 5's 3-way sync gap to just one remaining
unconfirmed leg: `kAllNotifTypesForPreferences` (the list actually
iterated to render the mute rows, sourced from `liveclass_models.dart`,
which was **not** re-uploaded this pass) against the real backend enum.

**Save path (`_apply`)** — one shared method for **every** control on the
screen (all 4 channel toggles, the digest dropdown, and every per-type
mute switch): optimistic update (`setState` before the network call),
`_saving` (a `Set<String>` of in-flight keys, so each control shows its
own small spinner and can't double-submit itself while a save is
in-flight), full-object `PATCH` (not a diff), revert-on-failure via a
saved `current` snapshot + snack. The code's own comment explains why
resending the whole small object is deliberate and safe: it's one row
per user (4 booleans + a list + an enum), cheap to resend, and the
backend's `partial=True` still protects against a concurrent change to a
field this screen doesn't touch.

**Mute-row semantics, confirmed in-file:** the switch reads as "notify
me" (on = **not** muted) rather than "mute me," specifically to avoid
reading backwards on a screen already full of other on-means-on toggles
— `value: !muted`, flips via `prefs.withMuted(notifType, !v)`.

**UI:** `ListView` sections — "Channels" (4-row card), "Email Digest"
(single dropdown row, off/daily/weekly), "Mute Specific Notifications"
(one row per `kAllNotifTypesForPreferences` entry, divider-separated).
Uses the shared `LiveClassCard`/`LiveClassLoading`/`LiveClassErrorState`
pieces (§2.2) — not part of the design-system-drift group (§8.5/§11.2).

---

#### `ClassroomReportsScreen` — `classroom_reports_screen.dart` ✅ (Screen 22, per the module's own architecture doc numbering)

Platform-staff-only review queue, no constructor params, no role gating
of its own — reached only via `_MyLearningTab`'s conditional "Reports"
flag icon (`isPlatformStaff`, §6.1); whoever pushes this screen is
entirely responsible for the check. The student-side "file a report"
action lives elsewhere, in `ClassroomDetailScreen._openReportDialog()`
(§6.1) — this screen is purely the review side.

**Two tabs, one `TabController(length: 2)`:**

**Tab 1 — "Classrooms"** (`_classroomsTab`, unchanged from before the
tab split, per the file's own header): whole-classroom reports.
- **API:** `LiveClassApi.classroomReports.list(status:)` (paginated,
  client-resorted newest-`createdAt`-first); `POST
  classroom-reports/{id}/review/` (`.review(id, status:, adminNote:)`).
- **State:** `_reports` (`List<ClassroomReport>`), `_loading`, `_error`,
  `_statusFilter` (defaults `ReportStatus.pending` — "that's the actual
  queue"; `null` = "All").
- **3+ pending reports on the same classroom = a "near auto-flag" signal**
  (`_pendingCountByClassroom`, a client-side `Map<int,int>` tally):
  matching cards get a red border and an inline warning row
  ("`N` pending reports on this classroom — may cross the auto-flag
  threshold."). Per the file's own header, this is purely an early-warning
  **display** — the actual 3-pending-reports auto-flag/hide-from-Explore
  behavior is a backend signal this screen doesn't implement or trigger
  itself.
- **Review sheet (`_openReviewSheet`/`_showReviewSheet`)** — ⚻ **confirmed
  fixed memory leak, in-file `FIX` comment:** `noteCtrl` used to be
  created without disposal, leaking one controller per open+close; now
  created fresh per open and released in a `finally` block, another
  confirmed §8.4 instance. Decision dropdown: Reviewed (no action
  needed) / Action Taken / Dismissed, plus an optional admin note
  ("internal, visible to staff only").
- **Card UI:** classroom title + reporter name, status pill, the
  near-auto-flag warning row (conditional), a reason chip
  (`_reasonLabel` — Scam/Not delivering as promised/Inappropriate
  content/Other) + timestamp, optional description, and — once reviewed —
  the admin note (if any) and a "Reviewed `<time>`" line; a "Review"
  button only while `status == pending`.

**Tab 2 — "Messages" (`_messagesTab`), NEW per the file's own header
comment ("Pass 14 frontend catch-up §1.4"):** per-chat-message reports.
Per that comment, this went into a second tab on this same
already-platform-staff-gated screen rather than a sibling
`chat_reports_screen.dart`, since the review flow (status + optional
admin note) is the same shape as Tab 1's, just scoped to a message
instead of a classroom.
- **API:** `LiveClassApi.chatMessageReports.list(status:)` and
  `.review(id, status:, adminNote:)` — **same shape as Tab 1's, and
  structurally identical to `ChatMessageReportsScreen`'s calls (§6.4),
  down to the parameter names.**
- **State/flow:** `_msgReports`/`_msgLoading`/`_msgError`/
  `_msgStatusFilter` — an exact parallel structure to Tab 1's, entirely
  independent state. Review sheet (`_openMessageReviewSheet`/
  `_showMessageReviewSheet`) is the same shape as Tab 1's, same
  leak-safe `finally`-disposed `noteCtrl` pattern, posting to
  `chatMessageReports.review` instead of `classroomReports.review`.
  Card UI mirrors Tab 1's (`_msgReportCard`) minus the near-auto-flag
  grouping (that signal is classroom-scoped, not defined for individual
  messages) and with a message-preview box (`'"${r.messagePreview}"'`,
  italic) swapped in for the classroom-title header.

⚠️⚠️ **NEW this pass — a real, unresolved contradiction against the
existing §6.4 write-up for `ChatMessageReportsScreen`, flagged rather than
silently resolved either way (see also the banner and §11):**
1. §6.4 states `ChatMessageReportApi.review()` "already dropped" a
   separate admin-note parameter and that `chat_message_reports_screen.dart`
   "doesn't try to reintroduce it" — sourced from that screen calling
   `.review(r.id, status: status)` with no third argument. **This
   screen's Tab 2 calls `LiveClassApi.chatMessageReports.review(r.id,
   status: status, adminNote: noteCtrl.text.trim())`** — a third
   positional/named argument that, for this file to compile as real
   source, the method signature must actually accept. Either §6.4's
   claim is wrong (the parameter exists and is simply unused by that one
   caller), or this screen is calling a signature that doesn't match the
   live API — **not distinguishable from this file alone.**
2. §6.4 also states `ChatMessageReportViewSet.get_queryset()` is
   confirmed **session-scoped only** ("no 'all reports for this
   classroom' endpoint anywhere in the API"), which is why
   `ChatMessageReportsScreen` is built as a session-picker shell. **This
   screen's Tab 2 calls `.list(status: _msgStatusFilter)` with no
   `sessionId` argument at all** — a global, platform-staff-scoped query
   across every session. This doesn't necessarily contradict §6.4's
   `get_queryset()` claim (a staff-scoped queryset could legitimately
   return everything when no session filter is supplied, while a
   non-staff caller's queryset stays session-restricted) but it **is** a
   second, structurally different consumer of the same list endpoint that
   the existing write-up didn't previously account for.

Both points are left as an open item (§11) pending a re-read of
`liveclass_api_service.dart`'s actual `ChatMessageReportApi.list`/
`.review` signatures — not something this pass's uploads can resolve on
their own, since that file wasn't re-uploaded this pass.

⚻ **Confirmed instance of the module's timezone/i18n fix (§8.2), called
out explicitly in-file (`FIX` comment) as an unusually complete
cross-reference — and, per that comment, the last one:** this was, per
the file's own header, **"the last screen in the module still on the
pre-fix pattern"** — reading `.day`/`.hour`/`.year` straight off a report's
`createdAt`/`reviewedAt` (UTC-parsed per `liveclass_models.dart`) with no
`.toLocal()` call, against the deprecated `kLiveClassMonths` array. Every
call site now goes through the shared `liveClassFmtDateTime()` helper via
a thin local `_fmtDateTime` wrapper. **If that self-description is
accurate, §8.2/§11's timezone-bug tracking can be considered closed
module-wide as of this pass** — flagged in §11 as resolved-with-caveat
(self-reported by the fixing file itself, not independently cross-checked
against every other screen).

⚠️ **Design-system drift, new instance confirmed this pass:** both
`_reportCard` and `_msgReportCard` hand-roll their own `Container` +
`BoxShadow` card chrome (`Colors.white`, `BorderRadius.circular(14)`,
manual shadow) rather than using the shared `LiveClassCard` — the same
pattern already tracked for `ClassroomPurchasesScreen` (§6.3, §8.5/§11.2).
The rest of the screen (`liveClassAppBar`, `liveClassInputDecoration`,
`LiveClassLoading`/`LiveClassErrorState`/`LiveClassEmptyState`) does use
the shared system correctly — only the card chrome drifts.

**UI:** white `Material`-wrapped `TabBar` ("Classrooms"/"Messages") above
a `TabBarView`; each tab has its own horizontal-scrolling status
`ChoiceChip` row (Pending/Reviewed/Action Taken/Dismissed/All) driving
that tab's own `_load*()` call independently of the other tab.

---

## 7. Cross-Screen Connection Graph

```
LiveClassHomeScreen
 ├─ ExploreScreen
 │   ├─ ClassroomDetailScreen ─────────────────┐
 │   ├─ ClassroomFormScreen ✅ (create)          │
 │   └─ MyProgressScreen                       │
 ├─ _MyLearningTab                             │
 │   ├─ MyPassesScreen ✅ ──► PassGiftClaimScreen (gift-inbox icon)  │
 │   ├─ JoinRequestsScreen.mine()               │
 │   ├─ WishlistScreen ─────► ClassroomDetailScreen
 │   ├─ WaitlistScreen (My Waitlist, sessionId omitted) ─► LiveSessionScreen
 │   ├─ CertificatesScreen (my mode)             │
 │   ├─ MyRemindersScreen ──► LiveSessionScreen │
 │   ├─ NotificationsScreen ─► ClassroomDetailScreen, NotificationPreferencesScreen
 │   └─ ClassroomReportsScreen ✅ (staff only, 2 tabs: Classrooms + Messages — see §6.6, §11)
 └─ CoinWalletScreen                             │
                                                  │
ClassroomDetailScreen ✅ ◄────────────────────────┘   (the hub — fully verified this pass)
 ├─ ClassroomFormScreen ✅ (edit)
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
 ├─ ChatMessageReportsScreen (session-scoped queue — see §11 re: ClassroomReportsScreen's
 │                             parallel, un-scoped consumer of the same chatMessageReports API)
 ├─ MaterialsScreen (full screen, canManage)
 ├─ NoticeBoardScreen (full screen, canManage)
 ├─ DoubtsScreen (full screen, canManage)
 ├─ AssignmentsScreen ✅ (full screen, canManage) ──► SubmissionGradingScreen
 ├─ RequestJoinScreen (join/renew, now a genuinely-existing class — §8.6 fix confirmed)
 ├─ _ScheduleTab (embedded) ──┬─ SessionsListScreen (view all)
 │                             ├─ LiveSessionScreen (2-arg, per-session)
 │                             └─ WaitlistScreen (teacher view, per-session)
 ├─ _MaterialsTab ──► LiveClassUploadLimits.checkXFile() (NEW this pass, materialMaxMB)
 ├─ _AssignmentsTab ──► LiveClassUploadLimits.checkXFile() (NEW this pass, submissionMaxMB)
 ├─ _MaterialsTab / _NoticesTab / _DoubtsTab / _ReviewsTab / _AssignmentsTab (embedded, lighter dupes of the full-screen variants above)
 └─ LiveSessionScreen (3-arg: sessionId+session+initialResult, "Enter Class" via startOrJoin())

liveclass_notification_handler.dart ✅ (push tap routing — full detail in §9)
 ├─ class_reminder / session_live  ──► LiveSessionScreen  (deep room join)
 ├─ pass_auto_renewed / auto_renew_failed / pass_gift_expired ──► MyPassesScreen
 └─ everything else (classroom-scoped, i.e. any type with classroom_id) ──► ClassroomDetailScreen

pip_service.dart ✅ NEW (services/, §5.4) ── claimed (not yet re-verified) wiring:
 └─ LiveSessionScreen ── setPipEnabled(true/false) around join/leave; enterPip() on the
                          existing mini-view button; onPipModeChanged drives chrome-free layout
```

**Orphans (not currently wired from anywhere in the module):** none
currently tracked. `PassGiftClaimScreen` was the last one — **RESOLVED
this pass**, confirmed wired from `my_passes_screen.dart`'s app-bar
"Gifted passes" icon (§6.3, §8.9, §11 item 1).

---

## 8. Cross-Cutting Fixes & Patterns Already Applied

*(Most entries below carry forward from prior passes. §8.1, §8.2, §8.4,
§8.5, and §8.9 have new confirming evidence **this** pass (the
`assignments_screen.dart`/`classroom_form_screen.dart`/
`classroom_reports_screen.dart` read) — noted inline. §8.3, §8.6–§8.8,
§8.10 are unchanged and not re-typed in full; see prior passes for their
complete text if needed. Condensed list below for orientation.)*

### 8.1 Corrupt-cache-on-failed-download bug
Affects: Certificates ✅, Materials ✅, Submission Grading ✅, Classroom
Detail ✅. Fix pattern unchanged from prior passes — see there for the
full code shape. **Not applicable to this pass's three new files:**
none of `assignments_screen.dart`/`classroom_form_screen.dart`/
`classroom_reports_screen.dart` do an authenticated file **download**
(the form screen only *uploads* a cover image; the other two have no
file-open flow at all) — no new confirmed instance, and no reason to
expect one from these three.

### 8.2 Timezone / i18n bug — **update this pass: `classroom_reports_screen.dart`
✅ confirmed as another real instance of this exact fix** (previously
hand-formatting `createdAt`/`reviewedAt` with no `.toLocal()`, now
routed through `liveClassFmtDateTime()` via a thin local wrapper — §6.6),
and `assignments_screen.dart` ✅ likewise for `dueDate` (§6.5). ⚠️ **Per
`classroom_reports_screen.dart`'s own header comment, it explicitly
self-describes as "the last screen in the module still on the pre-fix
pattern"** — if that self-assessment is accurate, this bug class is now
closed module-wide. Flagged in §11 as resolved-with-caveat (self-reported
by the fixing file, not independently cross-checked against every screen
in the module).
### 8.3 `ClassSchedule` zone resolution — unchanged, carried forward.
### 8.4 `TextEditingController` leaks in bottom sheets — unchanged
pattern; **new confirmed instances this pass:** `assignments_screen.dart`
✅'s 3-controller create sheet (§6.5) and `classroom_reports_screen.dart`
✅'s two parallel `noteCtrl`-based review sheets, one per tab (§6.6).
`classroom_form_screen.dart` ✅ uses a different, also-safe pattern —
its controllers are `final` instance fields disposed once in the
screen's own `dispose()` (it's a persistent form, not a per-open bottom
sheet) rather than the create-fresh-per-open/`finally`-dispose pattern
tracked here; not a leak, just a structurally different (and equally
correct) fix for a screen shape that doesn't need the sheet pattern.
### 8.5 Design-system drift — unchanged; `classroom_detail_screen.dart`
and `live_session_screen.dart` both confirmed to independently hand-roll
their own loading/error/app-bar UI, same structural pattern as the
screens already tracked here. **Two new, distinct instances confirmed
this pass:** `classroom_reports_screen.dart` ✅ hand-rolls its own
`Container`+`BoxShadow` card chrome in both tabs' card widgets rather
than using `LiveClassCard` — same specific flavor of drift already
tracked for `ClassroomPurchasesScreen` (§6.3) — bumping that count by
one more; and `classroom_form_screen.dart` ✅ defines its own local
`_decoration()` `InputDecoration` helper instead of calling the shared
`liveClassInputDecoration` — a **third, distinct flavor** of the same
underlying pattern (a shared primitive re-implemented locally) not
previously catalogued anywhere in this section. **One more confirmed a
pass later:** `notice_board_screen.dart` ✅ (§6.5) hand-rolls the same
`Container`+`BoxShadow` card chrome as `ClassroomPurchasesScreen`/
`ClassroomReportsScreen`, bumping that flavor's count again. See §11.2
for the updated running count.

### 8.6 Missing-class build break — unchanged, carried forward.

### 8.7 Flutter's built-in `MaterialType` collision — unchanged, carried
forward. Not applicable to this pass's three new files (none of them
touch `Material`/`ClassMaterial`).

### 8.8 Overstated refund dialog — unchanged, carried forward.
### 8.9 Previously-orphaned screens now wired in — unchanged, carried
forward; `classroom_detail_screen.dart`'s manage sheet (§6.1) remains the
confirmed wiring point for the screens already listed here.
**New this pass:** `classroom_reports_screen.dart` ✅ is itself another
confirmed instance of this exact pattern, one level up — per its own
header comment, before its "Reports" flag-icon entry point existed on
`_MyLearningTab` (§6.1), `ClassroomReportsScreen` "had zero reachable
entry points anywhere in the module": students could file a report via
`ClassroomDetailScreen._openReportDialog()`, but nothing ever let staff
open the review screen, so filed reports vanished into an unreachable
queue. Confirmed fixed — the flag-icon entry point is real and gated on
`isPlatformStaff` (§6.1).
**New this pass:** `my_passes_screen.dart` ✅ closes the module's last
remaining orphan, `PassGiftClaimScreen` (§6.3, §7, §11 item 1) — per that
screen's own header comment describing two intended entry points,
`MyPassesScreen`'s app-bar "Gifted passes" icon is confirmed as the
second, a general-browsing "My gifts" entry, pushing
`PassGiftClaimScreen(onClaimed:)` with no `giftId` (Received/Sent tabs).
With this, §7's "Orphans" list is now empty — every screen in the
module has at least one confirmed live navigation edge into it.
### 8.10 Mixed-language (Hindi) string leaks — unchanged, carried
forward. **Confirmed this pass: none of `assignments_screen.dart`,
`classroom_form_screen.dart`, or `classroom_reports_screen.dart` carries
any of this pattern** — all three are fully English throughout, in UI
copy and in code comments.

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

1. **RESOLVED this pass — `PassGiftClaimScreen` orphan.** Previously
   tracked here as "still orphaned." `my_passes_screen.dart` (§6.3,
   §8.9) was read line-by-line this pass and confirms a real, live push
   into `PassGiftClaimScreen(onClaimed:)` from its app-bar "Gifted
   passes" icon — the screen's own header comment describes this as its
   second of two intended entry points. §7's "Orphans" list is now
   empty. `PassGiftClaimScreen` itself remains 📋 SUMMARY-LEVEL — this
   resolves its reachability, not its own internal verification status.
2. **Design-system structural drift** — now confirmed to span **10**
   screens across **three distinct flavors** (§8.5, updated this pass):
   hand-rolled loading/error/app-bar UI (`WishlistScreen`,
   `WaitlistScreen`, `ScheduleManagerScreen`, `SessionsListScreen`,
   `ClassroomDetailScreen`, `LiveSessionScreen`); hand-rolled
   `Container`+`BoxShadow` card chrome instead of `LiveClassCard`
   (`ClassroomPurchasesScreen`, `ClassroomReportsScreen`, and **new this
   pass:** `NoticeBoardScreen`, §6.5); and a locally re-implemented
   `InputDecoration` helper instead of the shared
   `liveClassInputDecoration` (**a third flavor not previously
   catalogued before a prior pass:** `ClassroomFormScreen`). Same low-risk,
   low-priority cleanup as before — just a larger, now three-part list.
3. **`NotificationPreference` model shape** — unresolved, unchanged.
4. **`SessionEngagementReport` fields** — unresolved, unchanged.
5. **3-way `NotifType` sync rule** — unresolved, unchanged from the prior
   pass (still confirmed broken for `kAllNotifTypesForPreferences`; still
   unconfirmed either way for `notifications_screen.dart`'s icon switch
   and `notification_preferences_screen.dart`'s label map, since neither
   was re-uploaded this pass).
6. **Mixed-language (Hindi) string leaks** — unchanged count. **Newly
   confirmed this pass: none of `assignments_screen.dart`,
   `classroom_form_screen.dart`, or `classroom_reports_screen.dart` adds
   to this list** — all three are clean (§8.10).
7. **RESOLVED (already fixed, but this §11 entry was never updated to
   say so) — "Recording playback inconsistency."** This item was carried
   forward as "unresolved" across multiple passes on the assumption that
   `sessions_list_screen.dart` hadn't been re-uploaded/re-checked; it in
   fact was re-uploaded and read line-by-line in an earlier pass, and
   that pass's own §6.4 write-up for `SessionsListScreen` already
   confirms the "Recording" button does a real `url_launcher` hand-off
   (`Uri.tryParse` + `canLaunchUrl` + `launchUrl(...,
   mode: LaunchMode.externalApplication)`), matching
   `ClassroomRecordingsScreen`'s pattern — not the snack-only stub this
   item description implies. Re-confirmed again this pass against the
   freshly re-uploaded `sessions_list_screen.dart`: still fixed, no
   regression. This item entry itself was simply stale, not the code.
8. **`_fmtRelative` duplicated, not shared** — unresolved, unchanged.
9. **RESOLVED this pass — the last three architecture-level-uncertain 📋
   files are now ✅.** `assignments_screen.dart`, `classroom_form_screen.dart`,
   and `classroom_reports_screen.dart` were all read line-by-line this
   pass (§6.2/§6.5/§6.6) — §0 coverage moves 34✅/10📋 → **37✅/7📋**. The
   **7** files still remaining 📋 (`coupons_screen.dart`,
   `explore_screen.dart`, `join_requests_screen.dart`,
   `my_passes_screen.dart`, `notice_board_screen.dart`,
   `pass_gift_claim_screen.dart`, `pass_management_screen.dart`) are all
   narrower, lower-traffic flows — remaining gaps in them are private
   method names / exact widget trees / state variable names, not
   architecture-level (model/API-shape/hub-navigation) unknowns.
10. **`LiveClassApi.myEarnings()` non-compiling method** (§5.1, prior
    pass) — unresolved, unchanged. Not touched by this pass's three
    screen reads (none call it).
11. **Push `type` vs. `NotifType` vocabulary mismatch** (§4/§9.1, prior
    pass) — unresolved, unchanged.
12. **RESOLVED (prior pass) — `LiveClassSocket` wiring status.** Previously
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
17. **NEW this pass, UNRESOLVED — a real contradiction between
    `classroom_reports_screen.dart` and the existing §6.4 write-up for
    `ChatMessageReportsScreen`.** Two separate points (full detail in
    §6.6):
    (a) §6.4 states `ChatMessageReportApi.review()` "already dropped" a
    separate admin-note parameter; `classroom_reports_screen.dart` calls
    `.review(r.id, status:, adminNote:)` with a third argument that, for
    the file to compile, the real method signature must accept — either
    §6.4's claim is stale/wrong, or this screen calls a mismatched
    signature. Not distinguishable from Dart source alone.
    (b) §6.4 states the message-reports endpoint is confirmed
    session-scoped only (no "all reports" query exists);
    `classroom_reports_screen.dart` calls `.list(status:)` with **no
    `sessionId`** — a second, structurally different (global,
    staff-scoped) consumer of the same endpoint family that the existing
    write-up didn't account for. Plausible (a staff-scoped queryset could
    legitimately behave differently from a regular caller's) but
    unconfirmed. **Both need `liveclass_api_service.dart`'s actual
    `ChatMessageReportApi.list`/`.review` signatures re-read to resolve**
    — that file wasn't part of this pass's uploads.
18. **RESOLVED this pass — `LiveClassUploadLimits.coverImageMaxMB`/
    `.coverImageExtensions`/`.assignmentAttachmentMaxMB` "wired-but-unconfirmed"
    gap (§2.4, prior pass).** All three now have confirmed live call
    sites: `coverImageMaxMB`/`.coverImageExtensions` in
    `classroom_form_screen.dart`'s `_pickCover()` (via `checkXFile`), and
    `assignmentAttachmentMaxMB` in `assignments_screen.dart`'s create
    sheet (via `checkPlatformFile` — the module's first confirmed live
    use of that variant, since this is the first `file_picker`/
    `PlatformFile`-based upload flow read, as opposed to every other
    confirmed consumer's `file_selector`/`XFile`). Every
    `LiveClassUploadLimits` MB-cap constant now has at least one
    confirmed consumer — see §2.4.
19. **NEW this pass, resolved-with-caveat — timezone/i18n bug (§8.2) may
    now be closed module-wide.** `classroom_reports_screen.dart`'s own
    header comment explicitly self-describes as "the last screen in the
    module still on the pre-fix pattern," and its fix is confirmed
    present. If that self-assessment is accurate, no screen in the
    module still has the raw-UTC-field-read timezone bug. Caveat: this is
    the *fixing file's own claim*, not an independent line-by-line
    re-audit of every other screen in this doc — treat as
    likely-resolved, not certainty, until spot-checked.
20. **NEW this pass — `ClassroomFormScreen`'s multi-pop-result contract.**
    Callers of this screen (currently only `ExploreScreen`'s create flow
    and `ClassroomDetailScreen`'s edit-mode manage-sheet tile, per §7)
    need to handle **three** distinct `Navigator.pop` result shapes, not
    one: a `Classroom` object (created or updated), or the literal
    strings `'closed'`/`'deleted'` from the two lifecycle actions (§6.2).
    Worth flagging for anyone wiring a new caller to this screen —
    treating the result as always-a-`Classroom` will silently mishandle
    the close/delete paths.
21. **NEW this pass, RESOLVED (doc-only) — `CoinWalletScreen` had no
    actual write-up despite being tagged ✅ in §0.** Same class of gap as
    the two prior "claimed ✅, no body" fixes earlier in this doc's
    revision history — this time it was `coin_wallet_screen.dart` itself.
    A full write-up now exists in §6.6, sourced from this pass's actual
    line-by-line read. **Not fully resolved is a genuine, still-open code
    finding surfaced by that read:** the transaction tile's date line
    calls `liveClassFmtDateTime(t.createdAt)` with no `context` argument
    (§2.3), silently falling back to `intl`'s default locale instead of
    the device/app one — the same bug class already fixed at every call
    site in `my_reminders_screen.dart`, but **live and unfixed** here. Not
    a doc-only item; a real, actionable follow-up for whoever next touches
    `coin_wallet_screen.dart`. Worth a targeted sweep of every
    `liveClassFmtDate`/`liveClassFmtDateWeekday`/`liveClassFmtDateTime`
    call site module-wide for the same missing-`context` mistake, since
    this doc has now found it twice (previously `my_reminders_screen.dart`,
    now `coin_wallet_screen.dart`) purely by chance of which files got
    re-uploaded, not by a systematic check.
22. **NEW this pass — `my_passes_screen.dart`'s header comment is stale
    relative to its own body.** The header claims this screen "only ever
    displays status" and that refund is handled entirely elsewhere; the
    actual body implements self-service cancel-with-partial-refund, an
    auto-renew toggle, and a gift-a-pass flow (§6.3). Not a functional
    bug — the code itself is internally consistent and each feature has
    its own accurate `NOTE`/`NEW`/`FIX` comment — just a top-of-file
    summary that was never updated as those three features landed. Worth
    a quick pass over other screens' header comments for the same kind of
    staleness if this doc is ever used to onboard someone who reads only
    headers.
23. **RESOLVED this pass — §0 coverage moves 37✅/7📋 → 39✅/5📋.**
    `my_passes_screen.dart` and `notice_board_screen.dart` were both read
    line-by-line for the first time this pass (§6.3, §6.5) — the last
    two 📋 files with any material UI/logic surface. The 5 files still
    remaining 📋 (`coupons_screen.dart`, `explore_screen.dart`,
    `join_requests_screen.dart`, `pass_gift_claim_screen.dart`,
    `pass_management_screen.dart`) are unchanged from the prior pass's
    assessment — narrower, lower-traffic flows, private-detail gaps only.

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