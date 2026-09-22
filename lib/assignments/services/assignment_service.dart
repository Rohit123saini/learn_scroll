import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import 'assignment_models.dart';

// ============================================================
// ASSIGNMENTS — API SERVICE
//
// Endpoints (assigments/urls.py):
//   GET    {mount}/assigmentss/                      list
//   GET    {mount}/assigmentss/{id}/                 retrieve
//   POST   {mount}/assigmentss/                      create (personal only)
//   GET    {mount}/submissions/                      my submissions
//   POST   {mount}/submissions/                      create (personal flow)
//   PATCH  {mount}/submissions/{id}/submit_freeform/
//   POST   {mount}/submissions/{id}/submit_structured/
//   PATCH  {mount}/submissions/{id}/grade/                    free-form grade
//   POST   {mount}/submissions/{id}/answer/{qid}/review/      text-answer review
//   POST   {mount}/submissions/{id}/publish/                  mint public link
//   POST   {mount}/submissions/{id}/unpublish/                revoke public link
//   GET    {mount}/public/{slug}/                    (auth-free)
//
// Har call `AuthService.getValidToken()` use karti hai (getToken() nahi) —
// Task 8.4 wahi rule: expired token ke saath request bhejne se
// `onForceLogout` kabhi trigger nahi hota aur session expiry silently
// fail hoti rehti hai.
// ============================================================

/// CONFIRMED WITH BACKEND — `ASSIGNMENT_APP_MASTER.md` root-URLconf
/// snippet (both the deployment-plan section and the endpoint table)
/// mounts this app at `path("api/assigments/", include("assigments.urls"))`
/// — i.e. `/api/assigments/...`, not `/assigments/...`. Kept as a single
/// constant so a future re-mount is still a one-line change.
const String kAssignmentsMount = '/assigments';

class AssignmentApiException implements Exception {
  final String message;
  final int? statusCode;
  AssignmentApiException(this.message, {this.statusCode});
  @override
  String toString() => message;
}

class AssignmentService {
  AssignmentService._();

  static const Duration _timeout = Duration(seconds: 15);

