// import 'package:easy_audience_network_plus/easy_audience_network.dart';
// import 'package:flutter/material.dart';

// void main() => runApp(AdExampleApp());

// class AdExampleApp extends StatelessWidget {
//   @override
//   Widget build(BuildContext context) {
//     return MaterialApp(
//       debugShowCheckedModeBanner: false,
//       title: 'Audience Network Example',
//       theme: ThemeData(
//         primarySwatch: Colors.blue,
//         buttonTheme: ButtonThemeData(
//           textTheme: ButtonTextTheme.primary,
//           buttonColor: Colors.blue,
//         ),
//       ),
//       home: Scaffold(
//         appBar: AppBar(
//           title: Text(
//             'Audience Network Example',
//           ),
//         ),
//         body: AdsPage(),
//       ),
//     );
//   }
// }

// class AdsPage extends StatefulWidget {
//   final String idfa;

//   const AdsPage({Key? key, this.idfa = ''}) : super(key: key);

//   @override
//   AdsPageState createState() => AdsPageState();
// }

// class AdsPageState extends State<AdsPage> {
//   bool _isInterstitialAdLoaded = false;
//   bool _isRewardedAdLoaded = false;
//   InterstitialAd? _interstitialAd;
//   RewardedAd? _rewardedAd;

//   /// All widget ads are stored in this variable. When a button is pressed, its
//   /// respective ad widget is set to this variable and the view is rebuilt using
//   /// setState().
//   Widget _currentAd = SizedBox(
//     width: 0.0,
//     height: 0.0,
//   );

//   @override
//   void initState() {
//     super.initState();

//     // testingId is useful when you want to test if your implementation works in production
//     // without getting real ads, I believe it does not work properly on iOS,
//     // if you want to get your testingId, don't set any testingId and don't set testMode
//     EasyAudienceNetwork.init(
//       testingId: "b602d594afd2b0b327e07a06f36ca6a7e42546d0",
//       testMode: true,
//       iOSAdvertiserTrackingEnabled: true,
//     ).then((_) {
//       _loadInterstitialAd();
//       _loadRewardedVideoAd();
//     });
//   }

//   void _loadInterstitialAd() {
//     final interstitialAd = InterstitialAd(InterstitialAd.testPlacementId);
//     interstitialAd.listener = InterstitialAdListener(
//       onLoaded: () {
//         _isInterstitialAdLoaded = true;
//         print('interstitial ad loaded');
//       },
//       onError: (code, message) {
//         print('interstitial ad error\ncode = $code\nmessage = $message');
//       },
//       onDismissed: () {
//         // load next ad already
//         interstitialAd.destroy();
//         _isInterstitialAdLoaded = false;
//         _loadInterstitialAd();
//       },
//     );
//     interstitialAd.load();
//     _interstitialAd = interstitialAd;
//   }

