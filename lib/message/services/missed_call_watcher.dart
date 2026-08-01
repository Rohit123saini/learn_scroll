// message/services/missed_call_watcher.dart
//
// 🔥 NAYA — "Net off tha to call nahi aa sakti (ye normal hai, koi bhi
// app aisa nahi kar sakta), LEKIN net wapas aate hi turant pata chalna
// chahiye ki us dauraan koi call aayi thi" — bilkul WhatsApp/Instagram
// jaisa "Missed call" notification jo net restore hote hi pop hota hai.
//
// Ye kaam karta hai:
//   1. Device ki connectivity change sunta hai (`connectivity_plus`).
//   2. Jab "no network" se "kisi bhi network" me transition hota hai
//      (matlab net abhi-abhi wapas aaya), backend se pucho ki last baar
//      online hone ke baad koi missed call to nahi aayi.
//   3. Har missed call ke liye ek local notification dikhao (jaisi normal
//      call/chat notification dikhti hai), tap karne pe seedha us
//      conversation me le jaaye.
//   4. "last seen online" timestamp SharedPreferences me persist karta hai
//      taaki app kabhi bhi (kisi bhi state se) restart ho, hume pata rahe
//      ki kahan se missed-calls query karni hai.
//
// ⚠️ Pubspec me `connectivity_plus` dependency add karni hogi:
//     connectivity_plus: ^6.0.0   (ya jo bhi latest stable ho)
//
// ⚠️ Backend me `GET /message/calls/missed/?since=...` endpoint chahiye —
// dekh CallApiService.getMissedCalls() ka comment (call_api_service.dart).
//
// Kahan se start karna hai: PushNotificationService.init() ke andar hi
// `MissedCallWatcher.instance.start()` call kar diya hai — alag se kahin
// aur call karne ki zaroorat nahi.

import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'call_api_service.dart';

class MissedCallWatcher {
  MissedCallWatcher._();
  static final MissedCallWatcher instance = MissedCallWatcher._();

  static const _kLastOnlineKey = 'missed_call_watcher_last_online_at';
  static const _kChannelId = 'missed_calls';

  final FlutterLocalNotificationsPlugin _fln = FlutterLocalNotificationsPlugin();
  StreamSubscription<List<ConnectivityResult>>? _sub;
  bool _wasOffline = false;
  bool _started = false;

  /// Tap karne par conversation kholne ke liye — main.dart/chat navigation
  /// jahan bhi setup hai wahan se assign kar dena
  /// (PushNotificationService.onNotificationTap jaisa hi pattern).
  void Function(String conversationId)? onMissedCallTap;

  Future<void> start() async {
    if (_started) return;
    _started = true;

    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _fln.initialize(
      settings: const InitializationSettings(android: androidInit),
      onDidReceiveNotificationResponse: _onTap,
    );
    const channel = AndroidNotificationChannel(
      _kChannelId,
      'Missed Calls',
      description: 'Notifications for calls you missed while offline',
      importance: Importance.high,
    );
    await _fln
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(channel);

    // Shuru me current state note kar lo taaki pehli hi check galti se
    // "offline->online" na maan le agar app already online state me khula.
    final initial = await Connectivity().checkConnectivity();
    _wasOffline = _isOffline(initial);

    _sub = Connectivity().onConnectivityChanged.listen((results) async {
      final isOfflineNow = _isOffline(results);

      if (_wasOffline && !isOfflineNow) {
        // 🔥 Yahi hai wo transition: net abhi-abhi wapas aaya.
        await _checkMissedCalls();
      }

      if (isOfflineNow) {
        // Jab bhi offline ho, "last online" timestamp update kar do — taaki
        // agar app isi offline state me kill/restart bhi ho jaaye, tab bhi
        // sahi window se missed calls check ho.
        await _markOnlineNow();
      }

      _wasOffline = isOfflineNow;
    });

    // App start pe bhi ek baar online hai to timestamp set kar do (agar
    // pehle kabhi set hi nahi hua).
    if (!_wasOffline) await _markOnlineIfUnset();
  }

  bool _isOffline(List<ConnectivityResult> results) {
    return results.isEmpty || results.every((r) => r == ConnectivityResult.none);
  }

  Future<void> _markOnlineNow() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kLastOnlineKey, DateTime.now().toUtc().toIso8601String());
  }

  Future<void> _markOnlineIfUnset() async {
    final prefs = await SharedPreferences.getInstance();
    if (prefs.getString(_kLastOnlineKey) == null) {
      await prefs.setString(_kLastOnlineKey, DateTime.now().toUtc().toIso8601String());
    }
  }

  Future<void> _checkMissedCalls() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final lastOnlineStr = prefs.getString(_kLastOnlineKey);
      final since = lastOnlineStr != null ? DateTime.tryParse(lastOnlineStr) : null;

      final missed = await CallApiService.getMissedCalls(since: since);

      for (final call in missed) {
        await _showMissedCallNotification(call);
      }

      // Ab se aage ka window naya "last online" ban jaata hai.
      await prefs.setString(_kLastOnlineKey, DateTime.now().toUtc().toIso8601String());
    } catch (e) {
      developer.log("MissedCallWatcher check failed: $e");
    }
  }

  Future<void> _showMissedCallNotification(Map<String, dynamic> call) async {
    final callerName = call['caller_name']?.toString() ?? 'Unknown';
    final callType = call['call_type']?.toString() ?? 'audio';
    final conversationId = call['conversation_id']?.toString() ?? '';
    final callId = call['call_id']?.toString() ?? '';
    final isVideo = callType == 'video';

    const androidDetails = AndroidNotificationDetails(
      _kChannelId,
      'Missed Calls',
      importance: Importance.high,
      priority: Priority.high,
      category: AndroidNotificationCategory.call,
    );

    await _fln.show(
      id: callId.isNotEmpty ? callId.hashCode : DateTime.now().millisecondsSinceEpoch,
      title: isVideo ? 'Missed video call' : 'Missed voice call',
      body: '$callerName tried to reach you',
      notificationDetails: const NotificationDetails(android: androidDetails),
      payload: jsonEncode({'type': 'missed_call', 'conversation_id': conversationId, 'call_id': callId}),
    );
  }

  void _onTap(NotificationResponse response) {
    final payload = response.payload;
    if (payload == null) return;
    try {
      final data = jsonDecode(payload) as Map<String, dynamic>;
      final convId = data['conversation_id']?.toString();
      if (convId != null && convId.isNotEmpty) {
        onMissedCallTap?.call(convId);
      }
    } catch (_) {}
  }

  void dispose() {
    _sub?.cancel();
    _sub = null;
    _started = false;
  }
}