  static String get _base => '${Api.baseUrl}$kAssignmentsMount';

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw AssignmentApiException('NOT_AUTHENTICATED');
    }
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  /// DRF pagination on/off dono handle karta hai — `{"results": [...]}`
  /// aur plain `[...]`, dono shapes aati dikh sakti hain depending on
  /// settings, isliye yahan ek jagah normalize kar diya.
  static List<dynamic> _asList(dynamic decoded) {
    if (decoded is List) return decoded;
    if (decoded is Map && decoded['results'] is List) return decoded['results'] as List;
    return const [];
  }

  static Never _fail(http.Response r) {
    throw AssignmentApiException(
      'Request failed (${r.statusCode})',
      statusCode: r.statusCode,
    );
  }

  // ---------------- reads ----------------

  static Future<List<AssignmentModel>> listAssignments() async {
    final r = await http
        .get(Uri.parse('$_base/assigmentss/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => AssignmentModel.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<AssignmentModel> getAssignment(String id) async {
    final r = await http
        .get(Uri.parse('$_base/assigmentss/$id/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AssignmentModel.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  static Future<List<AssignmentSubmission>> listMySubmissions() async {
    final r = await http
        .get(Uri.parse('$_base/submissions/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => AssignmentSubmission.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<AssignmentSubmission> getSubmission(String id) async {
    final r = await http
        .get(Uri.parse('$_base/submissions/$id/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// List screen ka main call — assignments aur meri submissions ko client
  /// side pe join karta hai.
  ///
  /// Campus/liveclass assignments ke liye submission row backend pehle se
  /// bana chuka hota hai (`status=missing`, roster se); personal wale me
  /// row tab banti hai jab student pehli baar submit karta hai. Dono cases
  /// me UI ek jaisa dikhna chahiye, isliye join yahan hota hai aur
  /// "submission nahi mili" ka matlab simply "abhi tak submit nahi kiya".
  static Future<List<AssignmentWithSubmission>> getMyAssignments() async {
    final results = await Future.wait([
      listAssignments(),
      listMySubmissions().catchError((_) => <AssignmentSubmission>[]),
    ]);
    final assignments = results[0] as List<AssignmentModel>;
    final submissions = results[1] as List<AssignmentSubmission>;

    final byAssignment = <String, AssignmentSubmission>{};
    for (final s in submissions) {
      final existing = byAssignment[s.assignmentId];
      // Ek hi assignment pe theoretically ek hi submission hoti hai
      // (`unique_submission_per_student`), par agar kabhi do aa jaayein to
      // sabse latest wali dikhao.
      if (existing == null ||
          (s.submittedAt != null &&
              (existing.submittedAt == null || s.submittedAt!.isAfter(existing.submittedAt!)))) {
        byAssignment[s.assignmentId] = s;
      }
    }

    final joined = assignments
        .map((a) => AssignmentWithSubmission(assignment: a, submission: byAssignment[a.id]))
        .toList();

    // Sort: pehle pending (jinki due date sabse paas hai), phir baaki.
    joined.sort((a, b) {
      if (a.isPending != b.isPending) return a.isPending ? -1 : 1;
      final ad = a.assignment.dueDate;
      final bd = b.assignment.dueDate;
      if (ad == null && bd == null) return 0;
      if (ad == null) return 1;
      if (bd == null) return -1;
      return ad.compareTo(bd);
    });
    return joined;
  }

  // ---------------- create (posting your own assignment) ----------------

  /// `POST {mount}/assigmentss/` — the *only* create entry point this app
  /// exposes to a mobile client (`assigmentsViewSet.perform_create()` hard-
  /// wires `source=personal`; campus/liveclass assignments are created by
  /// those apps' own bridge, never through here). This is a genuinely
  /// **self-assignment**: nothing here adds other students to a roster, so
  /// realistically only the creator will ever see/submit it (see
  /// `assigmentsViewSet.get_queryset()` — non-staff users only see
  /// assignments they posted, or personal ones they hold a submission
  /// for).
  ///
  /// `attachment` + `hasStructuredQuestions=true` together are
  /// deliberately NOT supported here: `assigmentsCreateSerializer.questions`
  /// is a nested list, and DRF cannot parse a nested list out of a
  /// multipart/form-data body (no such syntax exists) — the exact same
  /// limitation `submit_structured`'s own backend docstring documents for
  /// `answers`, just with no server-side JSON-string workaround written
  /// for THIS endpoint. Callers must not pass both; the create screen
  /// enforces this by disabling the attachment picker once structured
  /// mode is on.
  static Future<AssignmentModel> createAssignment({
    required String title,
    String description = '',
    DateTime? dueDate,
    int? totalMarks,
    required bool hasStructuredQuestions,
    File? attachment,
    List<Map<String, dynamic>> questions = const [],
    // ---- publishing + projects (migration 0003) — all optional, so
    // existing callers that only pass the classic fields keep working.
    String kind = 'assignment', // 'assignment' | 'project'
    List<String> tags = const [],
    String difficulty = '',
    List<String> submissionTypes = const [],
    List<Map<String, dynamic>> rubric = const [],
  }) async {
    assert(!(attachment != null && hasStructuredQuestions),
        'attachment + structured questions cannot both be sent — see method docstring.');
    assert(!(rubric.isNotEmpty && hasStructuredQuestions),
        'a rubric grades free-form hand-ins; it cannot combine with structured questions.');

    final url = Uri.parse('$_base/assigmentss/');
    final fields = <String, String>{
      'title': title,
      'description': description,
      'has_structured_questions': hasStructuredQuestions.toString(),
      'kind': kind,
      if (difficulty.isNotEmpty) 'difficulty': difficulty,
      if (dueDate != null) 'due_date': _dateOnly(dueDate),
      // total_marks is read-only / server-computed once has_structured_
      // questions is true (sum of question marks) — only meaningful to
      // send for the free-form, manually-scaled case. Rubric-graded
      // projects are server-computed too (sum of rubric max_marks).
      if (!hasStructuredQuestions && rubric.isEmpty && totalMarks != null) 'total_marks': '$totalMarks',
    };

    if (attachment != null) {
      final token = await AuthService.getValidToken();
      if (token == null || token.isEmpty) throw AssignmentApiException('NOT_AUTHENTICATED');
      final req = http.MultipartRequest('POST', url)..headers['Authorization'] = 'Bearer $token';
      req.fields.addAll(fields);
      if (tags.isNotEmpty) req.fields['tags'] = jsonEncode(tags);
      if (submissionTypes.isNotEmpty) req.fields['submission_types'] = jsonEncode(submissionTypes);
      if (rubric.isNotEmpty) req.fields['rubric'] = jsonEncode(rubric);
      req.files.add(await http.MultipartFile.fromPath('attachment', attachment.path));
      final streamed = await req.send().timeout(const Duration(seconds: 60));
      final r = await http.Response.fromStream(streamed);
      if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
      return AssignmentModel.fromJson(
          Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
    }

    final body = <String, dynamic>{
      ...fields,
      'has_structured_questions': hasStructuredQuestions, // real bool for JSON, not the stringified field above
      if (hasStructuredQuestions) 'questions': questions,
      if (tags.isNotEmpty) 'tags': tags,
      if (submissionTypes.isNotEmpty) 'submission_types': submissionTypes,
      if (rubric.isNotEmpty) 'rubric': rubric,
    };
    final r = await http.post(url, headers: await _headers(), body: jsonEncode(body)).timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentModel.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  // ---------------- publishing / explore (migration 0003) ----------------

  /// `POST {mount}/assigmentss/{id}/publish/` — poster publishes a personal
  /// assignment/project. `visibility`: `'public'` (listed in Explore) or
  /// `'link'` (unlisted, share link only). Re-publishing keeps the same slug.
  static Future<AssignmentModel> publishAssignment(String assignmentId, {String visibility = 'public'}) async {
    final r = await http
        .post(Uri.parse('$_base/assigmentss/$assignmentId/publish/'),
            headers: await _headers(), body: jsonEncode({'visibility': visibility}))
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentModel.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `POST {mount}/assigmentss/{id}/unpublish/`.
  static Future<AssignmentModel> unpublishAssignment(String assignmentId) async {
    final r = await http
        .post(Uri.parse('$_base/assigmentss/$assignmentId/unpublish/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AssignmentModel.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `POST {mount}/assigmentss/{id}/join/` — idempotent: creates (or returns)
  /// the caller's own submission row so `submit_freeform`/`submit_structured`
  /// can be called right after. Personal, published assignments only.
  static Future<AssignmentSubmission> joinAssignment(String assignmentId) async {
    final r = await http
        .post(Uri.parse('$_base/assigmentss/$assignmentId/join/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `GET {mount}/assigmentss/explore/` — browse public assignments/projects.
  /// `ordering`: `'new'` (default) or `'popular'`.
  static Future<List<AssignmentModel>> exploreAssignments({
    String? search,
    String? tag,
    String? kind, // 'assignment' | 'project'
    String? difficulty,
    String ordering = 'new',
    String? pageUrl,
  }) async {
    final uri = pageUrl != null
        ? Uri.parse(pageUrl)
        : Uri.parse('$_base/assigmentss/explore/').replace(queryParameters: {
            if (search != null && search.isNotEmpty) 'search': search,
            if (tag != null && tag.isNotEmpty) 'tag': tag,
            if (kind != null && kind.isNotEmpty) 'kind': kind,
            if (difficulty != null && difficulty.isNotEmpty) 'difficulty': difficulty,
            'ordering': ordering,
          });
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => AssignmentModel.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// `POST {mount}/assigmentss/{id}/questions-import/` — CSV with the answer
  /// key (only while `has_structured_questions` is on and no one has started).
  static Future<Map<String, dynamic>> questionsImportCsv(String assignmentId, File csv) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) throw AssignmentApiException('NOT_AUTHENTICATED');
    final req = http.MultipartRequest(
      'POST',
      Uri.parse('$_base/assigmentss/$assignmentId/questions-import/'),
    )..headers['Authorization'] = 'Bearer $token';
    req.files.add(await http.MultipartFile.fromPath('file', csv.path));
    final streamed = await req.send().timeout(const Duration(seconds: 60));
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);
  }

  /// Full, absolute URL for a published ASSIGNMENT's public page (`p/{slug}/`
  /// — distinct from `publicUrlFor`, which is a student's finished SUBMISSION).
  static String publicAssignmentUrlFor(String slug) => '$_base/p/$slug/';

  static String _dateOnly(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  // ---------------- writes ----------------

  /// Personal assignment ke liye submission row banata hai. Campus/
  /// liveclass assignments me row already exist karti hai, to `submit_*`
  /// seedha usi id pe chalega — isliye ye tabhi call hota hai jab local
  /// join me koi submission mili hi na ho.
  static Future<AssignmentSubmission> createSubmission(String assignmentId) async {
    final r = await http
        .post(Uri.parse('$_base/submissions/'),
            headers: await _headers(), body: jsonEncode({'assigments': assignmentId}))
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// Free-form submit — likha hua jawab + optional ek file.
  /// PATCH multipart, kyunki `FreeformSubmitSerializer` me `file` ek real
  /// FileField hai.
  static Future<AssignmentSubmission> submitFreeform({
    required String submissionId,
    required String writtenContent,
    File? file,
    // Project hand-in link (repo / live demo / design file). http(s) only —
    // backend rejects anything else with a clean 400.
    String linkUrl = '',
  }) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) throw AssignmentApiException('NOT_AUTHENTICATED');

    final req = http.MultipartRequest(
      'PATCH',
      Uri.parse('$_base/submissions/$submissionId/submit_freeform/'),
    )..headers['Authorization'] = 'Bearer $token';

    req.fields['written_content'] = writtenContent;
    if (linkUrl.isNotEmpty) req.fields['link_url'] = linkUrl;
    if (file != null) {
      req.files.add(await http.MultipartFile.fromPath('file', file.path));
    }

    final streamed = await req.send().timeout(const Duration(seconds: 120));
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// Structured submit.
  ///
  /// Shape views.py ke `submit_structured` se exactly match karti hai:
  /// multipart me `answers` ek **JSON string** jaata hai (nested multipart
  /// fields nahi — multipart me nested structure ka koi syntax hi nahi
  /// hota), aur har per-question file flat `answer_<question_id>` key pe.
  /// View wapas file ko uski answer-entry pe merge kar deta hai.
  ///
  /// Koi file na ho to bhi multipart bhejna safe hai, par plain JSON
  /// sasta hai — isliye neeche wahi branch pehle.
  static Future<AssignmentSubmission> submitStructured({
    required String submissionId,
    required List<Map<String, dynamic>> answers,
    Map<String, File> filesByQuestionId = const {},
  }) async {
    final url = Uri.parse('$_base/submissions/$submissionId/submit_structured/');

    if (filesByQuestionId.isEmpty) {
      final r = await http
          .post(url, headers: await _headers(), body: jsonEncode({'answers': answers}))
          .timeout(_timeout);
      if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
      return AssignmentSubmission.fromJson(
          Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
    }

    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) throw AssignmentApiException('NOT_AUTHENTICATED');

    final req = http.MultipartRequest('POST', url)..headers['Authorization'] = 'Bearer $token';
    req.fields['answers'] = jsonEncode(answers);
    for (final entry in filesByQuestionId.entries) {
      req.files.add(await http.MultipartFile.fromPath('answer_${entry.key}', entry.value.path));
    }

    final streamed = await req.send().timeout(const Duration(seconds: 120));
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  // ---------------- grading / review (posted_by / staff only — server-enforced) ----------------
  //
  // These four exist so a self-assignment's creator can actually close the
  // loop on their own submission (free-form grade, and reviewing any
  // `text`-type structured answers, which auto-grade never touches).
  // `IsassignmentsStaffOrOwner` enforces the "who" server-side — a call
  // from anyone else 403s, which callers should treat as a normal
  // AssignmentApiException, not a special case to detect client-side.

  /// `PATCH {mount}/submissions/{id}/grade/` — free-form path only
  /// (`GradeFreeformSerializer`). Returns the updated submission.
  static Future<AssignmentSubmission> gradeFreeform({
    required String submissionId,
    required String grade,
    String feedback = '',
  }) async {
    final r = await http
        .patch(Uri.parse('$_base/submissions/$submissionId/grade/'),
            headers: await _headers(), body: jsonEncode({'grade': grade, 'feedback': feedback}))
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `PATCH {mount}/submissions/{id}/grade-rubric/` — project grading:
  /// `{"scores": {criterion: marks}, "feedback": ""}`. Returns the updated submission.
  static Future<AssignmentSubmission> gradeRubric({
    required String submissionId,
    required Map<String, int> scores,
    String feedback = '',
  }) async {
    final r = await http
        .patch(Uri.parse('$_base/submissions/$submissionId/grade-rubric/'),
            headers: await _headers(), body: jsonEncode({'scores': scores, 'feedback': feedback}))
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AssignmentSubmission.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `POST {mount}/submissions/{id}/answer/{qid}/review/` — structured
  /// path, `text`-type questions only (the view 400s for any other type).
  ///
  /// ⚠️ Returns a single `assigmentsAnswerSerializer` object, NOT the whole
  /// submission (`views.py`'s `review_answer` — `Response(
  /// assigmentsAnswerSerializer(answer).data)`). Callers must re-fetch the
  /// submission afterwards (`AssignmentService.getSubmission`) to see the
  /// possibly-updated overall `status` (checked / partially_checked).
  static Future<AssignmentAnswer> reviewAnswer({
    required String submissionId,
    required String questionId,
    required int marksAwarded,
    String feedback = '',
  }) async {
    final r = await http
        .post(Uri.parse('$_base/submissions/$submissionId/answer/$questionId/review/'),
            headers: await _headers(), body: jsonEncode({'marks_awarded': marksAwarded, 'feedback': feedback}))
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return AssignmentAnswer.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// `POST {mount}/submissions/{id}/publish/` — mints a fresh public slug.
  ///
  /// ⚠️ Returns `{"public_slug": "..."}` only (`views.py` — not the full
  /// submission shape every other write here returns). Returns the bare
  /// slug string; callers re-fetch the submission for the rest of its
  /// updated state if they need it.
  static Future<String> publishSubmission(String submissionId) async {
    final r = await http
        .post(Uri.parse('$_base/submissions/$submissionId/publish/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    final decoded = jsonDecode(utf8.decode(r.bodyBytes));
    return (decoded is Map ? decoded['public_slug'] : null)?.toString() ?? '';
  }

  /// `POST {mount}/submissions/{id}/unpublish/` — `views.py` returns a bare
  /// 204 No Content, so there is no body to decode here at all.
  static Future<void> unpublishSubmission(String submissionId) async {
    final r = await http
        .post(Uri.parse('$_base/submissions/$submissionId/unpublish/'), headers: await _headers())
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201 && r.statusCode != 204) _fail(r);
  }

  /// Full, absolute URL for a published submission's public page.
  ///
  /// ⚠️ This is a plain JSON API endpoint (`PublicSubmissionView` —
  /// `generics.RetrieveAPIView`), not a rendered web page. This backend
  /// zip doesn't ship a web front-end for it, so whoever opens this link
  /// in a browser today will see raw JSON, not a nice results page —
  /// callers surfacing this to a student should say so rather than imply
  /// it's a polished share link.
  static String publicUrlFor(String slug) => '$_base/public/$slug/';

  // ---------------- answer_data shapes ----------------
  //
  // ⚠️ Ye shapes ek hi jagah rakhi hain kyunki backend me inpe ek KNOWN
  // MISMATCH documented hai. `common/question_grading.py` (shared module)
  // mcq ko `answer_data == correct_answer` se compare karta hai — aur
  // assignments ka confirmed `correct_answer` shape `{"option_id": "..."}`
  // hai, isliye mcq answer bhi wahi dict shape bhejta hai. msq/list ke
  // liye wahi module `set(answer_data)` leta hai — yaani plain list.
  //
  // (Test Series app me msq ka shape ISSE ALAG hai:
  // `{"option_ids": [...]}` — dekho `testseries_service.dart`. Dono apps
  // ek hi grading module share karte hain par unki stored shapes alag
  // hain; ye backend ka documented sync gap hai, hamara bug nahi.)
  //
  // Backend kabhi in shapes ko align kare to sirf ye 4 functions badalni
  // hain, screens ko haath nahi lagana padega.

  static dynamic mcqAnswer(String optionId) => {'option_id': optionId};
  static dynamic msqAnswer(List<String> optionIds) => optionIds;
  static dynamic listAnswer(List<String> orderedOptionIds) => orderedOptionIds;
  static dynamic textAnswer(String text) => text;
}
