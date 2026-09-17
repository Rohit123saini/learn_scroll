# LearnScroll — Home Module Documentation

Ye doc `lib/home.dart` aur uske saare supporting files ka combined reference hai — kya-kya implement hua hai, kaise kaam karta hai, aur abhi bhi kya backend se confirm hona baaki hai. Jab bhi koi naya change ho, isi file ko update karte rehna taaki poora module ek hi jagah se samajh aa jaaye.

---

## 1. File Map (kaunsi file kahan hai)

| File | Path (lib/ se relative) | Role |
|---|---|---|
| `home.dart` | `lib/home.dart` | Home screen ka poora UI + state (feed, top sections, comments sheet, bottom nav) |
| `theme_service.dart` | `lib/theme_service.dart` | Light/Dark mode state + design tokens (ColorScheme) |
| `language_service.dart` | `lib/language_service.dart` | App-wide locale (EN/HI) state |
| `main.dart` | `lib/main.dart` | App bootstrap — Firebase, CallKit, Push, Downloader, Theme/Language init, routing |
| `session_service.dart` | `lib/services/session_service.dart` | Global `navigatorKey` + "session expired" flag (no navigation logic itself) |
| `services/home_api_model_service.dart` | `lib/services/home_api_model_service.dart` | Feed + home-extras (classrooms/live/stories/invite) models + API calls |
| `services/comment_service.dart` | `lib/services/comment_service.dart` | Comments CRUD, replies, reactions API |
| `widgets/error_widgets.dart` | `lib/widgets/error_widgets.dart` | Reusable error-state / empty-state widgets |
| `widgets/ls_ui.dart` | `lib/widgets/ls_ui.dart` | Shared design-system widgets (cards, buttons, chips, app bar) — Home/Assignments/Test-Series teeno use karte hain |
| `widgets/skeletons.dart` | `lib/widgets/skeletons.dart` | Shimmer loading placeholders |

**Important path note:** `home.dart`, `theme_service.dart`, `language_service.dart` sab `lib/` ke **root** pe hain — kisi subfolder ke andar nahi. Isliye inke imports relative `'theme_service.dart'` waale hain, `'../theme_service.dart'` nahi.

---

## 2. App Bootstrap — `main.dart`

Startup sequence (`main()` function), sab steps **independent try/catch** me hain — ek fail ho to baaki phir bhi chalte hain:

1. `AudioPlayer` global context setup (call ke liye).
2. `Firebase.initializeApp()` → success par hi `firebaseReady = true`.
3. `CallKitService.instance.init()` — Firebase ki zaroorat nahi, isliye Firebase fail ho jaaye tab bhi chalta hai (incoming-call popup infra ready rahe).
4. `PushNotificationService.instance.init()` — sirf `firebaseReady == true` ho tabhi.
5. `FileDownloader` init + notification config.
6. `WakelockPlus.disable()`.
7. `ThemeService.instance.init()` — saved theme load, taaki pehla frame hi sahi mode me render ho.
8. `LanguageService.instance.init()` — saved locale load + `timeago` locale messages register.
9. `runApp(MyApp())`.

**`MyApp` widget:**
- `ValueListenableBuilder<ThemeMode>` (ThemeService) ke andar `ValueListenableBuilder<Locale>` (LanguageService) nested — dono change hote hi turant poori app rebuild.
- `navigatorKey` **`session_service.dart` se import hota hai**, `main.dart` khud declare nahi karta (warna do alag keys ban jaatin aur redirect fail ho jaata).
- `routes: { '/login': ..., '/home': ... }` registered — session-expiry redirect isi pe depend karta hai.
- `AuthService.onForceLogout` yahan wire hota hai → sirf `SessionService.markExpired()` call karta hai, navigate khud nahi karta.
- App start pe `_checkAuth()` (SharedPreferences me `access_token` check) decide karta hai Login ya Home dikhana hai.
- `builder:` me `MinimizedCallBar` poori app ke upar stack me overlay rehta hai (WhatsApp-style minimized call bar).

---

## 3. Session Expiry Flow (Task 8)

3 files milke ye kaam karti hain:

