import 'dart:async';
import 'dart:io';
// FontFeature — timer ke digits tabular rahein, warna har second pe width
// badalti hai aur ghadi "hilti" dikhti hai.
import 'dart:ui' show FontFeature;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import 'test_result_screen.dart';

// ============================================================
// TEST ATTEMPT — QUESTION PLAYER
//
// Ek PageView, ek timer, ek question palette. Sab kuch theme + locale
// aware.
//
// ⚠️ TIMER KE BAARE ME EK ZAROORI BAAT (frontend MD me bhi likha hai):
// backend `TestAttempt` me koi `started_at` field expose nahi hoti (§4.6
// ka field list dekho) — sirf `submitted_at` hoti hai. Iska matlab timer
// ko **client side** chalana padta hai. Isliye:
//   • start time SharedPreferences me attempt-id ke against save hoti hai,
//     taaki app band karke wapas khole to ghadi reset na ho (warna
//     unlimited time mil jaata);
//   • par ye enforcement NAHI hai — app data clear karke user timer bypass
//     kar sakta hai. Asli enforcement ke liye backend ko `started_at` +
//     submit-time deadline check chahiye. Tab tak ye ek honest UX timer
//     hai, exam-grade guard nahi.
// ============================================================

class TestAttemptScreen extends StatefulWidget {
  final String attemptId;
  final TestSeriesModel series;

  const TestAttemptScreen({super.key, required this.attemptId, required this.series});

  @override
  State<TestAttemptScreen> createState() => _TestAttemptScreenState();
}

class _TestAttemptScreenState extends State<TestAttemptScreen> {
  final PageController _pager = PageController();

  List<TsQuestion> _questions = [];
  bool _loading = true;
  bool _failed = false;
  bool _submitting = false;
  int _index = 0;

  // answers
  final Map<String, String> _mcq = {};
  final Map<String, Set<String>> _msq = {};
  final Map<String, List<String>> _order = {};
  final Map<String, Map<String, String>> _match = {};
  final Map<String, TextEditingController> _text = {};
  final Map<String, File> _files = {};

  // timer
  Timer? _ticker;
  DateTime? _deadline;
  Duration _remaining = Duration.zero;

