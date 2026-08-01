// lib/message/services/push_notification_service.dart

import 'dart:convert';
import 'dart:developer' as developer;
import 'package:flutter/widgets.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';
import 'package:open_filex/open_filex.dart'; // 🔥 NAYA — download-complete notification tap pe file kholne ke liye
import 'package:uuid/uuid.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import 'call_kit_service.dart';
import 'message_api_service.dart';
import 'call_manager.dart'; // 🔥 NAYA — call waiting: active call check karne ke liye
import '../screens/incoming_call_screen.dart'; // 🔥 NAYA — foreground me app-wide call screen push karne ke liye
import 'missed_call_watcher.dart'; // 🔥 NAYA — net off->on hote hi missed call notification

// 🔥 FIX #2 (root cause of "app band ho to na reply aata na call aati"):
// Pehle isolate call type ke alawa kuch nahi karta tha — normal chat
// message background/killed state me silently drop ho jaata tha. Uske
// upar, agar backend FCM payload me top-level `notification` key bhejta
// hai (na ki sirf `data`), to Android OS us notification ko KHUD dikha
// deta hai aur background isolate ko tab tak trigger hi nahi karta jab
// tak user tap na kare — matlab Reply button, CallKit popup, ringtone
// kuch bhi nahi chalta jab tak app already foreground na ho.
//
// ⚠️ BACKEND ME BHI FIX ZAROORI HAI: FCM push me sirf `data` object bhejo,
// top-level `notification` key mat bhejo — na chat message ke liye, na
// call ke liye. Data-only message hamesha is background handler ko
// invoke karta hai chahe app killed hi kyun na ho.
@pragma('vm:entry-point')
Future<void> firebaseBackgroundHandler(RemoteMessage message) async {
  // 🔥 FIX: ye missing tha. Is background isolate me koi bhi plugin
  // (shared_preferences, secure_storage, waghera — jo AuthService.getToken()
  // ke andar use hote hain) tab tak kaam nahi karta jab tak binary
  // messenger initialize na ho. Iske bina wo calls silently hamesha ke
  // liye "await" pe atki reh jaati hain — yahi wajah thi ki notification
  // ke "Reply" se bheja gaya message kabhi jaata hi nahi tha aur spinner
  // hamesha ghoomta reh jaata tha.
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp();

  final data = message.data;

  if (data['type'] == 'incoming_call') {
    // App background/killed hai — native CallKit hi poora incoming-call
    // experience deta hai: full-screen system UI (lock screen ke upar
    // bhi), aur `ringtonePath: 'system_ringtone_default'` (call_kit_service.dart
    // me set hai) se ringtone bhi khud bajta hai. Kuch extra karne ki
    // zaroorat nahi.
    await CallKitService.showIncomingCall(data);
    return;
  }

  // 🔥 NAYA — kisi ne mere message pe reaction diya, app background/killed
  // ho tab bhi notification aani chahiye.
  if (data['type'] == 'reaction') {
    await _showBackgroundReactionNotification(data);
    return;
  }

  // 🔥 NAYA: chat message ho to background/killed isolate me bhi hamara
  // apna Reply-action wala local notification dikhao — pehle ye sirf
  // foreground `onMessage` listener me hota tha.
  await _showBackgroundChatNotification(message);
}

// Reaction ke liye alag, chhota helper — background isolate me chalta hai
// isliye apna khud ka FlutterLocalNotificationsPlugin instance banata hai
// (bilkul _showBackgroundChatNotification jaisa).
@pragma('vm:entry-point')
Future<void> _showBackgroundReactionNotification(Map<String, dynamic> data) async {
  try {
    final reactorName = data['reactor_name']?.toString() ?? 'Someone';
    final emoji = data['emoji']?.toString() ?? '👍';
    final convId = data['conversation_id']?.toString();

    final fln = FlutterLocalNotificationsPlugin();
    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    await fln.initialize(
      settings: const InitializationSettings(android: androidInit),
      onDidReceiveNotificationResponse: notificationTapBackground,
      onDidReceiveBackgroundNotificationResponse: notificationTapBackground,
    );

    const channel = AndroidNotificationChannel(
      'reactions',
      'Reactions',
      description: 'Message reaction notifications',
      importance: Importance.high,
    );
    await fln
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(channel);

    await fln.show(
      id: data.hashCode,
      title: '$reactorName reacted $emoji',
      body: 'Tap to view the message',
      notificationDetails: const NotificationDetails(
        android: AndroidNotificationDetails(
          'reactions',
          'Reactions',
          importance: Importance.high,
          priority: Priority.high,
        ),
      ),
      payload: jsonEncode({'conversation_id': convId ?? ''}),
    );
  } catch (e) {
    developer.log("Background reaction notification failed: $e");
  }
}

