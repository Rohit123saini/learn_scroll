// lib/profile/screens/activity_screen.dart
//
// P14-FE — Settings -> "Your activity". Backend: P14-BE.
//   * 7-day time-spent bar chart (tap a bar for that day's exact time)
//   * daily-limit reminder row (sets UserPreference.daily_limit_minutes)
//   * Liked / Comments / Saved tabs
//
// One scrollable only (a ListView) with state-driven tabs — same "no nested
// scrollables" rule profile.dart follows.
//
// Comments tab: P14-BE returns per-day comment COUNTS, not a list of
// comments, so that's what it shows. A real comment list needs a backend
// addition first.

import 'package:flutter/material.dart';

import '../../post/screens/singlepost.dart';
import '../../services/activity_service.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';

const List<String> _kMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const List<String> _kWeekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const List<int?> _kLimitChoices = [null, 15, 30, 45, 60, 90, 120];

String _fmtDuration(int seconds) {
  if (seconds < 60) return seconds == 0 ? '0m' : '<1m';
  final h = seconds ~/ 3600;
  final m = (seconds % 3600) ~/ 60;
  if (h == 0) return '${m}m';
  return m == 0 ? '${h}h' : '${h}h ${m}m';
}

String _fmtDay(DateTime d) => '${_kWeekdays[d.weekday - 1]}, ${d.day} ${_kMonths[d.month - 1]}';

String _ago(DateTime? d) {
  if (d == null) return '';
  final diff = DateTime.now().difference(d);
  if (diff.inMinutes < 1) return 'just now';
  if (diff.inHours < 1) return '${diff.inMinutes}m ago';
  if (diff.inDays < 1) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return '${d.day} ${_kMonths[d.month - 1]}';
}

