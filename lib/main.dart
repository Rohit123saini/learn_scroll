import 'dart:developer' as developer;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:background_downloader/background_downloader.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
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
import 'services/session_service.dart';
import 'message/services/push_notification_service.dart';
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

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

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
  try {
    await CallKitService.instance.init(navigatorKey);
    developer.log("CallKitService initialized OK");
  } catch (e) {
    developer.log("CallKitService init failed: $e");
  }

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

  // Theme preference load karo runApp se pehle taaki pehla frame hi
  // saved mode (light/dark) me render ho — flash of wrong theme na dikhe.
  try {
    await ThemeService.instance.init();
  } catch (e) {
    developer.log("ThemeService init failed: $e");
  }

  // 🔥 NAYA — saved language load karo runApp se pehle taaki pehla frame
  // hi saved language me render ho, aur `timeago` locale messages bhi
  // yahin register ho jaayein (dekhein LanguageService._registerTimeagoLocales).
  try {
    await LanguageService.instance.init();
  } catch (e) {
    developer.log("LanguageService init failed: $e");
  }

  runApp(const MyApp());
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
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
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
                '/login': (_) => const LoginScreen(),
                '/home': (_) => const HomeScreen(),
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
                    return const HomeScreen();
                  } else {
                    return const LoginScreen();
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