//   void _loadRewardedVideoAd() {
//     final rewardedAd = RewardedAd(RewardedAd.testPlacementId);
//     rewardedAd.listener = RewardedAdListener(
//       onLoaded: () {
//         _isRewardedAdLoaded = true;
//         print('rewarded ad loaded');
//       },
//       onError: (code, message) {
//         print('rewarded ad error\ncode = $code\nmessage = $message');
//       },
//       onVideoClosed: () {
//         // load next ad already
//         rewardedAd.destroy();
//         _isRewardedAdLoaded = false;
//         _loadRewardedVideoAd();
//       },
//     );
//     rewardedAd.load();
//     _rewardedAd = rewardedAd;
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Column(
//       mainAxisAlignment: MainAxisAlignment.start,
//       crossAxisAlignment: CrossAxisAlignment.center,
//       children: <Widget>[
//         Flexible(
//           child: Align(
//             alignment: Alignment(0, -1.0),
//             child: Padding(
//               padding: EdgeInsets.all(16),
//               child: _getAllButtons(),
//             ),
//           ),
//           fit: FlexFit.tight,
//           flex: 2,
//         ),
//         // Column(children: <Widget>[
//         //   _nativeAd(),
//         //   // _nativeBannerAd(),
//         //   _nativeAd(),
//         // ],),
//         Flexible(
//           child: Align(
//             alignment: Alignment(0, 1.0),
//             child: _currentAd,
//           ),
//           fit: FlexFit.tight,
//           flex: 3,
//         )
//       ],
//     );
//   }

//   Widget _getAllButtons() {
//     return GridView.count(
//       shrinkWrap: true,
//       crossAxisCount: 2,
//       childAspectRatio: 3,
//       children: <Widget>[
//         _getRaisedButton(title: "Banner Ad", onPressed: _showBannerAd),
//         _getRaisedButton(title: "Native Ad", onPressed: _showNativeAd),
//         _getRaisedButton(
//             title: "Native Banner Ad", onPressed: _showNativeBannerAd),
//         _getRaisedButton(
//             title: "Intestitial Ad", onPressed: _showInterstitialAd),
//         _getRaisedButton(title: "Rewarded Ad", onPressed: _showRewardedAd),
//       ],
//     );
//   }

//   Widget _getRaisedButton({required String title, void Function()? onPressed}) {
//     return Padding(
//       padding: EdgeInsets.all(8),
//       child: ElevatedButton(
//         onPressed: onPressed,
//         child: Text(
//           title,
//           textAlign: TextAlign.center,
//         ),
//       ),
//     );
//   }

//   _showInterstitialAd() {
//     final interstitialAd = _interstitialAd;

//     if (interstitialAd != null && _isInterstitialAdLoaded == true)
//       interstitialAd.show();
//     else
//       print("Interstial Ad not yet loaded!");
//   }

//   _showRewardedAd() {
//     final rewardedAd = _rewardedAd;

//     if (rewardedAd != null && _isRewardedAdLoaded) {
//       rewardedAd.show();
//     } else {
//       print("Rewarded Ad not yet loaded!");
//     }
//   }

//   _showBannerAd() {
//     setState(() {
//       _currentAd = BannerAd(
//         placementId: BannerAd.testPlacementId,
//         bannerSize: BannerSize.STANDARD,
//         listener: BannerAdListener(
//           onError: (code, message) =>
//               print('banner ad error\ncode: $code\nmessage:$message'),
//           onLoaded: () => print('banner ad loaded'),
//         ),
//       );
//     });
//   }

//   _showNativeBannerAd() {
//     setState(() {
//       _currentAd = _nativeBannerAd();
//     });
//   }

//   Widget _nativeBannerAd() {
//     return NativeAd(
//       placementId: NativeAd.testPlacementId,
//       adType: NativeAdType.NATIVE_BANNER_AD,
//       bannerAdSize: NativeBannerAdSize.HEIGHT_100,
//       width: double.infinity,
//       backgroundColor: Colors.blue,
//       titleColor: Colors.white,
//       descriptionColor: Colors.white,
//       buttonColor: Colors.deepPurple,
//       buttonTitleColor: Colors.white,
//       buttonBorderColor: Colors.white,
//       listener: NativeAdListener(
//         onError: (code, message) =>
//             print('native banner ad error\ncode: $code\nmessage:$message'),
//         onLoaded: () => print('native banner ad loaded'),
//         onMediaDownloaded: () => 'native banner ad media downloaded',
//       ),
//     );
//   }

//   _showNativeAd() {
//     setState(() {
//       _currentAd = _nativeAd();
//     });
//   }

//   Widget _nativeAd() {
//     return NativeAd(
//       placementId: NativeAd.testPlacementId,
//       adType: NativeAdType.NATIVE_AD_VERTICAL,
//       width: double.infinity,
//       height: 300,
//       backgroundColor: Colors.blue,
//       titleColor: Colors.white,
//       descriptionColor: Colors.white,
//       buttonColor: Colors.deepPurple,
//       buttonTitleColor: Colors.white,
//       buttonBorderColor: Colors.white,
//       listener: NativeAdListener(
//         onError: (code, message) =>
//             print('native ad error\ncode: $code\nmessage:$message'),
//         onLoaded: () => print('native ad loaded'),
//         onMediaDownloaded: () => 'native ad media downloaded',
//       ),
//       keepExpandedWhileLoading: true,
//       expandAnimationDuraion: 1000,
//     );
//   }
// }






























// import 'package:flutter/material.dart';
// import 'package:shared_preferences/shared_preferences.dart';
// import 'package:background_downloader/background_downloader.dart';
// import 'login/login_screen.dart';
// import 'home.dart';

// void main() async {
//   WidgetsFlutterBinding.ensureInitialized();
//   await FileDownloader().start();
//   await FileDownloader().configureNotification(
//     running: TaskNotification('Downloading {filename}', '{progress}'),
//     complete: TaskNotification('Download complete', '{filename}'),
//     error: TaskNotification('Download failed', '{filename}'),
//     progressBar: true,
//     tapOpensFile: true,
//   );
//   runApp(const MyApp());
// }

// class MyApp extends StatelessWidget {
//   const MyApp({super.key});
//   Future<bool> _checkAuth() async {
//     final prefs = await SharedPreferences.getInstance();
//     final String? token = prefs.getString('access_token');
//     return token!= null && token.isNotEmpty;
//   }
//   @override
//   Widget build(BuildContext context) {
//     return MaterialApp(
//       debugShowCheckedModeBanner: false,
//       title: 'LearnScroll App',
//       home: FutureBuilder<bool>(
//         future: _checkAuth(),
//         builder: (context, snapshot) {
//           if (snapshot.connectionState == ConnectionState.waiting) {
//             return const Scaffold(body: Center(child: CircularProgressIndicator()));
//           }
//           if (snapshot.hasData && snapshot.data == true) {
//             return const HomeScreen();
//           } else {
//             return const LoginScreen();
//           }
//         },
//       ),
//     );
//   }
// }





































import 'dart:developer' as developer;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:background_downloader/background_downloader.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:audioplayers/audioplayers.dart';
import 'firebase_options.dart';
import 'login/login_screen.dart';
import 'home.dart';
import 'message/services/push_notification_service.dart';
import 'message/services/call_kit_service.dart';
import 'message/services/call_manager.dart';
import 'message/screens/call_screen.dart';
import 'message/widgets/minimized_call_bar.dart';

final navigatorKey = GlobalKey<NavigatorState>();

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
    return MaterialApp(
      navigatorKey: navigatorKey,
      debugShowCheckedModeBanner: false,
      title: 'LearnScroll App',
      theme: ThemeData(
        useMaterial3: true,
        scaffoldBackgroundColor: const Color(0xFF0F0F11),
      ),
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
          if (snapshot.connectionState == AsyncSnapshot.waiting().connectionState) {
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
  }
}