// ============================================================
// TUITIONCLASS — TRIAL JOIN SHEET (student side)
//
// FEATURE (trial/demo access): a not-yet-enrolled student types the
// teacher-set trial code and, if it matches and a session is LIVE, gets
// joined into it for `classroom.trialDurationMinutes` — same idea as
// JoinRequestSheet, but a password gate instead of a pass picker, and it
// resolves immediately (no teacher approval step).
//
// Backend surface used: POST /tuitionclass/classrooms/<id>/trial-join/
// (TuitionClassApi.classrooms.trialJoin) — see that method's docstring for
// the exact response shape this hands back via Navigator.pop().
//
// NOTE (P3.2 port): l10n keys (tryTrialCta, trialPasswordLabel,
// trialPasswordHint) added in P4 alongside the full rename.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../services/tuitionclass_api_service.dart';
import '../models/tuitionclass_models.dart';

class TrialJoinSheet extends StatefulWidget {
  final int classroomId;
  const TrialJoinSheet({super.key, required this.classroomId});

  @override
  State<TrialJoinSheet> createState() => _TrialJoinSheetState();
}

class _TrialJoinSheetState extends State<TrialJoinSheet> {
  final _passwordCtrl = TextEditingController();
  bool _submitting = false;
  String? _error;

  Future<void> _submit() async {
    final code = _passwordCtrl.text.trim();
    if (code.isEmpty) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final info = await TuitionClassApi.classrooms.trialJoin(widget.classroomId, code);
      // Caller (classroom_detail_screen.dart._openTrialJoin) reads this the
      // same way _enterClass reads startOrJoin()'s result.
      if (mounted) Navigator.of(context).pop(info);
    } on TuitionClassApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  void dispose() {
    _passwordCtrl.dispose();
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
            Text(t.tryTrialCta, style: LsType.head(context, size: 16)),
            const SizedBox(height: 12),
            TextField(
              controller: _passwordCtrl,
              autofocus: true,
              onSubmitted: (_) => _submit(),
              decoration: InputDecoration(
                labelText: t.trialPasswordLabel,
                hintText: t.trialPasswordHint,
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
              label: t.tryTrialCta,
              loading: _submitting,
              onPressed: _submitting ? null : _submit,
            ),
          ]),
        ),
      ),
    );
  }
}