```
AuthService._doRefresh()  (refresh-token khud invalid)
        │
        ▼
AuthService.onForceLogout()      ← main.dart me subscribe kiya
        │
        ▼
SessionService.markExpired()     ← session_service.dart, sirf flag set karta hai
        │
        ▼
homeSessionExpiredNotifier.value = true
        │
        ▼
HomeScreen._onSessionExpired()   ← home.dart me listen ho raha hai
        │
        ▼
3-second "Session expired" snackbar → AuthService.logout() → SessionService.reset()
        │
        ▼
navigatorKey.currentState?.pushNamedAndRemoveUntil('/login', ...)
```

- `session_service.dart` **jaan-boojh kar** sirf `flutter/material` pe depend karti hai (na AuthService pe na kisi screen pe) — taaki circular import na ho.
- `_sessionExpiryHandled` flag ensure karta hai ki redirect ek hi baar chale, chahe notifier kitni baar fire ho.
- Home screen me `AuthService.getValidToken()` use hota hai (na ki plain `getToken()`) — expiry-aware, zaroorat pe proactive refresh karta hai, aur refresh bhi mar jaaye to yahi flow trigger hota hai.

---

## 4. Theme System — `theme_service.dart`

- Singleton (`ThemeService.instance`), `SharedPreferences` me `theme_mode` key se persist (`light` / `dark` / `system`).
- `ValueNotifier<ThemeMode> themeMode` — UI isi ko sunta hai.
- `AppColors` + `AppThemeTokens`: `learnscroll_home_final.html` ke CSS variables (`--bg`, `--surface`, `--surface2`, `--primary`, `--accent`, `--ink`, `--muted`, `--border`) ka 1:1 Dart mapping, `ColorScheme` ke through.
- **Fix note:** pehle sirf primary/secondary/surface/onSurface diye gaye the, baaki Material default fallback le rahe the — jisse borders solid-dark dikhte the, muted text full-contrast ink jaisa, aur tinted panels invisible ho jaate the. Ab poora set explicitly define hai.
- `ColorScheme.background` / `.onBackground` **jaan-boojh kar use nahi kiya** (Flutter 3.18+ me deprecated) — bg hamesha `AppThemeTokens.of(context).background` se aata hai.

---

## 5. Language / i18n — `language_service.dart`

- Singleton pattern, `theme_service.dart` jaisa hi.
- `ValueNotifier<Locale> locale`, default `en`. `supportedLocales = [Locale('en'), Locale('hi')]`.
- `init()`: pehli baar app khule to device locale try karta hai (agar supported hai), warna saved preference load karta hai; dono fail ho to English.
- `timeago` package ke liye Hindi messages explicitly register karni padti hain (`timeago.setLocaleMessages('hi', timeago.HiMessages())`) kyunki package by-default sirf en/es load karta hai.
- Naya language add karne ka process file ke top comment me hi likha hai: `.arb` file banao → yahan list me `Locale` add karo → `flutter gen-l10n` chalao.

---

## 6. Home Screen — `home.dart` (main file, ~2000 lines)

### 6.1 Structure (HTML → Flutter section mapping)

| HTML class | Method |
|---|---|
| `.brand-row` | `_buildBrandRow()` (SliverAppBar) |
| `.search-bar` | `_buildLsSearchBar()` |
| `.classroom-row` | `_buildClassroomSwitcher()` |
| `.invite-strip` | `_buildInviteStrip()` |
| `.stories` | `_buildStories()` |
| `.section` (live) | `_buildLiveNow()` |
| `.quick-grid` | `_buildQuickActionsGrid()` |
| `.feed-title` | `_buildFeedTitle()` |
| `.post-card` | `_buildPostCard()` |
| `.interstitial` | `_buildFeedInterstitial()` |
| `.bottomnav` | `_LsBottomNav` |

Colors kahin bhi hardcoded nahi — sab `Theme.of(context).colorScheme` se (Task 5.8 rule).

### 6.2 State & Data Loading

