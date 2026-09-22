import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../controllers/attempt_controller.dart';
import '../services/testseries_models.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_palette_sheet.dart';
import '../widgets/ts_question_view.dart';
import '../widgets/ts_timer_chip.dart';
import 'test_result_screen.dart';

// ============================================================
// TEST ATTEMPT — QUESTION PLAYER (screen)
//
// State + timer + draft + submit logic `TestAttemptController` me hai
// (controllers/attempt_controller.dart). Yahan sirf render aur navigation.
//
// Production behaviour:
//   • har jawab local draft me save hota hai — app kill / crash ke baad
//     resume pe wapas aata hai
//   • time-up pe khule dialog/sheet band hote hain, inputs lock hote hain,
//     aur auto-submit hota hai; network na ho to retry + device pe queue
//   • back / close pe confirm — par timer chalta rehta hai (attempt
//     in_progress hi hai), isliye message me saaf likha hai
//   • mark-for-review, palette, 10/5/1 minute warning
//
// ⚠️ Timer ki limitation ke liye controller ka header comment dekho.
// ============================================================

class TestAttemptScreen extends StatefulWidget {
  final String attemptId;
  final TestSeriesModel series;

  const TestAttemptScreen({super.key, required this.attemptId, required this.series});

  @override
  State<TestAttemptScreen> createState() => _TestAttemptScreenState();
}

class _TestAttemptScreenState extends State<TestAttemptScreen> with WidgetsBindingObserver {
  late final TestAttemptController _c;
  final PageController _pager = PageController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Test ke beech screen band na ho.
    WakelockPlus.enable();

