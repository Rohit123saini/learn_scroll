// ============================================================
// TEST SERIES — DATA MODELS
//
// Field list source: testseries_app_reference.md §4 (model field tables)
// aur §8 (serializer visibility rules).
//
// Do cheezein jo assignments se ALAG hain aur yahan alag-alag handle hoti
// hain (dono backend ki confirmed shapes hain, hamari choice nahi):
//   1. submit payload: assignments me `answers` ek LIST hai
//      (`[{question_id, answer_data}]`), yahan ek MAP hai
//      (`{question_id: answer_data}`).
//   2. msq ka answer shape: assignments me plain list, yahan
//      `{"option_ids": [...]}`.
// ============================================================

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
    this.attachment,
    this.correctAnswer,
  });

  factory TsQuestion.fromJson(Map<String, dynamic> j) {
    return TsQuestion(
      id: j['id']?.toString() ?? '',
      order: (j['order'] as num?)?.toInt() ?? 0,
      type: tsQuestionTypeFrom(j['question_type']?.toString()),
      text: j['text']?.toString() ?? '',
      attachment: (j['attachment']?.toString().isNotEmpty ?? false) ? j['attachment'].toString() : null,
      marks: (j['marks'] as num?)?.toInt() ?? 0,
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

  /// Auto-graded types — text hamesha manual review pe jaata hai.
  bool get isAutoGraded => type != TsQuestionType.text && type != TsQuestionType.unknown;
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
  final double? avgRating;
  final int reviewCount;
  final String creator;
  final DateTime? createdAt;

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
    this.durationMinutes,
    this.avgRating,
    this.createdAt,
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

    return TestSeriesModel(
      id: j['id']?.toString() ?? '',
      title: j['title']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
      source: j['source']?.toString() ?? 'individual',
      status: j['status']?.toString() ?? 'published',
      isPaid: j['is_paid'] == true,
      priceCoins: (j['price_coins'] as num?)?.toInt() ?? 0,
      durationMinutes: (j['duration_minutes'] as num?)?.toInt(),
      totalMarks: (j['total_marks'] as num?)?.toInt() ?? 0,
      attemptsAllowed: (j['attempts_allowed'] as num?)?.toInt() ?? 1,
      // avg_rating jaan-boojh kar `null` rehta hai jab koi review na ho —
      // backend bhi 0 nahi bhejta, kyunki "abhi koi rating nahi" aur "sabne
      // 0 diya" ek cheez nahi hai.
      avgRating: (j['avg_rating'] as num?)?.toDouble(),
      reviewCount: (j['review_count'] as num?)?.toInt() ?? 0,
      creator: creator,
      createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
    );
  }

  bool get isDraft => status == 'draft';
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
    return TsResponse(
      id: j['id']?.toString() ?? '',
      questionId: j['question']?.toString() ?? '',
      answerData: j['answer_data'],
      answerAttachment:
          (j['answer_attachment']?.toString().isNotEmpty ?? false) ? j['answer_attachment'].toString() : null,
      isAutoGraded: j['is_auto_graded'] == true,
      isCorrect: j['is_correct'] is bool ? j['is_correct'] as bool : null,
      marksAwarded: (j['marks_awarded'] as num?)?.toInt(),
      reviewerFeedback: j['reviewer_feedback']?.toString() ?? '',
    );
  }

  bool get awaitingReview => !isAutoGraded && marksAwarded == null;
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
  });

  factory TestAttemptModel.fromJson(Map<String, dynamic> j) {
    final r = j['responses'];
    // `series` FK id ho sakti hai ya nested object — dono handle.
    String seriesId = '';
    final s = j['series'];
    if (s is Map) {
      seriesId = (s['id'] ?? '').toString();
    } else if (s != null) {
      seriesId = s.toString();
    }

    return TestAttemptModel(
      id: j['id']?.toString() ?? '',
      seriesId: seriesId,
      attemptNumber: (j['attempt_number'] as num?)?.toInt() ?? 1,
      autoScore: (j['auto_score'] as num?)?.toInt() ?? 0,
      finalScore: (j['final_score'] as num?)?.toInt(),
      status: tsAttemptStatusFrom(j['status']?.toString()),
      submittedAt: DateTime.tryParse(j['submitted_at']?.toString() ?? ''),
      checkedAt: DateTime.tryParse(j['checked_at']?.toString() ?? ''),
      responses: r is List
          ? r.map((e) => TsResponse.fromJson(Map<String, dynamic>.from(e as Map))).toList()
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
      id: j['id']?.toString() ?? '',
      student: student,
      rating: (j['rating'] as num?)?.toInt() ?? 0,
      comment: j['comment']?.toString() ?? '',
      createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
    );
  }
}
