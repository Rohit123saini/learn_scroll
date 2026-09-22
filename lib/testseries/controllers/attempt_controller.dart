import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import '../config/testseries_config.dart';
import '../services/attempt_draft_store.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_format.dart';

// ============================================================
// TEST ATTEMPT — CONTROLLER
//
// Question player ka poora state + logic yahin hai; screen sirf render
// karti hai. Fayde: logic unit-testable hai, aur screen me setState ka
// jaal nahi.
//
// ⏱️ TIMER — kaise kaam karta hai (aur kya nahi kar sakta):
//   Backend `TestAttempt` me `started_at` expose nahi karta (§4.6), isliye
//   deadline client pe nikalni padti hai. Hum jitna ho sake tamper-resistant
//   banate hain:
//     • Deadline `attempt.deadline` / `attempt.started_at` se aati hai agar
//       backend future me bheje — frontend change nahi chahiye.
//     • Warna start time persist hota hai (attempt-id ke against) aur
//       server ke `Date` header ke hisaab se (device clock ke nahi) store hota hai.
//     • "Abhi kitne baje hain" = max(anchor + Stopwatch, device wall clock
//       + server offset). Session ke beech device clock peeche karne se
//       time wapas nahi milta; device sleep me Stopwatch ruk jaaye to wall
//       clock use hoti hai.
//   ⚠️ Phir bhi ye enforcement NAHI hai: app data clear / reinstall karne
//   se local start time chala jaata hai. Asli guard ke liye backend ko
//   `started_at` + submit pe deadline check chahiye (docs/TESTSERIES_FRONTEND.md §6).
// ============================================================

enum TsSubmitState { idle, submitting, pending }

enum TsLoadOutcome { ready, alreadyFinished, failed }

class TsSubmitResult {
  /// Success: server ka attempt.
  final TestAttemptModel? attempt;

  /// Failure: kyun.
  final TestSeriesApiException? error;

  /// Time-up ka submit network fail hone par device pe queue ho gaya.
  final bool queued;

  const TsSubmitResult({this.attempt, this.error, this.queued = false});
  bool get ok => attempt != null;
}

class TestAttemptController extends ChangeNotifier {
  TestAttemptController({required this.attemptId, required this.series});

  final String attemptId;
  final TestSeriesModel series;

  // ---------------- load state ----------------
  List<TsQuestion> questions = const [];
  bool loading = true;
  Object? loadError;
  TestAttemptModel? finishedAttempt;
  bool draftRestored = false;

  // ---------------- position ----------------
  int _index = 0;
  int get index => _index;

  // ---------------- answers ----------------
  final Map<String, String> _mcq = {};
  final Map<String, Set<String>> _msq = {};
  final Map<String, List<String>> _order = {};
  final Set<String> _orderTouched = {};
  final Map<String, Map<String, String>> _match = {};
  final Map<String, TextEditingController> _text = {};
  final Map<String, String> _lastText = {};
  final Map<String, File> _files = {};
  final Set<String> _marked = {};

  // ---------------- submit ----------------
  TsSubmitState submitState = TsSubmitState.idle;
  bool timeUp = false;
  TsPendingSubmit? _pending;
  bool get hasPendingSubmit => _pending != null;

  // ---------------- timer ----------------
  final ValueNotifier<Duration> remaining = ValueNotifier<Duration>(Duration.zero);
  Timer? _ticker;
  DateTime? _deadline;
  DateTime _anchor = DateTime.now().toUtc();
  Duration _offset = Duration.zero;
  final Stopwatch _sw = Stopwatch();
  final Set<int> _warned = {};

  /// Screen set karti hai.
  void Function(int minutesLeft)? onTimeWarning;
  VoidCallback? onTimeUp;

  bool get hasTimer => _deadline != null;

  // ---------------- internals ----------------
  Timer? _saveTimer;
  bool _disposed = false;
  bool _draftClosed = false;

  bool get locked => timeUp || submitState == TsSubmitState.submitting;

  // ============================================================
  // LOAD
  // ============================================================

  Future<T?> _opt<T>(Future<T> f) async {
    try {
      return await f;
    } catch (_) {
      return null;
    }
  }

