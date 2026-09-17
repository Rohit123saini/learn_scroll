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
//   GET    {mount}/public/{slug}/                    (auth-free)
//
// Har call `AuthService.getValidToken()` use karti hai (getToken() nahi) —
// Task 8.4 wahi rule: expired token ke saath request bhejne se
// `onForceLogout` kabhi trigger nahi hota aur session expiry silently
// fail hoti rehti hai.
// ============================================================

/// 🔧 CONFIRM WITH BACKEND — `assigments.urls` root URLconf me kahan mount
/// hai. urls.py ka apna docstring example `path("api/assigments/", ...)`
/// dikhata hai, par is app ke baaki endpoints (`/post/...`,
/// `/liveclass/...`) bina `/api` prefix ke chalte hain — isliye default
/// yahan `/assigments` rakha hai. Galat ho to sirf ye ek line badalni hai.
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
  }) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) throw AssignmentApiException('NOT_AUTHENTICATED');

    final req = http.MultipartRequest(
      'PATCH',
      Uri.parse('$_base/submissions/$submissionId/submit_freeform/'),
    )..headers['Authorization'] = 'Bearer $token';

    req.fields['written_content'] = writtenContent;
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
