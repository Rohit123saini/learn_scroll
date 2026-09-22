import 'dart:convert';
import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';

import '../config/testseries_config.dart';
import 'testseries_models.dart';
import 'testseries_service.dart';

// ============================================================
// TEST SERIES — LOCAL DRAFT STORE
//
// Kyun: attempt ke beech app kill ho jaye (low memory, crash, phone
// restart) to student ke saare jawab nahi jaane chahiye. Har change
// debounce ke saath SharedPreferences me JSON draft ban ke save hota hai
// aur resume pe restore hota hai.
//
// Teen cheezein yahan rehti hain:
//   1. TsDraft            — abhi tak ke jawab, marked-for-review, current index
//   2. TsPendingSubmit    — time-up (ya offline) pe jo submit server tak
//                           nahi pahunch paya; baad me TsSubmissionQueue
//                           isse bhej deta hai
//   3. Timer start time   — `ts_attempt_started_<id>` (purane key ke saath
//                           compatible)
// ============================================================

class TsPendingSubmit {
  /// Bana-banaya `{question_id: answer_data}` — bhejne ke liye questions
  /// dobara load karne ki zaroorat nahi.
  final Map<String, dynamic> answers;
  final Map<String, String> filePaths;
  final int queuedAtMs;

  const TsPendingSubmit({required this.answers, required this.filePaths, required this.queuedAtMs});

  Map<String, dynamic> toJson() => {
        'answers': answers,
        'files': filePaths,
        'queuedAt': queuedAtMs,
      };

  factory TsPendingSubmit.fromJson(Map<String, dynamic> j) => TsPendingSubmit(
        answers: Map<String, dynamic>.from(j['answers'] as Map),
        filePaths: (j['files'] as Map? ?? const {}).map((k, v) => MapEntry(k.toString(), v.toString())),
        queuedAtMs: (j['queuedAt'] as num?)?.toInt() ?? 0,
      );
}

class TsDraft {
  int index;
  final Map<String, String> mcq;
  final Map<String, List<String>> msq;
  final Map<String, List<String>> order;
  final Set<String> orderTouched;
  final Map<String, Map<String, String>> match;
  final Map<String, String> text;
  final Map<String, String> filePaths;
  final Set<String> marked;
  TsPendingSubmit? pending;
  int savedAtMs;

  TsDraft({
    this.index = 0,
    Map<String, String>? mcq,
    Map<String, List<String>>? msq,
    Map<String, List<String>>? order,
    Set<String>? orderTouched,
    Map<String, Map<String, String>>? match,
    Map<String, String>? text,
    Map<String, String>? filePaths,
    Set<String>? marked,
    this.pending,
    this.savedAtMs = 0,
  })  : mcq = mcq ?? {},
        msq = msq ?? {},
        order = order ?? {},
        orderTouched = orderTouched ?? {},
        match = match ?? {},
        text = text ?? {},
        filePaths = filePaths ?? {},
        marked = marked ?? {};

  Map<String, dynamic> toJson() => {
        'v': 1,
        'savedAt': savedAtMs,
        'index': index,
        'mcq': mcq,
        'msq': msq,
        'order': order,
        'orderTouched': orderTouched.toList(),
        'match': match,
        'text': text,
        'files': filePaths,
        'marked': marked.toList(),
        if (pending != null) 'pending': pending!.toJson(),
      };

  static List<String> _strList(dynamic v) => (v as List? ?? const []).map((e) => e.toString()).toList();

  factory TsDraft.fromJson(Map<String, dynamic> j) {
    Map<String, String> strMap(dynamic v) =>
        (v as Map? ?? const {}).map((k, val) => MapEntry(k.toString(), val.toString()));

    return TsDraft(
      index: (j['index'] as num?)?.toInt() ?? 0,
      mcq: strMap(j['mcq']),
      msq: (j['msq'] as Map? ?? const {}).map((k, v) => MapEntry(k.toString(), _strList(v))),
      order: (j['order'] as Map? ?? const {}).map((k, v) => MapEntry(k.toString(), _strList(v))),
      orderTouched: _strList(j['orderTouched']).toSet(),
      match: (j['match'] as Map? ?? const {}).map((k, v) => MapEntry(k.toString(), strMap(v))),
      text: strMap(j['text']),
      filePaths: strMap(j['files']),
      marked: _strList(j['marked']).toSet(),
      pending: j['pending'] is Map
          ? TsPendingSubmit.fromJson(Map<String, dynamic>.from(j['pending'] as Map))
          : null,
      savedAtMs: (j['savedAt'] as num?)?.toInt() ?? 0,
    );
  }
}

class TsDraftStore {
  TsDraftStore._();