  Future<TsLoadOutcome> load() async {
    loading = true;
    loadError = null;
    _safeNotify();
    try {
      unawaited(TsDraftStore.purgeStale());

      final results = await Future.wait<Object?>([
        TestSeriesService.getQuestions(series.id),
        // Attempt fetch fail ho to test phir bhi chalna chahiye.
        _opt<TestAttemptModel>(TestSeriesService.getAttempt(attemptId)),
      ]);
      final qs = results[0] as List<TsQuestion>;
      final attempt = results[1] as TestAttemptModel?;

      // Kisi aur device / pending flush se ye attempt pehle hi submit ho chuka ho.
      if (attempt != null && attempt.isFinished) {
        finishedAttempt = attempt;
        loading = false;
        _safeNotify();
        return TsLoadOutcome.alreadyFinished;
      }

      TsSubmissionQueue.activeAttemptId = attemptId;
      _setQuestions(qs);

      final draft = await TsDraftStore.load(attemptId);
      if (draft != null) _restore(draft);

      await _initTimer(attempt);
      _attachTextListeners();

      loading = false;
      _safeNotify();
      _startTicking();
      return TsLoadOutcome.ready;
    } catch (e, st) {
      TestSeriesService.onUnexpectedError?.call(e, st);
      loadError = e;
      loading = false;
      _safeNotify();
      return TsLoadOutcome.failed;
    }
  }

  void _setQuestions(List<TsQuestion> qs) {
    questions = qs;
    for (final q in qs) {
      if (q.type == TsQuestionType.text) {
        _text.putIfAbsent(q.id, () => TextEditingController());
      }
      if (q.type == TsQuestionType.list && q.listMode == TsListMode.order) {
        // Seeded shuffle — jawab leak na ho, aur resume pe order stable rahe.
        _order[q.id] = tsSeededShuffle<String>(q.choices.map((o) => o.id).toList(), '$attemptId:${q.id}');
      }
    }
  }

  /// Test ke liye: network ke bina questions inject karo.
  @visibleForTesting
  void debugSetQuestions(List<TsQuestion> qs) {
    _setQuestions(qs);
    loading = false;
  }

  void _restore(TsDraft d) {
    final byId = {for (final q in questions) q.id: q};

    d.mcq.forEach((qid, oid) {
      final q = byId[qid];
      if (q != null && q.type == TsQuestionType.mcq && q.choices.any((o) => o.id == oid)) {
        _mcq[qid] = oid;
      }
    });

    d.msq.forEach((qid, ids) {
      final q = byId[qid];
      if (q == null || q.type != TsQuestionType.msq) return;
      final valid = q.choices.map((o) => o.id).toSet();
      final keep = ids.where(valid.contains).toSet();
      if (keep.isNotEmpty) _msq[qid] = keep;
    });

    d.order.forEach((qid, ids) {
      final q = byId[qid];
      if (q == null || q.type != TsQuestionType.list || q.listMode != TsListMode.order) return;
      final expected = q.choices.map((o) => o.id).toList()..sort();
      final got = List<String>.of(ids)..sort();
      // Sirf tab restore jab wahi items hon (creator ne beech me edit kiya ho to nahi).
      if (listEquals(expected, got)) {
        _order[qid] = ids;
        if (d.orderTouched.contains(qid)) _orderTouched.add(qid);
      }
    });

    d.match.forEach((qid, pairs) {
      final q = byId[qid];
      if (q == null || q.type != TsQuestionType.list || q.listMode != TsListMode.match) return;
      final left = q.matchLeft.map((o) => o.id).toSet();
      final right = q.matchRight.map((o) => o.id).toSet();
      final keep = <String, String>{
        for (final e in pairs.entries)
          if (left.contains(e.key) && right.contains(e.value)) e.key: e.value,
      };
      if (keep.isNotEmpty) _match[qid] = keep;
    });

    d.text.forEach((qid, t) {
      final c = _text[qid];
      if (c != null && t.isNotEmpty) {
        c.text = t;
        _lastText[qid] = t;
      }
    });

    var lostFile = false;
    d.filePaths.forEach((qid, path) {
      if (!_text.containsKey(qid)) return;
      final f = File(path);
      if (f.existsSync()) {
        _files[qid] = f;
      } else {
        lostFile = true;
      }
    });
    missingAttachment = lostFile;

    _marked.addAll(d.marked.where(byId.containsKey));
    if (d.index >= 0 && d.index < questions.length) _index = d.index;
    _pending = d.pending;

    draftRestored = _mcq.isNotEmpty ||
        _msq.isNotEmpty ||
        _match.isNotEmpty ||
        _orderTouched.isNotEmpty ||
        _lastText.isNotEmpty ||
        _files.isNotEmpty;
  }

