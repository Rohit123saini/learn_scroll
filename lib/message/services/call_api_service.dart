import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class CallApiService {
  // TESTING (abhi): LAN IP hi rakho - dono phone same WiFi pe hone chahiye
  // PRODUCTION (backend deploy hone ke baad): sirf ye ek line badalni hai, jaise:
  //   static const String baseUrl = "https://api.yourdomain.com";
  static const String baseUrl = "http://10.224.54.189:8000";

  static Future<Map<String, String>> _getHeaders() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access_token') ?? "";
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer $token",
    };
  }

  /// Call initiate - conversation_id aur type (audio/video)
  static Future<Map<String, dynamic>> initiateCall(String conversationId, String type) async {
    try {
      final res = await http.post(
        Uri.parse("$baseUrl/message/calls/initiate/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "conversation_id": conversationId,
          "type": type, // "audio" or "video"
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return data;
      } else {
        throw Exception(data['error'] ?? "Failed to initiate call");
      }
    } catch (e) {
      throw Exception("Initiate call error: $e");
    }
  }

  /// Call actions - accept, reject, end
  static Future<Map<String, dynamic>> callAction(String callId, String action) async {
    try {
      final res = await http.post(
        Uri.parse("$baseUrl/message/calls/$callId/action/"),
        headers: await _getHeaders(),
        body: jsonEncode({"action": action}),
      );

      final data = jsonDecode(res.body);
      // end action pe 200 aayega, accept pe livekit token/url aayega
      return data;
    } catch (e) {
      // End call me agar server down bhi ho to app crash nahi honi chahiye
      if (action == 'end') return {"status": "ended"};
      throw Exception("Call action $action failed: $e");
    }
  }

  /// Get call status (optional - ringing check ke liye)
  static Future<Map<String, dynamic>> getCallStatus(String callId) async {
    final res = await http.get(
      Uri.parse("$baseUrl/message/calls/$callId/"),
      headers: await _getHeaders(),
    );
    return jsonDecode(res.body);
  }

  // 🔥 NAYA — GROUP CALL: "Add participant". WhatsApp jaisa hi — chalti
  // hui call (1-on-1 ya group) me se kisi aur conversation member ko
  // add kar sakte ho. LiveKit ka Room already multi-party (SFU) hai,
  // isliye naye banda ko sirf usi call_id/room ke liye incoming-call
  // invite bhejna hota hai — accept karne par wahi purana `callAction`
  // flow (livekit_url/livekit_token) use ho jaata hai, kuch alag nahi
  // chahiye.
  //
  // Backend me 2 naye endpoints add karne honge:
  //   GET  $baseUrl/message/calls/<call_id>/addable-participants/
  //     -> conversation ke un members ki list jo abhi call me NAHI hain
  //     response: [{"id": "...", "display_name": "...", "avatar": "..."}]
  //   POST $baseUrl/message/calls/<call_id>/add-participant/
  //     body: {"user_id": "..."}
  //     -> us user ko normal incoming-call push/socket event bhejta hai
  //        (jaisa naya call start hone par jaata hai), same call_id ke
  //        saath — response: {"status": "invited"}
  static Future<List<Map<String, dynamic>>> getAddableParticipants(String callId) async {
    try {
      final res = await http.get(
        Uri.parse("$baseUrl/message/calls/$callId/addable-participants/"),
        headers: await _getHeaders(),
      );
      final data = jsonDecode(res.body);
      if (res.statusCode == 200 && data is List) {
        return data.cast<Map<String, dynamic>>();
      }
      if (data is Map && data['results'] is List) {
        return (data['results'] as List).cast<Map<String, dynamic>>();
      }
      throw Exception(data is Map ? (data['error'] ?? "Failed to load participants") : "Failed to load participants");
    } catch (e) {
      throw Exception("Get addable participants error: $e");
    }
  }

  static Future<Map<String, dynamic>> addParticipant(String callId, String userId) async {
    try {
      final res = await http.post(
        Uri.parse("$baseUrl/message/calls/$callId/add-participant/"),
        headers: await _getHeaders(),
        body: jsonEncode({"user_id": userId}),
      );
      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return data;
      } else {
        throw Exception(data['error'] ?? "Failed to add participant");
      }
    } catch (e) {
      throw Exception("Add participant error: $e");
    }
  }

  // 🔥 NAYA — Google Meet-style study room ke liye. `initiateCall` se
  // ALAG hai: koi call_id/ringing/accept-reject nahi banta, koi push
  // notification/CallKit incoming popup trigger nahi hota. Ye sirf
  // isi conversation ke persistent LiveKit room ka token deta hai,
  // taaki jo bhi study room khole wo seedha (silently) media-connect ho
  // jaaye — jaise Meet link kholte hi ho jaata hai.
  //
  // Backend me naya endpoint add karna hoga:
  //   POST $baseUrl/message/study-room/<conversation_id>/join/
  //   response: { "livekit_url": "...", "livekit_token": "..." }
  // (call_id ki zaroorat nahi — room_name backend khud
  // conversation_id se derive kar sakta hai, taaki sab participants
  // ek hi persistent room me milein.)
  //
  // 🔥 NAYA — "always new session on open" ke liye `newSession: true`
  // bhejte hain (body: {"new_session": true}). Isse chat_screen ke
  // "Study Room" icon se HAR baar bilkul fresh room + fresh whiteboard
  // state milta hai (na ki purani persistent room reuse ho). Invite-card
  // tap se JOIN karne wale flow me ye flag false hi rehta hai, taaki wo
  // already-chal-rahi session me hi jud sakein.
  // Backend ko is flag ke saath ye karna hoga:
  //   - agar `new_session: true` hai to naya LiveKit room banao
  //     (jaise room_name = "<conversation_id>_<naya session uuid/timestamp>")
  //     is naye room ka hi livekit_url/livekit_token wapas bhejo, taaki
  //     purani session me abhi jo log connected hain unse ye alag ho.
  //   - is naye session ke against whiteboard-state bhi khaali/fresh maano
  //     (`GET .../study-room-state/` naye session ke liye khaali return kare).
  // 🔥 NAYA — NETWORK-RESTORE PE MISSED CALL: jab net off tha tab jitni bhi
  // calls aayi (ring hoke khud-ba-khud "missed" ho gayi kyunki push hi
  // nahi pahuncha), net wapas aate hi unko fetch karke notification
  // dikhani hai — bilkul WhatsApp jaisa "Missed call" list.
  //
  // Backend me naya endpoint add karna hoga:
  //   GET $baseUrl/message/calls/missed/?since=<ISO8601 timestamp>
  //     -> `since` ke baad ki saari calls jo is user ke liye ring hui
  //        thi lekin accept/reject nahi hui (status = missed)
  //     response: [{
  //       "call_id": "...", "conversation_id": "...",
  //       "caller_name": "...", "caller_avatar": "...",
  //       "call_type": "audio"|"video", "created_at": "<ISO8601>"
  //     }, ...]
  //   (agar `since` na bheja jaaye to backend apni marzi se last ~24h
  //   ki missed calls bhej sakta hai, taaki client-side clock skew se
  //   bach sake.)
  static Future<List<Map<String, dynamic>>> getMissedCalls({DateTime? since}) async {
    try {
      final query = since != null ? "?since=${Uri.encodeComponent(since.toUtc().toIso8601String())}" : "";
      final res = await http.get(
        Uri.parse("$baseUrl/message/calls/missed/$query"),
        headers: await _getHeaders(),
      );
      final data = jsonDecode(res.body);
      if (res.statusCode == 200 && data is List) {
        return data.cast<Map<String, dynamic>>();
      }
      if (data is Map && data['results'] is List) {
        return (data['results'] as List).cast<Map<String, dynamic>>();
      }
      return [];
    } catch (e) {
      // Missed-call check best-effort hai — fail hone par app ko block
      // nahi karna, bas silently skip.
      return [];
    }
  }

  static Future<Map<String, dynamic>> joinStudyRoom(String conversationId, {bool newSession = false}) async {
    try {
      final res = await http.post(
        Uri.parse("$baseUrl/message/study-room/$conversationId/join/"),
        headers: await _getHeaders(),
        body: jsonEncode({"new_session": newSession}),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return data;
      } else {
        throw Exception(data['error'] ?? "Failed to join study room");
      }
    } catch (e) {
      throw Exception("Join study room error: $e");
    }
  }
}