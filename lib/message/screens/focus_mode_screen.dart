// message/screens/focus_mode_screen.dart
//
// 🔥 NAYA (Feature 12) — "Smart do-not-disturb during focus/exam windows"
// Student yahan duration + exception rule set karta hai. Screen khud
// start/stop dono API calls handle karti hai aur Navigator.pop se
// updated `FocusSessionStatus?` wapas bhejti hai (caller — abhi
// `conversations_screen.dart` — sirf local state refresh karta hai).
//
// 🌐 LANGUAGE FIX — every visible string (including the Hinglish rule subtitles) now comes
// from AppLocalizations (ARB keys `focusMode*`); the raw `e.toString()` error is a localized message.
//
// 🎨 THEME FIX — poori file pehle hardcoded navy/off-white/amber colors use
// karti thi (koi bhi theme mode me same, dark mode me bhi safed background).
// Ab sab `Theme.of(context)`/`AppThemeTokens.of(context)` se — home.dart
// jaisa hi shared design system.

import 'dart:async';
import 'package:flutter/material.dart';
import '../services/message_api_service.dart';
import 'focus_session_history_screen.dart';
// 🔧 GAP FIX — FocusSessionStatus ab yahan se nahi, `message_models.dart`
// se aata hai. Pehle ye class isi file me define thi, lekin
// message_api_service.dart ke naye getFocusStatus()/startFocusSession()
// methods ko bhi FocusSessionStatus return type chahiye tha, aur
// message_api_service.dart <-> focus_mode_screen.dart ek dusre ko import
// nahi kar sakte (circular import). Isliye DTO ko shared
// `message_models.dart` me move kiya — baaki saare DTOs (ConversationModel
// etc.) bhi wahin hain, so ye pattern-consistent bhi hai.
import '../models/message_models.dart';
import '../../l10n/app_localizations.dart'; // 🌐 LANGUAGE FIX — all text now from ARB (en/hi)
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

class FocusModeScreen extends StatefulWidget {
  final FocusSessionStatus? current;
  const FocusModeScreen({super.key, this.current});

  @override
  State<FocusModeScreen> createState() => _FocusModeScreenState();
}

class _FocusModeScreenState extends State<FocusModeScreen> {
  // Preset durations — coaching context me ye sabse common windows hain
  // (ek period, do period, poora exam-block).
  static const _presets = [30, 60, 120, 180];
  int _selectedMinutes = 60;
  String _exceptionRule = 'teachers_only';
  bool _isSaving = false;
  String? _error;

  // 🎨 THEME FIX — `_kNavy` was a literal navy, now the theme's own primary
  // (matches header/buttons everywhere else); `_kAnnouncement` was a fixed
  // amber, now the shared `AppThemeTokens.warning` token (same semantic
  // "attention" color used for the Focus Mode banner in conversations_screen.dart).
  Color _navy(BuildContext context) => Theme.of(context).colorScheme.primary;
  Color _warn(BuildContext context) => AppThemeTokens.of(context).warning;

  /// "30 min" / "1h" / "1h 30m" — same ARB keys the history screen uses (Hindi: "30 मिनट" / "1 घंटे").
  String _durationLabel(AppLocalizations l10n, int mins) {
    if (mins < 60) return l10n.focusHistoryMinutes(mins);
    final h = mins ~/ 60;
    final m = mins % 60;
    return m == 0 ? l10n.focusHistoryHours(h) : l10n.focusHistoryHoursMinutes(h, m);
  }

  @override
  void initState() {
    super.initState();
    if (widget.current != null) {
      _exceptionRule = widget.current!.exceptionRule;
    }
  }

  Future<void> _start() async {
    setState(() { _isSaving = true; _error = null; });
    try {
      final status = await MessageApiService.startFocusSession(
        durationMinutes: _selectedMinutes,
        exceptionRule: _exceptionRule,
      );
      if (!mounted) return;
      Navigator.pop(context, status);
    } catch (e) {
      if (!mounted) return;
      setState(() { _isSaving = false; _error = AppLocalizations.of(context)!.focusModeFailed; });
    }
  }

  Future<void> _stop() async {
    setState(() { _isSaving = true; _error = null; });
    try {
      await MessageApiService.cancelFocusSession();
      if (!mounted) return;
      Navigator.pop(context, null);
    } catch (e) {
      if (!mounted) return;
      setState(() { _isSaving = false; _error = AppLocalizations.of(context)!.focusModeFailed; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final isActive = widget.current?.active ?? false;
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final navy = _navy(context);
    final warn = _warn(context);

    return Scaffold(
      // 🎨 THEME FIX — was hardcoded const Color(0xFFF6F7FB); falls back to
      // the theme's scaffoldBackgroundColor now (light/dark both correct).
      appBar: AppBar(
        backgroundColor: navy,
        title: Text(l10n.focusModeTitle, style: TextStyle(color: cs.onPrimary, fontWeight: FontWeight.w700)),
        iconTheme: IconThemeData(color: cs.onPrimary),
        // 🔧 FIX — Feature 12 gap: `focus_session_history_screen.dart` was
        // built but had no entry point anywhere in the app. Wiring it here
        // per that file's own header-comment instructions.
        actions: [
          IconButton(
            icon: const Icon(Icons.history),
            tooltip: l10n.focusModeHistoryTooltip,
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const FocusSessionHistoryScreen()),
            ),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (isActive) _activeBanner(widget.current!),
          if (isActive) const SizedBox(height: 24),

          Text(
            isActive ? l10n.focusModeChangeDuration : l10n.focusModeHowLong,
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: navy),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: _presets.map((mins) {
              final selected = _selectedMinutes == mins;
              final label = _durationLabel(l10n, mins);
              return ChoiceChip(
                label: Text(label),
                selected: selected,
                selectedColor: warn.withOpacity(0.2),
                labelStyle: TextStyle(
                  color: selected ? warn : cs.onSurface,
                  fontWeight: selected ? FontWeight.w700 : FontWeight.normal,
                ),
                onSelected: (_) => setState(() => _selectedMinutes = mins),
              );
            }).toList(),
          ),
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: () => _showCustomDurationPicker(context),
              child: Text(l10n.focusModeCustomDuration),
            ),
          ),

          const SizedBox(height: 28),
          Text(
            l10n.focusModeWhoCanReach,
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: navy),
          ),
          const SizedBox(height: 8),
          _ruleTile(
            value: 'teachers_only',
            title: l10n.focusModeRuleTeachersTitle,
            subtitle: l10n.focusModeRuleTeachersSub,
            icon: Icons.school_rounded,
          ),
          _ruleTile(
            value: 'nobody',
            title: l10n.focusModeRuleNobodyTitle,
            subtitle: l10n.focusModeRuleNobodySub,
            icon: Icons.do_not_disturb_on_rounded,
          ),

          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: TextStyle(color: cs.error, fontSize: 13)),
          ],

