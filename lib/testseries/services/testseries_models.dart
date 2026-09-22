// ============================================================
// TEST SERIES — DATA MODELS
//
// Field list source: testseries_app_reference.md §4 (model field tables)
// aur §8 (serializer visibility rules).
//
// Do cheezein jo assignments se ALAG hain (dono backend ki confirmed
// shapes hain, hamari choice nahi):
//   1. submit payload: assignments me `answers` ek LIST hai
//      (`[{question_id, answer_data}]`), yahan ek MAP hai
//      (`{question_id: answer_data}`).
//   2. msq ka answer shape: assignments me plain list, yahan
//      `{"option_ids": [...]}`.
//
// Parsing policy: models kabhi crash nahi karte. Missing / galat-type
// field ka safe default hota hai, aur FK fields id ya nested object
// dono form me accept hote hain.
// ============================================================

/// FK / nested-object dono me se id nikaalta hai.
String tsIdOf(dynamic v) {
  if (v == null) return '';
  if (v is Map) return (v['id'] ?? v['pk'] ?? '').toString();
  return v.toString();
}

enum TsQuestionType { text, mcq, msq, list, unknown }

TsQuestionType tsQuestionTypeFrom(String? raw) {
  switch (raw) {
    case 'text':
      return TsQuestionType.text;
    case 'mcq':
      return TsQuestionType.mcq;
    case 'msq':
      return TsQuestionType.msq;
    case 'list':
      return TsQuestionType.list;
    default:
      return TsQuestionType.unknown;
  }
}

/// `list` question do bilkul alag cheezein ho sakta hai (§4.3):
///   order → items ko sahi sequence me lagao
///   match → left items ko right items se jodo
/// `correct_answer.list_mode` student ko dikhta hi nahi (serializer usko
/// strip kar deta hai), isliye mode `options` ki shape se derive hota hai:
/// Map with left/right → match, warna order.
enum TsListMode { order, match }

enum TsAttemptStatus { inProgress, submitted, partiallyChecked, checked, unknown }

TsAttemptStatus tsAttemptStatusFrom(String? raw) {
  switch (raw) {
    case 'in_progress':
      return TsAttemptStatus.inProgress;
    case 'submitted':
      return TsAttemptStatus.submitted;
    case 'partially_checked':
      return TsAttemptStatus.partiallyChecked;
    case 'checked':
      return TsAttemptStatus.checked;
    default:
      return TsAttemptStatus.unknown;
  }
}

/// Series ka source — teeno forms: individual, campus, live class.
enum TsSource { individual, campus, liveclass, unknown }

TsSource tsSourceFrom(String? raw) {
  final k = (raw ?? '').toLowerCase().replaceAll(RegExp(r'[\s_\-]'), '');
  switch (k) {
    case 'individual':
    case 'personal':
      return TsSource.individual;
    case 'campus':
      return TsSource.campus;
    case 'liveclass':
      return TsSource.liveclass;
    default:
      return TsSource.unknown;
  }
}

/// DRF paginated response ka ek page.
class TsPage<T> {
  final List<T> items;

  /// Agla page ka absolute URL (already host-validated), warna null.
  final String? nextUrl;
  const TsPage({required this.items, this.nextUrl});
  bool get hasMore => nextUrl != null;
}

class TsOption {
  final String id;
  final String text;
  const TsOption({required this.id, required this.text});

  factory TsOption.fromAny(dynamic raw, int index) {
    if (raw is Map) {
      final id = (raw['id'] ?? '$index').toString();
      return TsOption(id: id, text: (raw['text'] ?? id).toString());
    }
    final s = raw?.toString() ?? '$index';
    return TsOption(id: s, text: s);
  }
}

class TsQuestion {
  final String id;
  final int order;
  final TsQuestionType type;
  final String text;
  final String? attachment;
  final int marks;

  /// Negative marking (migration 0003) — deducted only when the question
  /// was actually answered wrong; blank answers never lose marks.
  final int negativeMarks;
  final String topic;
  final String difficulty;

  /// Sirf creator ko milta hai (serializer baaki sabke liye strip kar deta
  /// hai) — student ke liye hamesha empty string.
  final String explanation;