// Background isolate me `PushNotificationService.instance._fln` available
// nahi hota (naya isolate hai, app ka normal init nahi chala) — isliye
// apna khud ka FlutterLocalNotificationsPlugin instance banate hain, jo
// bilkul _showLocalNotification() jaisa hi Reply action attach karta hai.
@pragma('vm:entry-point')
Future<void> _showBackgroundChatNotification(RemoteMessage message) async {
  try {
    final data = message.data;

    final title = message.notification?.title ??
        data['sender_name']?.toString() ??
        data['title']?.toString();
    // 🔥 NAYA — study room invite ho to raw "Study Room" text ki jagah
    // ek friendly, recognizable notification body dikhao.
    final isStudyRoomInvite = data['message_type']?.toString() == 'study_room';
    final body = isStudyRoomInvite
        ? "🧑‍🎓 Started a Study Room — tap to join"
        : (message.notification?.body ?? data['text']?.toString() ?? data['body']?.toString());
    if ((title == null || title.isEmpty) && (body == null || body.isEmpty)) {
      return;
    }

    final fln = FlutterLocalNotificationsPlugin();
    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    await fln.initialize(
      settings: const InitializationSettings(android: androidInit),
      onDidReceiveNotificationResponse: notificationTapBackground,
      onDidReceiveBackgroundNotificationResponse: notificationTapBackground,
    );

    const channel = AndroidNotificationChannel(
      'chat_messages',
      'Chat Messages',
      description: 'New message notifications',
      importance: Importance.high,
    );
    await fln
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(channel);

    final androidDetails = AndroidNotificationDetails(
      'chat_messages',
      'Chat Messages',
      importance: Importance.high,
      priority: Priority.high,
      actions: <AndroidNotificationAction>[
        const AndroidNotificationAction(
          'reply_action',
          'Reply',
          inputs: [
            AndroidNotificationActionInput(label: 'Type a message...'),
          ],
          allowGeneratedReplies: true,
          showsUserInterface: false, // app khole bina background me hi bhej do
          cancelNotification: true,
        ),
      ],
    );

    await fln.show(
      id: message.hashCode,
      title: title ?? 'New message',
      body: body ?? '',
      notificationDetails: NotificationDetails(android: androidDetails),
      payload: jsonEncode(data),
    );
  } catch (e) {
    developer.log("Background chat notification failed: $e");
  }
}

// ============================================================
// 🔥 NAYA: Notification pe hi "Reply" dabane se ye chalta hai.
// Android ise ALAG ISOLATE me chalata hai (chahe app foreground me ho ya
// nahi), isliye ye function TOP-LEVEL hona zaroori hai (class ke andar
// nahi) aur @pragma('vm:entry-point') lagana zaroori hai — warna Android
// ise dhoondh nahi payega aur silently reply fail ho jayega.
// ============================================================
@pragma('vm:entry-point')
void notificationTapBackground(NotificationResponse response) {
  // 🔥 FIX: reply-from-notification isi function se trigger hota hai, aur
  // ye bhi apna alag background isolate hota hai — yahan bhi plugin
  // channels ke liye binding init karna zaroori hai, warna
  // AuthService.getToken() (shared_preferences/secure_storage) wali call
  // hamesha ke liye latak jaati hai aur reply kabhi backend tak nahi
  // pahunchta.
  WidgetsFlutterBinding.ensureInitialized();
  _handleNotificationResponse(response);
}

