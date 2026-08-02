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
// pubspec.yaml me EXACT pin rakho (^ mat lagao, warna pub resolver kabhi
// bhi 3.x uthaa lega aur API breaking change ki wajah se build fail hogi):
//
//   dependencies:
//     flutter_callkit_incoming: 2.0.4+2
//     uuid: ^4.5.1
//
// Agar `flutter pub get` phir bhi koi aur version resolve kare (kisi doosri
// dependency ki wajah se), to pubspec.yaml me ye bhi add karo:
//
//   dependency_overrides:
//     flutter_callkit_incoming: 2.0.4+2
//
// Uske baad zaroor chalao:
//   flutter clean
//   flutter pub get

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

    // 🔥 FIX — ROOT CAUSE of "popup aata hai par chhota/normal notification
    // hota hai, full-screen nahi": Android 14 (API 34)+ pe USE_FULL_SCREEN_INTENT
    // manifest permission akele kaafi nahi hai — user ko Settings me jaake
    // ise EXPLICITLY allow karna padta hai, warna Android khud call ko
    // chhote heads-up notification me downgrade kar deta hai (koi
    // accept/decline full-screen UI nahi). Ye check + request ab package
    // upgrade (2.0.4+2 -> 2.5.0+, jahan ye methods add hue) ke baad kaam
    // karega — pubspec.yaml update karna zaroori hai.
    try {
      final canFullScreen = await FlutterCallkitIncoming.canUseFullScreenIntent();
      if (canFullScreen != true) {
        // Ye seedha system Settings screen kholta hai — user ko manually
        // "Allow full screen notifications" ON karna hoga is app ke liye.
        // Koi bhi code se ise auto-grant nahi kiya ja sakta (Android policy).
        await FlutterCallkitIncoming.requestFullIntentPermission();
      }
    } catch (e) {
      developer.log("Full screen intent permission check/request error: $e");
    }

    await _eventSub?.cancel();
    _eventSub = FlutterCallkitIncoming.onEvent.listen(_onCallKitEvent);

    // App terminated state se khula ho aur pehle se koi accepted call ho
    // (rare edge-case), use resume karne ke liye.
    unawaited(_checkPendingCallOnLaunch());
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

    // 🔥 FIX: pehle yahan hamesha `https://i.pravatar.cc/500` (ek random,
    // har call pe badalta hua ajnabi ka photo) dikhta tha — jo galat aur
    // gair-professional lagta hai. Ab agar backend FCM payload me
    // `caller_avatar` bhejta hai to wahi dikhega, warna koi background
    // photo nahi (solid color) — jaise WhatsApp bhi photo na hone par
    // sirf initials/solid background dikhata hai, random photo nahi.
    final callerAvatarUrl = data['caller_avatar']?.toString();
    final hasAvatar = callerAvatarUrl != null && callerAvatarUrl.isNotEmpty;

    final params = CallKitParams(
      id: uuid,
      nameCaller: callerName,
      appName: 'LearnScroll',
      avatar: hasAvatar ? callerAvatarUrl : null,
      handle: isVideo ? 'Video Call' : 'Voice Call',
      type: isVideo ? 1 : 0,
      duration: 30000, // 🔥 30 sec tak ring hoga — caller side CallManager ke 30s no-answer auto-hangup se sync
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
        // 🔥 FIX: system_ringtone_default hi bajega, yehi sahi hai.
        // Custom mp3 bajana hai to use `android/app/src/main/res/raw/` me daalna padta hai
        // aur yahan `ringtonePath: 'raw_incoming_ring'` likhna padta hai.
        ringtonePath: 'system_ringtone_default',
        backgroundColor: '#0F0F11',
        backgroundUrl: hasAvatar ? callerAvatarUrl : null,
        actionColor: '#25D366', // WhatsApp-style accept green
        // 🔥 FIX: Android me notification channel ek baar ban jaaye to
        // uska sound/ringtone setting CODE SE dobara change NAHI hoti —
        // channel immutable hai. Agar tumne pehle bhi is app ko install
        // karke test kiya tha, purana "Incoming Calls" channel silent/bina
        // sound ke already ban chuka hoga aur wahi use ho raha hoga chahe
        // kuch bhi badlo. Naam badal ke (v3) Android ko FORCE karte hain ki
        // ek bilkul naya channel bane jisme sound sahi se set ho.
        //
        // ⚠ Ye tabhi kaam karega jab app ko FRESH install karoge (uninstall
        // karke phir install, sirf hot-restart se nahi) — kyunki purana
        // channel already device ki notification settings me stored hai.
        incomingCallNotificationChannelName: "Incoming Calls v3",
        missedCallNotificationChannelName: "Missed Calls v3",
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

    await FlutterCallkitIncoming.showCallkitIncoming(params);
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

      navigatorKey?.currentState?.push(MaterialPageRoute(
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