  /// Raw `options` — shape question type pe depend karti hai, isliye raw
  /// rakh ke neeche typed getters diye hain.
  final dynamic optionsRaw;

  /// Sirf creator ko milta hai (serializer baaki sabke liye strip kar deta
  /// hai) — student ke liye hamesha `null`.
  final dynamic correctAnswer;

  const TsQuestion({
    required this.id,
    required this.order,
    required this.type,
    required this.text,
    required this.marks,
    required this.optionsRaw,
    this.negativeMarks = 0,
    this.topic = '',
    this.difficulty = '',
    this.explanation = '',
    this.attachment,
    this.correctAnswer,
  });

  factory TsQuestion.fromJson(Map<String, dynamic> j) {
    final att = j['attachment']?.toString() ?? '';
    return TsQuestion(
      id: tsIdOf(j['id']),
      order: (j['order'] as num?)?.toInt() ?? 0,
      type: tsQuestionTypeFrom(j['question_type']?.toString()),
      text: j['text']?.toString() ?? '',
      attachment: att.isNotEmpty ? att : null,
      marks: (j['marks'] as num?)?.toInt() ?? 0,
      negativeMarks: (j['negative_marks'] as num?)?.toInt() ?? 0,
      topic: j['topic']?.toString() ?? '',
      difficulty: j['difficulty']?.toString() ?? '',
      explanation: j['explanation']?.toString() ?? '',
      optionsRaw: j['options'],
      correctAnswer: j['correct_answer'],
    );
  }

  /// mcq / msq / list-order ke liye — flat selectable list.
  List<TsOption> get choices {
    final raw = optionsRaw;
    if (raw is List) {
      return List.generate(raw.length, (i) => TsOption.fromAny(raw[i], i));
    }
    return const [];
  }

  TsListMode get listMode {
    final raw = optionsRaw;
    if (raw is Map && (raw['left'] != null || raw['right'] != null)) return TsListMode.match;
    return TsListMode.order;
  }

  List<TsOption> get matchLeft {
    final raw = optionsRaw;
    if (raw is Map && raw['left'] is List) {
      final l = raw['left'] as List;
      return List.generate(l.length, (i) => TsOption.fromAny(l[i], i));
    }
    return const [];
  }

  List<TsOption> get matchRight {
    final raw = optionsRaw;
    if (raw is Map && raw['right'] is List) {
      final r = raw['right'] as List;
      return List.generate(r.length, (i) => TsOption.fromAny(r[i], i));
    }
    return const [];
  }

  /// Ye app version is question type ko render kar sakta hai?
  bool get isSupported => type != TsQuestionType.unknown;

  /// Auto-graded types — text hamesha manual review pe jaata hai.
  bool get isAutoGraded => type != TsQuestionType.text && type != TsQuestionType.unknown;
}

/// Delivery mode (migration 0003) — decides whether `starts_at`/`ends_at`/
/// live-video fields are even relevant.
enum TsDeliveryMode { selfPaced, scheduled, live, unknown }

TsDeliveryMode tsDeliveryModeFrom(String? raw) {
  switch (raw) {
    case 'self_paced':
      return TsDeliveryMode.selfPaced;
    case 'scheduled':
      return TsDeliveryMode.scheduled;
    case 'live':
      return TsDeliveryMode.live;
    default:
      return TsDeliveryMode.unknown;
  }
}

enum TsProctoring { none, camera, unknown }

TsProctoring tsProctoringFrom(String? raw) {
  switch (raw) {
    case 'none':
      return TsProctoring.none;
    case 'camera':
      return TsProctoring.camera;
    default:
      return TsProctoring.unknown;
  }
}

enum TsResultRelease { instant, afterEnd, manual, unknown }

TsResultRelease tsResultReleaseFrom(String? raw) {
  switch (raw) {
    case 'instant':
      return TsResultRelease.instant;
    case 'after_end':
      return TsResultRelease.afterEnd;
    case 'manual':
      return TsResultRelease.manual;
    default:
      return TsResultRelease.unknown;
  }
}