- `initState()` parallel calls karta hai: `_initAds()`, `_loadFeed()`, `_loadSaved()`, `_loadMyUsername()`, `_loadHomeExtras()`, aur session-expiry listener attach.
- **Feed loading (`_loadFeed`)**: pehle cached feed (SharedPreferences) turant dikhaata hai agar available ho, phir background me fresh feed fetch karta hai aur silently update karta hai (Instagram-jaisa behavior — agar cache dikh raha hai to fail hone par error-state nahi dikhata, stale data ke saath chup-chaap rehta hai).
- **Infinite scroll (`_onScroll`)**: threshold 300px se badhaakar 700px kiya gaya (300 pe premature spinner dikh jaata tha, 700 pe agla page usually load ho chuka hota hai) — Task 10.5.
- **Home extras (`_loadHomeExtras`)**: classrooms, live-classes, stories, invite-info — har section apna independent try/catch leke chalta hai (`Future.wait` + per-call `catchError`), taaki ek section fail ho to baaki dikhte rahein.
- **Notifications badge**: hardcoded `0` hai kyunki uploaded backend docs me koi global unread-notifications endpoint confirm nahi hua. Endpoint milte hi `_unreadNotifications` set karna hai.

### 6.3 Feed Slots — Ads & Interstitial cadence (Task 6.2)

```dart
const int kAdEveryPosts = 5;            // har 5th post ke baad ad
const int kInterstitialEveryPosts = 18; // har 18th post ke baad interstitial ("Jump back in")
```

- Ad pehle check hota hai, phir interstitial — dono kabhi ek hi slot pe overlap nahi karte.
- **Fix note:** purana builder-level modular arithmetic (`index % 6 == 5` jabki `childCount` `~/5` se calculate ho raha tha) mismatch tha — lambi feed me aakhri posts kat jaate the. Ab `_rebuildFeedSlots()` se ek precomputed `_slots` list banti hai, jisse ye bug fix ho gaya.

### 6.4 Not-yet-wired items (UI ready, backend/navigation pending)

| Feature | Current behavior | Kya chahiye |
|---|---|---|
| Notifications icon | Snackbar "coming soon" | Notifications screen banni hai, phir `Navigator.push` |
| Classroom-row tap (non-live classroom) | Snackbar, no-op | Backend confirm kare classroom-switch ka behavior (Task 4) |
| "Notices" / "Wallet" quick actions | "Coming soon" | Backend/screens pending |
| Live-now list endpoint | Uncertain — 2 candidate endpoints, koi confirm nahi | Backend se confirm karna hai (`home_api_model_service.dart` comment) |
| "Stories" concept | Backend me koi `stories` model exist nahi karta | Backend confirm/spec chahiye |
| Campus module | Koi screen project me nahi hai | Screen banni baaki hai |

### 6.5 Post interactions

- Like / Save / Reaction — teeno `HomeFeedService` (see §7) ke through.
- `toggleSave` ka sirf ek hi final version hai (pehle duplicate tha, remove kiya gaya).

### 6.6 Comment sheet & Threading (Task 7.x)

- Poora comment bottom-sheet ab `ColorScheme` se colors leta hai (pehle Facebook-Messenger-grey hardcoded literals the) — Task 7.2.
- **Reactions**: emoji map — 👍 like, 🤔 confuse, ❗ wrong, ⭐ imp, 💡 explain. Reaction colors **jaan-boojh kar fixed brand hex** hain (Facebook jaisa), theme-independent, taaki "like" hamesha same blue dikhe light/dark dono me.
- **Thread structure fix**: pehle nested replies alag-alag indent levels pe the; ab sab replies (kitni bhi deep) `level: 1` pe force hote hain, taaki ek hi flat series me dikhein (WhatsApp/Instagram jaisa), khaali gehra staircase-indent nahi.
- Media support comment ke andar: image (inline thumb), video (thumb + fullscreen player), file (icon + filename, tap to download+open via `Dio` + `OpenFilex`).
- Long-press pe reaction-overlay popover; long-press comment tile pe options menu (edit/delete/hide — jo bhi `_canShowMenu` allow kare).

### 6.7 Bottom Navigation

- Sirf **Home (0)** aur **Profile (2)** asal me `IndexedStack` tabs hain.
- Baaki (Search, Classes, Chat) push-routes hain, tab nahi.
- `HomeScreen(initialIndex: ...)` ka index-convention jaan-boojh kar fix rakha gaya hai kyunki dusri screens (jaise `conversations_screen.dart`) isी convention se `HomeScreen(initialIndex: 2)` bolke seedha Profile pe jump karti hain.

---

## 7. `home_api_model_service.dart` — Models + API

### Models
`PostModel`, `FeedResponse`, `ClassroomModel`, `LiveClassModel`, `StoryModel`, `InviteEarnModel`.

