import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'call_api_service.dart';
import '../models/study_room_models.dart';

/// Study Room whiteboard (jo text/sticky-notes likhe gaye hain) se AI
/// summary notes aur quiz banwane ke liye. Same pattern jaisa
/// CallApiService use karta hai — same baseUrl, same auth-token header —
/// taaki backend ka ek hi JWT sab jagah kaam kare.
class AiStudyService {
  // TESTING/PRODUCTION baseUrl CallApiService se hi liya hai — dono
  // services ek hi backend host pe hit karte hain, alag rakhne ki
  // zaroorat nahi.
  // 🔧 FIX — `CallApiService.baseUrl` ek getter/method hai (constant value
  // nahi), isliye `const` me evaluate nahi ho sakta ("Constant evaluation
  // error"). Ek static getter isko lazily resolve karta hai, aur neeche
  // har jagah `_baseUrl` string-interpolation me waise hi kaam karta hai
  // jaise pehle field ki tarah karta tha — koi aur change nahi chahiye.
  static String get _baseUrl => CallApiService.baseUrl;

  static Future<Map<String, String>> _getHeaders() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access_token') ?? "";
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer $token",
    };
  }

  /// Backend me naya endpoint add karna hoga (jaise calls/initiate/ hai
  /// waise hi):
  ///   POST $baseUrl/message/study-room/ai-tools/
  ///   body:     { "mode": "summary" | "quiz", "content": "<board text>" }
  ///   response (summary): { "summary": "..." }
  ///   response (quiz):    { "questions": [ { "question": "...", "options": ["..."], "answer": "..." }, ... ] }
  ///
  /// AI provider (Anthropic API waghera) ko backend se hi call karna —
  /// app ke andar koi AI key kabhi hardcode mat karna.
  static Future<Map<String, dynamic>> generate({
    required String mode,
    required String content,
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/study-room/ai-tools/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "mode": mode,
          "content": content,
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return data as Map<String, dynamic>;
      } else {
        throw Exception(data['error'] ?? "Failed to generate $mode");
      }
    } catch (e) {
      throw Exception("AI $mode generation error: $e");
    }
  }

  // 🔥 NAYA — manual/on-demand voice-note transcription (Phase 2, §17.4).
  // Backend auto-transcribes har audio message background me (§7.6, WS
  // `meta_update` se live aa jaata hai) — ye method sirf FALLBACK button
  // ke liye hai: agar auto-transcript kisi wajah se nahi aaya (AI_ENABLED
  // false tha us waqt, ya bahut purana voice note jo feature se pehle
  // bheja gaya tha), user manually "Transcribe" tap kare to yahi hit hoga.
  //   POST $baseUrl/message/ai/transcribe/
  //   body:     { "file_url": "<voice message file_url>", "mime_type": "audio/mp4" }
  //   response: { "transcript": "..." }
  // Throttled 15/min/user server-side (AiTranscribeThrottle) — 429 aa
  // sakta hai agar bahut baar tap kiya.
  static Future<String> transcribe({
    required String fileUrl,
    String? mimeType,
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/ai/transcribe/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "file_url": fileUrl,
          "mime_type": mimeType ?? _guessAudioMimeType(fileUrl),
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return (data['transcript'] ?? '').toString();
      } else if (res.statusCode == 429) {
        throw Exception("Bahut zyada transcribe requests — thodi der baad try karo");
      } else if (res.statusCode == 503) {
        throw Exception("AI transcription abhi available nahi hai");
      } else {
        throw Exception(data['error'] ?? "Transcription failed");
      }
    } catch (e) {
      throw Exception("Transcribe error: $e");
    }
  }

  // App voice notes ko `record` package se `.m4a` (AAC-LC) me record
  // karta hai (chat_screen.dart `_startRecording`), isliye default
  // "audio/mp4" — extension se guess kar lo agar kabhi kuch aur ho.
  // ==========================================================================
  // 🔥 NAYA — FEATURE 3: Class transcript chunk registration + search
  // ==========================================================================

  /// Har local-mic-recording chunk upload (`MessageApiService.uploadFile`)
  /// ke baad ye call karo taaki backend usko transcribe karke transcript
  /// timeline me jode. Fire-and-forget jaisa treat karo caller side —
  /// transcription background me (Celery) hoti hai, ye call turant return
  /// ho jaata hai.
  ///   POST $baseUrl/message/study-room/<conversationId>/transcript-chunk/
  static Future<void> registerTranscriptChunk({
    required String conversationId,
    required String sessionId,
    required String audioFileUrl,
    required Duration startOffset,
    required Duration endOffset,
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/study-room/$conversationId/transcript-chunk/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "session_id": sessionId,
          "audio_file_url": audioFileUrl,
          "start_offset_seconds": startOffset.inMilliseconds / 1000.0,
          "end_offset_seconds": endOffset.inMilliseconds / 1000.0,
        }),
      );
      if (res.statusCode != 200 && res.statusCode != 201) {
        // Best-effort feature — ek chunk transcribe na ho paye to poori
        // class ka experience block nahi hona chahiye, bas is chunk ka
        // transcript missing rahega.
        throw Exception(jsonDecode(res.body)['error'] ?? "chunk register failed");
      }
    } catch (e) {
      // Silent-ish, jaise MissedCallWatcher pattern — bas debug ke liye
      // rethrow taaki caller chahe to log kar sake.
      rethrow;
    }
  }

  /// `sessionId` na do to backend khud us conversation ki SABSE RECENT
  /// session ka transcript de deta hai (revision ke liye usually last
  /// class hi chahiye hota hai). `query` diya jaaye to sirf matching
  /// segments milte hain — "jump to where teacher explained X" isi se
  /// power hota hai.
  ///   GET $baseUrl/message/study-room/<conversationId>/transcript/?session_id=&q=
  static Future<List<TranscriptSegmentModel>> searchTranscript({
    required String conversationId,
    String? sessionId,
    String? query,
  }) async {
    final params = <String, String>{};
    if (sessionId != null && sessionId.isNotEmpty) params['session_id'] = sessionId;
    if (query != null && query.isNotEmpty) params['q'] = query;
    final uri = Uri.parse("$_baseUrl/message/study-room/$conversationId/transcript/")
        .replace(queryParameters: params.isEmpty ? null : params);

    final res = await http.get(uri, headers: await _getHeaders());
    final data = jsonDecode(res.body);
    if (res.statusCode == 200) {
      final segments = (data['segments'] as List?) ?? [];
      return segments.map((s) => TranscriptSegmentModel.fromJson(s as Map<String, dynamic>)).toList();
    }
    throw Exception(data['error'] ?? "Transcript search failed");
  }

  // ==========================================================================
  // 🔥 NAYA — FEATURE 4: AI copilot grounded in full classroom context
  // ==========================================================================

  /// `boardContent` optional — study room se current whiteboard text bhej
  /// sakte ho (`_collectBoardTextContent()` study_room_screen.dart me
  /// already exist karta hai). Backend recent chat + whiteboard + matching
  /// transcript excerpts — teeno combine karke Gemini ko bhejta hai, isliye
  /// jawab "generic AI" jaisa nahi, isi class ke actual content se aata hai.
  ///   POST $baseUrl/message/ai/classroom-copilot/
  static Future<String> askClassroomCopilot({
    required String conversationId,
    required String question,
    String boardContent = '',
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/ai/classroom-copilot/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "conversation_id": conversationId,
          "question": question,
          "board_content": boardContent,
        }),
      );
      final data = jsonDecode(res.body);
      if (res.statusCode == 200) {
        return (data['answer'] ?? '').toString();
      } else if (res.statusCode == 429) {
        throw Exception("Bahut zyada sawaal pooch liye — thodi der baad try karo");
      } else {
        throw Exception(data['error'] ?? "Copilot answer nahi mila");
      }
    } catch (e) {
      throw Exception("Classroom copilot error: $e");
    }
  }

  // ==========================================================================
  // 🔥 NAYA — FEATURE 5: Revision Deck (flashcards + quiz for exam prep)
  // ==========================================================================

  /// Generates (and server-side saves) a fresh revision deck from this
  /// class's chat + whiteboard + transcript, combined server-side.
  /// `boardContent` — pass `_collectBoardTextContent()` from
  /// study_room_screen.dart, same as `generate()`/`askClassroomCopilot()`.
  /// `sessionId` optional — omit to use the most recent class session.
  ///   POST $baseUrl/message/study-room/<conversationId>/revision-deck/
  ///   response: { "flashcards": [{"front":"...","back":"..."}, ...],
  ///               "quiz": [{"question":"...","options":[...],"answer":"..."}, ...],
  ///               "created_at": "..." }
  /// Throttled 10/min/user server-side (RevisionDeckThrottle).
  static Future<RevisionDeckModel> generateRevisionDeck({
    required String conversationId,
    String boardContent = '',
    String? sessionId,
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/study-room/$conversationId/revision-deck/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "board_content": boardContent,
          if (sessionId != null && sessionId.isNotEmpty) "session_id": sessionId,
        }),
      );
      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return RevisionDeckModel.fromJson(data as Map<String, dynamic>);
      } else if (res.statusCode == 429) {
        throw Exception("Bahut zyada revision decks generate kar liye — thodi der baad try karo");
      } else {
        throw Exception((data as Map)['error'] ?? "Revision deck nahi ban paya");
      }
    } catch (e) {
      throw Exception("Revision deck error: $e");
    }
  }

  /// Fetches the last-generated deck without hitting Gemini again — for
  /// "open Study Room → revise what I already made" without a fresh
  /// generate tap every time. Empty flashcards/quiz means none exist yet.
  ///   GET $baseUrl/message/study-room/<conversationId>/revision-deck/
  static Future<RevisionDeckModel> getSavedRevisionDeck(String conversationId) async {
    final res = await http.get(
      Uri.parse("$_baseUrl/message/study-room/$conversationId/revision-deck/"),
      headers: await _getHeaders(),
    );
    final data = jsonDecode(res.body);
    if (res.statusCode == 200) {
      return RevisionDeckModel.fromJson(data as Map<String, dynamic>);
    }
    throw Exception((data as Map)['error'] ?? "Revision deck load nahi hua");
  }

  static String _guessAudioMimeType(String fileUrl) {
    final path = fileUrl.split('?').first.toLowerCase();
    if (path.endsWith('.m4a') || path.endsWith('.mp4')) return "audio/mp4";
    if (path.endsWith('.aac')) return "audio/aac";
    if (path.endsWith('.mp3')) return "audio/mpeg";
    if (path.endsWith('.wav')) return "audio/wav";
    if (path.endsWith('.ogg') || path.endsWith('.opus')) return "audio/ogg";
    return "audio/mp4";
  }
}