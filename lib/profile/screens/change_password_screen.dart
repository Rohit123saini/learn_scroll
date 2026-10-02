// lib/profile/screens/change_password_screen.dart
//
// ============================================================
// CHANGE PASSWORD — Settings > Security.
//
// Backend `login.ChangePasswordAPIView` (`POST /login/auth/change-password/`)
// was already fully production-ready — it's used nowhere in the app today.
// This is that missing screen, same "backend ready, no UI ever built for it"
// gap this codebase already has a name for (see `settings_screen.dart`,
// `blocked_accounts_screen.dart`).
//
// ⚠️ FLAGGED, NOT FIXED (out of scope for this pass — a settings-UI/
// persistence task, not an auth-hardening one): `ChangePasswordSerializer`
// (backend/login/serializers.py) only takes `new_password` +
// `confirm_password` — there is no `current_password` field/check at all,
// so this screen intentionally does NOT ask for or claim to verify the
// current password (asking for one the backend silently ignores would be
// worse than not asking — it would look like a security check that isn't
// actually happening). Anyone with a live, already-authenticated session
// can change the password without re-proving it. Worth a real backend fix
// (add `current_password` to the serializer + `request.user.check_password()`)
// in a dedicated auth-security pass.
// ============================================================

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../services/auth_service.dart';

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _newPasswordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();

  bool _obscureNew = true;
  bool _obscureConfirm = true;
  bool _saving = false;

  @override
  void dispose() {
    _newPasswordController.dispose();
    _confirmPasswordController.dispose();
    super.dispose();
  }

  String? _validateNewPassword(String? value) {
    final v = value ?? '';
    if (v.isEmpty) return 'Enter a new password';
    if (v.trim() != v) return 'Password cannot start or end with spaces';
    if (v.length < 8) return 'At least 8 characters';
    if (!RegExp(r'[A-Z]').hasMatch(v)) return 'Add at least one uppercase letter';
    if (!RegExp(r'[a-z]').hasMatch(v)) return 'Add at least one lowercase letter';
    if (!RegExp(r'[0-9]').hasMatch(v)) return 'Add at least one number';
    if (!RegExp(r'[!@#\$%^&*(),.?":{}|<>]').hasMatch(v)) return 'Add at least one special character';
    return null;
  }

  String? _validateConfirmPassword(String? value) {
    if (value != _newPasswordController.text) return 'Passwords do not match';
    return null;
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() => _saving = true);
    try {
      final token = await AuthService.getValidToken();
      if (token == null) {
        throw Exception('Session expired. Please log in again.');
      }

      final res = await http.post(
        Uri.parse('${Api.baseUrl}/login/auth/change-password/'),
        headers: {
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
        body: jsonEncode({
          'new_password': _newPasswordController.text,
          'confirm_password': _confirmPasswordController.text,
        }),
      );

      final decoded = _tryDecodeMap(res.body);

      if (res.statusCode == 200) {
        if (!mounted) return;
        lsSnack(context, decoded?['message']?.toString() ?? 'Password changed successfully.');
        Navigator.pop(context);
        return;
      }

      throw Exception(_extractError(decoded, res.statusCode));
    } catch (e) {
      if (!mounted) return;
      lsSnack(context, e.toString().replaceFirst('Exception: ', ''), error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  String _extractError(Map<String, dynamic>? body, int statusCode) {
    if (body == null) return 'Could not change password ($statusCode).';
    // 🔥 Same shape both this endpoint and every other DRF one in this app
    // use: either {"status": false, "message": "..."} (view-level error,
    // e.g. "new password same as current") or {"field": ["message"]}
    // (serializer validation error, e.g. weak password / mismatch).
    final message = body['message'];
    if (message is String && message.isNotEmpty) return message;
    for (final value in body.values) {
      if (value is List && value.isNotEmpty) return value.first.toString();
    }
    return 'Could not change password ($statusCode).';
  }

  Map<String, dynamic>? _tryDecodeMap(String body) {
    try {
      final decoded = jsonDecode(body);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Change password'),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(kLsPad),
          children: [
            Text(
              'Choose a strong password you don\u2019t use anywhere else.',
              style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.4),
            ),
            const SizedBox(height: 20),
            TextFormField(
              controller: _newPasswordController,
              obscureText: _obscureNew,
              autovalidateMode: AutovalidateMode.onUserInteraction,
              decoration: InputDecoration(
                labelText: 'New password',
                prefixIcon: const Icon(Icons.lock_outline_rounded),
                suffixIcon: IconButton(
                  icon: Icon(_obscureNew ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                  onPressed: () => setState(() => _obscureNew = !_obscureNew),
                ),
              ),
              validator: _validateNewPassword,
            ),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: Text(
                'At least 8 characters, with uppercase, lowercase, a number and a special character.',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant, height: 1.35),
              ),
            ),
            const SizedBox(height: 14),
            TextFormField(
              controller: _confirmPasswordController,
              obscureText: _obscureConfirm,
              autovalidateMode: AutovalidateMode.onUserInteraction,
              decoration: InputDecoration(
                labelText: 'Confirm new password',
                prefixIcon: const Icon(Icons.lock_outline_rounded),
                suffixIcon: IconButton(
                  icon: Icon(_obscureConfirm ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                  onPressed: () => setState(() => _obscureConfirm = !_obscureConfirm),
                ),
              ),
              validator: _validateConfirmPassword,
              onFieldSubmitted: (_) => _saving ? null : _submit(),
            ),
            const SizedBox(height: 26),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _saving ? null : _submit,
                child: _saving
                    ? SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary),
                      )
                    : const Text('Update password'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