  @override
  void initState() {
    super.initState();
    // Test ke beech screen band na ho — home.dart ke comment upload flow
    // me bhi yahi pattern use hota hai.
    WakelockPlus.enable();
    _load();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _pager.dispose();
    for (final c in _text.values) {
      c.dispose();
    }
    WakelockPlus.disable();
    super.dispose();
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _failed = false;
      });
    }
    try {
      final qs = await TestSeriesService.getQuestions(widget.series.id);
      if (!mounted) return;
      for (final q in qs) {
        if (q.type == TsQuestionType.text) {
          _text.putIfAbsent(q.id, () => TextEditingController());
        }
        if (q.type == TsQuestionType.list && q.listMode == TsListMode.order) {
          _order.putIfAbsent(q.id, () => q.choices.map((o) => o.id).toList());
        }
      }
      setState(() {
        _questions = qs;
        _loading = false;
      });
      await _startTimer();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  // ---------------- timer ----------------

  Future<void> _startTimer() async {
    final minutes = widget.series.durationMinutes;
    if (minutes == null || minutes <= 0) return;

    final prefs = await SharedPreferences.getInstance();
    final key = 'ts_attempt_started_${widget.attemptId}';
    int? startedMs = prefs.getInt(key);
    if (startedMs == null) {
      startedMs = DateTime.now().millisecondsSinceEpoch;
      await prefs.setInt(key, startedMs);
    }

    final deadline = DateTime.fromMillisecondsSinceEpoch(startedMs).add(Duration(minutes: minutes));
    if (!mounted) return;
    setState(() {
      _deadline = deadline;
      _remaining = deadline.difference(DateTime.now());
    });

    _ticker?.cancel();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      final left = _deadline!.difference(DateTime.now());
      setState(() => _remaining = left);
      if (left.isNegative) {
        _ticker?.cancel();
        _autoSubmit();
      }
    });
  }

  Future<void> _clearTimerKey() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('ts_attempt_started_${widget.attemptId}');
    } catch (_) {}
  }

  String _formatRemaining(Duration d) {
    if (d.isNegative) d = Duration.zero;
    final h = d.inHours;
    final m = d.inMinutes % 60;
    final s = d.inSeconds % 60;
    final mm = m.toString().padLeft(2, '0');
    final ss = s.toString().padLeft(2, '0');
    return h > 0 ? '$h:$mm:$ss' : '$mm:$ss';
  }

  // ---------------- answers ----------------

  bool _isAnswered(TsQuestion q) {
    switch (q.type) {
      case TsQuestionType.mcq:
        return _mcq[q.id] != null;
      case TsQuestionType.msq:
        return (_msq[q.id] ?? const {}).isNotEmpty;
      case TsQuestionType.list:
        if (q.listMode == TsListMode.match) return (_match[q.id] ?? const {}).isNotEmpty;
        // order type ka default sequence bhi ek valid answer hai, par
        // "answered" tabhi maano jab user ne usko chhua ho — warna palette
        // me sab green dikhega bina kuch kiye.
        return _order.containsKey(q.id) && _orderTouched.contains(q.id);
      case TsQuestionType.text:
      case TsQuestionType.unknown:
        return (_text[q.id]?.text.trim().isNotEmpty ?? false) || _files[q.id] != null;
    }
  }

  final Set<String> _orderTouched = {};

  int get _answeredCount => _questions.where(_isAnswered).length;

  Map<String, dynamic> _buildAnswers() {
    final out = <String, dynamic>{};
    for (final q in _questions) {
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
            out[q.id] = TestSeriesService.matchAnswer(_match[q.id] ?? const {});
          } else {
            out[q.id] = TestSeriesService.orderAnswer(_order[q.id] ?? const []);
          }
          break;
        case TsQuestionType.text:
        case TsQuestionType.unknown:
          // File-only answer ke liye bhi entry bhejni hoti hai — backend
          // `answer_<question_id>` file ko isi entry pe merge karta hai.
          out[q.id] = TestSeriesService.textAnswer(_text[q.id]?.text.trim() ?? '');
          break;
      }
    }
    return out;
  }

  // ---------------- submit ----------------

  Future<void> _autoSubmit() async {
    if (_submitting || !mounted) return;
    final l10n = AppLocalizations.of(context)!;
    lsSnack(context, l10n.testTimeUp);
    await _doSubmit(showConfirm: false);
  }

  Future<void> _confirmSubmit() async {
    final l10n = AppLocalizations.of(context)!;
    final unanswered = _questions.length - _answeredCount;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.testSubmitConfirmTitle),
        content: Text(unanswered > 0
            ? l10n.testSubmitConfirmUnanswered(unanswered)
            : l10n.testSubmitConfirmBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.testSubmit)),
        ],
      ),
    );
    if (ok == true) await _doSubmit(showConfirm: false);
  }

  Future<void> _doSubmit({bool showConfirm = true}) async {
    if (_submitting) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _submitting = true);
    try {
      final attempt = await TestSeriesService.submitAttempt(
        attemptId: widget.attemptId,
        answers: _buildAnswers(),
        filesByQuestionId: Map<String, File>.from(_files),
      );
      _ticker?.cancel();
      await _clearTimerKey();
      if (!mounted) return;
      HapticFeedback.mediumImpact();
      // Result screen replace karti hai, back nahi — submit ke baad
      // question player pe wapas jaana galat state hai.
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (_) => TestResultScreen(
            attemptId: attempt.id,
            series: widget.series,
            initialAttempt: attempt,
          ),
        ),
      );
    } on TestSeriesApiException catch (e) {
      if (!mounted) return;
      setState(() => _submitting = false);
      lsSnack(context, e.message, error: true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _submitting = false);
      lsSnack(context, l10n.testSubmitFailed, error: true);
    }
  }

  Future<bool> _confirmExit() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.testExitTitle),
        content: Text(l10n.testExitBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.testExitConfirm)),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _pickPhoto(String questionId) async {
    try {
      final XFile? img = await ImagePicker().pickImage(source: ImageSource.camera, imageQuality: 85);
      if (img != null && mounted) setState(() => _files[questionId] = File(img.path));
    } catch (_) {}
  }

  void _goTo(int i) {
    setState(() => _index = i);
    _pager.animateToPage(i, duration: const Duration(milliseconds: 220), curve: Curves.easeOut);
  }

  void _openPalette() {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(kLsPad),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.testPaletteTitle, style: LsType.head(context, size: 14)),
            const SizedBox(height: 4),
            Text(l10n.testAnsweredOf(_answeredCount, _questions.length),
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 14),
            Wrap(
              spacing: 9,
              runSpacing: 9,
              children: List.generate(_questions.length, (i) {
                final answered = _isAnswered(_questions[i]);
                final current = i == _index;
                return GestureDetector(
                  onTap: () {
                    Navigator.pop(ctx);
                    _goTo(i);
                  },
                  child: Container(
                    width: 40,
                    height: 40,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: answered ? cs.primary : cs.surfaceVariant,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: current ? cs.secondary : Colors.transparent, width: 2),
                    ),
                    child: Text('${i + 1}',
                        style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                            color: answered ? cs.onPrimary : cs.onSurface)),
                  ),
                );
              }),
            ),
            const SizedBox(height: 16),
          ]),
        ),
      ),
    );
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final t = lsTokens(context);

    final lowTime = _deadline != null && _remaining.inMinutes < 5;

    return PopScope(
      canPop: false,
      // `onPopInvoked` (bina result ke) Flutter 3.22 me deprecate ho chuka
      // hai — ye naya wala har SDK me chalega.
      onPopInvokedWithResult: (didPop, result) async {
        if (didPop) return;
        if (await _confirmExit() && mounted) Navigator.pop(context);
      },
      child: Scaffold(
        backgroundColor: lsBg(context),
        appBar: AppBar(
          backgroundColor: lsBg(context),
          surfaceTintColor: Colors.transparent,
          elevation: 0,
          scrolledUnderElevation: 0,
          leading: IconButton(
            icon: const Icon(Icons.close_rounded),
            tooltip: l10n.testExitTitle,
            onPressed: () async {
              if (await _confirmExit() && mounted) Navigator.pop(context);
            },
          ),
          titleSpacing: 0,
          title: Text(widget.series.title,
              maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 14)),
          actions: [
            if (_deadline != null)
              Center(
                child: Container(
                  margin: const EdgeInsets.only(right: 8),
                  padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
                  decoration: BoxDecoration(
                    color: (lowTime ? t.danger : cs.primary)
                        .withOpacity(Theme.of(context).brightness == Brightness.dark ? .22 : .12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.timer_outlined, size: 14, color: lowTime ? t.danger : cs.primary),
                    const SizedBox(width: 5),
                    Text(_formatRemaining(_remaining),
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            fontFeatures: const [FontFeature.tabularFigures()],
                            color: lowTime ? t.danger : cs.primary)),
                  ]),
                ),
              ),
            IconButton(
              icon: const Icon(Icons.grid_view_rounded, size: 20),
              tooltip: l10n.testPaletteTitle,
              onPressed: _questions.isEmpty ? null : _openPalette,
            ),
          ],
        ),
        body: _buildBody(cs, l10n),
        bottomNavigationBar: _questions.isEmpty ? null : _buildBottomBar(cs, l10n),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_failed) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }
    if (_questions.isEmpty) {
      return EmptyStateWidget(icon: Icons.help_outline_rounded, title: l10n.testNoQuestions);
    }

    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
        child: LsProgressBar(value: (_index + 1) / _questions.length),
      ),
      Expanded(
        child: PageView.builder(
          controller: _pager,
          onPageChanged: (i) => setState(() => _index = i),
          itemCount: _questions.length,
          itemBuilder: (context, i) => SingleChildScrollView(
            padding: const EdgeInsets.only(bottom: 20),
            child: _buildQuestion(_questions[i], i, cs, l10n),
          ),
        ),
      ),
    ]);
  }

  Widget _buildQuestion(TsQuestion q, int i, ColorScheme cs, AppLocalizations l10n) {
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionOf(i + 1, _questions.length),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const Spacer(),
          LsStatusChip(label: l10n.marksShort(q.marks), color: cs.onSurfaceVariant),
        ]),
        const SizedBox(height: 10),
        Text(q.text, style: TextStyle(fontSize: 14, height: 1.5, color: cs.onSurface)),
        const SizedBox(height: 6),
        Text(_hint(q, l10n), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
        const SizedBox(height: 12),
        ..._input(q, cs, l10n),
      ]),
    );
  }

  String _hint(TsQuestion q, AppLocalizations l10n) {
    switch (q.type) {
      case TsQuestionType.mcq:
        return l10n.answerHintSelectOne;
      case TsQuestionType.msq:
        return l10n.answerHintSelectMultiple;
      case TsQuestionType.list:
        return q.listMode == TsListMode.match ? l10n.answerHintMatch : l10n.answerHintArrange;
      case TsQuestionType.text:
      case TsQuestionType.unknown:
        return l10n.answerHintText;
    }
  }

  List<Widget> _input(TsQuestion q, ColorScheme cs, AppLocalizations l10n) {
    switch (q.type) {
      case TsQuestionType.mcq:
        return q.choices
            .map((o) => _Choice(
                  label: o.text,
                  selected: _mcq[q.id] == o.id,
                  multi: false,
                  onTap: () => setState(() => _mcq[q.id] = o.id),
                ))
            .toList();

      case TsQuestionType.msq:
        final sel = _msq[q.id] ?? <String>{};
        return q.choices.map((o) {
          final on = sel.contains(o.id);
          return _Choice(
            label: o.text,
            selected: on,
            multi: true,
            onTap: () {
              final next = Set<String>.from(sel);
              on ? next.remove(o.id) : next.add(o.id);
              setState(() => _msq[q.id] = next);
            },
          );
        }).toList();

      case TsQuestionType.list:
        if (q.listMode == TsListMode.match) return _matchInput(q, cs, l10n);
        return _orderInput(q, cs);

      case TsQuestionType.text:
      case TsQuestionType.unknown:
        return [
          TextField(
            controller: _text[q.id],
            maxLines: 8,
            minLines: 4,
            textCapitalization: TextCapitalization.sentences,
            style: TextStyle(fontSize: 13.5, height: 1.5, color: cs.onSurface),
            decoration: InputDecoration(hintText: l10n.testTypeAnswerHint),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 10),
          if (_files[q.id] == null)
            LsOutlineButton(
              label: l10n.testAttachPhoto,
              icon: Icons.photo_camera_outlined,
              onPressed: () => _pickPhoto(q.id),
            )
          else
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
              child: Row(children: [
                Icon(Icons.image_outlined, size: 16, color: cs.onSurfaceVariant),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(_files[q.id]!.path.split('/').last,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12, color: cs.onSurface)),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  icon: Icon(Icons.close_rounded, size: 17, color: cs.onSurfaceVariant),
                  onPressed: () => setState(() => _files.remove(q.id)),
                ),
              ]),
            ),
        ];
    }
  }

  List<Widget> _orderInput(TsQuestion q, ColorScheme cs) {
    final order = _order[q.id] ?? q.choices.map((o) => o.id).toList();
    String label(String id) {
      for (final o in q.choices) {
        if (o.id == id) return o.text;
      }
      return id;
    }

    return [
      ReorderableListView(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        buildDefaultDragHandles: false,
        onReorder: (oldIndex, newIndex) {
          final next = List<String>.from(order);
          if (newIndex > oldIndex) newIndex -= 1;
          next.insert(newIndex, next.removeAt(oldIndex));
          setState(() {
            _order[q.id] = next;
            _orderTouched.add(q.id);
          });
        },
        children: [
          for (int i = 0; i < order.length; i++)
            Padding(
              key: ValueKey('${q.id}_${order[i]}'),
              padding: const EdgeInsets.only(bottom: 8),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                decoration: BoxDecoration(
                  color: cs.surfaceVariant,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: cs.outlineVariant),
                ),
                child: Row(children: [
                  Text('${i + 1}',
                      style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary)),
                  const SizedBox(width: 12),
                  Expanded(child: Text(label(order[i]), style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
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
  }

  List<Widget> _matchInput(TsQuestion q, ColorScheme cs, AppLocalizations l10n) {
    final pairs = _match[q.id] ?? <String, String>{};
    final right = q.matchRight;

    return q.matchLeft.map((left) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Row(children: [
          Expanded(
            flex: 5,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              decoration: BoxDecoration(
                color: cs.surfaceVariant,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: cs.outlineVariant),
              ),
              child: Text(left.text, style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
            ),
          ),
          const SizedBox(width: 8),
          Icon(Icons.arrow_forward_rounded, size: 15, color: cs.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            flex: 5,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              decoration: BoxDecoration(
                color: cs.surface,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: pairs[left.id] != null ? cs.primary : cs.outlineVariant),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  isExpanded: true,
                  value: pairs[left.id],
                  hint: Text(l10n.testMatchSelect,
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  icon: Icon(Icons.expand_more_rounded, size: 18, color: cs.onSurfaceVariant),
                  dropdownColor: cs.surface,
                  style: TextStyle(fontSize: 12.5, color: cs.onSurface),
                  items: right
                      .map((r) => DropdownMenuItem(
                            value: r.id,
                            child: Text(r.text, maxLines: 1, overflow: TextOverflow.ellipsis),
                          ))
                      .toList(),
                  onChanged: (v) {
                    if (v == null) return;
                    final next = Map<String, String>.from(pairs)..[left.id] = v;
                    setState(() => _match[q.id] = next);
                  },
                ),
              ),
            ),
          ),
        ]),
      );
    }).toList();
  }

  Widget _buildBottomBar(ColorScheme cs, AppLocalizations l10n) {
    final isLast = _index == _questions.length - 1;
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
            if (_index > 0)
              LsOutlineButton(
                label: l10n.testPrevious,
                icon: Icons.chevron_left_rounded,
                onPressed: () => _goTo(_index - 1),
              ),
            const Spacer(),
            Text(l10n.testAnsweredOf(_answeredCount, _questions.length),
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            const SizedBox(width: 12),
            if (isLast)
              LsPrimaryButton(
                label: l10n.testSubmit,
                icon: Icons.check_rounded,
                expanded: false,
                loading: _submitting,
                onPressed: _submitting ? null : _confirmSubmit,
              )
            else
              LsPrimaryButton(
                label: l10n.testNext,
                icon: Icons.chevron_right_rounded,
                expanded: false,
                onPressed: () => _goTo(_index + 1),
              ),
          ]),
        ),
      ),
    );
  }
}

class _Choice extends StatelessWidget {
  final String label;
  final bool selected;
  final bool multi;
  final VoidCallback onTap;
  const _Choice({required this.label, required this.selected, required this.multi, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Semantics(
        inMutuallyExclusiveGroup: !multi,
        checked: selected,
        label: label,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () {
            HapticFeedback.selectionClick();
            onTap();
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 13),
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
                size: 19,
                color: selected ? cs.primary : cs.onSurfaceVariant,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(label,
                    style: TextStyle(
                        fontSize: 13,
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