  /// Draft restore me koi attached photo disk se gayab mili.
  bool missingAttachment = false;

  bool _listenersAttached = false;

  void _attachTextListeners() {
    if (_listenersAttached) return; // retry-load pe double listener nahi
    _listenersAttached = true;
    _text.forEach((qid, c) {
      c.addListener(() {
        if (c.text == (_lastText[qid] ?? '')) return; // sirf cursor/selection badla
        _lastText[qid] = c.text;
        _touch();
      });
    });
  }

  // ============================================================
  // TIMER
  // ============================================================

  Future<void> _initTimer(TestAttemptModel? attempt) async {
    final minutes = series.durationMinutes;
    if (minutes == null || minutes <= 0) return;
    final total = Duration(minutes: minutes);

    _offset = TestSeriesService.serverOffset ?? Duration.zero;
    _anchor = TestSeriesService.serverNow;

    DateTime? deadline = attempt?.deadlineAt;
    if (deadline == null) {
      DateTime? start = attempt?.startedAt;
      if (start == null) {
        final ms = await TsDraftStore.getStartMs(attemptId);
        if (ms != null) {
          start = DateTime.fromMillisecondsSinceEpoch(ms, isUtc: true);
        } else {
          start = _anchor;
          await TsDraftStore.setStartMs(attemptId, _anchor.millisecondsSinceEpoch);
        }
      }
      deadline = start.add(total);
    }

    // Start future me dikhe (clock skew) to bhi duration se zyada time nahi milta.
    final cap = _anchor.add(total);
    _deadline = deadline.isAfter(cap) ? cap : deadline;
    _sw
      ..reset()
      ..start();

    // Jo warning threshold load pe hi paar ho chuki hai uska toast nahi.
    final left = _deadline!.difference(_anchor);
    for (final w in TsConfig.timeWarningMinutes) {
      if (left <= Duration(minutes: w)) _warned.add(w);
    }
    remaining.value = left.isNegative ? Duration.zero : left;
  }

  DateTime _now() {
    final viaWatch = _anchor.add(_sw.elapsed);
    final viaWall = DateTime.now().toUtc().add(_offset);
    return viaWatch.isAfter(viaWall) ? viaWatch : viaWall;
  }

  void _startTicking() {
    if (_deadline == null) return;
    _ticker?.cancel();
    _tick();
    if (timeUp) return;
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
  }

  /// Screen foreground me aaye to turant ek tick (background me Timer ruk sakta hai).
  void onResumed() => _tick();

  void _tick() {
    final dl = _deadline;
    if (dl == null || _disposed) return;
    final left = dl.difference(_now());
    remaining.value = left.isNegative ? Duration.zero : left;

    if (left > Duration.zero) {
      for (final w in TsConfig.timeWarningMinutes) {
        if (!_warned.contains(w) && left <= Duration(minutes: w)) {
          _warned.add(w);
          onTimeWarning?.call(w);
        }
      }
      return;
    }

    if (!timeUp) {
      timeUp = true;
      _ticker?.cancel();
      _safeNotify();
      onTimeUp?.call();
    }
  }

  // ============================================================
  // ANSWERS
  // ============================================================

  int get answerableCount => questions.where((q) => q.isSupported).length;
  int get answeredCount => questions.where(isAnswered).length;
  int get markedCount => _marked.length;

  bool isAnswered(TsQuestion q) {
    switch (q.type) {
      case TsQuestionType.mcq:
        return _mcq.containsKey(q.id);
      case TsQuestionType.msq:
        return (_msq[q.id] ?? const <String>{}).isNotEmpty;
      case TsQuestionType.list:
        if (q.listMode == TsListMode.match) return (_match[q.id] ?? const <String, String>{}).isNotEmpty;
        // Order type me default (shuffled) sequence "jawab" nahi hai — sirf
        // tab jab user ne khud chhua ho.
        return _orderTouched.contains(q.id);
      case TsQuestionType.text:
        return (_text[q.id]?.text.trim().isNotEmpty ?? false) || _files.containsKey(q.id);
      case TsQuestionType.unknown:
        return false;
    }
  }

  bool isMarked(String qid) => _marked.contains(qid);

