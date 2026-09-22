import 'dart:io';

import 'package:dio/dio.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import '../../l10n/app_localizations.dart';
import '../../services/auth_service.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/assignment_models.dart';
import '../services/assignment_service.dart';
import 'assignments_screen.dart' show assignmentStatusColor, assignmentStatusLabel, formatAssignmentDate;

// ============================================================
// ASSIGNMENT DETAIL + SUBMIT
//
// Ek hi screen do kaam karti hai, kyunki student ke liye ye ek hi cheez
// hai ("mera assignment"):
//   • submit nahi hua  → question form / free-form answer box
//   • submit ho gaya   → read-only result: har answer, marks, feedback
//
// Backend ke do submit paths ka mapping:
//   has_structured_questions = false → PATCH submit_freeform/
//   has_structured_questions = true  → POST  submit_structured/
// ============================================================

class AssignmentDetailScreen extends StatefulWidget {
  final String assignmentId;
  final AssignmentModel? initialAssignment;
  final AssignmentSubmission? initialSubmission;

  const AssignmentDetailScreen({
    super.key,
    required this.assignmentId,
    this.initialAssignment,
    this.initialSubmission,
  });

  @override
  State<AssignmentDetailScreen> createState() => _AssignmentDetailScreenState();
}

class _AssignmentDetailScreenState extends State<AssignmentDetailScreen> {
  AssignmentModel? _assignment;
  AssignmentSubmission? _submission;
  bool _loading = true;
  bool _failed = false;
  bool _submitting = false;
  bool _changed = false;

  // free-form
  final TextEditingController _writtenCtrl = TextEditingController();
  File? _freeformFile;

  // structured: questionId -> answer state
  final Map<String, String> _mcqAnswers = {};
  final Map<String, Set<String>> _msqAnswers = {};
  final Map<String, List<String>> _listAnswers = {};
  final Map<String, TextEditingController> _textAnswers = {};
  final Map<String, File> _answerFiles = {};

  // ---------------- grading (self-review of your own submission) ----------------
  //
  // Only meaningful for personal/self-assignments: there's no roster for
  // this `source`, so the only person who could ever end up on this
  // screen with something to grade is the same person who posted it.
  // `IsassignmentsStaffOrOwner` still enforces that server-side — these
  // controls just being visible doesn't mean the call will succeed for
  // someone who isn't allowed to grade this particular submission.
  final TextEditingController _gradeCtrl = TextEditingController();
  final TextEditingController _gradeFeedbackCtrl = TextEditingController();
  bool _gradeSeeded = false;
  bool _savingGrade = false;
  bool _publishing = false;

  @override
  void initState() {
    super.initState();
    _assignment = widget.initialAssignment;
    _submission = widget.initialSubmission;
    _loading = _assignment == null;
    _load();
  }

  @override
  void dispose() {
    _writtenCtrl.dispose();
    for (final c in _textAnswers.values) {
      c.dispose();
    }
    _gradeCtrl.dispose();
    _gradeFeedbackCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final assignment = await AssignmentService.getAssignment(widget.assignmentId);
      AssignmentSubmission? submission = _submission;
      if (submission != null) {
        // Detail kholte waqt submission ka fresh copy — list se aaya hua
        // object purana ho sakta hai (teacher ne beech me check kar diya ho).
        try {
          submission = await AssignmentService.getSubmission(submission.id);
        } catch (_) {}
      }
      if (!mounted) return;
      setState(() {
        _assignment = assignment;
        _submission = submission;
        _loading = false;
        _failed = false;
        _seedListAnswers();
        if (!_gradeSeeded && submission != null) {
          _gradeCtrl.text = submission.grade;
          _gradeFeedbackCtrl.text = submission.feedback;
          _gradeSeeded = true;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = _assignment == null;
      });
    }
  }

  /// Answer ka question dhoondhne ke liye — `firstWhere` yahan kaam nahi
  /// aata (nullable return chahiye), isliye plain loop.
  AssignmentQuestion? _questionById(AssignmentModel a, String id) {
    for (final q in a.questions) {
      if (q.id == id) return q;
    }
    return null;
  }

  /// `list` type questions ka default order = jaisa question me diya hai.
  /// Student usko drag karke badalta hai.
  void _seedListAnswers() {
    final a = _assignment;
    if (a == null) return;
    for (final q in a.questions) {
      if (q.type == AssignmentQuestionType.list && !_listAnswers.containsKey(q.id)) {
        _listAnswers[q.id] = q.options.map((o) => o.id).toList();
      }
      if (q.type == AssignmentQuestionType.text) {
        _textAnswers.putIfAbsent(q.id, () => TextEditingController());
      }
    }
  }

  bool get _isSubmitted => _submission != null && _submission!.isSubmitted;

  // ---------------- file pickers ----------------
  //
  // Dono pickers ab apni har failure UI ko batati hain (silent catch pehle
  // user ko bina kisi feedback ke chhod deta tha — tap karo, kuch na ho,
  // aur pata hi na chale ke fail hua ya user ne khud cancel kiya).

