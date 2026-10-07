import 'dart:async';
import 'dart:developer' as developer;
import 'dart:ui' show PlatformDispatcher;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:background_downloader/background_downloader.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'utils/app_route_observer.dart'; // REELS — pause/resume playback when a route/sheet covers the Reels screen
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'firebase_options.dart';
import 'theme_service.dart';
// 🔥 NAYA (Task 2 — i18n) — language state (theme_service.dart jaisa
// pattern) + `flutter gen-l10n` se generated strings class.
// `l10n.yaml` me `synthetic-package: false` set hai isliye ye seedha
// `lib/l10n/` me generate hota hai — relative import se aa jaata hai,
// alag package import ki zaroorat nahi.
import 'language_service.dart';
import 'l10n/app_localizations.dart';
import 'login/login_screen.dart';
import 'home.dart';
// 🔥 TASK 8 — force-logout hook + app-wide navigatorKey.
import 'services/auth_service.dart';
import 'services/account_manager.dart'; // P15-FE — multi-account
import 'profile/api_service.dart' as profile_api; // P15-FE — clearProfileCache hook
import 'services/session_service.dart';
import 'services/deep_link_service.dart'; // P9-FE / TASK 11 — profile + parent-invite links, QR payloads
import 'services/activity_service.dart'; // P14-FE — foreground-time heartbeat
// 🔥 NAYA — sliding-expiry session: global 401 "TOKEN_EXPIRED"
// interceptor (backend/login/authentication.py). Ek hi jagah likha hai —
// main() ka poora async body isi ke zone ke andar chalta hai taaki app
// ke HAR http.get/post/... call (login ke alawa doosri services bhi)
// bina kisi change ke isse guzre.
import 'services/session_http_interceptor.dart';
import 'services/app_image_cache.dart'; // C5 — size-limited image cache
import 'message/services/push_notification_service.dart';
import 'message/services/inbox_socket_service.dart'; // P15-FE
import 'services/event_tracker.dart'; // P15-FE
import 'widgets/feed_video_preloader.dart'; // P15-FE
import 'message/services/call_kit_service.dart';
import 'message/services/call_manager.dart';
import 'message/screens/call_screen.dart';
import 'message/widgets/minimized_call_bar.dart';

// ⚠️ `navigatorKey` ab yahan declare NAHI hota — wo `session_service.dart`
// me hai aur is file me import se aata hai. Wajah: home.dart ko bhi wahi key
// chahiye (session expire hone pe login pe redirect karne ke liye), aur agar
// dono files apni-apni key banayein to MaterialApp sirf EK se attach hoga —
// doosri ka `.currentState` hamesha null rahega aur redirect chupchaap fail
// ho jaayega. CallKitService, call-screen push, sab wahi ek key use karte
// hain — inke liye kuch nahi badla.

// 🔧 FIX (production gap #6 — no crash reporting): backend has Sentry
// fully wired (Django + Celery), but nothing on the Flutter side ever
// reported a crash — uncaught errors just vanished. This gives every
// FATAL crash exactly one place to land, across all three of Flutter's
// separate error surfaces: inside the widget/build/layout/paint pipeline
// (FlutterError.onError), engine/platform-channel errors that land after
// the first frame and do NOT flow through FlutterError.onError
// (PlatformDispatcher.instance.onError — this was the one gap: only
// FlutterError.onError + runZonedGuarded were wired before this pass),
// and anything else that escapes every try/catch — async gaps, timers,
// stream callbacks (the runZonedGuarded handler). It logs today so
// nothing here depends on a crash-reporting package/pubspec.yaml that
// wasn't in this export; once you add `sentry_flutter` or
// `firebase_crashlytics`, report the error+stack to it inside
// `_reportCrash` below and everything upstream already routes through
// it. Non-fatal, already-handled errors (a cache read failing, a
// background refresh failing — the operation has its own fallback, so
// the app doesn't crash) go through the separate, lower-severity
// `CrashReportingService.logError()` (`services/crash_reporting_service.dart`)
// instead — same "wire this one place once the package exists" shape,
// kept apart from FATAL so a dashboard can tell the two apart later.
void _reportCrash(Object error, StackTrace stack, {bool fatal = false}) {
  // TODO: forward to Sentry/Crashlytics once the package is added, e.g.
  //   Sentry.captureException(error, stackTrace: stack);
  // or
  //   FirebaseCrashlytics.instance.recordError(error, stack, fatal: fatal);
  developer.log(
    fatal ? "FATAL crash" : "Uncaught error",
    error: error,
    stackTrace: stack,
    level: 1000,
  );
}

