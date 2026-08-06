// message/services/call_kit_service.dart
//
// 🔥 Instagram/WhatsApp jaisa NATIVE incoming-call popup dikhane ke liye.
// `flutter_callkit_incoming` Android par apni khud ki full-screen
// notification/Telecom UI use karta hai — isliye ye kaam karta hai chahe app:
//   - foreground me ho
//   - background me ho
//   - completely killed/terminated ho
// (jab tak FCM data-message device tak pahunch jaaye)
//
// ⚠️ PACKAGE VERSION — ye zaroori hai, warna full-screen popup nahi aayega:
// `canUseFullScreenIntent()` / `requestFullIntentPermission()` (Android 14+
// full-screen-intent fix, neeche dekho) sirf `flutter_callkit_incoming`
// >= 2.5.0 me maujood hain. pubspec.yaml me EXACT pin rakho:
//
//   dependencies:
//     flutter_callkit_incoming: 2.5.0+2
//     uuid: ^4.5.1
//
// Agar `flutter pub get` phir bhi koi aur version resolve kare (kisi doosri
// dependency ki wajah se), to pubspec.yaml me ye bhi add karo:
//
//   dependency_overrides:
//     flutter_callkit_incoming: 2.5.0+2
//
// Uske baad zaroor chalao (sirf hot-restart kaafi NAHI hai):
//   flutter clean
//   flutter pub get
//   flutter run   (device se app pehle PURA uninstall karke)
//
// Uninstall+reinstall zaroori hai kyunki Android notification channels
// (neeche `incomingCallNotificationChannelName` dekho) ek baar ban jaayein
// to immutable hote hain — purana install already ek "silent/no full-screen"
// channel bana chuka hoga jo code se overwrite nahi hota.

import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter_callkit_incoming/flutter_callkit_incoming.dart';
import 'package:flutter_callkit_incoming/entities/entities.dart';
import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

// ⚠ Ye file `message/services/` folder me rakhni hai (jahan
// push_notification_service.dart/call_api_service.dart hain).
import '../screens/call_screen.dart';
import 'call_api_service.dart';

class CallKitService {
  CallKitService._();
  static final instance = CallKitService._();

  /// main.dart me `navigatorKey` set karke yahan pass karo, taaki
  /// background/notification-tap se bhi CallScreen tak navigate kar sakein
  /// (bina BuildContext ke).
  static GlobalKey<NavigatorState>? navigatorKey;

  /// Debug/QA ke liye — init() ke baad ye batata hai ki Android ne
  /// full-screen-intent permission diya hai ya nahi. `null` = abhi check
  /// nahi hua ya platform iOS hai (jahan ye concept apply nahi hota).
  static bool? lastKnownFullScreenIntentGranted;

  StreamSubscription? _eventSub;

  /// App start hote hi (MyApp ke initState me) ek baar call karo.
  Future<void> init(GlobalKey<NavigatorState> navKey) async {
    navigatorKey = navKey;

    // Android 13+ : notification permission zaroori hai warna popup nahi dikhega
    try {
      await FlutterCallkitIncoming.requestNotificationPermission({
        "title": "Notification Permission",
        "rationaleMessagePermission":
            "Incoming calls dikhane ke liye notification permission chahiye.",
        "postNotificationMessageRequired":
            "Please settings me jaake notification permission allow karo.",
      });
    } catch (e) {
      developer.log("CallKit notification permission error: $e");
    }

    await _ensureFullScreenIntentPermission();

    await _eventSub?.cancel();
    _eventSub = FlutterCallkitIncoming.onEvent.listen(_onCallKitEvent);

    // App terminated state se khula ho aur pehle se koi accepted call ho
    // (rare edge-case), use resume karne ke liye.
    unawaited(_checkPendingCallOnLaunch());
  }

  // ROOT CAUSE FIX of "popup aata hai par chhota/normal notification hota
  // hai, full-screen nahi": Android 14 (API 34)+ pe akele
  // USE_FULL_SCREEN_INTENT manifest permission kaafi nahi hai — user ko
  // Settings me jaake ise EXPLICITLY allow karna padta hai, warna Android
  // khud call ko chhote heads-up notification me downgrade kar deta hai
  // (koi accept/decline full-screen UI nahi).
  //
  // Ye method sirf pub package >= 2.5.0 ke saath kaam karega — header
  // comment dekho.
  Future<void> _ensureFullScreenIntentPermission() async {
    try {
      final canFullScreen = await FlutterCallkitIncoming.canUseFullScreenIntent();
      lastKnownFullScreenIntentGranted = canFullScreen == true;

      if (canFullScreen != true) {
        developer.log(
            "CallKit: full-screen-intent NOT granted yet — opening system settings for user to allow it manually.");
        // Ye seedha system Settings screen kholta hai — user ko manually
        // "Allow full screen notifications" ON karna hoga is app ke liye.
        // Koi bhi code se ise auto-grant nahi kiya ja sakta (Android policy),
        // isliye is call ka koi return value nahi hota jo turant "granted"
        // confirm kare — agli baar init() chalne par (jaise app restart)
        // re-check ho jaayega.
        await FlutterCallkitIncoming.requestFullIntentPermission();
      } else {
        developer.log("CallKit: full-screen-intent already granted.");
      }
    } catch (e) {
      // Agar ye methods hi missing hain (purana package version resolve
      // hua), to yahan exception aayega — is case me full-screen popup
      // KABHI nahi dikhega jab tak package upgrade na ho. Loudly log karo
      // taaki QA/dev ko turant pata chale, silent fail na ho.
      developer.log(
          "CallKit: full-screen-intent check/request FAILED — is flutter_callkit_incoming >= 2.5.0 installed? Error: $e");
    }
  }