Future<void> _handleNotificationResponse(NotificationResponse response) async {
  final payload = response.payload;
  if (payload == null) return;

  Map<String, dynamic> data;
  try {
    data = jsonDecode(payload) as Map<String, dynamic>;
  } catch (_) {
    return;
  }

  // 🔥 NAYA — download-complete notification pe tap karne se seedha wahi
  // file khulni chahiye jahan save hui thi, koi conversation navigate
  // nahi karni.
  if (data['type'] == 'file_download') {
    final path = data['path']?.toString();
    if (path != null && path.isNotEmpty) {
      try {
        await OpenFilex.open(path);
      } catch (e) {
        developer.log("Opening downloaded file from notification failed: $e");
      }
    }
    return;
  }

  final convId = data['conversation_id']?.toString();
  if (convId == null || convId.isEmpty) return;

  // "reply_action" tabhi aata hai jab user ne notification ke "Reply"
  // button me type karke seedha bhej diya — bina app khole.
  if (response.actionId == 'reply_action') {
    final text = response.input?.trim();
    if (text == null || text.isEmpty) return;
    try {
      await MessageApiService.sendMessageRest(
        convId,
        type: 'text',
        text: text,
        clientId: const Uuid().v4(),
      );
      developer.log("Reply sent from notification: $text");
    } catch (e) {
      developer.log("Reply from notification failed: $e");
    }
    return;
  }

  // Normal tap (koi actionId nahi) -> us conversation ko app me khol do
  PushNotificationService.instance.onNotificationTap?.call(convId);
}

class PushNotificationService {
  PushNotificationService._();
  static final instance = PushNotificationService._();

  final _fln = FlutterLocalNotificationsPlugin();
  final _fcm = FirebaseMessaging.instance;

  static String? currentOpenConversationId;
  void Function(String conversationId)? onNotificationTap;

