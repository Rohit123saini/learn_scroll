import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../services/api_service.dart';

/// Minor-safety helpers shared by signup + Google sign-in.
///
/// Rules mirror the backend (login/age.py): under 13 can't sign up,
/// under 18 is a minor (account forced private by the server).
class DobRules {
  static const int minSignupAge = 13;
  static const int adultAge = 18;

  static int ageOn(DateTime dob, [DateTime? today]) {
    final t = today ?? DateTime.now();
    var age = t.year - dob.year;
    if (t.month < dob.month || (t.month == dob.month && t.day < dob.day)) age--;
    return age;
  }

  static bool isTooYoung(DateTime dob) => ageOn(dob) < minSignupAge;
  static bool isMinor(DateTime dob) => ageOn(dob) < adultAge;
}

/// Shows the date picker, starting a sensible number of years back so the
/// user doesn't have to scroll from today.
Future<DateTime?> pickDateOfBirth(BuildContext context, {DateTime? initial}) {
  final now = DateTime.now();
  return showDatePicker(
    context: context,
    initialDate: initial ?? DateTime(now.year - 16, now.month, now.day),
    firstDate: DateTime(now.year - 100),
    lastDate: now,
    initialEntryMode: DatePickerEntryMode.calendarOnly,
  );
}

String formatDob(DateTime d) =>
    "${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}";

/// Blocks (modal, not dismissible) until the user has saved a valid date of
/// birth. Used right after Google auth when the server says `dob_missing`.
/// Returns true if saved, false if the screen was torn down first.
Future<bool> promptForDateOfBirth(BuildContext context) async {
  final saved = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => const _DobPromptDialog(),
  );
  return saved == true;
}

class _DobPromptDialog extends StatefulWidget {
  const _DobPromptDialog();

  @override
  State<_DobPromptDialog> createState() => _DobPromptDialogState();
}

class _DobPromptDialogState extends State<_DobPromptDialog> {
  DateTime? _dob;
  bool _saving = false;
  String? _error;

  Future<void> _pick() async {
    final picked = await pickDateOfBirth(context, initial: _dob);
    if (picked != null && mounted) setState(() { _dob = picked; _error = null; });
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    final dob = _dob;
    if (dob == null) {
      setState(() => _error = l10n.signupDobRequired);
      return;
    }
    if (DobRules.isTooYoung(dob)) {
      setState(() => _error = l10n.signupDobTooYoung);
      return;
    }
    setState(() { _saving = true; _error = null; });
    try {
      final becamePrivate = await ApiService().setDateOfBirth(dob);
      if (!mounted) return;
      if (becamePrivate && DobRules.isMinor(dob)) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.dobMinorPrivateNote), behavior: SnackBarBehavior.floating),
        );
      }
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = e.toString().replaceAll("Exception:", "").trim();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return PopScope(
      canPop: false,
      child: AlertDialog(
        title: Text(l10n.dobPromptTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.dobPromptSubtitle, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13.5)),
            const SizedBox(height: 16),
            OutlinedButton.icon(
              onPressed: _saving ? null : _pick,
              icon: const Icon(Icons.cake_outlined),
              label: Text(_dob == null ? l10n.signupDob : formatDob(_dob!)),
            ),
            if (_error != null) ...[
              const SizedBox(height: 10),
              Text(_error!, style: TextStyle(color: cs.error, fontSize: 13)),
            ],
          ],
        ),
        actions: [
          FilledButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l10n.dobPromptSave),
          ),
        ],
      ),
    );
  }
}
