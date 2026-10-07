// lib/referrals/referral_capture.dart
//
// TASK 12 / 12.5 — remembers the `?ref=<code>` of any link the app was opened
// with, and reports it to the backend (POST /referrals/attribute/) once the
// user is logged in. A link can land while the user is logged OUT (they still
// have to sign up), so the code is kept in SharedPreferences until a flush
// succeeds or the backend definitively refuses it.
//
//   ReferralCapture.captureFromUri(uri)   // DeepLinkService._onUri
//   ReferralCapture.flush()               // DeepLinkService.flushPending
//
// Only ever stores a short alphanumeric code (validated), never the raw URL.

import 'dart:developer' as developer;

import 'package:shared_preferences/shared_preferences.dart';

import 'api/referrals_api.dart';

class ReferralCapture {
  ReferralCapture._();

  static const _kCode = 'pending_referral_code';
  static const _kType = 'pending_referral_source_type';
  static const _kId = 'pending_referral_source_id';
  static final RegExp _codeRe = RegExp(r'^[A-Za-z0-9]{4,20}$');
  static bool _flushing = false;

  /// Saves the `ref` query param of [uri] if it has a valid-looking one.
  /// Returns true when something was stored.
  static Future<bool> captureFromUri(Uri uri) async {
    final code = uri.queryParameters['ref']?.trim() ?? '';
    if (!_codeRe.hasMatch(code)) return false;

    final path = '${uri.host}/${uri.path}'.toLowerCase();
    String type = 'app';
    if (path.contains('classroom')) {
      type = 'classroom';
    } else if (path.contains('test')) {
      type = 'testseries';
    }
    final segs = uri.pathSegments.where((s) => s.isNotEmpty).toList();
    final id = segs.isNotEmpty ? segs.last : '';

    try {
      final prefs = await SharedPreferences.getInstance();
      // First link wins locally too — mirrors the backend's first-touch rule.
      if ((prefs.getString(_kCode) ?? '').isNotEmpty) return false;
      await prefs.setString(_kCode, code);
      await prefs.setString(_kType, type);
      await prefs.setString(_kId, id.length > 64 ? id.substring(0, 64) : id);
      return true;
    } catch (e) {
      developer.log('ReferralCapture store failed: $e');
      return false;
    }
  }

  /// Sends the pending code to the backend. Safe to call any number of times:
  /// no-op when nothing is pending, when not logged in, or while another flush
  /// is running. Keeps the code on transient failures (401/429/5xx/network).
  static Future<void> flush() async {
    if (_flushing) return;
    _flushing = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      final code = prefs.getString(_kCode) ?? '';
      if (code.isEmpty) return;
      try {
        await ReferralsApi.instance.attribute(
          code,
          sourceType: prefs.getString(_kType) ?? 'app',
          sourceId: prefs.getString(_kId) ?? '',
        );
      } on ReferralApiException catch (e) {
        final transient = e.statusCode == 401 || e.statusCode == 429 || e.statusCode >= 500;
        if (transient) return;
      } catch (_) {
        return; // timeout / offline — try again next time
      }
      await prefs.remove(_kCode);
      await prefs.remove(_kType);
      await prefs.remove(_kId);
    } catch (e) {
      developer.log('ReferralCapture flush failed: $e');
    } finally {
      _flushing = false;
    }
  }
}