          const SizedBox(height: 32),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _isSaving ? null : _start,
              style: ElevatedButton.styleFrom(
                backgroundColor: warn,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              ),
              child: _isSaving
                  ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary))
                  : Text(
                      isActive ? l10n.focusModeUpdate : l10n.focusModeStart,
                      style: TextStyle(color: cs.onPrimary, fontWeight: FontWeight.w700, fontSize: 15),
                    ),
            ),
          ),
          if (isActive) ...[
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: _isSaving ? null : _stop,
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  side: BorderSide(color: cs.error),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                ),
                child: Text(l10n.focusModeEndNow, style: TextStyle(color: cs.error, fontWeight: FontWeight.w700)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _activeBanner(FocusSessionStatus status) {
    final remaining = status.endsAt.difference(DateTime.now());
    final h = remaining.inHours;
    final m = remaining.inMinutes.remainder(60);
    final warn = _warn(context);
    final l10n = AppLocalizations.of(context)!;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: warn.withOpacity(0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(children: [
        Icon(Icons.bolt_rounded, color: warn),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            l10n.focusModeActiveLeft(h > 0 ? l10n.focusHistoryHoursMinutes(h, m) : l10n.focusHistoryMinutes(m)),
            style: TextStyle(fontWeight: FontWeight.w600, color: warn),
          ),
        ),
      ]),
    );
  }

  Widget _ruleTile({required String value, required String title, required String subtitle, required IconData icon}) {
    final selected = _exceptionRule == value;
    final cs = Theme.of(context).colorScheme;
    final warn = _warn(context);
    return InkWell(
      onTap: () => setState(() => _exceptionRule = value),
      borderRadius: BorderRadius.circular(10),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: cs.surface,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: selected ? warn : cs.outlineVariant, width: selected ? 1.6 : 1),
        ),
        child: Row(children: [
          Icon(icon, color: selected ? warn : cs.onSurfaceVariant),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: TextStyle(fontWeight: FontWeight.w700, color: selected ? warn : cs.onSurface)),
              const SizedBox(height: 2),
              Text(subtitle, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
            ]),
          ),
          Radio<String>(
            value: value,
            groupValue: _exceptionRule,
            activeColor: warn,
            onChanged: (v) => setState(() => _exceptionRule = v!),
          ),
        ]),
      ),
    );
  }

  Future<void> _showCustomDurationPicker(BuildContext context) async {
    int hours = _selectedMinutes ~/ 60;
    int minutes = _selectedMinutes % 60;
    final warn = _warn(context);
    final onWarn = Theme.of(context).colorScheme.onPrimary;
    final l10n = AppLocalizations.of(context)!;
    final picked = await showModalBottomSheet<int>(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: const EdgeInsets.all(20),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text(l10n.focusModeCustomDuration, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
              const SizedBox(height: 16),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                _stepper(label: l10n.focusModeHours, value: hours, min: 0, max: 8, onChanged: (v) => setSheetState(() => hours = v)),
                const SizedBox(width: 24),
                _stepper(label: l10n.focusModeMinutes, value: minutes, min: 0, max: 45, step: 15, onChanged: (v) => setSheetState(() => minutes = v)),
              ]),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: warn),
                  onPressed: (hours * 60 + minutes) < 5
                      ? null
                      : () => Navigator.pop(ctx, hours * 60 + minutes),
                  child: Text(l10n.focusModeSet, style: TextStyle(color: onWarn)),
                ),
              ),
            ]),
          );
        });
      },
    );
    if (picked != null) setState(() => _selectedMinutes = picked.clamp(5, 480));
  }

  Widget _stepper({required String label, required int value, required int min, required int max, int step = 1, required void Function(int) onChanged}) {
    return Column(children: [
      Text(label, style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
      const SizedBox(height: 6),
      Row(children: [
        IconButton(icon: const Icon(Icons.remove_circle_outline), onPressed: value > min ? () => onChanged(value - step) : null),
        SizedBox(width: 32, child: Text('$value', textAlign: TextAlign.center, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700))),
        IconButton(icon: const Icon(Icons.add_circle_outline), onPressed: value < max ? () => onChanged(value + step) : null),
      ]),
    ]);
  }
}