  void dispose() {
    _eventSub?.cancel();
  }

  // ============================================================
  // FCM se yahan call karo jab data['type'] == 'incoming_call' ho
  // (foreground listener aur background handler dono se) — data me
  // kam se kam ye keys honi chahiye: call_id, caller_name, call_type,
  // conversation_id, channel_name
  // ============================================================
  static Future<void> showIncomingCall(Map<String, dynamic> data) async {
    final callId = data['call_id']?.toString();
    if (callId == null) return;

    final callerName = data['caller_name']?.toString() ?? 'Unknown';
    final callType = data['call_type']?.toString() ?? 'audio';
    final isVideo = callType == 'video';
    final uuid = const Uuid().v4();

    // Backend agar FCM payload me `caller_avatar` bheje to wahi dikhega,
    // warna koi background photo nahi (solid color) — jaise WhatsApp bhi
    // photo na hone par sirf initials/solid background dikhata hai, koi
    // random placeholder photo nahi.
    final callerAvatarUrl = data['caller_avatar']?.toString();
    final hasAvatar = callerAvatarUrl != null && callerAvatarUrl.isNotEmpty;

    final params = CallKitParams(
      id: uuid,
      nameCaller: callerName,
      appName: 'LearnScroll',
      avatar: hasAvatar ? callerAvatarUrl : null,
      handle: isVideo ? 'Video Call' : 'Voice Call',
      type: isVideo ? 1 : 0,
      duration: 30000, // 30 sec tak ring hoga — caller side CallManager ke 30s no-answer auto-hangup se sync
      textAccept: 'Accept',
      textDecline: 'Decline',
      missedCallNotification: const NotificationParams(
        showNotification: true,
        isShowCallback: false,
        subtitle: 'Missed call',
      ),
      extra: <String, dynamic>{
        'call_id': callId,
        'conversation_id': data['conversation_id']?.toString() ?? '',
        'channel_name': data['channel_name']?.toString() ?? '',
        'call_type': callType,
        'caller_name': callerName,
        'caller_avatar': callerAvatarUrl ?? '',
      },
      android: AndroidParams(
        isCustomNotification: true,
        isShowLogo: false,
        // system_ringtone_default hi bajega, yehi sahi hai. Custom mp3
        // bajana ho to use `android/app/src/main/res/raw/` me daalna
        // padta hai aur yahan `ringtonePath: 'raw_incoming_ring'` likhna
        // padta hai.
        ringtonePath: 'system_ringtone_default',
        backgroundColor: '#0F0F11',
        backgroundUrl: hasAvatar ? callerAvatarUrl : null,
        actionColor: '#25D366', // WhatsApp-style accept green
        // Android me notification channel ek baar ban jaaye to uska
        // sound/full-screen setting CODE SE dobara change NAHI hoti —
        // channel immutable hai. Naam "v4" rakha hai (v3 se bump) taaki
        // is fix ke baad Android FORCE se ek bilkul naya channel banaye —
        // purana channel (agar kisi purane test-install se already bana
        // hua ho) ignore ho jaaye.
        // ⚠ Ye tabhi effect karega jab app FRESH install ho (uninstall
        // karke phir install, sirf hot-restart se nahi).
        incomingCallNotificationChannelName: "Incoming Calls v4",
        missedCallNotificationChannelName: "Missed Calls v4",
        isShowFullLockedScreen: true,
      ),
      ios: const IOSParams(
        iconName: 'CallKitLogo',
        handleType: 'generic',
        supportsVideo: true,
        maximumCallGroups: 1,
        maximumCallsPerCallGroup: 1,
        audioSessionMode: 'default',
        audioSessionActive: true,
        audioSessionPreferredSampleRate: 44100.0,
        audioSessionPreferredIOBufferDuration: 0.005,
        supportsDTMF: true,
        supportsHolding: true,
        supportsGrouping: false,
        supportsUngrouping: false,
        ringtonePath: 'system_ringtone_default',
      ),
    );

    try {
      await FlutterCallkitIncoming.showCallkitIncoming(params);
    } catch (e) {
      // Agar native popup show hi nahi ho paaya (permission missing,
      // plugin error, etc), silently mat chhodo — kam se kam log to karo
      // taaki crash reports/QA se pakda ja sake. Caller (background
      // handler) already best-effort hai isliye yahan rethrow nahi karte.
      developer.log("CallKit: showCallkitIncoming failed: $e");
    }
  }

