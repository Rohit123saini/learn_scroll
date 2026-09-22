import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../services/assignment_models.dart';
import '../services/assignment_service.dart';
import 'assignment_detail_screen.dart';
import 'create_assignment_screen.dart';

// ============================================================
// ASSIGNMENTS — LIST SCREEN
//
// Home ke quick-action "Assignments" tile se khulti hai.
// Teen filters: Pending / Submitted / Checked — kyunki student ka pehla
// sawaal hamesha "kya submit karna baaki hai" hota hai, isliye Pending
// default tab hai aur us list me sabse paas wali due date sabse upar.
// ============================================================

class AssignmentsScreen extends StatefulWidget {
  const AssignmentsScreen({super.key});
  @override
  State<AssignmentsScreen> createState() => _AssignmentsScreenState();
}

class _AssignmentsScreenState extends State<AssignmentsScreen> {
  List<AssignmentWithSubmission> _all = [];
  bool _loading = true;
  bool _failed = false;
  int _filter = 0; // 0 pending, 1 submitted, 2 checked

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      final data = await AssignmentService.getMyAssignments();
      if (!mounted) return;
      setState(() {
        _all = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = _all.isEmpty;
      });
    }
  }

  List<AssignmentWithSubmission> get _visible {
    switch (_filter) {
      case 1:
        return _all.where((a) => a.isSubmitted).toList();
      case 2:
        return _all.where((a) => a.isChecked).toList();
      default:
        return _all.where((a) => a.isPending).toList();
    }
  }

  Future<void> _openDetail(AssignmentWithSubmission item) async {
    HapticFeedback.selectionClick();
    await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => AssignmentDetailScreen(
          assignmentId: item.assignment.id,
          initialAssignment: item.assignment,
          initialSubmission: item.submission,
        ),
      ),
    );
    // Wapas aate hi hamesha reload — detail screen `true` return karti hai
    // jab submit hua ho, par Android ka system-back koi result nahi bhejta,
    // isliye us signal pe bharosa karna reliable nahi hai.
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.assignments),
      body: Column(children: [
        _StatsStrip(
          all: _all,
          selectedIndex: _filter,
          onSelected: (i) {
            HapticFeedback.selectionClick();
            setState(() => _filter = i);
          },
        ),
        const SizedBox(height: 4),
        LsFilterChips(
          labels: [l10n.assignmentsTabPending, l10n.assignmentsTabSubmitted, l10n.assignmentsTabChecked],
          selectedIndex: _filter,
          onSelected: (i) {
            HapticFeedback.selectionClick();
            setState(() => _filter = i);
          },
        ),
        const SizedBox(height: 6),
        Expanded(child: _buildBody(cs, l10n)),
      ]),
      floatingActionButton: FloatingActionButton(
        onPressed: () async {
          final created = await Navigator.push<bool>(
            context,
            MaterialPageRoute(builder: (_) => const CreateAssignmentScreen()),
          );
          // Naya self-assignment turant Pending tab me dikhna chahiye —
          // list ka join fresh chahiye, isliye seedha reload.
          if (created == true) _load();
        },
        tooltip: l10n.assignmentCreateTitle,
        child: const Icon(Icons.add),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _all.isEmpty) {
      return ListView.builder(
        padding: const EdgeInsets.only(top: 4, bottom: 24),
        itemCount: 4,
        itemBuilder: (_, __) => const LsPostCardSkeleton(sidePad: kLsPad),
      );
    }
    if (_failed && _all.isEmpty) {
      return ErrorStateWidget(
        title: l10n.assignmentsErrorTitle,
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final items = _visible;
    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: items.isEmpty
          // AlwaysScrollable — warna khaali list me pull-to-refresh kaam
          // nahi karta aur user phansa hua mehsoos karta hai.
          ? ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                const SizedBox(height: 40),
                EmptyStateWidget(
                  icon: Icons.assignment_outlined,
                  title: l10n.assignmentsEmptyTitle,
                  subtitle: _filter == 0 ? l10n.assignmentsEmptySubtitle : null,
                ),
              ],
            )
          : ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(top: 4, bottom: 28),
              itemCount: items.length,
              itemBuilder: (context, i) => _AssignmentCard(
                item: items[i],
                onTap: () => _openDetail(items[i]),
              ),
            ),
    );
  }
}

/// Home ke `.quick-grid` wala hi visual recipe (48px, radius 15, tinted bg
/// `.12`/`.24` opacity) — bas icon ki jagah yahan ek count number hai,
/// kyunki ye tap-to-navigate action tile nahi, tap-to-filter stat tile hai.
/// Home jaisa consistent look, par apna alag purpose.
class _StatsStrip extends StatelessWidget {
  final List<AssignmentWithSubmission> all;
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  const _StatsStrip({required this.all, required this.selectedIndex, required this.onSelected});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    // Home ki _buildQuickActionsGrid() me isi naam se yahi do values hain.
    final bgOpacity = isDark ? 0.24 : 0.12;

    final tiles = [
      (count: all.where((a) => a.isPending).length, label: l10n.assignmentsTabPending, color: t.danger),
      (count: all.where((a) => a.isSubmitted).length, label: l10n.assignmentsTabSubmitted, color: cs.primary),
      (count: all.where((a) => a.isChecked).length, label: l10n.assignmentsTabChecked, color: t.success),
    ];

    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 4),
      child: Row(
        children: List.generate(tiles.length, (i) {
          final tile = tiles[i];
          final active = i == selectedIndex;
          return Expanded(
            child: Padding(
              padding: EdgeInsets.only(right: i == tiles.length - 1 ? 0 : 10),
              child: Semantics(
                button: true,
                selected: active,
                label: '${tile.label}: ${tile.count}',
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () {
                    HapticFeedback.selectionClick();
                    onSelected(i);
                  },
                  child: Column(children: [
                    Container(
                      width: 48,
                      height: 48,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: tile.color.withOpacity(bgOpacity),
                        borderRadius: BorderRadius.circular(15),
                        border: active ? Border.all(color: tile.color, width: 1.4) : null,
                      ),
                      child: Text('${tile.count}',
                          style: GoogleFonts.sora(fontSize: 17, fontWeight: FontWeight.w700, color: tile.color)),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      tile.label,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.w600, color: cs.onSurface),
                    ),
                  ]),
                ),
              ),
            ),
          );
        }),
      ),
    );
  }
}