class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key});

  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  ActivitySummary? _data;
  bool _loading = true;
  String? _error;
  int _selectedDay = 6; // index into days; defaults to today (last)
  int _tab = 0; // 0 = Liked, 1 = Comments, 2 = Saved
  bool _savingLimit = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final data = await ActivityService.fetch();
      if (!mounted) return;
      setState(() {
        _data = data;
        _selectedDay = data.days.isEmpty ? 0 : data.days.length - 1;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        // On a pull-refresh failure keep showing what we already have.
        if (_data == null) _error = e.toString();
      });
    }
  }

  Future<void> _pickLimit() async {
    final data = _data;
    if (data == null || _savingLimit) return;
    final cs = Theme.of(context).colorScheme;

    final picked = await showModalBottomSheet<_LimitChoice>(
      context: context,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 12),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Daily time limit',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: cs.onSurface)),
            const SizedBox(height: 4),
            Text('We\u2019ll remind you once a day when you reach it.',
                style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
            const SizedBox(height: 8),
            for (final m in _kLimitChoices)
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: Text(m == null ? 'Off' : '$m minutes'),
                trailing: m == data.dailyLimitMinutes
                    ? Icon(Icons.check_rounded, color: cs.primary)
                    : null,
                onTap: () => Navigator.pop(ctx, _LimitChoice(m)),
              ),
          ]),
        ),
      ),
    );
    if (picked == null || picked.minutes == data.dailyLimitMinutes) return;

    setState(() => _savingLimit = true);
    try {
      await ActivityService.setDailyLimit(picked.minutes);
      if (!mounted) return;
      setState(() {
        _data = picked.minutes == null
            ? _data!.copyWith(clearLimit: true)
            : _data!.copyWith(dailyLimitMinutes: picked.minutes);
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Couldn\u2019t update the limit: $e')),
      );
    } finally {
      if (mounted) setState(() => _savingLimit = false);
    }
  }

  void _openPost(ActivityPost p) {
    if (p.postId.isEmpty) return;
    Navigator.push(context, MaterialPageRoute(builder: (_) => SinglePostPage(postId: p.postId)));
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Your activity'),
      body: _body(cs),
    );
  }

  Widget _body(ColorScheme cs) {
    if (_loading && _data == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _data == null) {
      return Center(
        child: ErrorStateWidget(
          title: 'Couldn\u2019t load your activity',
          subtitle: _error,
          retryLabel: 'Retry',
          onRetry: _load,
        ),
      );
    }
    final data = _data!;

    return RefreshIndicator(
      color: cs.primary,
      onRefresh: () => _load(silent: true),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(kLsPad, 12, kLsPad, 32),
        children: [
          _timeCard(cs, data),
          const SizedBox(height: 12),
          _limitCard(cs, data),
          const SizedBox(height: 18),
          _tabBar(cs, data),
          const SizedBox(height: 12),
          if (_tab == 0) _postList(cs, data.likedPosts, verb: 'Liked', emptyText: 'No liked posts yet.'),
          if (_tab == 1) _commentsTab(cs, data),
          if (_tab == 2) _postList(cs, data.savedPosts, verb: 'Saved', emptyText: 'No saved posts yet.'),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------- time card
  Widget _timeCard(ColorScheme cs, ActivitySummary data) {
    final days = data.days;
    final sel = (days.isEmpty) ? null : days[_selectedDay.clamp(0, days.length - 1).toInt()];
    final maxSeconds = days.fold<int>(0, (m, d) => d.seconds > m ? d.seconds : m);

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(kLsRadius),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Time spent', style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
        const SizedBox(height: 2),
        Text(
          'Daily average ${_fmtDuration(data.avgSecondsPerDay)}',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: cs.onSurface),
        ),
        const SizedBox(height: 4),
        Text(
          sel == null ? '' : '${_fmtDay(sel.date)} \u00b7 ${_fmtDuration(sel.seconds)}',
          style: TextStyle(fontSize: 12.5, color: cs.primary, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 14),
        SizedBox(
          height: 150,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var i = 0; i < days.length; i++)
                Expanded(
                  child: _bar(
                    cs,
                    day: days[i],
                    fraction: maxSeconds == 0 ? 0 : days[i].seconds / maxSeconds,
                    selected: i == _selectedDay,
                    onTap: () => setState(() => _selectedDay = i),
                  ),
                ),
            ],
          ),
        ),
      ]),
    );
  }

  Widget _bar(
    ColorScheme cs, {
    required ActivityDay day,
    required double fraction,
    required bool selected,
    required VoidCallback onTap,
  }) {
    const labelHeight = 22.0;
    return Semantics(
      button: true,
      label: '${_fmtDay(day.date)}, ${_fmtDuration(day.seconds)}',
      child: InkWell(
        onTap: onTap,
        child: LayoutBuilder(builder: (context, c) {
          final chartH = c.maxHeight - labelHeight;
          // Zero-usage days still get a thin stub so the row reads as 7 bars.
          final h = (fraction * chartH).clamp(4.0, chartH).toDouble();
          return Column(children: [
            Expanded(
              child: Align(
                alignment: Alignment.bottomCenter,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 350),
                  curve: Curves.easeOutCubic,
                  width: 22,
                  height: h,
                  decoration: BoxDecoration(
                    color: selected ? cs.primary : cs.primary.withOpacity(.3),
                    borderRadius: BorderRadius.circular(6),
                  ),
                ),
              ),
            ),
            SizedBox(
              height: labelHeight,
              child: Center(
                child: Text(
                  _kWeekdays[day.date.weekday - 1].substring(0, 1),
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: selected ? FontWeight.w800 : FontWeight.w500,
                    color: selected ? cs.onSurface : cs.onSurfaceVariant,
                  ),
                ),
              ),
            ),
          ]);
        }),
      ),
    );
  }

  // --------------------------------------------------------------- limit card
  Widget _limitCard(ColorScheme cs, ActivitySummary data) {
    final limit = data.dailyLimitMinutes;
    final reached = limit != null && data.todaySeconds >= limit * 60;
    final progress = limit == null ? 0.0 : (data.todaySeconds / (limit * 60)).clamp(0.0, 1.0).toDouble();

    return Semantics(
      button: true,
      label: 'Daily time limit, tap to change',
      child: InkWell(
        borderRadius: BorderRadius.circular(kLsRadius),
        onTap: _pickLimit,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: BorderRadius.circular(kLsRadius),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Row(children: [
            Icon(Icons.timer_outlined, size: 20, color: reached ? cs.error : cs.onSurfaceVariant),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Daily time limit',
                    style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: cs.onSurface)),
                const SizedBox(height: 2),
                Text(
                  limit == null
                      ? 'Off \u2014 get a reminder when you reach a daily limit'
                      : '${_fmtDuration(data.todaySeconds)} of $limit min today'
                          '${reached ? ' \u2014 limit reached' : ''}',
                  style: TextStyle(fontSize: 11.5, color: reached ? cs.error : cs.onSurfaceVariant),
                ),
                if (limit != null) ...[
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: progress,
                      minHeight: 5,
                      backgroundColor: cs.surfaceVariant,
                      color: reached ? cs.error : cs.primary,
                    ),
                  ),
                ],
              ]),
            ),
            const SizedBox(width: 8),
            if (_savingLimit)
              const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
            else
              Icon(Icons.chevron_right_rounded, size: 20, color: cs.outline),
          ]),
        ),
      ),
    );
  }

  // --------------------------------------------------------------------- tabs
  Widget _tabBar(ColorScheme cs, ActivitySummary data) {
    String count(int? n) => n == null ? '' : ' ($n)';
    final labels = [
      'Liked${count(data.totalLikes)}',
      'Comments${count(data.totalComments)}',
      'Saved',
    ];
    return Row(children: [
      for (var i = 0; i < labels.length; i++) ...[
        if (i > 0) const SizedBox(width: 8),
        Expanded(
          child: Semantics(
            button: true,
            selected: _tab == i,
            label: labels[i],
            child: InkWell(
              borderRadius: BorderRadius.circular(20),
              onTap: () => setState(() => _tab = i),
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 9),
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: _tab == i ? cs.primary : cs.surface,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: _tab == i ? cs.primary : cs.outlineVariant),
                ),
                child: Text(
                  labels[i],
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    color: _tab == i ? cs.onPrimary : cs.onSurface,
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    ]);
  }

  Widget _emptyText(ColorScheme cs, String text) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 36),
        child: Center(child: Text(text, style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant))),
      );

  IconData _iconFor(String? type) {
    switch (type) {
      case 'video':
        return Icons.play_circle_outline_rounded;
      case 'image':
        return Icons.image_outlined;
      case 'repost':
        return Icons.repeat_rounded;
      case 'document':
      case 'pdf':
      case 'docx':
      case 'doc':
      case 'excel':
      case 'xls':
        return Icons.description_outlined;
      default:
        return Icons.article_outlined;
    }
  }

  Widget _postList(ColorScheme cs, List<ActivityPost> posts, {required String verb, required String emptyText}) {
    if (posts.isEmpty) return _emptyText(cs, emptyText);
    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(kLsRadius),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(children: [
        for (var i = 0; i < posts.length; i++) ...[
          if (i > 0) Divider(height: 1, color: cs.outlineVariant),
          InkWell(
            onTap: () => _openPost(posts[i]),
            borderRadius: BorderRadius.circular(kLsRadius),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              child: Row(children: [
                Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(color: cs.primary.withOpacity(.12), shape: BoxShape.circle),
                  child: Icon(_iconFor(posts[i].postType), size: 19, color: cs.primary),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(
                      (posts[i].ownerUsername ?? '').isEmpty ? 'Post' : '@${posts[i].ownerUsername}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: cs.onSurface),
                    ),
                    const SizedBox(height: 2),
                    Text('$verb ${_ago(posts[i].at)}'.trim(),
                        style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                  ]),
                ),
                Icon(Icons.chevron_right_rounded, size: 20, color: cs.outline),
              ]),
            ),
          ),
        ],
      ]),
    );
  }

  Widget _commentsTab(ColorScheme cs, ActivitySummary data) {
    if (data.totalComments == null && data.totalShares == null) {
      return _emptyText(cs, 'Comment activity isn\u2019t available right now.');
    }
    final withActivity = data.days
        .where((d) => (d.comments ?? 0) > 0 || (d.shares ?? 0) > 0)
        .toList()
        .reversed
        .toList(); // newest first
    if (withActivity.isEmpty) return _emptyText(cs, 'No comments or shares in the last 7 days.');

    String line(ActivityDay d) {
      final parts = <String>[];
      final c = d.comments ?? 0;
      final s = d.shares ?? 0;
      if (c > 0) parts.add('$c comment${c == 1 ? '' : 's'}');
      if (s > 0) parts.add('$s share${s == 1 ? '' : 's'}');
      return parts.join(' \u00b7 ');
    }

    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(kLsRadius),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Column(children: [
        for (var i = 0; i < withActivity.length; i++) ...[
          if (i > 0) Divider(height: 1, color: cs.outlineVariant),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
            child: Row(children: [
              Expanded(
                child: Text(_fmtDay(withActivity[i].date),
                    style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: cs.onSurface)),
              ),
              Text(line(withActivity[i]), style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
            ]),
          ),
        ],
      ]),
    );
  }
}

/// Wrapper so "Off" (null minutes) can be told apart from a dismissed sheet
/// (which returns null from showModalBottomSheet).
class _LimitChoice {
  final int? minutes;
  const _LimitChoice(this.minutes);
}
