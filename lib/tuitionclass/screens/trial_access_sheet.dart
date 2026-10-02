// ============================================================
// TUITIONCLASS — TRIAL ACCESS SETTINGS SHEET (teacher side)
//
// FEATURE (trial/demo access): classroom-owner-only control for turning
// trial previews on/off, setting the short code students will type, and
// how long a trial join lasts. A scrollable bottom sheet with one form +
// one submit button, matching the other teacher-side sheets in this
// module (e.g. JoinRequestSheet).
//
// Backend surface used: PATCH /tuitionclass/classrooms/<id>/ with
// is_trial_enabled / trial_password / trial_duration_minutes
// (TuitionClassApi.classrooms.updateTrial) — see ClassroomSerializer.validate()
// for why a code is required before trial can be switched on, and why the
// existing code is never sent back (hasTrialPassword is the only signal
// this screen gets that one is already set).
//
// NOTE (P3.2 port): l10n keys referenced below (trialAccessSettingsTitle,
// enableTrialAccessLabel, trialPasswordLabel, trialPasswordHint,
// trialDurationLabel, setTrialPasswordFirst) don't exist in app_en.arb /
// app_hi.arb yet — added in P4 alongside the full rename.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../services/tuitionclass_api_service.dart';
import '../models/tuitionclass_models.dart';

class TrialAccessSheet extends StatefulWidget {
  final Classroom classroom;
  const TrialAccessSheet({super.key, required this.classroom});

  @override
  State<TrialAccessSheet> createState() => _TrialAccessSheetState();
}

class _TrialAccessSheetState extends State<TrialAccessSheet> {
  late bool _enabled = widget.classroom.isTrialEnabled;
  late final _passwordCtrl = TextEditingController();
  late final _durationCtrl =
      TextEditingController(text: widget.classroom.trialDurationMinutes.toString());
  bool _submitting = false;
  String? _error;

  Future<void> _save() async {
    final rawDuration = int.tryParse(_durationCtrl.text.trim());
    final duration = (rawDuration == null) ? widget.classroom.trialDurationMinutes : rawDuration.clamp(1, 30);
    final newPassword = _passwordCtrl.text.trim();

    // Same rule the backend enforces (ClassroomSerializer.validate) —
    // checked here too so the teacher sees it inline instead of a round
    // trip to the server: can't flip trial ON with no code, old or new.
    final willHaveAPassword = newPassword.isNotEmpty || widget.classroom.hasTrialPassword;
    if (_enabled && !willHaveAPassword) {
      setState(() => _error = AppLocalizations.of(context)!.setTrialPasswordFirst);
      return;
    }

    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await TuitionClassApi.classrooms.updateTrial(
        widget.classroom.id,
        isTrialEnabled: _enabled,
        trialPassword: newPassword.isEmpty ? null : newPassword,
        trialDurationMinutes: duration,
      );
      if (mounted) Navigator.of(context).pop(true);
    } on TuitionClassApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  void dispose() {
    _passwordCtrl.dispose();
    _durationCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(kLsPad),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(t.trialAccessSettingsTitle, style: LsType.head(context, size: 16)),
            const SizedBox(height: 12),
            SwitchListTile(
              value: _enabled,
              onChanged: (v) => setState(() => _enabled = v),
              contentPadding: EdgeInsets.zero,
              activeColor: cs.primary,
              title: Text(t.enableTrialAccessLabel, style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
            ),
            const SizedBox(height: 4),
            TextField(
              controller: _passwordCtrl,
              obscureText: true,
              decoration: InputDecoration(
                labelText: t.trialPasswordLabel,
                hintText: widget.classroom.hasTrialPassword ? '••••••' : t.trialPasswordHint,
                filled: true,
                fillColor: cs.surfaceVariant,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _durationCtrl,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: t.trialDurationLabel,
                filled: true,
                fillColor: cs.surfaceVariant,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!, style: TextStyle(fontSize: 12, color: cs.error)),
            ],
            const SizedBox(height: 16),
            LsPrimaryButton(
              label: t.save,
              loading: _submitting,
              onPressed: _submitting ? null : _save,
            ),
          ]),
        ),
      ),
    );
  }
}