  Future<void> _pickFreeformFile() async {
    final f = await _pickAnyFile();
    if (f != null && mounted) setState(() => _freeformFile = f);
  }

  Future<File?> _pickAnyFile() async {
    try {
      const XTypeGroup all = XTypeGroup(label: 'all');
      final XFile? f = await openFile(acceptedTypeGroups: [all]);
      if (f == null) return null; // user ne cancel kiya — koi error nahi
      return File(f.path);
    } catch (e) {
      if (mounted) lsSnack(context, AppLocalizations.of(context)!.assignmentPickFailed, error: true);
      return null;
    }
  }

  /// Camera *ya* gallery — pehle sirf camera thi, jo un devices/emulators pe
  /// bhi zaroorat se zyada restrictive hai jahan camera hi nahi hota, ya
  /// jab student ke paas already li gayi photo ho.
  Future<void> _pickAnswerPhoto(String questionId) async {
    final l10n = AppLocalizations.of(context)!;
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.photo_camera_outlined),
            title: Text(l10n.assignmentPickFromCamera),
            onTap: () => Navigator.pop(ctx, ImageSource.camera),
          ),
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: Text(l10n.assignmentPickFromGallery),
            onTap: () => Navigator.pop(ctx, ImageSource.gallery),
          ),
        ]),
      ),
    );
    if (source == null || !mounted) return; // user ne sheet dismiss kar diya

    try {
      final picker = ImagePicker();
      final XFile? img = await picker.pickImage(source: source, imageQuality: 85);
      if (img != null && mounted) setState(() => _answerFiles[questionId] = File(img.path));
    } catch (e) {
      if (mounted) lsSnack(context, l10n.assignmentPickFailed, error: true);
    }
  }

  // ---------------- submit ----------------

  Future<void> _submit() async {
    final a = _assignment;
    if (a == null || _submitting) return;
    final l10n = AppLocalizations.of(context)!;

    // Kya kuch bhara bhi hai?
    final unanswered = a.hasStructuredQuestions ? _unansweredCount(a) : 0;
    if (!a.hasStructuredQuestions && _writtenCtrl.text.trim().isEmpty && _freeformFile == null) {
      lsSnack(context, l10n.assignmentNothingToSubmit, error: true);
      return;
    }

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.assignmentSubmitConfirmTitle),
        content: Text(unanswered > 0
            ? l10n.assignmentSubmitConfirmUnanswered(unanswered)
            : l10n.assignmentSubmitConfirmBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _submitting = true);
    try {
      // Submission row nahi hai (personal assignment ka pehla submit) to
      // pehle bana lo — campus/liveclass me ye row bridge already bana
      // chuka hota hai.
      var submission = _submission;
      submission ??= await AssignmentService.createSubmission(a.id);

      final AssignmentSubmission updated;
      if (a.hasStructuredQuestions) {
        updated = await AssignmentService.submitStructured(
          submissionId: submission.id,
          answers: _buildStructuredAnswers(a),
          filesByQuestionId: Map<String, File>.from(_answerFiles),
        );
      } else {
        updated = await AssignmentService.submitFreeform(
          submissionId: submission.id,
          writtenContent: _writtenCtrl.text.trim(),
          file: _freeformFile,
        );
      }

      if (!mounted) return;
      HapticFeedback.mediumImpact();
      setState(() {
        _submission = updated;
        _submitting = false;
        _changed = true;
      });
      lsSnack(context, l10n.assignmentSubmitSuccess);
    } catch (e) {
      if (!mounted) return;
      setState(() => _submitting = false);
      lsSnack(context, l10n.assignmentSubmitFailed, error: true);
    }
  }

  int _unansweredCount(AssignmentModel a) {
    var n = 0;
    for (final q in a.questions) {
      switch (q.type) {
        case AssignmentQuestionType.mcq:
          if (_mcqAnswers[q.id] == null) n++;
          break;
        case AssignmentQuestionType.msq:
          if ((_msqAnswers[q.id] ?? const {}).isEmpty) n++;
          break;
        case AssignmentQuestionType.text:
          if ((_textAnswers[q.id]?.text.trim().isEmpty ?? true) && _answerFiles[q.id] == null) n++;
          break;
        case AssignmentQuestionType.list:
        case AssignmentQuestionType.unknown:
          break; // list ka default order hi ek valid answer hai
      }
    }
    return n;
  }

  List<Map<String, dynamic>> _buildStructuredAnswers(AssignmentModel a) {
    final out = <Map<String, dynamic>>[];
    for (final q in a.questions) {
      dynamic data;
      switch (q.type) {
        case AssignmentQuestionType.mcq:
          final sel = _mcqAnswers[q.id];
          data = sel == null ? <String, dynamic>{} : AssignmentService.mcqAnswer(sel);
          break;
        case AssignmentQuestionType.msq:
          data = AssignmentService.msqAnswer((_msqAnswers[q.id] ?? const <String>{}).toList());
          break;
        case AssignmentQuestionType.list:
          data = AssignmentService.listAnswer(_listAnswers[q.id] ?? const []);
          break;
        case AssignmentQuestionType.text:
        case AssignmentQuestionType.unknown:
          data = AssignmentService.textAnswer(_textAnswers[q.id]?.text.trim() ?? '');
          break;
      }
      out.add({'question_id': q.id, 'answer_data': data});
    }
    return out;
  }

  // ---------------- attachment open ----------------

  Future<void> _openAttachment(String url, String fallbackName) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final token = await AuthService.getValidToken();
      final dir = await getTemporaryDirectory();
      final name = Uri.parse(url).pathSegments.isNotEmpty
          ? Uri.parse(url).pathSegments.last
          : fallbackName;
      final savePath = '${dir.path}/${name.replaceAll(' ', '_')}';
      if (!await File(savePath).exists()) {
        await Dio().download(url, savePath,
            options: Options(
                headers: token != null && token.isNotEmpty ? {'Authorization': 'Bearer $token'} : {}));
      }
      await OpenFilex.open(savePath);
    } catch (e) {
      // Raw exception text (stack-trace-ish Dio/IO errors) seedha user ko
      // dikhana production me kharab UX + minor info-leak dono hai —
      // isliye ek chhota, jaani-pehchaani set of causes friendly text me
      // map karte hain, aur sirf genuinely-unknown case me hi raw message
      // (already localized wrapper string ke andar) dikhate hain.
      if (!mounted) return;
      final msg = e is DioException
          ? (e.response?.statusCode == 404 ? l10n.assignmentFileNotFound : l10n.assignmentNoInternet)
          : e.toString();
      lsSnack(context, l10n.openFailed(msg), error: true);
    }
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(
        context,
        title: l10n.assignments,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => Navigator.pop(context, _changed),
        ),
      ),
      body: _buildBody(cs, l10n),
      bottomNavigationBar:
          (_assignment != null && !_isSubmitted && !_loading) ? _buildSubmitBar(cs, l10n) : null,
    );
  }

  // ---------------- grading / review / share actions ----------------

  Future<void> _saveGrade() async {
    final l10n = AppLocalizations.of(context)!;
    if (_submission == null) return;
    setState(() => _savingGrade = true);
    try {
      final updated = await AssignmentService.gradeFreeform(
        submissionId: _submission!.id,
        grade: _gradeCtrl.text.trim(),
        feedback: _gradeFeedbackCtrl.text.trim(),
      );
      if (!mounted) return;
      setState(() => _submission = updated);
      lsSnack(context, l10n.assignmentGradeSaved);
    } catch (e) {
      if (mounted) lsSnack(context, l10n.assignmentGradeFailed, error: true);
    } finally {
      if (mounted) setState(() => _savingGrade = false);
    }
  }

  /// `review_answer` returns just the single updated answer (see
  /// `assignment_service.dart`'s docstring on `reviewAnswer`) — the
  /// overall submission `status` (checked / partially_checked) can only
  /// change as a side effect, so we always re-fetch the whole submission
  /// afterwards rather than trying to patch one answer into local state.
  Future<void> _reviewAnswer(String questionId, int marksAwarded, String feedback) async {
    final l10n = AppLocalizations.of(context)!;
    if (_submission == null) return;
    try {
      await AssignmentService.reviewAnswer(
        submissionId: _submission!.id,
        questionId: questionId,
        marksAwarded: marksAwarded,
        feedback: feedback,
      );
      final refreshed = await AssignmentService.getSubmission(_submission!.id);
      if (!mounted) return;
      setState(() => _submission = refreshed);
      lsSnack(context, l10n.assignmentReviewSaved);
    } catch (e) {
      if (mounted) lsSnack(context, l10n.assignmentReviewFailed, error: true);
    }
  }

  Future<void> _togglePublish() async {
    final l10n = AppLocalizations.of(context)!;
    if (_submission == null || _publishing) return;
    setState(() => _publishing = true);
    try {
      if (_submission!.publicSlug.isEmpty) {
        await AssignmentService.publishSubmission(_submission!.id);
      } else {
        await AssignmentService.unpublishSubmission(_submission!.id);
      }
      final refreshed = await AssignmentService.getSubmission(_submission!.id);
      if (!mounted) return;
      setState(() => _submission = refreshed);
      if (refreshed.publicSlug.isNotEmpty) {
        // Ye ek pure JSON API endpoint hai (`PublicSubmissionView`), ek
        // asli rendered webpage nahi — koi web wrapper is backend zip me
        // nahi hai. Share karte waqt yahi seedha bata dete hain, warna
        // link kholne wale ko sirf raw JSON dikhega aur confuse hoga.
        await Clipboard.setData(ClipboardData(text: AssignmentService.publicUrlFor(refreshed.publicSlug)));
        if (mounted) lsSnack(context, l10n.assignmentLinkCopied);
      }
    } catch (e) {
      if (mounted) {
        lsSnack(
          context,
          _submission!.publicSlug.isEmpty ? l10n.assignmentPublishFailed : l10n.assignmentUnpublishFailed,
          error: true,
        );
      }
    } finally {
      if (mounted) setState(() => _publishing = false);
    }
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _assignment == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_failed && _assignment == null) {
      return ErrorStateWidget(
        title: l10n.assignmentsErrorTitle,
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final a = _assignment!;
    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(top: 6, bottom: 32),
        children: [
          _buildHeaderCard(a, cs, l10n),
          if (_isSubmitted) ..._buildResult(a, cs, l10n) else ..._buildForm(a, cs, l10n),
        ],
      ),
    );
  }

  Widget _buildHeaderCard(AssignmentModel a, ColorScheme cs, AppLocalizations l10n) {
    final status = _submission?.status ?? AssignmentStatus.missing;
    final statusColor = assignmentStatusColor(context, status);
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(child: Text(a.title, style: LsType.head(context, size: 16))),
          const SizedBox(width: 10),
          LsStatusChip(label: assignmentStatusLabel(l10n, status), color: statusColor),
        ]),
        if (a.postedBy.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(l10n.assignmentPostedBy(a.postedBy),
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ],
        if (a.description.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(a.description, style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface.withOpacity(.92))),
        ],
        const SizedBox(height: 12),
        Divider(height: 1, color: cs.outlineVariant),
        const SizedBox(height: 8),
        LsMetaRow(
          icon: Icons.schedule_rounded,
          label: l10n.assignmentDueLabel,
          value: a.dueDate == null ? l10n.assignmentNoDueDate : formatAssignmentDate(context, a.dueDate!),
          valueColor: (!_isSubmitted && a.isOverdue) ? lsTokens(context).danger : null,
        ),
        LsMetaRow(
          icon: Icons.workspace_premium_outlined,
          label: l10n.assignmentTotalMarksLabel,
          value: '${a.totalMarks}',
        ),
        if (a.hasStructuredQuestions)
          LsMetaRow(
            icon: Icons.help_outline_rounded,
            label: l10n.assignmentQuestionsLabel,
            value: '${a.questions.length}',
          ),
        if (a.attachment != null) ...[
          const SizedBox(height: 8),
          LsOutlineButton(
            label: l10n.assignmentOpenAttachment,
            icon: Icons.attach_file_rounded,
            onPressed: () => _openAttachment(a.attachment!, 'assignment'),
          ),
        ],
      ]),
    );
  }

  // ---------------- form ----------------

  List<Widget> _buildForm(AssignmentModel a, ColorScheme cs, AppLocalizations l10n) {
    if (!_isSubmitted && a.isOverdue) {
      return [
        _lateNotice(cs, l10n),
        ..._formBody(a, cs, l10n),
      ];
    }
    return _formBody(a, cs, l10n);
  }

  Widget _lateNotice(ColorScheme cs, AppLocalizations l10n) {
    final t = lsTokens(context);
    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(
        color: t.warning.withOpacity(Theme.of(context).brightness == Brightness.dark ? .18 : .12),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(children: [
        Icon(Icons.warning_amber_rounded, size: 17, color: t.warning),
        const SizedBox(width: 9),
        Expanded(
          child: Text(l10n.assignmentLateNotice,
              style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurface)),
        ),
      ]),
    );
  }

  List<Widget> _formBody(AssignmentModel a, ColorScheme cs, AppLocalizations l10n) {
    if (!a.hasStructuredQuestions) {
      return [
        LsCard(
          margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.assignmentYourAnswer, style: LsType.head(context, size: 13)),
            const SizedBox(height: 10),
            TextField(
              controller: _writtenCtrl,
              maxLines: 8,
              minLines: 5,
              textCapitalization: TextCapitalization.sentences,
              style: TextStyle(fontSize: 13.5, height: 1.5, color: cs.onSurface),
              decoration: InputDecoration(hintText: l10n.assignmentAnswerHint),
            ),
            const SizedBox(height: 12),
            _fileRow(
              file: _freeformFile,
              onPick: _pickFreeformFile,
              onRemove: () => setState(() => _freeformFile = null),
              l10n: l10n,
              cs: cs,
            ),
          ]),
        ),
      ];
    }

    if (a.questions.isEmpty) {
      return [
        EmptyStateWidget(icon: Icons.help_outline_rounded, title: l10n.assignmentNoQuestions),
      ];
    }

    return [
      for (int i = 0; i < a.questions.length; i++)
        _QuestionCard(
          index: i,
          total: a.questions.length,
          question: a.questions[i],
          mcqValue: _mcqAnswers[a.questions[i].id],
          msqValue: _msqAnswers[a.questions[i].id] ?? <String>{},
          listValue: _listAnswers[a.questions[i].id] ?? const [],
          textController: _textAnswers[a.questions[i].id],
          attachedFile: _answerFiles[a.questions[i].id],
          onMcq: (v) => setState(() => _mcqAnswers[a.questions[i].id] = v),
          onMsq: (v) => setState(() => _msqAnswers[a.questions[i].id] = v),
          onList: (v) => setState(() => _listAnswers[a.questions[i].id] = v),
          onAttach: () => _pickAnswerPhoto(a.questions[i].id),
          onRemoveAttach: () => setState(() => _answerFiles.remove(a.questions[i].id)),
          onOpenAttachment: _openAttachment,
        ),
    ];
  }

  Widget _fileRow({
    required File? file,
    required VoidCallback onPick,
    required VoidCallback onRemove,
    required AppLocalizations l10n,
    required ColorScheme cs,
  }) {
    if (file == null) {
      return LsOutlineButton(label: l10n.assignmentAttachFile, icon: Icons.attach_file_rounded, onPressed: onPick);
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: cs.surfaceVariant,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(children: [
        Icon(Icons.insert_drive_file_outlined, size: 16, color: cs.onSurfaceVariant),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            file.path.split('/').last,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12, color: cs.onSurface),
          ),
        ),
        IconButton(
          visualDensity: VisualDensity.compact,
          icon: Icon(Icons.close_rounded, size: 17, color: cs.onSurfaceVariant),
          tooltip: l10n.assignmentRemoveFile,
          onPressed: onRemove,
        ),
      ]),
    );
  }

  Widget _buildSubmitBar(ColorScheme cs, AppLocalizations l10n) {
    final a = _assignment!;
    final answered = a.hasStructuredQuestions ? a.questions.length - _unansweredCount(a) : 0;
    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border(top: BorderSide(color: cs.outlineVariant)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
          child: Row(children: [
            if (a.hasStructuredQuestions) ...[
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                  Text(l10n.assignmentAnsweredOf(answered, a.questions.length),
                      style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
                  const SizedBox(height: 6),
                  LsProgressBar(value: a.questions.isEmpty ? 0 : answered / a.questions.length),
                ]),
              ),
              const SizedBox(width: 14),
            ] else
              const Spacer(),
            LsPrimaryButton(
              label: l10n.assignmentSubmit,
              icon: Icons.send_rounded,
              expanded: false,
              loading: _submitting,
              onPressed: _submitting ? null : _submit,
            ),
          ]),
        ),
      ),
    );
  }

  // ---------------- result ----------------

  List<Widget> _buildResult(AssignmentModel a, ColorScheme cs, AppLocalizations l10n) {
    final s = _submission!;
    final t = lsTokens(context);
    final widgets = <Widget>[];

    widgets.add(LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l10n.assignmentResult, style: LsType.head(context, size: 13)),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: LsScoreTile(
              value: s.totalMarksAwarded != null ? '${s.totalMarksAwarded}' : '—',
              label: l10n.assignmentMarksAwarded,
              color: t.success,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: LsScoreTile(
              value: '${a.totalMarks}',
              label: l10n.assignmentTotalMarksLabel,
              color: cs.primary,
            ),
          ),
          if (s.grade.isNotEmpty) ...[
            const SizedBox(width: 10),
            Expanded(
              child: LsScoreTile(value: s.grade, label: l10n.assignmentGrade, color: t.info),
            ),
          ],
        ]),
        const SizedBox(height: 12),
        if (s.submittedAt != null)
          LsMetaRow(
            icon: Icons.upload_rounded,
            label: l10n.assignmentSubmittedOn,
            value: formatAssignmentDate(context, s.submittedAt!),
            valueColor: s.isLate ? t.warning : null,
          ),
        if (s.checkedAt != null)
          LsMetaRow(
            icon: Icons.verified_outlined,
            label: l10n.assignmentCheckedOn,
            value: formatAssignmentDate(context, s.checkedAt!),
          ),
        if (s.status == AssignmentStatus.partiallyChecked) ...[
          const SizedBox(height: 8),
          Row(children: [
            Icon(Icons.hourglass_bottom_rounded, size: 15, color: t.info),
            const SizedBox(width: 8),
            Expanded(
              child: Text(l10n.assignmentPartiallyCheckedNote,
                  style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurfaceVariant)),
            ),
          ]),
        ],
        if (s.feedback.isNotEmpty) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(l10n.assignmentTeacherFeedback,
                  style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
              const SizedBox(height: 5),
              Text(s.feedback, style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
            ]),
          ),
        ],
        const SizedBox(height: 12),
        Divider(height: 1, color: cs.outlineVariant),
        const SizedBox(height: 10),
        Row(children: [
          Icon(s.publicSlug.isEmpty ? Icons.lock_outline_rounded : Icons.public_rounded,
              size: 15, color: cs.onSurfaceVariant),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              s.publicSlug.isEmpty ? l10n.assignmentNotShared : l10n.assignmentSharePublicNote,
              style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
            ),
          ),
          const SizedBox(width: 8),
          LsOutlineButton(
            label: s.publicSlug.isEmpty ? l10n.assignmentShareResult : l10n.assignmentUnshareResult,
            icon: s.publicSlug.isEmpty ? Icons.share_outlined : Icons.link_off_rounded,
            onPressed: _publishing ? null : _togglePublish,
          ),
        ]),
      ]),
    ));

    // Free-form submission — jo bheja tha wahi dikhao.
    if (!a.hasStructuredQuestions) {
      widgets.add(LsCard(
        margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.assignmentYourAnswer, style: LsType.head(context, size: 13)),
          const SizedBox(height: 9),
          if (s.writtenContent.isNotEmpty)
            Text(s.writtenContent, style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface))
          else
            Text(l10n.assignmentNoWrittenAnswer, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
          if (s.file != null) ...[
            const SizedBox(height: 12),
            LsOutlineButton(
              label: l10n.assignmentOpenSubmittedFile,
              icon: Icons.insert_drive_file_outlined,
              onPressed: () => _openAttachment(s.file!, 'submission'),
            ),
          ],
        ]),
      ));
      widgets.add(_buildGradePanel(cs, l10n));
      return widgets;
    }

    // Structured — per-answer breakdown.
    for (int i = 0; i < s.answers.length; i++) {
      widgets.add(_AnswerResultCard(
        index: i,
        answer: s.answers[i],
        question: _questionById(a, s.answers[i].questionId),
        onOpenAttachment: _openAttachment,
        onReview: (marksAwarded, feedback) => _reviewAnswer(s.answers[i].questionId, marksAwarded, feedback),
      ));
    }
    return widgets;
  }

  /// Free-form assignments never auto-grade — the only way `grade` /
  /// `feedback` ever get set is this panel calling `grade_freeform`
  /// (`GradeFreeformSerializer`). Shown always (not just pre-grading) so
  /// the same person can revisit and adjust their own self-assigned mark.
  Widget _buildGradePanel(ColorScheme cs, AppLocalizations l10n) {
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l10n.assignmentGradeSectionTitle, style: LsType.head(context, size: 13)),
        const SizedBox(height: 10),
        TextField(
          controller: _gradeCtrl,
          // Backend `GradeFreeformSerializer.grade` is `CharField(max_length=10)`
          // — enforcing the same limit here avoids a 400 on save.
          maxLength: 10,
          decoration: InputDecoration(labelText: l10n.assignmentGrade),
        ),
        const SizedBox(height: 10),
        TextField(
          controller: _gradeFeedbackCtrl,
          minLines: 2,
          maxLines: 4,
          decoration: InputDecoration(hintText: l10n.assignmentFeedbackHint),
        ),
        const SizedBox(height: 12),
        LsPrimaryButton(
          label: l10n.save,
          icon: Icons.check_rounded,
          loading: _savingGrade,
          onPressed: _savingGrade ? null : _saveGrade,
        ),
      ]),
    );
  }
}

