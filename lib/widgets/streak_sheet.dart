import 'package:flutter/material.dart';

import '../services/streak_service.dart';

// ============================================================
// Streak sheet — streak + freeze tokens + daily goal ("aaj ka N minute
// challenge") ek jagah.
//
//  * Freeze: missed din par token apne-aap kharch hota hai (server-side,
//    `Streak.objects.record_activity`). Yahan se coins me khareed sakte ho
//    (`POST /profile/streak/freeze/`); cost/cap server batata hai.
//  * Daily goal: progress server ke foreground heartbeat se aata hai
//    (`GET /profile/daily-goal/`); goal minutes yahin se badal sakte ho.
//
// `onCoinsChanged` — khareed ke baad caller apna coin balance refresh kare.
// NOTE: strings hardcoded (profile.dart ke purane streak sheet jaisa).
// ============================================================
Future<void> showStreakSheet(
  BuildContext context,
  StreakInfo initial, {
  VoidCallback? onCoinsChanged,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (_) => _StreakSheet(initial: initial, onCoinsChanged: onCoinsChanged),
  );
}

class _StreakSheet extends StatefulWidget {
  final StreakInfo initial;
  final VoidCallback? onCoinsChanged;
  const _StreakSheet({required this.initial, this.onCoinsChanged});

  @override
  State<_StreakSheet> createState() => _StreakSheetState();
}

class _StreakSheetState extends State<_StreakSheet> {
  late StreakInfo streak = widget.initial;
  DailyGoalInfo? goal;
  bool buying = false;
  String? message; // success / error line under the freeze section

  @override
  void initState() {
    super.initState();
    _loadGoal();
  }

  Future<void> _loadGoal() async {
    final g = await StreakService.fetchDailyGoal();
    if (!mounted || g == null) return;
    setState(() => goal = g);
  }

  Future<void> _buyFreeze() async {
    setState(() {
      buying = true;
      message = null;
    });
    final r = await StreakService.buyFreeze();
    if (!mounted) return;
    setState(() {
      buying = false;
      if (r.ok) {
        streak = r.streak!;
        message = '❄️ Freeze mil gaya! Missed din par apne-aap use hoga.';
      } else {
        message = r.message;
      }
    });
    if (r.ok) widget.onCoinsChanged?.call();
  }

  Future<void> _setGoal(int minutes) async {
    final g = await StreakService.setDailyGoal(minutes);
    if (!mounted || g == null) return;
    setState(() => goal = g);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final atMax = streak.freezeTokens >= streak.freezeMaxTokens;
    final g = goal;

    return SafeArea(
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(20, 20, 20, 24 + MediaQuery.of(context).viewInsets.bottom),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Text('🔥', style: TextStyle(fontSize: 22)),
              const SizedBox(width: 8),
              Text('${streak.currentStreak}-day streak',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: cs.onSurface)),
            ]),
            const SizedBox(height: 4),
            Text(
              streak.isActiveToday
                  ? "You're checked in for today — come back tomorrow to keep it going."
                  : 'Open LearnScroll again today to keep your streak alive.',
              style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: 16),
            Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              _stat(cs, 'Best streak', '${streak.longestStreak} days'),
              _stat(cs, 'Total active days', '${streak.totalActiveDays}'),
              _stat(cs, 'Days saved by freeze', '${streak.freezesUsedTotal}'),
            ]),

            // ---------------- Freeze ----------------
            const SizedBox(height: 20),
            Divider(color: cs.outlineVariant),
            const SizedBox(height: 12),
            Row(children: [
              const Text('❄️', style: TextStyle(fontSize: 18)),
              const SizedBox(width: 8),
              Text('Streak freeze', style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: cs.onSurface)),
              const Spacer(),
              Text('${streak.freezeTokens}/${streak.freezeMaxTokens}',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: cs.primary)),
            ]),
            const SizedBox(height: 4),
            Text(
              'Ek din miss ho jaye to freeze apne-aap use hota hai aur streak nahi tootti. '
              'Har 7 daily goals par ek free freeze bhi milta hai.',
              style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: (buying || atMax) ? null : _buyFreeze,
                icon: buying
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.ac_unit_rounded, size: 18),
                label: Text(atMax ? 'Max freezes held' : 'Buy freeze — ${streak.freezeCostCoins} coins'),
              ),
            ),
            if (message != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(message!, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              ),

            // ---------------- Daily goal ----------------
            const SizedBox(height: 20),
            Divider(color: cs.outlineVariant),
            const SizedBox(height: 12),
            Row(children: [
              const Text('🎯', style: TextStyle(fontSize: 18)),
              const SizedBox(width: 8),
              Text(
                'Aaj ka ${g?.goalMinutes ?? streak.dailyGoalMinutes} minute challenge',
                style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: cs.onSurface),
              ),
              const Spacer(),
              if (g?.completed == true || streak.goalCompletedToday)
                Icon(Icons.check_circle_rounded, color: cs.primary, size: 20),
            ]),
            const SizedBox(height: 10),
            if (g == null)
              const LinearProgressIndicator()
            else ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: LinearProgressIndicator(value: g.fraction, minHeight: 10),
              ),
              const SizedBox(height: 6),
              Text(
                g.completed
                    ? 'Goal complete! ${g.goalsCompletedTotal} goals ho chuke hain.'
                    : '${g.minutesDone} / ${g.goalMinutes} min — LearnScroll par itna time bitao.',
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 10),
              Wrap(spacing: 6, runSpacing: 0, children: [
                for (final m in g.options)
                  ChoiceChip(
                    label: Text('$m min', style: const TextStyle(fontSize: 12)),
                    selected: g.goalMinutes == m,
                    visualDensity: VisualDensity.compact,
                    onSelected: (_) => _setGoal(m),
                  ),
              ]),
            ],
          ],
        ),
      ),
    );
  }

  Widget _stat(ColorScheme cs, String label, String value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: cs.onSurface)),
          const SizedBox(height: 2),
          Text(label, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ],
      );
}