class TestSeriesModel {
  final String id;
  final String title;
  final String description;
  final String source; // individual / campus / liveclass
  final String status; // draft / published / archived
  final bool isPaid;
  final int priceCoins;
  final int? durationMinutes;
  final int totalMarks;
  final int attemptsAllowed;
  final int questionCount;
  final double? avgRating;
  final int reviewCount;
  final String creator;
  final DateTime? createdAt;

  // ---- advanced delivery / certification (migration 0003) ----
  final TsDeliveryMode deliveryMode;
  final DateTime? startsAt;
  final DateTime? endsAt;
  final int lateEntryMinutes;

  /// Server-computed: `scheduled` / `live` / `ended` / `` (self-paced).
  /// See `TestSeries.window_state()` — this drives whether "Start" / "Join
  /// live" / "Window ended" should show, so the UI never has to reimplement
  /// the start/end-time math itself.
  final String windowState;
  final TsProctoring proctoring;
  final bool recordLive;
  final int? passPercentage;
  final bool certificateEnabled;
  final String certificateTitle;
  final TsResultRelease resultRelease;
  final DateTime? resultsReleasedAt;
  final bool showSolutions;
  final String? shareSlug;
  final String? shareUrl;

  const TestSeriesModel({
    required this.id,
    required this.title,
    required this.description,
    required this.source,
    required this.status,
    required this.isPaid,
    required this.priceCoins,
    required this.totalMarks,
    required this.attemptsAllowed,
    required this.reviewCount,
    required this.creator,
    this.questionCount = 0,
    this.durationMinutes,
    this.avgRating,
    this.createdAt,
    this.deliveryMode = TsDeliveryMode.selfPaced,
    this.startsAt,
    this.endsAt,
    this.lateEntryMinutes = 0,
    this.windowState = '',
    this.proctoring = TsProctoring.none,
    this.recordLive = false,
    this.passPercentage,
    this.certificateEnabled = false,
    this.certificateTitle = '',
    this.resultRelease = TsResultRelease.instant,
    this.resultsReleasedAt,
    this.showSolutions = true,
    this.shareSlug,
    this.shareUrl,
  });

  factory TestSeriesModel.fromJson(Map<String, dynamic> j) {
    // `creator` FK serializer ke hisaab se id (int/uuid) ya nested object
    // ho sakta hai — dono handle.
    String creator = '';
    final c = j['creator'];
    if (c is Map) {
      creator = (c['username'] ?? c['name'] ?? c['id'] ?? '').toString();
    } else if (c != null) {
      creator = c.toString();
    }
    final slug = j['share_slug']?.toString() ?? '';
    final url = j['share_url']?.toString() ?? '';

    return TestSeriesModel(
      id: tsIdOf(j['id']),
      title: j['title']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
      source: j['source']?.toString() ?? 'individual',
      status: j['status']?.toString() ?? 'published',
      isPaid: j['is_paid'] == true,
      priceCoins: (j['price_coins'] as num?)?.toInt() ?? 0,
      durationMinutes: (j['duration_minutes'] as num?)?.toInt(),
      totalMarks: (j['total_marks'] as num?)?.toInt() ?? 0,
      attemptsAllowed: (j['attempts_allowed'] as num?)?.toInt() ?? 1,
      questionCount: (j['question_count'] as num?)?.toInt() ?? 0,
      // avg_rating jaan-boojh kar `null` rehta hai jab koi review na ho —
      // backend bhi 0 nahi bhejta, kyunki "abhi koi rating nahi" aur "sabne
      // 0 diya" ek cheez nahi hai.
      avgRating: (j['avg_rating'] as num?)?.toDouble(),
      reviewCount: (j['review_count'] as num?)?.toInt() ?? 0,
      creator: creator,
      createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
      deliveryMode: tsDeliveryModeFrom(j['delivery_mode']?.toString()),
      startsAt: DateTime.tryParse(j['starts_at']?.toString() ?? ''),
      endsAt: DateTime.tryParse(j['ends_at']?.toString() ?? ''),
      lateEntryMinutes: (j['late_entry_minutes'] as num?)?.toInt() ?? 0,
      windowState: j['window_state']?.toString() ?? '',
      proctoring: tsProctoringFrom(j['proctoring']?.toString()),
      recordLive: j['record_live'] == true,
      passPercentage: (j['pass_percentage'] as num?)?.toInt(),
      certificateEnabled: j['certificate_enabled'] == true,
      certificateTitle: j['certificate_title']?.toString() ?? '',
      resultRelease: tsResultReleaseFrom(j['result_release']?.toString()),
      resultsReleasedAt: DateTime.tryParse(j['results_released_at']?.toString() ?? ''),
      showSolutions: j['show_solutions'] == null ? true : j['show_solutions'] == true,
      shareSlug: slug.isNotEmpty ? slug : null,
      shareUrl: url.isNotEmpty ? url : null,
    );
  }