  String? mcqSelection(String qid) => _mcq[qid];
  Set<String> msqSelection(String qid) => _msq[qid] ?? const <String>{};
  List<String> orderOf(TsQuestion q) => _order[q.id] ?? q.choices.map((o) => o.id).toList();
  bool orderTouched(String qid) => _orderTouched.contains(qid);
  Map<String, String> matchPairs(String qid) => _match[qid] ?? const <String, String>{};
  TextEditingController? textController(String qid) => _text[qid];
  File? fileOf(String qid) => _files[qid];

  void setIndex(int i) {
    if (i == _index || i < 0 || i >= questions.length) return;
    _index = i;
    _safeNotify();
    _scheduleSave();
  }

  void setMcq(String qid, String optionId) {
    _mcq[qid] = optionId;
    _touch();
  }

  void clearMcq(String qid) {
    if (_mcq.remove(qid) != null) _touch();
  }

  void toggleMsq(String qid, String optionId) {
    final next = Set<String>.from(_msq[qid] ?? const <String>{});
    if (!next.add(optionId)) next.remove(optionId);
    _msq[qid] = next;
    _touch();
  }

  void reorder(TsQuestion q, int oldIndex, int newIndex) {
    final next = List<String>.from(orderOf(q));
    if (newIndex > oldIndex) newIndex -= 1;
    next.insert(newIndex, next.removeAt(oldIndex));
    _order[q.id] = next;
    _orderTouched.add(q.id);
    _touch();
  }

  /// `rightId == null` → is left item ka pair hata do.
  void setMatch(String qid, String leftId, String? rightId) {
    final next = Map<String, String>.from(_match[qid] ?? const <String, String>{});
    if (rightId == null) {
      next.remove(leftId);
    } else {
      next[leftId] = rightId;
    }
    _match[qid] = next;
    _touch();
  }

  void setFile(String qid, File file) {
    _files[qid] = file;
    _touch();
  }

  void removeFile(String qid) {
    if (_files.remove(qid) != null) _touch();
  }

  void toggleMark(String qid) {
    if (!_marked.add(qid)) _marked.remove(qid);
    _touch();
  }

  /// Koi bhi user-driven change: UI refresh + debounce draft save. Koi
  /// change hua to purana queued submit bhi invalid (naye jawab ab latest hain).
  void _touch() {
    if (!timeUp) _pending = null;
    _safeNotify();
    _scheduleSave();
  }

  // ---------------- payload ----------------

  /// `{question_id: answer_data}` — backend ka confirmed shape.
  ///
  /// Unanswered ke liye khaali payload jaata hai (mcq → `{}`, msq → `[]`,
  /// order (untouched) → khaali sequence). Untouched order ka default
  /// sequence KABHI nahi bheja jaata — wo student ka jawab hai hi nahi.
  Map<String, dynamic> buildAnswers() {
    final out = <String, dynamic>{};
    for (final q in questions) {
      switch (q.type) {
        case TsQuestionType.mcq:
          final v = _mcq[q.id];
          out[q.id] = v == null ? <String, dynamic>{} : TestSeriesService.mcqAnswer(v);
          break;
        case TsQuestionType.msq:
          out[q.id] = TestSeriesService.msqAnswer((_msq[q.id] ?? const <String>{}).toList());
          break;
        case TsQuestionType.list:
          if (q.listMode == TsListMode.match) {
            out[q.id] = TestSeriesService.matchAnswer(_match[q.id] ?? const <String, String>{});
          } else {
            out[q.id] = TestSeriesService.orderAnswer(
              _orderTouched.contains(q.id) ? (_order[q.id] ?? const <String>[]) : const <String>[],
            );
          }
          break;
        case TsQuestionType.text:
          // File-only answer ke liye bhi entry chahiye — backend
          // `answer_<question_id>` file ko isi entry pe merge karta hai.
          out[q.id] = TestSeriesService.textAnswer(_text[q.id]?.text.trim() ?? '');
          break;
        case TsQuestionType.unknown:
          break; // is app version ko is type ka pata nahi — jhooti entry nahi bhejte
      }
    }
    return out;
  }

  Map<String, File> filesForSubmit() => {
        for (final e in _files.entries)
          if (e.value.existsSync()) e.key: e.value,
      };

  // ============================================================
  // DRAFT
  // ============================================================