class _AssignmentCard extends StatelessWidget {
  final AssignmentWithSubmission item;
  final VoidCallback onTap;
  const _AssignmentCard({required this.item, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;
    final a = item.assignment;
    final s = item.submission;

    final statusColor = assignmentStatusColor(context, item.effectiveStatus);

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 12),
      onTap: onTap,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          // Home ki quick-action tile jaisa hi recipe (48px, radius 15,
          // `.12`/`.24` tint) — icon-container ka size/opacity ab poore app
          // me ek jaisa hai, sirf ye card apna status-color use karta hai.
          Container(
            width: 48,
            height: 48,
            decoration: BoxDecoration(
              color: statusColor.withOpacity(Theme.of(context).brightness == Brightness.dark ? .24 : .12),
              borderRadius: BorderRadius.circular(15),
            ),
            child: Icon(
              a.hasStructuredQuestions ? Icons.fact_check_outlined : Icons.description_outlined,
              size: 22,
              color: statusColor,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(a.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: LsType.head(context, size: 13.5)),
              const SizedBox(height: 3),
              Text(
                a.postedBy.isEmpty ? '' : l10n.assignmentPostedBy(a.postedBy),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
              ),
            ]),
          ),
          const SizedBox(width: 8),
          LsStatusChip(label: assignmentStatusLabel(l10n, item.effectiveStatus), color: statusColor),
        ]),
        const SizedBox(height: 11),
        Row(children: [
          _DueBadge(assignment: a, submitted: !item.isPending),
          const Spacer(),
          if (s != null && s.totalMarksAwarded != null)
            Text(
              l10n.assignmentMarksOf(s.totalMarksAwarded!, a.totalMarks),
              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: t.success),
            )
          else if (s != null && s.grade.isNotEmpty)
            Text(
              l10n.assignmentGradeValue(s.grade),
              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: t.success),
            )
          else
            Text(
              l10n.assignmentTotalMarks(a.totalMarks),
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
            ),
        ]),
      ]),
    );
  }
}

class _DueBadge extends StatelessWidget {
  final AssignmentModel assignment;
  final bool submitted;
  const _DueBadge({required this.assignment, required this.submitted});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;
    final due = assignment.dueDate;

    if (due == null) {
      return Text(l10n.assignmentNoDueDate, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant));
    }

    final days = assignment.daysLeft!;
    // Submit ho chuka ho to due date sirf information hai — usko red me
    // dikhana bekaar ka panic deta hai.
    final Color color;
    final String label;
    if (submitted) {
      color = cs.onSurfaceVariant;
      label = l10n.assignmentDueOn(_fmt(context, due));
    } else if (days < 0) {
      color = t.danger;
      label = l10n.assignmentOverdue;
    } else if (days == 0) {
      color = t.danger;
      label = l10n.assignmentDueToday;
    } else if (days == 1) {
      color = t.warning;
      label = l10n.assignmentDueTomorrow;
    } else if (days <= 3) {
      color = t.warning;
      label = l10n.assignmentDaysLeft(days);
    } else {
      color = cs.onSurfaceVariant;
      label = l10n.assignmentDueOn(_fmt(context, due));
    }

    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.schedule_rounded, size: 13, color: color),
      const SizedBox(width: 5),
      Text(label, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: color)),
    ]);
  }
}

// ---------------- shared helpers (detail screen bhi use karti hai) ----------------

/// Date formatting hamesha current locale me — `intl` already pubspec me
/// hai, aur `Localizations.localeOf()` LanguageService ke saath badalta
/// hai, isliye Hindi me date bhi Hindi format me aayegi.
String _fmt(BuildContext context, DateTime d) =>
    DateFormat.MMMd(Localizations.localeOf(context).toString()).format(d.toLocal());

String formatAssignmentDate(BuildContext context, DateTime d) =>
    DateFormat.yMMMd(Localizations.localeOf(context).toString()).add_jm().format(d.toLocal());

Color assignmentStatusColor(BuildContext context, AssignmentStatus status) {
  final t = lsTokens(context);
  final cs = Theme.of(context).colorScheme;
  switch (status) {
    case AssignmentStatus.checked:
      return t.success;
    case AssignmentStatus.partiallyChecked:
      return t.info;
    case AssignmentStatus.submitted:
      return cs.primary;
    case AssignmentStatus.late:
      return t.warning;
    case AssignmentStatus.missing:
      return t.danger;
    case AssignmentStatus.unknown:
      return cs.onSurfaceVariant;
  }
}

String assignmentStatusLabel(AppLocalizations l10n, AssignmentStatus status) {
  switch (status) {
    case AssignmentStatus.checked:
      return l10n.assignmentStatusChecked;
    case AssignmentStatus.partiallyChecked:
      return l10n.assignmentStatusPartiallyChecked;
    case AssignmentStatus.submitted:
      return l10n.assignmentStatusSubmitted;
    case AssignmentStatus.late:
      return l10n.assignmentStatusLate;
    case AssignmentStatus.missing:
      return l10n.assignmentStatusMissing;
    case AssignmentStatus.unknown:
      return l10n.assignmentStatusMissing;
  }
}