  TsSource get sourceType => tsSourceFrom(source);
  bool get isDraft => status == 'draft';
  bool get isArchived => status == 'archived';

  /// `attempts_allowed <= 0` ko "unlimited" maante hain (backend enforce
  /// karta hai; ye sirf UI ke CTA ke liye hai).
  bool get unlimitedAttempts => attemptsAllowed <= 0;

  bool get isLive => deliveryMode == TsDeliveryMode.live;
  bool get isScheduled => deliveryMode == TsDeliveryMode.scheduled;
  bool get isProctored => proctoring == TsProctoring.camera;
  bool get windowEnded => windowState == 'ended';
  bool get windowIsLiveNow => windowState == 'live';
  bool get resultsAlreadyReleased => resultsReleasedAt != null;
}

class TsResponse {
  final String id;
  final String questionId;
  final dynamic answerData;
  final String? answerAttachment;
  final bool isAutoGraded;
  final bool? isCorrect;
  final int? marksAwarded;
  final String reviewerFeedback;

  const TsResponse({
    required this.id,
    required this.questionId,
    required this.answerData,
    required this.isAutoGraded,
    required this.reviewerFeedback,
    this.answerAttachment,
    this.isCorrect,
    this.marksAwarded,
  });

  factory TsResponse.fromJson(Map<String, dynamic> j) {
    final att = j['answer_attachment']?.toString() ?? '';
    return TsResponse(
      id: tsIdOf(j['id']),
      questionId: tsIdOf(j['question']),
      answerData: j['answer_data'],
      answerAttachment: att.isNotEmpty ? att : null,
      isAutoGraded: j['is_auto_graded'] == true,
      isCorrect: j['is_correct'] is bool ? j['is_correct'] as bool : null,
      marksAwarded: (j['marks_awarded'] as num?)?.toInt(),
      reviewerFeedback: j['reviewer_feedback']?.toString() ?? '',
    );
  }

  bool get awaitingReview => !isAutoGraded && marksAwarded == null;

  /// Student ne is question ka kuch bhi jawab diya tha?
  bool get wasAnswered {
    final d = answerData;
    if (answerAttachment != null) return true;
    if (d == null) return false;
    if (d is String) return d.trim().isNotEmpty;
    if (d is List) return d.isNotEmpty;
    if (d is Map) {
      if (d.isEmpty) return false;
      if (d['option_id'] != null) return true;
      for (final k in const ['option_ids', 'sequence', 'pairs']) {
        final v = d[k];
        if (v is List && v.isNotEmpty) return true;
        if (v is Map && v.isNotEmpty) return true;
      }
      final t = d['text'];
      return t != null && t.toString().trim().isNotEmpty;
    }
    return true;
  }
}

class TestAttemptModel {
  final String id;
  final String seriesId;
  final int attemptNumber;
  final int autoScore;
  final int? finalScore;
  final TsAttemptStatus status;
  final DateTime? submittedAt;
  final DateTime? checkedAt;

  final DateTime? startedAt;
  final DateTime? deadlineAt;

  /// Server ka `Date` header offset TestSeriesService pehle se track karta
  /// hai (`serverNow`) — is field ki zarurat nahi timer ke liye, sirf
  /// `attempts/{id}/` ke ek-time snapshot ke liye rakha hai.
  final DateTime? serverTime;

  // ---- advanced (migration 0003) ----
  final bool submittedLate;
  final double? percentage;
  final bool? passed;
  final int integrityFlags;
  final String? certificateCode;
  final bool resultsReleased;

  final List<TsResponse> responses;