### `HomeFeedService`
- `kApiTimeout = 15s` — **Task 11.3**: pehle koi timeout hi nahi tha, server hang hone par app hamesha spinner dikhata reh jaata.
- Saare token lookups ab `AuthService.getValidToken()` use karte hain, plain `getToken()` nahi — **Task 8.4** (expiry-aware; refresh-token bhi mar chuka ho to force-logout chain trigger hoti hai — dekho §3).
- `getCachedFeed()` / `getHomeFeed()` / `refreshFeed()` / `clearFeedCache()` — cache-first strategy, page 1 par background refresh.
- `toggleLike`, `toggleSave` (collection support), `toggleReaction` — sab POST calls, JWT bearer header ke saath.

### `HomeExtrasService` (Task 4/5 — mock/pending)
- `getMyClassrooms`, `getLiveNow`, `getStories`, `getInviteInfo` — kuch abhi **mock data return kar rahe hain** taaki UI unblock rahe. Comments me clearly likha hai kaunse endpoints backend se confirm karne hain:
  - Live-now: 2 candidate endpoints available, konsa sahi hai confirm nahi.
  - Stories: backend me concept hi exist nahi karta abhi.
  - Invite/coins: response JSON keys (`coins_per_referral`, `invite_link`) ek assumption hain, backend confirm chahiye.

---

## 8. Shared UI Kit — `ls_ui.dart`

Home + Assignments + Test-Series teeno modules yahi common widgets use karte hain (ek jagah design tweak karo, sab jagah reflect ho):

- `LsType.head()` — heading font (Sora via google_fonts).
- `LsSectionHead`, `LsCard`, `LsStatusChip`, `LsPrimaryButton`, `LsOutlineButton`, `LsFilterChips`, `LsMetaRow`, `LsScoreTile`, `LsProgressBar`.
- `lsAppBar()` — flat app bar (page-bg background, no elevation).
- `lsSnack()` — common snackbar helper.
- `lsBg(context)` — page background shortcut, `AppThemeTokens` se (kyunki `ColorScheme.background` deprecated hai).
- Koi hardcoded string ya color nahi — sab caller se (l10n) ya theme se aata hai.

---

## 9. Error / Empty States — `error_widgets.dart`

- `ErrorStateWidget` — icon + title + optional subtitle + optional retry button. `compact` flag se chhoti (inline section) ya badi (full feed) version.
- `EmptyStateWidget` — data load ho gaya par khaali hai (retry button jaan-boojh kar nahi diya — retry se bhi khaali list hi aayegi, user ko action chahiye).
- Strings hardcoded nahi — caller `AppLocalizations` se translated text pass karta hai, taaki widget khud l10n pe depend na kare.

---

## 10. Skeleton Loaders — `skeletons.dart`

- Custom shimmer (`LsShimmer`) — `shimmer` package use nahi kiya (extra dependency + fixed grey palette jo dark-mode theme se match nahi karta). Base/highlight dono `ColorScheme` se.
- **Reduced-motion respect**: `MediaQuery.disableAnimations == true` ho to static grey block dikhata hai, sweep animation nahi — Task 15.4.
- Section-specific skeletons: `LsClassroomRowSkeleton`, `LsStoriesSkeleton`, `LsLiveRowSkeleton`, `LsInviteStripSkeleton`, `LsPostCardSkeleton` — sab real widgets ke exact dimensions match karte hain taaki data aane par layout-jump (CLS) na ho.

---

## 11. Pending / Backend-confirmation Checklist

- [ ] Global unread-notifications endpoint (badge abhi hardcoded 0)
- [ ] Notifications screen banani hai + wire karni hai
- [ ] Classroom-switch tap behavior confirm (non-live classroom)
- [ ] Live-now endpoint — 2 candidates me se sahi wala confirm
- [ ] "Stories" backend concept hi define karna hai
- [ ] Invite/coins API response shape (`coins_per_referral`, `invite_link`) confirm
- [ ] Campus module screen
- [ ] "Notices" / "Wallet" quick actions ke screens

---

*Last updated from source review of: `home.dart`, `theme_service.dart`, `language_service.dart`, `main.dart`, `session_service.dart`, `home_api_model_service.dart`, `error_widgets.dart`, `ls_ui.dart`, `skeletons.dart`.*