  static const String _draftPrefix = 'ts_draft_v1_';
  // Purana key — same rakha taaki update ke baad chalta hua attempt reset na ho.
  static const String _startPrefix = 'ts_attempt_started_';

  static String _dk(String attemptId) => '$_draftPrefix$attemptId';
  static String _sk(String attemptId) => '$_startPrefix$attemptId';

  /// Corrupt / purana format ho to `null` — draft kabhi crash ki wajah nahi banta.
  static Future<TsDraft?> load(String attemptId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_dk(attemptId));
      if (raw == null) return null;
      return TsDraft.fromJson(Map<String, dynamic>.from(jsonDecode(raw) as Map));
    } catch (_) {
      return null;
    }
  }

  static Future<void> save(String attemptId, TsDraft draft) async {
    try {
      draft.savedAtMs = DateTime.now().millisecondsSinceEpoch;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_dk(attemptId), jsonEncode(draft.toJson()));
    } catch (_) {
      // Draft best-effort hai; save fail hone se test nahi rukna chahiye.
    }
  }

  static Future<void> clear(String attemptId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_dk(attemptId));
      await prefs.remove(_sk(attemptId));
    } catch (_) {}
  }

  // ---- timer start (server-epoch ms) ----

  static Future<int?> getStartMs(String attemptId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getInt(_sk(attemptId));
    } catch (_) {
      return null;
    }
  }

  static Future<void> setStartMs(String attemptId, int ms) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(_sk(attemptId), ms);
    } catch (_) {}
  }

  // ---- queue helpers ----

  /// Woh attempt ids jinka time-up submit abhi server tak nahi pahuncha.
  static Future<List<String>> pendingAttemptIds() async {
    final out = <String>[];
    try {
      final prefs = await SharedPreferences.getInstance();
      for (final key in prefs.getKeys()) {
        if (!key.startsWith(_draftPrefix)) continue;
        final id = key.substring(_draftPrefix.length);
        final d = await load(id);
        if (d?.pending != null) out.add(id);
      }
    } catch (_) {}
    return out;
  }

  /// `draftMaxAge` se purane drafts hata do (pending wale chhod ke — unka
  /// submit ab bhi bhejna hai).
  static Future<void> purgeStale() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final cutoff = DateTime.now().subtract(TsConfig.draftMaxAge).millisecondsSinceEpoch;
      for (final key in prefs.getKeys().toList()) {
        if (!key.startsWith(_draftPrefix)) continue;
        final id = key.substring(_draftPrefix.length);
        final d = await load(id);
        if (d == null) {
          await prefs.remove(key);
          continue;
        }
        if (d.pending == null && d.savedAtMs > 0 && d.savedAtMs < cutoff) {
          await clear(id);
        }
      }
    } catch (_) {}
  }
}

/// Time-up ya offline ki wajah se atke hue submits ko baad me bhejta hai.
/// Screens (list / detail) load par `flush()` call karti hain.
class TsSubmissionQueue {
  TsSubmissionQueue._();

  static bool _running = false;

  /// Attempt player khula hai to uska apna submit chal raha hota hai —
  /// double-submit se bachne ke liye queue us id ko chhod deti hai.
  static String? activeAttemptId;

  /// Jo attempts is call me successfully submit hue unki list.
  static Future<List<TestAttemptModel>> flush() async {
    if (_running) return const [];
    _running = true;
    final done = <TestAttemptModel>[];
    try {
      final ids = await TsDraftStore.pendingAttemptIds();
      for (final id in ids) {
        if (id == activeAttemptId) continue;
        final draft = await TsDraftStore.load(id);
        final p = draft?.pending;
        if (p == null) continue;

        try {
          final files = <String, File>{
            for (final e in p.filePaths.entries) e.key: File(e.value),
          };
          final a = await TestSeriesService.submitAttempt(
            attemptId: id,
            answers: p.answers,
            filesByQuestionId: files,
          );
          await TsDraftStore.clear(id);
          done.add(a);
        } on TestSeriesApiException catch (e) {
          if (e.isRetryable || e.isUnauthorized) break; // abhi bhi offline / logged out — baad me
          // Permanent error: shayad server pe pehle hi submit ho chuka hai.
          final rec = await TestSeriesService.tryReconcile(id);
          if (rec != null) {
            await TsDraftStore.clear(id);
            done.add(rec);
          } else if (e.kind == TsErrorKind.notFound) {
            await TsDraftStore.clear(id); // attempt hi nahi raha
          }
        }
      }
    } catch (_) {
      // Queue kabhi UI ko crash nahi karti.
    } finally {
      _running = false;
    }
    return done;
  }
}