void main() {
  // 🔥 NAYA — poora existing async main() body ab isi wrapper ke zone ke
  // andar chalta hai, taaki us zone ke andar ho raha HAR http.get/post/...
  // call (checkSessionAlive() samet, aur har doosri service ka bhi)
  // automatically SessionAwareHttpClient se guzre — see
  // services/session_http_interceptor.dart.
  //
  // 🔧 FIX (gap #6) — this whole body now also runs inside
  // runZonedGuarded, so any error that escapes every try/catch (async
  // gaps, timers, stream callbacks) is still caught and reported instead
  // of crashing silently or just printing to console.
  runZonedGuarded(() {
    runAppWithSessionAwareClient(() async {
      WidgetsFlutterBinding.ensureInitialized();

      // C5 — cap decoded-image memory cache (disk cache is capped in
      // AppImageCache.manager).
      AppImageCache.configureMemoryCache();

      // 🔧 FIX (gap #6) — Flutter's own error pipeline (build/layout/paint,
      // gesture callbacks, etc.) does NOT flow through runZonedGuarded by
      // default; it has to be hooked separately here.
      FlutterError.onError = (FlutterErrorDetails details) {
        FlutterError.presentError(details);
        _reportCrash(details.exception, details.stack ?? StackTrace.current,
            fatal: true);
      };

      // 🔧 FIX (gap #6, previously missing) — errors that reach the engine
      // after the first frame (platform-channel callbacks, some async
      // engine-level errors) go through NEITHER FlutterError.onError NOR
      // runZonedGuarded's error handler — PlatformDispatcher.instance.onError
      // is the only hook that sees those. Returning `true` tells the
      // engine this error has been handled (matches FlutterError.onError's
      // behavior above — otherwise the engine would additionally dump it
      // to stderr and, on some platforms, still treat it as unhandled).
      PlatformDispatcher.instance.onError = (error, stack) {
        _reportCrash(error, stack, fatal: true);
        return true;
      };

  // TASK G17 (growth_and_feature_tasks.md) — app startup time. This used
  // to `await` AudioPlayer-context / Firebase.initializeApp() / CallKit /
  // PushNotificationService / FileDownloader / WakelockPlus ONE AFTER
  // ANOTHER before the very first frame ever rendered — Firebase alone is
  // a platform-channel round trip that can be genuinely slow on a cold
  // start, for reasons that have nothing to do with what that first
  // frame actually shows. None of those six steps affect the first
  // frame's appearance, so they're kicked off here in the background
  // (still inside this same session-aware-HTTP zone) and NOT awaited —
  // every call site that depends on one of them (CallKitService,
  // PushNotificationService, FileDownloader) already guards itself with
  // its own try/catch elsewhere in the app, exactly as it did when this
  // init could fail for other reasons before this pass.
  unawaited(_initBackgroundServices());

  // Theme + language DO affect the first frame (a saved dark-mode user
  // seeing a light flash, or the wrong language for a beat, is a real
  // regression) — so, and only these two, still block runApp(). They
  // don't depend on each other, so load them in parallel rather than one
  // after another.
  await Future.wait([
    () async {
      try {
        await ThemeService.instance.init();
      } catch (e) {
        developer.log("ThemeService init failed: $e");
      }
    }(),
    () async {
      try {
        await LanguageService.instance.init();
      } catch (e) {
        developer.log("LanguageService init failed: $e");
      }
    }(),
  ]);

      runApp(const MyApp());
    });
  }, (error, stack) {
    // Catches anything that escapes the zone above (async errors outside
    // any try/catch, stream/timer callbacks, isolate-level errors).
    _reportCrash(error, stack, fatal: true);
  });
}