  Future<void> init() async {
    await _fcm.requestPermission(alert: true, badge: true, sound: true);
    await Permission.notification.request();

    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    const initSettings = InitializationSettings(android: androidInit);

    // 🔥 FIX 1: Use named parameter `settings:` for initialize
    // 🔥 NAYA: onDidReceiveBackgroundNotificationResponse add kiya —
    // isके bina reply-from-notification sirf tab kaam karta jab app pehle
    // se foreground me chal raha ho.
    await _fln.initialize(
      settings: initSettings,
      onDidReceiveNotificationResponse: _handleNotificationResponse,
      onDidReceiveBackgroundNotificationResponse: notificationTapBackground,
    );

    const channel = AndroidNotificationChannel(
      'chat_messages',
      'Chat Messages',
      description: 'New message notifications',
      importance: Importance.high,
    );

    await _fln
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(channel);

    // 🔥 NAYA — downloads aur reactions ke apne channels, taaki user
    // Android settings me inhe chat messages se alag control kar sake.
    const downloadsChannel = AndroidNotificationChannel(
      'downloads',
      'Downloads',
      description: 'File download complete notifications',
      importance: Importance.high,
    );
    const reactionsChannel = AndroidNotificationChannel(
      'reactions',
      'Reactions',
      description: 'Message reaction notifications',
      importance: Importance.high,
    );
    await _fln
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(downloadsChannel);
    await _fln
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(reactionsChannel);

    FirebaseMessaging.onMessage.listen((message) {
      // 🔥 FIX: call ka payload aaye to Instagram/WhatsApp jaisa CallKit
      // popup dikhao — app foreground me hone par bhi chat-jaisi simple
      // notification kaafi nahi hoti call ke liye.
      if (message.data['type'] == 'incoming_call') {
        final d = message.data;
        final incomingCallId = d['call_id']?.toString() ?? '';

        // 🔥 NAYA — CALL WAITING: agar ek call already chal rahi hai, to
        // poori IncomingCallScreen mat push karo (wo current call ko chhupa
        // degi) — bas CallManager ko batao, CallScreen khud apna chhota
        // "call waiting" banner dikha dega (already listen kar raha hai).
        if (CallManager.instance.isActive) {
          CallManager.instance.setWaitingCall(
            callId: incomingCallId,
            callerName: d['caller_name']?.toString() ?? 'Unknown',
            callType: d['call_type']?.toString() ?? 'audio',
            conversationId: d['conversation_id']?.toString() ?? '',
            callerAvatar: d['caller_avatar']?.toString(),
          );
          return;
        }

        // App FOREGROUND me hai (kisi bhi screen pe ho, ChatScreen zaroori
        // nahi) — seedha apna full-screen IncomingCallScreen global
        // navigatorKey (CallKitService.navigatorKey, main.dart me set hota
        // hai) se push karo. Native CallKit.showIncomingCall() yahan
        // JAAN-BOOJH KAR call NAHI kiya — warna foreground me native popup
        // aur ye Flutter screen dono ek saath dikh sakte the. Native
        // CallKit sirf background/killed state ke liye reserved hai
        // (upar firebaseBackgroundHandler me).
        IncomingCallScreen.showIfNeeded(
          CallKitService.navigatorKey?.currentState,
          callId: incomingCallId,
          callType: d['call_type']?.toString() ?? 'audio',
          callerName: d['caller_name']?.toString() ?? 'Unknown',
          callerAvatar: d['caller_avatar']?.toString(),
          conversationId: d['conversation_id']?.toString() ?? '',
        );
        return;
      }

      // 🔥 NAYA — kisi ne mere message pe reaction diya, foreground me
      // (chahe app kisi bhi screen pe ho) turant notification aani chahiye.
      if (message.data['type'] == 'reaction') {
        _showReactionNotification(message);
        return;
      }

      final convId = message.data['conversation_id'];
      if (convId != null && convId == currentOpenConversationId) {
        return;
      }
      _showLocalNotification(message);
    });

    FirebaseMessaging.onMessageOpenedApp.listen((message) {
      final convId = message.data['conversation_id'];
      if (convId != null) onNotificationTap?.call(convId);
    });

    final initialMessage = await _fcm.getInitialMessage();
    if (initialMessage != null) {
      final convId = initialMessage.data['conversation_id'];
      if (convId != null) onNotificationTap?.call(convId);
    }

    // 🔥 ROOT-CAUSE FIX: pehle `registerToken()` sirf `onTokenRefresh` pe hi
    // call hoti thi — jo Firebase months tak fire hi nahi karta. Matlab
    // pehli install/login ke baad device ka FCM token backend tak KABHI
    // nahi pahuchta tha, isliye backend ke paas push bhejne ke liye token
    // hi registered nahi hota — na ringtone bajti thi, na message
    // notification aati thi. Ab app start hote hi (agar user already
    // logged in hai) ek baar explicitly register karte hain.
    await registerToken();

    _fcm.onTokenRefresh.listen((_) => registerToken());

    // 🔥 NAYA — net off tha to jitni calls miss hui, net wapas aate hi
    // unke liye missed-call notification dikhao. Tap karne pe wahi
    // conversation khulni chahiye jaise normal notification tap se khulti
    // hai — isliye same `onNotificationTap` callback reuse kar rahe hain.
    MissedCallWatcher.instance.onMissedCallTap = (convId) => onNotificationTap?.call(convId);
    await MissedCallWatcher.instance.start();
  }

  Future<void> _showLocalNotification(RemoteMessage message) async {
    // 🔥 NAYA: "Reply" action button jisme ek text input field hota hai —
    // isi se notification pe hi se reply bhej sakte ho, bina app khole.
    final androidDetails = AndroidNotificationDetails(
      'chat_messages',
      'Chat Messages',
      importance: Importance.high,
      priority: Priority.high,
      actions: <AndroidNotificationAction>[
        const AndroidNotificationAction(
          'reply_action',
          'Reply',
          inputs: [
            AndroidNotificationActionInput(label: 'Type a message...'),
          ],
          allowGeneratedReplies: true,
          showsUserInterface: false, // app khole bina background me hi bhej do
          cancelNotification: true,
        ),
      ],
    );

    // 🔥 FIX 2: Use named parameters for .show()
    final isStudyRoomInvite = message.data['message_type']?.toString() == 'study_room';
    await _fln.show(
      id: message.hashCode,
      title: message.notification?.title ?? 'New message',
      body: isStudyRoomInvite
          ? "🧑‍🎓 Started a Study Room — tap to join"
          : (message.notification?.body ?? ''),
      notificationDetails: NotificationDetails(android: androidDetails),
      payload: jsonEncode(message.data),
    );
  }