// ============================================================
// QUESTION CARD (form mode)
// ============================================================

class _QuestionCard extends StatelessWidget {
  final int index;
  final int total;
  final AssignmentQuestion question;
  final String? mcqValue;
  final Set<String> msqValue;
  final List<String> listValue;
  final TextEditingController? textController;
  final File? attachedFile;
  final ValueChanged<String> onMcq;
  final ValueChanged<Set<String>> onMsq;
  final ValueChanged<List<String>> onList;
  final VoidCallback onAttach;
  final VoidCallback onRemoveAttach;
  final Future<void> Function(String url, String name) onOpenAttachment;

  const _QuestionCard({
    required this.index,
    required this.total,
    required this.question,
    required this.mcqValue,
    required this.msqValue,
    required this.listValue,
    required this.textController,
    required this.attachedFile,
    required this.onMcq,
    required this.onMsq,
    required this.onList,
    required this.onAttach,
    required this.onRemoveAttach,
    required this.onOpenAttachment,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionOf(index + 1, total),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const Spacer(),
          LsStatusChip(label: l10n.marksShort(question.marks), color: cs.onSurfaceVariant),
        ]),
        const SizedBox(height: 9),
        Text(question.text, style: TextStyle(fontSize: 13.5, height: 1.45, color: cs.onSurface)),
        if (question.attachment != null) ...[
          const SizedBox(height: 10),
          LsOutlineButton(
            label: l10n.assignmentOpenAttachment,
            icon: Icons.image_outlined,
            onPressed: () => onOpenAttachment(question.attachment!, 'question'),
          ),
        ],
        const SizedBox(height: 4),
        Text(_hint(l10n), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
        const SizedBox(height: 8),
        ..._input(context, cs, l10n),
      ]),
    );
  }

  String _hint(AppLocalizations l10n) {
    switch (question.type) {
      case AssignmentQuestionType.mcq:
        return l10n.answerHintSelectOne;
      case AssignmentQuestionType.msq:
        return l10n.answerHintSelectMultiple;
      case AssignmentQuestionType.list:
        return l10n.answerHintArrange;
      case AssignmentQuestionType.text:
      case AssignmentQuestionType.unknown:
        return l10n.answerHintText;
    }
  }

  List<Widget> _input(BuildContext context, ColorScheme cs, AppLocalizations l10n) {
    switch (question.type) {
      case AssignmentQuestionType.mcq:
        return question.options
            .map((o) => _OptionTile(
                  label: o.text,
                  selected: mcqValue == o.id,
                  multi: false,
                  onTap: () => onMcq(o.id),
                ))
            .toList();

      case AssignmentQuestionType.msq:
        return question.options.map((o) {
          final sel = msqValue.contains(o.id);
          return _OptionTile(
            label: o.text,
            selected: sel,
            multi: true,
            onTap: () {
              final next = Set<String>.from(msqValue);
              if (sel) {
                next.remove(o.id);
              } else {
                next.add(o.id);
              }
              onMsq(next);
            },
          );
        }).toList();

      case AssignmentQuestionType.list:
        final order = listValue.isEmpty ? question.options.map((o) => o.id).toList() : listValue;
        return [
          // shrinkWrap ReorderableListView — parent ListView ke andar hai,
          // isliye apni scrolling nahi karti.
          ReorderableListView(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            buildDefaultDragHandles: false,
            onReorder: (oldIndex, newIndex) {
              final next = List<String>.from(order);
              if (newIndex > oldIndex) newIndex -= 1;
              next.insert(newIndex, next.removeAt(oldIndex));
              onList(next);
            },
            children: [
              for (int i = 0; i < order.length; i++)
                Padding(
                  key: ValueKey(order[i]),
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
                    decoration: BoxDecoration(
                      color: cs.surfaceVariant,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: cs.outlineVariant),
                    ),
                    child: Row(children: [
                      Text('${i + 1}',
                          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary)),
                      const SizedBox(width: 11),
                      Expanded(
                        child: Text(_optionText(order[i]),
                            style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
                      ),
                      ReorderableDragStartListener(
                        index: i,
                        child: Icon(Icons.drag_handle_rounded, size: 18, color: cs.onSurfaceVariant),
                      ),
                    ]),
                  ),
                ),
            ],
          ),
        ];

      case AssignmentQuestionType.text:
      case AssignmentQuestionType.unknown:
        return [
          TextField(
            controller: textController,
            maxLines: 6,
            minLines: 3,
            textCapitalization: TextCapitalization.sentences,
            style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface),
            decoration: InputDecoration(hintText: l10n.assignmentAnswerHint),
          ),
          const SizedBox(height: 10),
          if (attachedFile == null)
            LsOutlineButton(
              label: l10n.assignmentAttachPhoto,
              icon: Icons.photo_camera_outlined,
              onPressed: onAttach,
            )
          else
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
              child: Row(children: [
                Icon(Icons.image_outlined, size: 16, color: cs.onSurfaceVariant),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(attachedFile!.path.split('/').last,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12, color: cs.onSurface)),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  icon: Icon(Icons.close_rounded, size: 17, color: cs.onSurfaceVariant),
                  onPressed: onRemoveAttach,
                ),
              ]),
            ),
        ];
    }
  }

  String _optionText(String id) {
    for (final o in question.options) {
      if (o.id == id) return o.text;
    }
    return id;
  }
}

