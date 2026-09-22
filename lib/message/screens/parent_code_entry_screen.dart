// message/screens/parent_code_entry_screen.dart
//
// Feature 8 — entry point for "Parent Mode". No student login needed:
// a parent types in the code their child generated and shared with them.
//
// Reachable from LoginScreen (`l10n.parentLoginLink` text button). If this device
// already holds a valid parent session it skips straight to the dashboard.
//
// 🌐 LANGUAGE FIX — all text from AppLocalizations (was hardcoded Hinglish).
// 🔧 FIX — `setState` after `await` without a `mounted` check (crash if the
// parent backed out while the code was being verified).

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../services/parent_service.dart';
import 'parent_dashboard_screen.dart';

class ParentCodeEntryScreen extends StatefulWidget {
  const ParentCodeEntryScreen({super.key});

  @override
  State<ParentCodeEntryScreen> createState() => _ParentCodeEntryScreenState();
}

class _ParentCodeEntryScreenState extends State<ParentCodeEntryScreen> {
  final _codeController = TextEditingController();
  bool _loading = false;
  bool _emptyCode = false;
  ParentModeError? _error;

  @override
  void initState() {
    super.initState();
    _resumeExistingSession();
  }

  /// A parent who already verified a code on this device shouldn't have to type it again.
  Future<void> _resumeExistingSession() async {
    final hasSession = await ParentService.instance.hasActiveSession();
    if (!hasSession || !mounted) return;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (_) => const ParentDashboardScreen()),
    );
  }

  @override
  void dispose() {
    _codeController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _codeController.text.trim();
    if (code.isEmpty) {
      setState(() {
        _emptyCode = true;
        _error = null;
      });
      return;
    }

    setState(() {
      _loading = true;
      _emptyCode = false;
      _error = null;
    });

    try {
      await ParentService.instance.verifyCode(code);
      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => const ParentDashboardScreen()),
      );
    } on ParentModeException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.error);
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = ParentModeError.generic);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final errorText = _emptyCode
        ? l10n.parentEntryCodeRequired
        : (_error != null ? ParentModeException(_error!).localized(l10n) : null);

    return Scaffold(
      appBar: AppBar(title: Text(l10n.parentModeTitle)),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              l10n.parentEntryHeading,
              style: TextStyle(color: cs.onSurface, fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            Text(
              l10n.parentEntryBody,
              style: TextStyle(color: cs.onSurfaceVariant, fontSize: 14),
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _codeController,
              textCapitalization: TextCapitalization.characters,
              onSubmitted: (_) => _loading ? null : _submit(),
              style: TextStyle(color: cs.onSurface, fontSize: 20, letterSpacing: 4),
              decoration: InputDecoration(
                hintText: l10n.parentEntryCodeHint,
                hintStyle: TextStyle(color: cs.onSurfaceVariant),
                errorText: errorText,
              ),
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _loading ? null : _submit,
                child: _loading
                    ? const SizedBox(
                        height: 20, width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(l10n.parentEntryViewProgress),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
