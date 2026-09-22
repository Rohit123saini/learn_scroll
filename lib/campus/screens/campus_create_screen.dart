import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// CAMPUS — CREATE
//
// `POST /campuses/` (design doc §19). Koi bhi authenticated user apna
// campus bana sakta hai — server side pe usi transaction me uska pehla
// `StaffProfile` (role=admin) ban jaata hai. Naya campus hamesha
// `verification_status=pending` se shuru hota hai; jab tak platform admin
// approve nahi karta, staff/student add karna 403 dega — is screen pe
// hum wahi expectation upfront set kar dete hain (`campusCreateSuccess`),
// warna user submit ke turant baad "staff add karo" try karega aur confuse
// hoga.
//
// `type` free-text nahi — backend `"school"|"college"|"coaching"` hi
// accept karta hai (§19), isliye segmented buttons, TextField nahi.
// ============================================================

class CampusCreateScreen extends StatefulWidget {
  const CampusCreateScreen({super.key});

  @override
  State<CampusCreateScreen> createState() => _CampusCreateScreenState();
}

class _CampusCreateScreenState extends State<CampusCreateScreen> {
  final _name = TextEditingController();
  String _type = 'school';
  double _threshold = 75;
  bool _feeModule = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => _error = l10n.campusCreateNameRequired);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final campus = await CampusService.createCampus(
        name: name,
        type: _type,
        attendanceAlertThresholdPercent: _threshold.round(),
        feeModuleEnabled: _feeModule,
      );
      if (!mounted) return;
      lsSnack(context, l10n.campusCreateSuccess);
      Navigator.pop(context, campus);
    } on CampusApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: lsAppBar(context, title: l10n.campusCreateTitle),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 18, 18, 32),
        children: [
          TextField(
            controller: _name,
            enabled: !_saving,
            textCapitalization: TextCapitalization.words,
            decoration: InputDecoration(
              labelText: l10n.campusCreateNameLabel,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 20),
          Text(l10n.campusCreateTypeLabel, style: LsType.head(context, size: 13)),
          const SizedBox(height: 10),
          SegmentedButton<String>(
            segments: [
              ButtonSegment(value: 'school', label: Text(l10n.campusCreateTypeSchool)),
              ButtonSegment(value: 'college', label: Text(l10n.campusCreateTypeCollege)),
              ButtonSegment(value: 'coaching', label: Text(l10n.campusCreateTypeCoaching)),
            ],
            selected: {_type},
            onSelectionChanged: _saving ? null : (s) => setState(() => _type = s.first),
          ),
          const SizedBox(height: 24),
          Row(
            children: [
              Expanded(
                child: Text(l10n.campusCreateThresholdLabel, style: LsType.head(context, size: 13)),
              ),
              LsStatusChip(label: '${_threshold.round()}%', color: cs.primary),
            ],
          ),
          Slider(
            value: _threshold,
            min: 50,
            max: 100,
            divisions: 50,
            label: '${_threshold.round()}%',
            onChanged: _saving ? null : (v) => setState(() => _threshold = v),
          ),
          Text(l10n.campusCreateThresholdHint,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant, height: 1.4)),
          const SizedBox(height: 14),
          SwitchListTile(
            value: _feeModule,
            onChanged: _saving ? null : (v) => setState(() => _feeModule = v),
            contentPadding: EdgeInsets.zero,
            title: Text(l10n.campusCreateFeeModuleLabel, style: const TextStyle(fontSize: 13.5)),
            subtitle: Text(l10n.campusCreateFeeModuleHint,
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
          ],
          const SizedBox(height: 22),
          LsPrimaryButton(
            label: l10n.campusCreateSubmit,
            icon: Icons.add_business_rounded,
            loading: _saving,
            onPressed: _saving ? null : _submit,
          ),
        ],
      ),
    );
  }
}