// 🔥 N8-FE — push tap (foreground / background / terminated) -> screen.
// Cold start me MaterialApp ka navigator thodi der baad ready hota hai,
// isliye max ~5s tak wait.
Future<void> _openFromPush(Map<String, dynamic> data) async {
  for (var i = 0; i < 25 && navigatorKey.currentState == null; i++) {
    await Future.delayed(const Duration(milliseconds: 200));
  }
  if (navigatorKey.currentState == null) return;

  // TODO(N7): yahan apni N7 deep-link routing call karo — `data` me
  // notif_type, post_id, comment_id, story_id, series_id, actor_id,
  // actor_username, conversation_id, classroom_id, session_id aate hain
  // (core/services.py PUSH_DATA_KEYS). Jab tak N7 wired nahi, sirf chat/
  // mention taps (conversation_id) purane tarah khulte hain.
  final convId = data['conversation_id']?.toString();
  if (convId != null && convId.isNotEmpty) {
    PushNotificationService.instance.onNotificationTap?.call(convId);
  }
}

// TASK G17 — everything here used to run sequentially, one `await` after
// another, before runApp() (see the single call site above for why that
// changed). Kept in its own function purely for readability; the
// ordering/dependency between steps (Firebase before CallKit/Push — see
// the "ROOT-CAUSE FIX" comment below, unchanged from before this pass) and
// every individual try/catch are exactly what they were, just no longer
// blocking the first frame.
Future<void> _initBackgroundServices() async {
  try {
    await AudioPlayer.global.setAudioContext(
      AudioContext(
        android: AudioContextAndroid(
          isSpeakerphoneOn: true,
          stayAwake: true,
          contentType: AndroidContentType.sonification,
          usageType: AndroidUsageType.voiceCommunication,
          audioFocus: AndroidAudioFocus.gain,
        ),
        iOS: AudioContextIOS(
          // 🔥 FIX: `defaultToSpeaker` sirf `playAndRecord` category ke
          // saath valid hai — `playback` ke saath ye assertion throw karta
          // tha (chahe Android pe ho ya iOS pe), jo silently is try/catch
          // me pakda jaa raha tha.
          category: AVAudioSessionCategory.playAndRecord,
          options: {
            AVAudioSessionOptions.defaultToSpeaker,
            AVAudioSessionOptions.mixWithOthers,
          },
        ),
      ),
    );
  } catch (e) {
    developer.log("AudioPlayer global config failed: $e");
  }

  // 🔥 ROOT-CAUSE FIX: pehle Firebase.initializeApp() + PushNotificationService
  // + CallKitService — teeno EK hi try/catch me the. Agar PushNotificationService
  // ke init me kahin bhi exception aata (kisi bhi device pe, kabhi bhi), to
  // CallKitService.instance.init() us try-chain ke baad hona ki wajah se
  // KABHI chalta hi nahi tha. CallKitService.init() hi POST_NOTIFICATIONS
  // permission maangta hai aur CallKit ke accept/decline events sunta hai —
  // iske bina native incoming-call popup poori app me kahin nahi dikhta,
  // sirf chat_screen ka apna socket-based fallback dialog dikhta (jo sirf
  // us particular chat ki websocket khuli hone par kaam karta hai). Yahi
  // wajah thi "chat screen pe calling aati hai, kahin aur nahi".
  //
  // Ab teeno steps independent hain — ek fail ho to baaki phir bhi chalte hain.

  bool firebaseReady = false;
  try {
    await Firebase.initializeApp(
      options: DefaultFirebaseOptions.currentPlatform,
    );
    FirebaseMessaging.onBackgroundMessage(firebaseBackgroundHandler);
    firebaseReady = true;
  } catch (e) {
    developer.log("Firebase init failed: $e");
  }

  // CallKitService ko Firebase ki zaroorat nahi (ye sirf navigatorKey +
  // notification permission + local event listener use karta hai) —
  // isliye Firebase fail ho jaaye tab bhi ye chalna chahiye taaki agar
  // koi aur rasta (jaise chat_screen ka socket fallback) call event bhejta
  // hai to bhi CallKit popup ka infra ready rahe.
  //
  // TASK G17 — this runs after runApp() now instead of before it, so
  // `navigatorKey` is passed the same GlobalKey instance as always but its
  // `.currentState` will still be null for a brief moment until the first
  // frame mounts MaterialApp. That's fine: CallKitService only reads
  // `.currentState` later, in response to an actual incoming-call event,
  // by which point the app has always finished its first frame.
  try {
    await CallKitService.instance.init(navigatorKey);
    developer.log("CallKitService initialized OK");
  } catch (e) {
    developer.log("CallKitService init failed: $e");
  }

  // 🔥 N8-FE — rich-push body tap yahin se app me navigate karta hai.
  // init() se PEHLE set karna zaroori hai taaki cold-start tap miss na ho
  // (hold hua tap setter me hi flush ho jaata hai).
  PushNotificationService.instance.onPushNavigate = _openFromPush;

  if (firebaseReady) {
    try {
      await PushNotificationService.instance.init();
      developer.log("PushNotificationService initialized OK");
    } catch (e) {
      developer.log("PushNotificationService init failed: $e");
    }
  } else {
    developer.log(
        "PushNotificationService skipped: Firebase not ready (no FCM push -> incoming calls/messages won't arrive in background/killed state, sirf app foreground + socket fallback kaam karega)");
  }

  try {
    await FileDownloader().start();
    await FileDownloader().configureNotification(
      running: TaskNotification('Downloading {filename}', '{progress}'),
      complete: TaskNotification('Download complete', '{filename}'),
      error: TaskNotification('Download failed', '{filename}'),
      progressBar: true,
      tapOpensFile: true,
      groupNotificationId: "learnscroll.downloads",
    );

    FileDownloader().registerCallbacks(
      taskNotificationTapCallback: (task, notificationType) {
        developer.log("Notification tapped: $notificationType for ${task.filename}");
      },
    );
  } catch (e) {
    developer.log("FileDownloader init failed: $e");
  }

  await WakelockPlus.disable();
}