  // 🔥 NAYA — foreground me reaction push aane par dikhta hai. Tap karne
  // pe (payload me conversation_id hai) `_handleNotificationResponse` ke
  // normal-tap wale path se hi conversation khul jaati hai — koi alag
  // handling nahi chahiye.
  Future<void> _showReactionNotification(RemoteMessage message) async {
    final reactorName = message.data['reactor_name']?.toString() ?? 'Someone';
    final emoji = message.data['emoji']?.toString() ?? '👍';
    const androidDetails = AndroidNotificationDetails(
      'reactions',
      'Reactions',
      importance: Importance.high,
      priority: Priority.high,
    );
    await _fln.show(
      id: message.hashCode,
      title: '$reactorName reacted $emoji',
      body: 'Tap to view the message',
      notificationDetails: const NotificationDetails(android: androidDetails),
      payload: jsonEncode(message.data),
    );
  }

  // 🔥 NAYA — chat_screen.dart se download poora hone par call karo. Tap
  // karne par `_handleNotificationResponse` file ko seedha OpenFilex se
  // khol deta hai (uska apna 'file_download' type-check already hai).
  Future<void> showDownloadCompleteNotification({
    required String fileName,
    required String filePath,
  }) async {
    const androidDetails = AndroidNotificationDetails(
      'downloads',
      'Downloads',
      importance: Importance.high,
      priority: Priority.high,
    );
    await _fln.show(
      id: filePath.hashCode,
      title: 'Download complete',
      body: fileName,
      notificationDetails: const NotificationDetails(android: androidDetails),
      payload: jsonEncode({'type': 'file_download', 'path': filePath, 'file_name': fileName}),
    );
  }

  /// Device ka FCM token backend ke `/message/devices/register/` par
  /// bhejta hai. `init()` se (app start pe) aur token refresh hone par
  /// khud-ba-khud call hoti hai — LOGIN SCREEN se successful login ke
  /// turant baad bhi isko manually call karo (jab tak user login nahi
  /// karta tab tak `AuthService.getToken()` null rehta hai, isliye
  /// app-start wali call se register nahi ho paata):
  ///
  ///   await PushNotificationService.instance.registerToken();
  ///
  Future<void> registerToken() async {
    try {
      final authToken = await AuthService.getToken();
      if (authToken == null || authToken.isEmpty) {
        // User abhi login nahi hai — registration ka koi matlab nahi,
        // login ke baad dobara call karo.
        developer.log("registerToken skipped: user not logged in yet");
        return;
      }

      final fcmToken = await _fcm.getToken();
      if (fcmToken == null) {
        developer.log("registerToken skipped: FCM token null (Firebase init issue?)");
        return;
      }

      final res = await http.post(
        Uri.parse("${Api.baseUrl}/message/devices/register/"),
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer $authToken",
        },
        body: jsonEncode({"token": fcmToken, "platform": "android"}),
      );

      if (res.statusCode >= 200 && res.statusCode < 300) {
        developer.log("FCM token registered with backend successfully");
      } else {
        developer.log(
            "FCM token registration failed: ${res.statusCode} ${res.body}");
      }
    } catch (e) {
      developer.log("registerToken error: $e");
    }
  }

  Future<void> unregisterToken() async {
    try {
      final fcmToken = await _fcm.getToken();
      if (fcmToken == null) return;
      final authToken = await AuthService.getToken();
      await http.delete(
        Uri.parse("${Api.baseUrl}/message/devices/register/"),
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer $authToken",
        },
        body: jsonEncode({"token": fcmToken}),
      );
    } catch (_) {}
  }
}