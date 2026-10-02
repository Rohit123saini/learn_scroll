# Reels Screen (P12 Flutter) - setup

## Files (lib/ ke andar)
- `widgets/feed_video_preloader.dart`   CHANGED: `release(url)`, per-owner cap (Home cap 2 same)
- `post/widgets/reels_player_pool.dart` NEW: prev/current/next pool, global mute applied to every controller
- `post/screens/reels_screen.dart`      NEW: PageView, gestures, heart burst, lifecycle, progress
- `post/services/reels_sound_service.dart` NEW: persisted mute flag (SharedPreferences key `reels_muted`, default sound ON)
- `utils/app_route_observer.dart`       NEW
- `main.dart`                           import + `navigatorObservers: [appRouteObserver]`

## pubspec.yaml
```yaml
dependencies:
  flutter_blurhash: ^0.8.2   # baaki packages (video_player, wakelock_plus, cached_network_image, shared_preferences) pehle se hain
```

## Open
```dart
Navigator.push(context, MaterialPageRoute(builder: (_) => ReelsScreen(startPostId: post.id)));
```

## Gestures
tap = pause/play | double-tap = like (heart burst, kabhi unlike nahi) | long-press = hold-to-pause | swipe up/down = next/prev

---

# P13 — Overlays, data states, entry points

## Files (lib/ ke andar)
- `post/widgets/reels_overlays.dart`  NEW: right rail, author/Follow/caption/hashtags, "more" sheet, "Why am I seeing this" sheet
- `post/screens/reels_screen.dart`    CHANGED: actions (like/save/follow optimistic + rollback, comment, share, hide + Undo), prefetch/retry, slide-out
- `post/widgets/reels_player_pool.dart` CHANGED: `dropFrom(index)` (list changed under the pool)
- `home.dart`                         CHANGED: 6-tab nav, `Reels` = index 1 (pushes `ReelsScreen`), Profile = index 5
- `widgets/app_bottom_nav.dart`       CHANGED: `AppTab.reels` (Campus / Chat screens mirror the Home nav)
- `post/screens/post_list_screen.dart` CHANGED: video tile (hashtag / explore / saved) -> `ReelsScreen(startPostId)`
- `profile/screens/profile.dart`, `target_profile.dart` CHANGED: video tile -> `ReelsScreen.open(context, startPostId: id)`
- `l10n/*.arb` + generated `app_localizations*.dart`: reelsTab, reelsEmpty, reelNotInterested, reelShowFewer,
  reelWhySeeing, reelHidden, reelFewerDone, reelCaptionMore, reelCaptionLess (EN + HI)

Naya package nahi chahiye (share_plus, shared_preferences, flutter_blurhash pehle se).

## Overlays
- Rail: like (rail = toggle, double-tap = like only) / comment (`CommentBottomSheet`, count +1 on send) / share (native sheet: caption + video url) / save (`HomeFeedService.toggleSave`, Home ke `saved_posts` prefs sync) / more
- More: Not interested, Show fewer like this (category + first 2 hashtags), Why am I seeing this — `FeedFeedbackService`. Apni reel pe sirf "Why".
- Bottom: author (tap -> profile) + Follow / Following / Requested (`ProfileApi.ApiService.followUser`, private account = PENDING), caption 2 lines + more/less, hashtags -> `PostListScreen.hashtag`

## Data
- Prefetch: settled index >= len-3 -> next page. Fail -> Retry pill (last reel pe), agla settle bhi retry karta hai.
- Empty / error+retry / loading first page; poora page duplicate/unplayable ho to agla page khud fetch.
- Hide: reel slide-out (260ms) -> list + pool se hata, snackbar UNDO (server undo + feedback remove, reel wapas usi jagah). API fail -> slide back / re-insert.
- `?start=` video Reels me nahi mila (backend ne ignore kiya) -> `SinglePostPage` pe replace (koi aur video nahi khulta).

## Nav order (Home)
0 Home · 1 Reels · 2 Campus · 3 Classes · 4 Chat · 5 Profile  (`HomeScreen.initialIndex` abhi bhi IndexedStack index: 0 home, 2 profile)
