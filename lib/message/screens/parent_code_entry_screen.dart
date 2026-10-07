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
//
// TASK 11.3 — link + QR:
//   • [initialCode] pre-fills the field (link `…/parent-link?code=XXXX` opened by
//     DeepLinkService); [autoSubmit] verifies it right away so tapping the link is enough.
//   • "Scan QR" button: parent scans the QR the student/teacher shows; the code is read
//     from the payload (`?code=`) and verified. Only the code is ever read from a QR here.

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../services/deep_link_service.dart';
import '../../widgets/scan_qr_screen.dart';
import '../services/parent_service.dart';
import 'parent_dashboard_screen.dart';

class ParentCodeEntryScreen extends StatefulWidget {
  /// Code from a tapped link / scanned QR (null = parent types it).
  final String? initialCode;

  /// Verify [initialCode] immediately after the screen opens.
  final bool autoSubmit;

  const ParentCodeEntryScreen({super.key, this.initialCode, this.autoSubmit = false});

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
    final initial = widget.initialCode?.trim();
    if (initial != null && initial.isNotEmpty) _codeController.text = initial.toUpperCase();
    _resumeExistingSession();
  }

  /// A parent who already verified a code on this device shouldn't have to type it again.
  /// (A NEW code from a link/QR wins over an old session: the parent may be adding a second
  /// child, so we verify it instead of jumping to the old dashboard.)
  Future<void> _resumeExistingSession() async {
    final hasNewCode = (widget.initialCode ?? '').trim().isNotEmpty;
    if (hasNewCode) {
      if (widget.autoSubmit) {
        // Wait one frame so the context/localizations are ready for any error text.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _submit();
        });
      }
      return;
    }
    final hasSession = await ParentService.instance.hasActiveSession();
    if (!hasSession || !mounted) return;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (_) => const ParentDashboardScreen()),
    );
  }

  Future<void> _scanQr() async {
    final raw = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const ScanQrScreen(returnRaw: true)),
    );
    if (raw == null || !mounted) return;
    final target = DeepLinkService.parsePayload(raw);
    // Scanner already rejects non-LearnScroll payloads; a profile QR isn't a parent code.
    if (target is! ParentInviteTarget) {
      setState(() => _error = ParentModeError.invalidCode);
      return;
    }
    _codeController.text = target.code;
    _submit();
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
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _loading ? null : _scanQr,
                icon: const Icon(Icons.qr_code_scanner_rounded, size: 18),
                label: Text(l10n.parentEntryScanQr),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