    _c = TestAttemptController(attemptId: widget.attemptId, series: widget.series)
      ..onTimeWarning = _onTimeWarning
      ..onTimeUp = _onTimeUp;
    _boot();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _c.dispose();
    _pager.dispose();
    WakelockPlus.disable();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _c.onResumed();
    } else {
      // Background / inactive: OS kabhi bhi process maar sakta hai — draft abhi likh do.
      unawaited(_c.flushDraft());
    }
  }

  Future<void> _boot() async {
    final outcome = await _c.load();
    if (!mounted) return;

    switch (outcome) {
      case TsLoadOutcome.alreadyFinished:
        _openResult(_c.finishedAttempt!);
        return;
      case TsLoadOutcome.failed:
        return; // build() error state dikhata hai
      case TsLoadOutcome.ready:
        break;
    }

    if (_c.index > 0) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _pager.hasClients) _pager.jumpToPage(_c.index);
      });
    }

    final l10n = AppLocalizations.of(context)!;
    if (_c.missingAttachment) {
      lsSnack(context, l10n.tsPhotoMissing, error: true);
    } else if (_c.draftRestored) {
      lsSnack(context, l10n.tsDraftRestored);
    }

    // Pichhli baar time-up pe submit atka tha (app band ho gaya) — abhi bhejo.
    if (_c.hasPendingSubmit) unawaited(_runSubmit(auto: true));
  }

  // ---------------- timer callbacks ----------------

  void _onTimeWarning(int minutes) {
    if (!mounted) return;
    HapticFeedback.mediumImpact();
    lsSnack(context, AppLocalizations.of(context)!.tsTimeLeftWarning(minutes));
  }

  void _onTimeUp() {
    if (!mounted) return;
    FocusManager.instance.primaryFocus?.unfocus();
    // Khula hua confirm dialog / palette / photo sheet band karo, warna
    // result screen ke peeche latka rehta.
    final route = ModalRoute.of(context);
    if (route != null) Navigator.of(context).popUntil((r) => r == route);
    lsSnack(context, AppLocalizations.of(context)!.testTimeUp);
    unawaited(_runSubmit(auto: true));
  }

  // ---------------- submit / exit ----------------

  Future<void> _confirmSubmit() async {
    final l10n = AppLocalizations.of(context)!;
    final unanswered = _c.answerableCount - _c.answeredCount;
    final lines = <String>[
      unanswered > 0 ? l10n.testSubmitConfirmUnanswered(unanswered) : l10n.testSubmitConfirmBody,
      if (_c.markedCount > 0) l10n.tsSubmitConfirmMarked(_c.markedCount),
    ];

    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.testSubmitConfirmTitle),
        content: Text(lines.join('\n\n')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.testSubmit)),
        ],
      ),
    );
    if (ok == true && mounted && !_c.timeUp) await _runSubmit(auto: false);
  }

  Future<void> _runSubmit({required bool auto}) async {
    final res = await _c.submit(auto: auto);
    if (!mounted) return;
    final l10n = AppLocalizations.of(context)!;

    if (res.attempt != null) {
      HapticFeedback.mediumImpact();
      _openResult(res.attempt!);
    } else if (res.queued) {
      // Banner (build) retry button ke saath dikhata hai; sirf ek line snack.
      lsSnack(context, l10n.tsSubmitPendingBody, error: true);
    } else if (res.error != null) {
      lsSnack(context, tsErrorMessage(l10n, res.error!), error: true);
    }
  }

  void _openResult(TestAttemptModel attempt) {
    // Result screen replace karti hai, back nahi — submit ke baad
    // question player pe wapas jaana galat state hai.
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => TestResultScreen(
          attemptId: attempt.id,
          series: widget.series,
          initialAttempt: attempt,
        ),
      ),
    );
  }

  Future<bool> _confirmExit() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.testExitTitle),
        content: Text(_c.hasTimer ? l10n.tsExitBodyTimerRunning : l10n.tsExitBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.testExitConfirm)),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _handleBack() async {
    if (_c.submitState == TsSubmitState.submitting) return;
    final nothingToLose = _c.loading || _c.questions.isEmpty || _c.submitState == TsSubmitState.pending;
    if (!nothingToLose && !await _confirmExit()) return;
    if (!mounted) return;
    await _c.flushDraft();
    if (mounted) Navigator.of(context).pop();
  }

  void _goTo(int i) {
    FocusScope.of(context).unfocus();
    _pager.animateToPage(i, duration: const Duration(milliseconds: 220), curve: Curves.easeOut);
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return PopScope(
      canPop: false,
      // `onPopInvoked` Flutter 3.22 me deprecate ho chuka hai — ye naya wala har SDK me chalta hai.
      onPopInvokedWithResult: (didPop, result) {
        if (!didPop) _handleBack();
      },
      child: AnimatedBuilder(
        animation: _c,
        builder: (context, _) => Scaffold(
          backgroundColor: lsBg(context),
          appBar: AppBar(
            backgroundColor: lsBg(context),
            surfaceTintColor: Colors.transparent,
            elevation: 0,
            scrolledUnderElevation: 0,
            leading: IconButton(
              icon: const Icon(Icons.close_rounded),
              tooltip: l10n.testExitTitle,
              onPressed: _handleBack,
            ),
            titleSpacing: 0,
            title: Text(widget.series.title,
                maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 14)),
            actions: [
              if (_c.hasTimer) TsTimerChip(remaining: _c.remaining),
              IconButton(
                icon: const Icon(Icons.grid_view_rounded, size: 20),
                tooltip: l10n.testPaletteTitle,
                onPressed: _c.questions.isEmpty || _c.locked
                    ? null
                    : () => showTsPaletteSheet(context: context, controller: _c, onSelect: _goTo),
              ),
            ],
          ),
          body: _buildBody(cs, l10n),
          bottomNavigationBar: _c.loading || _c.questions.isEmpty ? null : _buildBottomBar(cs, l10n),
        ),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_c.loading) return const Center(child: CircularProgressIndicator());
    if (_c.loadError != null) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: tsErrorMessage(l10n, _c.loadError!),
        retryLabel: l10n.retry,
        onRetry: _boot,
      );
    }
    if (_c.questions.isEmpty) {
      return EmptyStateWidget(icon: Icons.help_outline_rounded, title: l10n.testNoQuestions);
    }

    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
        child: LsProgressBar(value: (_c.index + 1) / _c.questions.length),
      ),
      if (_c.timeUp || _c.submitState == TsSubmitState.pending) _lockedBanner(cs, l10n),
      Expanded(
        // Time-up / submitting ke dauran jawab badalne nahi dene.
        child: IgnorePointer(
          ignoring: _c.locked,
          child: PageView.builder(
            controller: _pager,
            onPageChanged: (i) {
              FocusScope.of(context).unfocus();
              _c.setIndex(i);
            },
            itemCount: _c.questions.length,
            itemBuilder: (context, i) => SingleChildScrollView(
              keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
              padding: const EdgeInsets.only(bottom: 20),
              child: TsQuestionView(controller: _c, question: _c.questions[i], index: i),
            ),
          ),
        ),
      ),
    ]);
  }

  Widget _lockedBanner(ColorScheme cs, AppLocalizations l10n) {
    final t = lsTokens(context);
    final pending = _c.submitState == TsSubmitState.pending;
    final submitting = _c.submitState == TsSubmitState.submitting;

    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: (pending ? t.danger : t.info).withOpacity(.12),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(children: [
        if (submitting)
          const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
        else
          Icon(pending ? Icons.cloud_off_rounded : Icons.timer_off_outlined,
              size: 18, color: pending ? t.danger : t.info),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (pending)
              Text(l10n.tsSubmitPendingTitle,
                  style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
            Text(pending ? l10n.tsSubmitPendingBody : l10n.tsTimeUpLocked,
                style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurface)),
          ]),
        ),
        if (pending) ...[
          const SizedBox(width: 8),
          TextButton(
            onPressed: () => _runSubmit(auto: true),
            child: Text(l10n.tsRetrySubmit),
          ),
        ],
      ]),
    );
  }

  Widget _buildBottomBar(ColorScheme cs, AppLocalizations l10n) {
    final isLast = _c.index == _c.questions.length - 1;
    final q = _c.questions[_c.index];
    final marked = _c.isMarked(q.id);
    final t = lsTokens(context);
    final busy = _c.locked;

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
            if (_c.index > 0)
              LsOutlineButton(
                label: l10n.testPrevious,
                icon: Icons.chevron_left_rounded,
                onPressed: () {
                  if (!busy) _goTo(_c.index - 1);
                },
              ),
            const Spacer(),
            IconButton(
              tooltip: marked ? l10n.tsUnmarkReview : l10n.tsMarkForReview,
              onPressed: busy ? null : () => _c.toggleMark(q.id),
              icon: Icon(marked ? Icons.flag_rounded : Icons.outlined_flag_rounded,
                  color: marked ? t.warning : cs.onSurfaceVariant),
            ),
            const SizedBox(width: 8),
            if (isLast)
              LsPrimaryButton(
                label: l10n.testSubmit,
                icon: Icons.check_rounded,
                expanded: false,
                loading: _c.submitState == TsSubmitState.submitting,
                onPressed: busy ? null : _confirmSubmit,
              )
            else
              LsPrimaryButton(
                label: l10n.testNext,
                icon: Icons.chevron_right_rounded,
                expanded: false,
                onPressed: busy ? null : () => _goTo(_c.index + 1),
              ),
          ]),
        ),
      ),
    );
  }
}