  TsDraft _snapshot() {
    return TsDraft(
      index: _index,
      mcq: Map<String, String>.from(_mcq),
      msq: {for (final e in _msq.entries) e.key: e.value.toList()},
      order: {for (final e in _order.entries) e.key: List<String>.from(e.value)},
      orderTouched: Set<String>.from(_orderTouched),
      match: {for (final e in _match.entries) e.key: Map<String, String>.from(e.value)},
      text: {
        for (final e in _text.entries)
          if (e.value.text.isNotEmpty) e.key: e.value.text,
      },
      filePaths: {for (final e in _files.entries) e.key: e.value.path},
      marked: Set<String>.from(_marked),
      pending: _pending,
    );
  }

  void _scheduleSave() {
    if (_draftClosed || _disposed) return;
    _saveTimer?.cancel();
    _saveTimer = Timer(TsConfig.draftSaveDebounce, () => unawaited(flushDraft()));
  }

  /// Turant draft likho (exit / background / submit se pehle).
  Future<void> flushDraft() async {
    _saveTimer?.cancel();
    if (_draftClosed || questions.isEmpty) return;
    await TsDraftStore.save(attemptId, _snapshot());
  }

  Future<void> _closeDraft() async {
    _draftClosed = true;
    _saveTimer?.cancel();
    _pending = null;
    await TsDraftStore.clear(attemptId);
  }

  // ============================================================
  // SUBMIT
  // ============================================================

  /// [auto] = time-up ka submit. Wo zaroori hai, isliye network fail par
  /// backoff ke saath retry hota hai aur phir bhi fail ho to device pe
  /// queue ho jaata hai (baad me `TsSubmissionQueue` bhejta hai).
  /// Manual submit fail ho to seedha error — user khud dobara try karta hai.
  Future<TsSubmitResult> submit({required bool auto}) async {
    if (submitState == TsSubmitState.submitting) {
      return const TsSubmitResult(error: null);
    }
    submitState = TsSubmitState.submitting;
    _safeNotify();

    final answers = buildAnswers();
    final files = filesForSubmit();
    // Jo draft pending me pehle se hai (app restart ke baad) uska answers
    // hi authoritative hai — controller ke controllers waise bhi restore ho chuke hain.
    var tries = 0;

    while (true) {
      try {
        final a = await TestSeriesService.submitAttempt(
          attemptId: attemptId,
          answers: answers,
          filesByQuestionId: files,
        );
        _ticker?.cancel();
        await _closeDraft();
        submitState = TsSubmitState.idle;
        _safeNotify();
        return TsSubmitResult(attempt: a);
      } on TestSeriesApiException catch (e) {
        // Shayad pehla request server tak pahunch gaya tha (response kho gaya).
        final rec = await TestSeriesService.tryReconcile(attemptId);
        if (rec != null) {
          _ticker?.cancel();
          await _closeDraft();
          submitState = TsSubmitState.idle;
          _safeNotify();
          return TsSubmitResult(attempt: rec);
        }

        if (auto && e.isRetryable && tries < TsConfig.autoSubmitRetries) {
          tries++;
          await Future<void>.delayed(TsConfig.retryBaseDelay * (1 << tries));
          continue;
        }

        if (auto && e.isRetryable) {
          _pending = TsPendingSubmit(
            answers: answers,
            filePaths: {for (final f in files.entries) f.key: f.value.path},
            queuedAtMs: DateTime.now().millisecondsSinceEpoch,
          );
          submitState = TsSubmitState.pending;
          await flushDraft();
          _safeNotify();
          return TsSubmitResult(error: e, queued: true);
        }

        submitState = TsSubmitState.idle;
        _safeNotify();
        return TsSubmitResult(error: e);
      } catch (e, st) {
        TestSeriesService.onUnexpectedError?.call(e, st);
        submitState = TsSubmitState.idle;
        _safeNotify();
        return TsSubmitResult(error: TestSeriesApiException('UNKNOWN', kind: TsErrorKind.unknown));
      }
    }
  }

  // ============================================================
  // LIFECYCLE
  // ============================================================

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _ticker?.cancel();
    _saveTimer?.cancel();
    if (!_draftClosed && questions.isNotEmpty) {
      // Controllers dispose hone se PEHLE snapshot (sync), save baad me.
      unawaited(TsDraftStore.save(attemptId, _snapshot()));
    }
    if (TsSubmissionQueue.activeAttemptId == attemptId) TsSubmissionQueue.activeAttemptId = null;
    for (final c in _text.values) {
      c.dispose();
    }
    remaining.dispose();
    super.dispose();
  }
}