  /// Har CallKit call-entry ko safely Map me convert karta hai — kuch
  /// resolved sub-versions me `activeCalls()` List<Map> deta hai to kuch me
  /// List<dynamic> jisme har item Map jaisa hi hota hai. Ye helper dono
  /// case me kaam karega aur agar shape unexpected ho to crash nahi karega.
  static Map<String, dynamic>? _asMap(dynamic item) {
    if (item is Map) return Map<String, dynamic>.from(item);
    return null;
  }

  /// Jab call backend se end/reject ho jaaye (dusre side se), CallKit UI
  /// bhi hata do — warna popup screen pe atka reh jaayega.
  static Future<void> endCallUiByCallId(String callId) async {
    try {
      final calls = await FlutterCallkitIncoming.activeCalls();
      if (calls is List) {
        for (final c in calls) {
          final callMap = _asMap(c);
          if (callMap == null) continue;
          final extra = _asMap(callMap['extra']) ?? {};
          if (extra['call_id']?.toString() == callId) {
            final id = callMap['id']?.toString();
            if (id != null) {
              await FlutterCallkitIncoming.endCall(id);
            }
          }
        }
      }
    } catch (e) {
      developer.log("endCallUiByCallId error: $e");
    }
  }

  Future<void> _checkPendingCallOnLaunch() async {
    try {
      final calls = await FlutterCallkitIncoming.activeCalls();
      if (calls is List && calls.isNotEmpty) {
        final callMap = _asMap(calls.first);
        if (callMap != null && callMap['isAccepted'] == true) {
          await _acceptAndNavigate(callMap);
        }
      }
    } catch (e) {
      developer.log("checkPendingCallOnLaunch error: $e");
    }
  }

  void _onCallKitEvent(CallEvent? event) async {
    if (event == null) return;
    final body = _asMap(event.body) ?? {};
    final extra = _asMap(body['extra']) ?? {};
    final callId = extra['call_id']?.toString();
    if (callId == null) return;

    switch (event.event) {
      case Event.actionCallAccept:
        await _acceptAndNavigate(body);
        break;

      case Event.actionCallDecline:
      case Event.actionCallTimeout:
        try {
          await CallApiService.callAction(callId, 'reject');
        } catch (e) {
          developer.log("Reject via CallKit failed: $e");
        }
        break;

      case Event.actionCallEnded:
        // user ne CallKit UI se hi end kiya (CallScreen khulne se pehle)
        try {
          await CallApiService.callAction(callId, 'end');
        } catch (_) {}
        break;

      default:
        break;
    }
  }

  /// Cold start (app killed state se CallKit popup accept karke khula) me
  /// `Event.actionCallAccept` bahut jaldi fire ho sakta hai — usse pehle ki
  /// MaterialApp/Navigator poori tarah mount ho chuka ho. Agar us waqt
  /// seedha `navigatorKey.currentState` use kiya jaaye to wo null milega
  /// aur `.push(...)` chup-chaap kuch nahi karega (na error, na screen) —
  /// yahi ek wajah ho sakti hai "accept hua par screen nahi khuli" jaisa
  /// lagne ki. Isliye thoda wait karke retry karte hain (bounded, taaki
  /// kabhi hamesha ke liye na latke).
  Future<NavigatorState?> _waitForNavigator({
    Duration timeout = const Duration(seconds: 6),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      final nav = navigatorKey?.currentState;
      if (nav != null) return nav;
      await Future.delayed(const Duration(milliseconds: 150));
    }
    return navigatorKey?.currentState;
  }

  Future<void> _acceptAndNavigate(Map<String, dynamic> body) async {
    final extra = _asMap(body['extra']) ?? {};
    final callId = extra['call_id']?.toString();
    if (callId == null) return;

    try {
      final actionData = await CallApiService.callAction(callId, 'accept');
      final livekitUrl = actionData['livekit_url']?.toString();
      final livekitToken = actionData['livekit_token']?.toString();
      if (livekitUrl == null || livekitToken == null) {
        throw Exception("LiveKit credentials server se nahi mile");
      }

      final conversationId = extra['conversation_id']?.toString() ?? '';
      final callType = extra['call_type']?.toString() ?? 'audio';
      final callerName = extra['caller_name']?.toString();
      final callerAvatar = extra['caller_avatar']?.toString();

      final navigator = await _waitForNavigator();
      if (navigator == null) {
        developer.log(
            "CallKit: navigator never became ready — cannot open CallScreen after accept (callId=$callId).");
        return;
      }

      navigator.push(MaterialPageRoute(
        builder: (_) => CallScreen(
          callId: callId,
          conversationId: conversationId,
          isVideo: callType == 'video',
          isCaller: false,
          livekitUrl: livekitUrl,
          livekitToken: livekitToken,
          peerName: callerName,
          peerAvatar: (callerAvatar != null && callerAvatar.isNotEmpty) ? callerAvatar : null,
        ),
      ));
    } catch (e) {
      developer.log("Call accept/navigate failed: $e");
    }
  }
}