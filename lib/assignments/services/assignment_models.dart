// ============================================================
// ASSIGNMENTS — DATA MODELS
//
// Backend: `assigments` app (haan, backend me spelling isi tarah hai —
// `assigments`, ek 'n' kam. URLs aur JSON keys wahi spelling use karti
// hain, isliye yahan bhi jaan-boojh kar wahi likha hai; sirf Dart-side
// class/field naam sahi spelling me hain).
//
// Field list source: ASSIGNMENT_APP_MASTER.md ke serializers —
//   assigmentsSerializer / assigmentsQuestionSerializer /
//   assigmentsSubmissionSerializer / assigmentsAnswerSerializer.
// ============================================================

/// `assigmentsQuestion.QuestionTypeChoices` — text/mcq/msq/list.
enum AssignmentQuestionType { text, mcq, msq, list, unknown }

AssignmentQuestionType assignmentQuestionTypeFrom(String? raw) {
  switch (raw) {
    case 'text':
      return AssignmentQuestionType.text;
    case 'mcq':
      return AssignmentQuestionType.mcq;
    case 'msq':
      return AssignmentQuestionType.msq;
    case 'list':
      return AssignmentQuestionType.list;
    default:
      return AssignmentQuestionType.unknown;
  }
}

/// `assigmentsSubmission.SubmissionStatus`.
enum AssignmentStatus { missing, submitted, late, partiallyChecked, checked, unknown }

AssignmentStatus assignmentStatusFrom(String? raw) {
  switch (raw) {
    case 'missing':
      return AssignmentStatus.missing;
    case 'submitted':
      return AssignmentStatus.submitted;
    case 'late':
      return AssignmentStatus.late;
    case 'partially_checked':
      return AssignmentStatus.partiallyChecked;
    case 'checked':
      return AssignmentStatus.checked;
    default:
      return AssignmentStatus.unknown;
  }
}

/// Ek selectable option. Backend `options` ko "opaque list" maanta hai:
/// tests `[{"id": "opt_4", "text": "4"}]` shape use karte hain, par model
/// comment plain `["opt_a", "opt_b"]` bhi allow karta hai. Isliye parser
/// dono shapes handle karta hai — warna ek hi legacy assignment poori
/// screen crash kara deta.
class AssignmentOption {
  final String id;
  final String text;
  const AssignmentOption({required this.id, required this.text});

  factory AssignmentOption.fromAny(dynamic raw, int index) {
    if (raw is Map) {
      final id = (raw['id'] ?? raw['value'] ?? '$index').toString();
      final text = (raw['text'] ?? raw['label'] ?? id).toString();
      return AssignmentOption(id: id, text: text);
    }
    final s = raw?.toString() ?? '$index';
    return AssignmentOption(id: s, text: s);
  }
}

class AssignmentQuestion {
  final String id;
  final int order;
  final AssignmentQuestionType type;
  final String text;
  final String? attachment;
  final int marks;
  final List<AssignmentOption> options;

  const AssignmentQuestion({
    required this.id,
    required this.order,
    required this.type,
    required this.text,
    required this.marks,
    required this.options,
    this.attachment,
  });

  factory AssignmentQuestion.fromJson(Map<String, dynamic> j) {
    final rawOptions = j['options'];
    return AssignmentQuestion(
      id: j['id']?.toString() ?? '',
      order: (j['order'] as num?)?.toInt() ?? 0,
      type: assignmentQuestionTypeFrom(j['question_type']?.toString()),
      text: j['text']?.toString() ?? '',
      attachment: (j['attachment']?.toString().isNotEmpty ?? false) ? j['attachment'].toString() : null,
      marks: (j['marks'] as num?)?.toInt() ?? 0,
      options: rawOptions is List
          ? List.generate(rawOptions.length, (i) => AssignmentOption.fromAny(rawOptions[i], i))
          : const [],
    );
  }
}

class AssignmentModel {
  final String id;
  final String title;
  final String description;
  final String? attachment;
  final DateTime? dueDate;
  final int totalMarks;
  final bool hasStructuredQuestions;
  final String postedBy;
  final String source; // personal / campus / liveclass
  final List<AssignmentQuestion> questions;
  final DateTime? createdAt;

  const AssignmentModel({
    required this.id,
    required this.title,
    required this.description,
    required this.totalMarks,
    required this.hasStructuredQuestions,
    required this.postedBy,
    required this.source,
    required this.questions,
    this.attachment,
    this.dueDate,
    this.createdAt,
  });

  factory AssignmentModel.fromJson(Map<String, dynamic> j) {
    final q = j['questions'];
    return AssignmentModel(
      id: j['id']?.toString() ?? '',
      title: j['title']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
      attachment: (j['attachment']?.toString().isNotEmpty ?? false) ? j['attachment'].toString() : null,
      dueDate: DateTime.tryParse(j['due_date']?.toString() ?? ''),
      totalMarks: (j['total_marks'] as num?)?.toInt() ?? 0,
      hasStructuredQuestions: j['has_structured_questions'] == true,
      postedBy: j['posted_by']?.toString() ?? '',
      source: j['source']?.toString() ?? 'personal',
      questions: q is List
          ? q.map((e) => AssignmentQuestion.fromJson(Map<String, dynamic>.from(e as Map))).toList()
          : const [],
      createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
    );
  }