  const TestAttemptModel({
    required this.id,
    required this.seriesId,
    required this.attemptNumber,
    required this.autoScore,
    required this.status,
    required this.responses,
    this.finalScore,
    this.submittedAt,
    this.checkedAt,
    this.startedAt,
    this.deadlineAt,
    this.serverTime,
    this.submittedLate = false,
    this.percentage,
    this.passed,
    this.integrityFlags = 0,
    this.certificateCode,
    this.resultsReleased = true,
  });

  factory TestAttemptModel.fromJson(Map<String, dynamic> j) {
    final r = j['responses'];
    // `deadline_at` backend ka confirmed field naam hai (migration 0003);
    // purane `deadline`/`expires_at`/`ends_at` fallback purani builds ke
    // saath compat ke liye rakhe hain.
    final deadlineRaw = j['deadline_at'] ?? j['deadline'] ?? j['expires_at'] ?? j['ends_at'];
    final cert = j['certificate_code']?.toString() ?? '';

    return TestAttemptModel(
      id: tsIdOf(j['id']),
      seriesId: tsIdOf(j['series']),
      attemptNumber: (j['attempt_number'] as num?)?.toInt() ?? 1,
      autoScore: (j['auto_score'] as num?)?.toInt() ?? 0,
      finalScore: (j['final_score'] as num?)?.toInt(),
      status: tsAttemptStatusFrom(j['status']?.toString()),
      submittedAt: DateTime.tryParse(j['submitted_at']?.toString() ?? ''),
      checkedAt: DateTime.tryParse(j['checked_at']?.toString() ?? ''),
      startedAt: DateTime.tryParse(j['started_at']?.toString() ?? ''),
      deadlineAt: DateTime.tryParse(deadlineRaw?.toString() ?? ''),
      serverTime: DateTime.tryParse(j['server_time']?.toString() ?? ''),
      submittedLate: j['submitted_late'] == true,
      percentage: (j['percentage'] as num?)?.toDouble(),
      passed: j['passed'] is bool ? j['passed'] as bool : null,
      integrityFlags: (j['integrity_flags'] as num?)?.toInt() ?? 0,
      certificateCode: cert.isNotEmpty ? cert : null,
      // Field absent (older backend build) → default to visible, same as
      // before this field existed.
      resultsReleased: j['results_released'] == null ? true : j['results_released'] == true,
      responses: r is List
          ? r.whereType<Map>().map((e) => TsResponse.fromJson(Map<String, dynamic>.from(e))).toList()
          : const [],
    );
  }

  bool get isInProgress => status == TsAttemptStatus.inProgress;
  bool get isChecked => status == TsAttemptStatus.checked;
  bool get isFinished => status != TsAttemptStatus.inProgress && status != TsAttemptStatus.unknown;

  /// Jo score dikhana hai: fully checked ho to `final_score`, warna abhi
  /// tak ka auto score.
  int get displayScore => finalScore ?? autoScore;
}

// =====================================================================
// ADVANCED: certificates / recordings / proctoring
// =====================================================================

class TsCertificate {
  final String id;
  final String code;
  final String title;
  final String seriesId;
  final String seriesTitle;
  final String studentName;
  final int score;
  final int totalMarks;
  final double percentage;
  final DateTime? issuedAt;
  final bool isValid;
  final String revokedReason;
  final String verifyPath;

  const TsCertificate({
    required this.id,
    required this.code,
    required this.title,
    required this.seriesId,
    required this.seriesTitle,
    required this.studentName,
    required this.score,
    required this.totalMarks,
    required this.percentage,
    required this.isValid,
    this.issuedAt,
    this.revokedReason = '',
    this.verifyPath = '',
  });

  factory TsCertificate.fromJson(Map<String, dynamic> j) {
    return TsCertificate(
      id: tsIdOf(j['id']),
      code: j['code']?.toString() ?? '',
      title: j['title']?.toString() ?? '',
      seriesId: tsIdOf(j['series']),
      seriesTitle: j['series_title']?.toString() ?? '',
      studentName: j['student_name']?.toString() ?? '',
      score: (j['score'] as num?)?.toInt() ?? 0,
      totalMarks: (j['total_marks'] as num?)?.toInt() ?? 0,
      percentage: (j['percentage'] as num?)?.toDouble() ?? 0,
      issuedAt: DateTime.tryParse(j['issued_at']?.toString() ?? ''),
      isValid: j['is_valid'] == true,
      revokedReason: j['revoked_reason']?.toString() ?? '',
      verifyPath: j['verify_path']?.toString() ?? '',
    );
  }
}