class MyApp extends StatefulWidget {
  const MyApp({super.key});
  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);

    // 🔥 TASK 8.2 — refresh-token khud mar jaaye to AuthService._doRefresh()
    // ye callback maarta hai. Yahan se seedha navigate NAHI karna: home
    // screen ko pehle 3 second ka "Session expired" banner dikhana hai, wo
    // logic home.dart me hai. Yahan sirf global flag set hota hai.
    AuthService.onForceLogout = () {
      SessionService.markExpired();
    };

    // 🔥 P15-FE — per-user state that must be reset on account switch.
    // LEAVE hooks run while the OUTGOING token is still valid; ENTER hooks
    // run once the INCOMING token is installed. Anything user-scoped that
    // lives in a singleton belongs here.
    AccountManager.instance
      // ---- LEAVE: old user's token is still valid here ----
      ..addLeaveHook(() async {
        // Send the old user's buffered analytics under the OLD token, then
        // drop the queue so none of it is ever sent under the next account.
        await EventTracker.instance.flush(force: true);
        await EventTracker.instance.clear();
      })
      ..addLeaveHook(() async {
        // Old user must stop receiving pushes on this device: delete the
        // device-token row server-side (old bearer), then rotate the local
        // FCM token so the old registration is dead even if that call fails.
        await PushNotificationService.instance.unregisterToken();
        try {
          await FirebaseMessaging.instance.deleteToken();
        } catch (_) {}
      })
      ..addLeaveHook(() async {
        InboxSocketService.instance.disconnect();
        FeedVideoPreloader.instance.clear();
      })
      ..addLeaveHook(() async {
        await profile_api.ApiService.clearProfileCache();
      })
      // ---- ENTER: new user's token is installed ----
      ..addEnterHook(() async {
        await PushNotificationService.instance.registerToken();
      })
      ..addEnterHook(() async {
        // Inbox socket is a global singleton (chat sockets are per-screen
        // and die with the old route stack). conversations_screen also
        // calls connect() — it is idempotent.
        unawaited(InboxSocketService.instance.connect());
        unawaited(ThemeService.instance.resyncFromBackend());
        unawaited(LanguageService.instance.resyncFromBackend());
      });

    // 🔥 NAYA — app start par active session-check (fire-and-forget,
    // startup ko block nahi karna). Local token expiry check ke saath-
    // saath ek lightweight authenticated ping bhi, taaki 10-din-inactivity
    // wali sliding-expiry turant pakdi jaaye — agle kisi screen-specific
    // API call ka intezaar nahi karna padta. Result khud interceptor
    // (SessionAwareHttpClient) handle karta hai.
    AuthService.checkSessionAlive();

    // P9-FE — start listening for learnscroll://u/<username> (cold-start link + live stream).
    // Fire-and-forget; a link that arrives before the app is ready is held and opened by
    // DeepLinkGate below once HomeScreen is on screen.
    unawaited(DeepLinkService.instance.init());

    // P14-FE — time-spent heartbeat (every 60 s while the app is in the
    // foreground; lifecycle callbacks below start/stop it). On a cold start
    // no lifecycle event fires, so start it here if we're already visible.
    ActivityHeartbeat.instance.onLimitReached = _showDailyLimitReached;
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    if (lifecycle == null || lifecycle == AppLifecycleState.resumed) {
      ActivityHeartbeat.instance.onForeground();
    }
  }

  @override
  void dispose() {
    ActivityHeartbeat.instance.onBackground(); // P14-FE
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // P14-FE — the server says this beat crossed the user's daily limit
  // (once a day). A SnackBar via the global navigator's context; if no
  // route is mounted yet it's simply skipped (the bell notification the
  // backend also writes still lands).
  void _showDailyLimitReached() {
    final ctx = navigatorKey.currentContext;
    if (ctx == null) return;
    ScaffoldMessenger.maybeOf(ctx)?.showSnackBar(
      const SnackBar(
        content: Text('\u23f0 You\u2019ve reached your daily time limit on LearnScroll.'),
        duration: Duration(seconds: 6),
      ),
    );
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // P14-FE — count only time the app is actually visible. `inactive` is a
    // transient state (permission dialog, notification shade, incoming call
    // overlay) and is deliberately ignored; paused/hidden/detached stop the
    // clock.
    switch (state) {
      case AppLifecycleState.resumed:
        ActivityHeartbeat.instance.onForeground();
        break;
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
      case AppLifecycleState.detached:
        ActivityHeartbeat.instance.onBackground();
        break;
      case AppLifecycleState.inactive:
        break;
    }

    if (state == AppLifecycleState.resumed) {
      // 🔥 NAYA — app resume (background -> foreground) par bhi wahi
      // active session-check, jaisa app-start par hota hai. Yahi wo
      // jagah hai jahan "10 din tak app khula hi nahi" wala case sabse
      // pehle pakda ja sakta hai — user ke resumed screen pe kuch tap
      // karne se pehle hi.
      AuthService.checkSessionAlive();
      DeepLinkService.instance.flushPending(); // P9-FE — e.g. link tapped while logged out, then logged in

      final cm = CallManager.instance;
      if (cm.isActive && cm.isMinimized) {
        cm.unminimize();
        navigatorKey.currentState?.push(MaterialPageRoute(
          builder: (_) => CallScreen(
            callId: cm.callId ?? '',
            conversationId: cm.conversationId ?? '',
            isVideo: cm.isVideo,
            isCaller: cm.isCaller,
            livekitUrl: '',
            livekitToken: '',
            peerName: cm.peerName,
            peerAvatar: cm.peerAvatar,
          ),
        ));
      }
    }
  }

  Future<bool> _checkAuth() async {
    try {
      // P15-FE — load vault; recover if the app was killed mid "Add account".
      await AccountManager.instance.init();
      await AccountManager.instance.restoreIfInterrupted();
      final prefs = await SharedPreferences.getInstance();
      final String? token = prefs.getString('access_token');
      return token != null && token.isNotEmpty;
    } catch (e) {
      return false;
    }
  }

  @override
  Widget build(BuildContext context) {
    // 🔥 NAYA — theme_service.dart ke ValueNotifier<ThemeMode> ko sunte
    // hain taaki ThemeService.instance.toggle()/setThemeMode() call hote
    // hi poori app turant rebuild ho jaaye, bina context lookup ke.
    // Purana hardcoded `theme: ThemeData(scaffoldBackgroundColor: Color(0xFF0F0F11))`
    // yahin se AppTheme.dark (design tokens ke saath) me replace hua hai.
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: ThemeService.instance.themeMode,
      builder: (context, mode, child) {
        // 🔥 NAYA (Task 2 — i18n) — `ThemeMode` ValueListenableBuilder ke
        // andar hi `Locale` waala nest kiya hai (bilkul same pattern) taaki
        // `LanguageService.instance.setLocale()` call hote hi bhi poori app
        // turant naye language me rebuild ho jaaye — koi context lookup
        // ya extra InheritedWidget ki zaroorat nahi.
        return ValueListenableBuilder<Locale>(
          valueListenable: LanguageService.instance.locale,
          builder: (context, locale, child) {
            return MaterialApp(
              navigatorKey: navigatorKey,
              navigatorObservers: [appRouteObserver], // REELS — RouteAware (pause under sheets/routes)
              debugShowCheckedModeBanner: false,
              title: 'LearnScroll App',
              theme: AppTheme.light,
              darkTheme: AppTheme.dark,
              themeMode: mode,
              // 🔥 NAYA — i18n wiring. `locale:` LanguageService se bound
              // hai, `supportedLocales` waahi 10-language list hai jo
              // language_service.dart me maintain hoti hai.
              localizationsDelegates: const [
                AppLocalizations.delegate,
                GlobalMaterialLocalizations.delegate,
                GlobalWidgetsLocalizations.delegate,
                GlobalCupertinoLocalizations.delegate,
              ],
              supportedLocales: LanguageService.supportedLocales,
              locale: locale,
              // 🔥 TASK 8.3 — home.dart session expire hone pe
              // `pushNamedAndRemoveUntil('/login', ...)` call karta hai,
              // isliye ye named route register hona ZAROORI hai. Pehle
              // `routes:` map tha hi nahi, to wo call exception deti.
              routes: {
                '/login': (_) => const DeepLinkGate(child: LoginScreen()), // TASK 11 — loginless parent links
                '/home': (_) => const DeepLinkGate(child: HomeScreen()), // P9-FE
              },
              // 🔥 NAYA — WhatsApp-style floating call bar jo call minimize karne
              // ke baad app ke UPAR, kisi bhi screen pe, hamesha dikhta hai.
              builder: (context, child) {
                return Stack(
                  children: [
                    if (child != null) child,
                    const MinimizedCallBar(),
                  ],
                );
              },
              home: FutureBuilder<bool>(
                future: _checkAuth(),
                builder: (context, snapshot) {
                  // Pehle `AsyncSnapshot.waiting().connectionState` likha tha
                  // — kaam to karta tha (ek throwaway snapshot bana ke uska
                  // enum padhna), par seedha enum compare karna wahi cheez
                  // saaf tarike se karta hai.
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Scaffold(body: Center(child: CircularProgressIndicator()));
                  }
                  if (snapshot.hasData && snapshot.data == true) {
                    return const DeepLinkGate(child: HomeScreen()); // P9-FE
                  } else {
                    return const DeepLinkGate(child: LoginScreen()); // TASK 11 — loginless parent links
                  }
                },
              ),
            );
          },
        );
      },
    );
  }
}