  bool get isOverdue => dueDate != null && DateTime.now().isAfter(dueDate!);

  /// Due date se kitne din bache — negative = overdue. `null` = koi due
  /// date hi nahi.
  int? get daysLeft {
    if (dueDate == null) return null;
    final now = DateTime.now();
    final d = DateTime(dueDate!.year, dueDate!.month, dueDate!.day);
    final t = DateTime(now.year, now.month, now.day);
    return d.difference(t).inDays;
  }
}

class AssignmentAnswer {
  final String id;
  final String questionId;
  final String questionText;
  final int questionMarks;
  final dynamic answerData;
  final String? answerAttachment;
  final bool isAutoGraded;
  final bool? isCorrect;
  final int? marksAwarded;
  final String reviewerFeedback;

  const AssignmentAnswer({
    required this.id,
    required this.questionId,
    required this.questionText,
    required this.questionMarks,
    required this.answerData,
    required this.isAutoGraded,
    required this.reviewerFeedback,
    this.answerAttachment,
    this.isCorrect,
    this.marksAwarded,
  });

  factory AssignmentAnswer.fromJson(Map<String, dynamic> j) {
    return AssignmentAnswer(
      id: j['id']?.toString() ?? '',
      questionId: j['question']?.toString() ?? '',
      questionText: j['question_text']?.toString() ?? '',
      questionMarks: (j['question_marks'] as num?)?.toInt() ?? 0,
      answerData: j['answer_data'],
      answerAttachment:
          (j['answer_attachment']?.toString().isNotEmpty ?? false) ? j['answer_attachment'].toString() : null,
      isAutoGraded: j['is_auto_graded'] == true,
      isCorrect: j['is_correct'] is bool ? j['is_correct'] as bool : null,
      marksAwarded: (j['marks_awarded'] as num?)?.toInt(),
      reviewerFeedback: j['reviewer_feedback']?.toString() ?? '',
    );
  }

  /// `text` question jo abhi tak teacher ne review nahi kiya.
  bool get awaitingReview => !isAutoGraded && marksAwarded == null;
}

class AssignmentSubmission {
  final String id;
  final String assignmentId;
  final String student;
  final String writtenContent;
  final String? file;
  final AssignmentStatus status;
  final String grade;
  final int? totalMarksAwarded;
  final String feedback;
  final String publicSlug;
  final DateTime? submittedAt;
  final DateTime? checkedAt;
  final bool isLate;
  final List<AssignmentAnswer> answers;

  const AssignmentSubmission({
    required this.id,
    required this.assignmentId,
    required this.student,
    required this.writtenContent,
    required this.status,
    required this.grade,
    required this.feedback,
    required this.publicSlug,
    required this.isLate,
    required this.answers,
    this.file,
    this.totalMarksAwarded,
    this.submittedAt,
    this.checkedAt,
  });

  factory AssignmentSubmission.fromJson(Map<String, dynamic> j) {
    final a = j['answers'];
    return AssignmentSubmission(
      id: j['id']?.toString() ?? '',
      assignmentId: j['assigments']?.toString() ?? '',
      student: j['student']?.toString() ?? '',
      writtenContent: j['written_content']?.toString() ?? '',
      file: (j['file']?.toString().isNotEmpty ?? false) ? j['file'].toString() : null,
      status: assignmentStatusFrom(j['status']?.toString()),
      grade: j['grade']?.toString() ?? '',
      totalMarksAwarded: (j['total_marks_awarded'] as num?)?.toInt(),
      feedback: j['feedback']?.toString() ?? '',
      publicSlug: j['public_slug']?.toString() ?? '',
      submittedAt: DateTime.tryParse(j['submitted_at']?.toString() ?? ''),
      checkedAt: DateTime.tryParse(j['checked_at']?.toString() ?? ''),
      isLate: j['is_late'] == true,
      answers: a is List
          ? a.map((e) => AssignmentAnswer.fromJson(Map<String, dynamic>.from(e as Map))).toList()
          : const [],
    );
  }

  bool get isSubmitted => status != AssignmentStatus.missing && submittedAt != null;
  bool get isFullyChecked => status == AssignmentStatus.checked;
  bool get isPending => status == AssignmentStatus.missing;
}

/// Ek assignment + us par MERI submission — list screen ko dono chahiye
/// hote hain (kya submit karna baaki hai / kitne marks mile), par backend
/// do alag endpoints deta hai. Ye pair client-side me banti hai
/// (`AssignmentService.getMyAssignments()`).
class AssignmentWithSubmission {
  final AssignmentModel assignment;
  final AssignmentSubmission? submission;
  const AssignmentWithSubmission({required this.assignment, this.submission});

  AssignmentStatus get effectiveStatus => submission?.status ?? AssignmentStatus.missing;
  bool get isPending => submission == null || submission!.isPending;
  bool get isChecked =>
      submission != null &&
      (submission!.status == AssignmentStatus.checked ||
          submission!.status == AssignmentStatus.partiallyChecked);
  bool get isSubmitted => submission != null && submission!.isSubmitted && !isChecked;
}