class TsRecording {
  final String id;
  final String kind; // live_session / proctor
  final String status; // recording / ready / failed ...
  final String? url;
  final DateTime? startedAt;
  final DateTime? endedAt;
  final int? durationSeconds;

  const TsRecording({
    required this.id,
    required this.kind,
    required this.status,
    this.url,
    this.startedAt,
    this.endedAt,
    this.durationSeconds,
  });

  factory TsRecording.fromJson(Map<String, dynamic> j) {
    final u = j['url']?.toString() ?? '';
    return TsRecording(
      id: tsIdOf(j['id']),
      kind: j['kind']?.toString() ?? '',
      status: j['status']?.toString() ?? '',
      url: u.isNotEmpty ? u : null,
      startedAt: DateTime.tryParse(j['started_at']?.toString() ?? ''),
      endedAt: DateTime.tryParse(j['ended_at']?.toString() ?? ''),
      durationSeconds: (j['duration_seconds'] as num?)?.toInt(),
    );
  }

  bool get isReady => status == 'ready';
}

/// `POST attempts/{id}/proctor-events/` ke liye input — camera-off, tab
/// switch, jaisa client-reported integrity signal.
class TsProctorEvent {
  final String eventType;
  final DateTime? occurredAt;
  final Map<String, dynamic> meta;
  const TsProctorEvent({required this.eventType, this.occurredAt, this.meta = const {}});

  Map<String, dynamic> toJson() => {
        'event_type': eventType,
        if (occurredAt != null) 'occurred_at': occurredAt!.toUtc().toIso8601String(),
        if (meta.isNotEmpty) 'meta': meta,
      };
}

/// `live-start` / `live-token` ka response — dono host aur viewer/candidate
/// payload isi shape se aate hain (§ views_advanced.py `_host_payload`).
class TsLiveToken {
  final String room;
  final String url;
  final String token;
  final String? sessionStatus;

  const TsLiveToken({required this.room, required this.url, required this.token, this.sessionStatus});

  factory TsLiveToken.fromJson(Map<String, dynamic> j) => TsLiveToken(
        room: j['room']?.toString() ?? '',
        url: j['url']?.toString() ?? '',
        token: j['token']?.toString() ?? '',
        sessionStatus: j['session_status']?.toString() ?? j['status']?.toString(),
      );
}

class TsLeaderboardRow {
  final int rank;
  final String studentName;
  final int score;
  final double? percentage;
  final bool isMe;

  const TsLeaderboardRow({
    required this.rank,
    required this.studentName,
    required this.score,
    this.percentage,
    this.isMe = false,
  });

  factory TsLeaderboardRow.fromJson(Map<String, dynamic> j) => TsLeaderboardRow(
        rank: (j['rank'] as num?)?.toInt() ?? 0,
        studentName: j['student_name']?.toString() ?? '',
        score: (j['score'] as num?)?.toInt() ?? 0,
        percentage: (j['percentage'] as num?)?.toDouble(),
        isMe: j['is_me'] == true,
      );
}

class TestSeriesReview {
  final String id;
  final String student;
  final int rating;
  final String comment;
  final DateTime? createdAt;

  const TestSeriesReview({
    required this.id,
    required this.student,
    required this.rating,
    required this.comment,
    this.createdAt,
  });

  factory TestSeriesReview.fromJson(Map<String, dynamic> j) {
    String student = '';
    final s = j['student'];
    if (s is Map) {
      student = (s['username'] ?? s['id'] ?? '').toString();
    } else if (s != null) {
      student = s.toString();
    }
    return TestSeriesReview(
      id: tsIdOf(j['id']),
      student: student,
      rating: ((j['rating'] as num?)?.toInt() ?? 0).clamp(0, 5).toInt(),
      comment: j['comment']?.toString() ?? '',
      createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
    );
  }
}