class _OptionTile extends StatelessWidget {
  final String label;
  final bool selected;
  final bool multi;
  final VoidCallback onTap;
  const _OptionTile({required this.label, required this.selected, required this.multi, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Semantics(
        inMutuallyExclusiveGroup: !multi,
        checked: selected,
        label: label,
        child: GestureDetector(
          onTap: () {
            HapticFeedback.selectionClick();
            onTap();
          },
          behavior: HitTestBehavior.opaque,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
            decoration: BoxDecoration(
              color: selected ? cs.primary.withOpacity(.12) : cs.surfaceVariant,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: selected ? cs.primary : cs.outlineVariant),
            ),
            child: Row(children: [
              Icon(
                multi
                    ? (selected ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded)
                    : (selected ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded),
                size: 18,
                color: selected ? cs.primary : cs.onSurfaceVariant,
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Text(label,
                    style: TextStyle(
                        fontSize: 12.5,
                        height: 1.4,
                        fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                        color: cs.onSurface)),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// ANSWER RESULT CARD (read-only mode)
// ============================================================

class _AnswerResultCard extends StatefulWidget {
  final int index;
  final AssignmentAnswer answer;
  final AssignmentQuestion? question;
  final Future<void> Function(String url, String name) onOpenAttachment;
  final Future<void> Function(int marksAwarded, String feedback) onReview;

  const _AnswerResultCard({
    required this.index,
    required this.answer,
    required this.question,
    required this.onOpenAttachment,
    required this.onReview,
  });

  @override
  State<_AnswerResultCard> createState() => _AnswerResultCardState();
}

class _AnswerResultCardState extends State<_AnswerResultCard> {
  late final TextEditingController _marksCtrl =
      TextEditingController(text: '${widget.answer.marksAwarded ?? 0}');
  late final TextEditingController _feedbackCtrl = TextEditingController(text: widget.answer.reviewerFeedback);
  bool _saving = false;

  @override
  void dispose() {
    _marksCtrl.dispose();
    _feedbackCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    // Backend note: `AnswerReviewSerializer` itself only checks
    // `marks_awarded >= 0`; the upper bound (`<= question.marks`) is only
    // enforced later, inside `assigmentsAnswer.mark_answer()`, as a plain
    // Python `ValueError` that `views.py`'s `review_answer` action never
    // catches — an out-of-range value wouldn't come back as a clean 400,
    // it would 500. Clamping client-side avoids relying on that at all.
    final parsed = int.tryParse(_marksCtrl.text.trim()) ?? 0;
    final maxMarks = widget.answer.questionMarks;
    final marks = parsed < 0 ? 0 : (parsed > maxMarks ? maxMarks : parsed);
    if (marks != parsed) _marksCtrl.text = '$marks';
    setState(() => _saving = true);
    try {
      await widget.onReview(marks, _feedbackCtrl.text.trim());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final answer = widget.answer;
    final question = widget.question;
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;

    final Color statusColor;
    final String statusLabel;
    if (answer.awaitingReview) {
      statusColor = t.info;
      statusLabel = l10n.assignmentAwaitingReview;
    } else if (answer.isCorrect == true) {
      statusColor = t.success;
      statusLabel = l10n.answerCorrect;
    } else if (answer.isCorrect == false) {
      statusColor = t.danger;
      statusLabel = l10n.answerIncorrect;
    } else {
      statusColor = cs.primary;
      statusLabel = l10n.assignmentReviewed;
    }

    // `review_answer` sirf text-type questions pe allowed hai (`views.py`
    // baaki sab types ke liye 400 deta hai) — so the inline review form
    // only ever appears here, never for mcq/msq/list (jo waise bhi
    // auto-graded hone ki wajah se kabhi `awaitingReview` hote hi nahi).
    final canReview = answer.awaitingReview && question?.type == AssignmentQuestionType.text;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionShort(widget.index + 1),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const Spacer(),
          LsStatusChip(label: statusLabel, color: statusColor),
          const SizedBox(width: 8),
          Text(
            l10n.assignmentMarksOf(answer.marksAwarded ?? 0, answer.questionMarks),
            style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurface),
          ),
        ]),
        const SizedBox(height: 9),
        Text(answer.questionText.isNotEmpty ? answer.questionText : (question?.text ?? ''),
            style: TextStyle(fontSize: 13, height: 1.45, color: cs.onSurface)),
        const SizedBox(height: 11),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(11),
          decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.assignmentYourAnswer,
                style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
            const SizedBox(height: 5),
            Text(
              _readableAnswer(l10n),
              style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface),
            ),
          ]),
        ),
        if (answer.answerAttachment != null) ...[
          const SizedBox(height: 10),
          LsOutlineButton(
            label: l10n.assignmentOpenSubmittedFile,
            icon: Icons.image_outlined,
            onPressed: () => widget.onOpenAttachment(answer.answerAttachment!, 'answer'),
          ),
        ],
        if (answer.reviewerFeedback.isNotEmpty) ...[
          const SizedBox(height: 10),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.rate_review_outlined, size: 15, color: cs.onSurfaceVariant),
            const SizedBox(width: 8),
            Expanded(
              child: Text(answer.reviewerFeedback,
                  style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurfaceVariant)),
            ),
          ]),
        ],
        if (canReview) ...[
          const SizedBox(height: 12),
          Divider(height: 1, color: cs.outlineVariant),
          const SizedBox(height: 10),
          Text(l10n.assignmentReviewAnswerTitle,
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
          const SizedBox(height: 8),
          Row(children: [
            SizedBox(
              width: 90,
              child: TextField(
                controller: _marksCtrl,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(labelText: l10n.assignmentMarksAwarded),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: TextField(
                controller: _feedbackCtrl,
                decoration: InputDecoration(hintText: l10n.assignmentFeedbackHint),
              ),
            ),
          ]),
          const SizedBox(height: 10),
          LsPrimaryButton(
            label: l10n.save,
            icon: Icons.check_rounded,
            expanded: false,
            loading: _saving,
            onPressed: _saving ? null : _save,
          ),
        ],
      ]),
    );
  }

  /// `answer_data` ka shape question type pe depend karta hai (string /
  /// `{"option_id": ...}` / list). Yahan sirf padhne layak text banana hai,
  /// isliye teeno shapes ko tolerantly handle kiya hai — koi bhi unexpected
  /// shape aaye to raw toString, crash nahi.
  String _readableAnswer(AppLocalizations l10n) {
    final data = widget.answer.answerData;
    if (data == null) return l10n.answerNotAnswered;

    String labelFor(String id) {
      final q = widget.question;
      if (q == null) return id;
      for (final o in q.options) {
        if (o.id == id) return o.text;
      }
      return id;
    }

    if (data is String) return data.trim().isEmpty ? l10n.answerNotAnswered : data;
    if (data is Map) {
      final optionId = data['option_id'];
      if (optionId != null) return labelFor(optionId.toString());
      final ids = data['option_ids'];
      if (ids is List) {
        return ids.isEmpty ? l10n.answerNotAnswered : ids.map((e) => labelFor(e.toString())).join(', ');
      }
      if (data.isEmpty) return l10n.answerNotAnswered;
      return data.toString();
    }
    if (data is List) {
      return data.isEmpty ? l10n.answerNotAnswered : data.map((e) => labelFor(e.toString())).join(', ');
    }
    return data.toString();
  }
}