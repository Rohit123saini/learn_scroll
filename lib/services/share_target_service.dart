// lib/services/share_target_service.dart
//
// ============================================================
// SHARE TARGET — "LearnScroll" appears in OTHER apps' share sheet
// (WhatsApp / Gallery / Chrome / Files ... -> Share -> LearnScroll).
//
//   ShareTargetService.instance.init()           // DeepLinkService.init() calls this once
//   ShareTargetService.instance.flushPending()   // DeepLinkService.flushPending() calls this
//
// Same "pending" idea as DeepLinkService: a share can land (a) before the
// Navigator has mounted (cold start) or (b) while the user is logged out.
// It's remembered and opened from the places where the app becomes ready
// (DeepLinkGate, login, app resume) — a logged-out user is never shown the
// chat list, and the shared content isn't lost either.
//
// What can be received: plain text / links, images, videos, any file.
// Opens ShareTargetScreen (pick chats -> send) via the app-wide navigatorKey.
//
// Native side (one-time, see SHARE_TARGET_SETUP.md):
//   Android: intent-filters in AndroidManifest.xml (SEND / SEND_MULTIPLE).
//   iOS:     Share Extension target (package README) + App Group.
// ============================================================

import 'dart:async';
import 'dart:developer' as developer;
import 'dart:io';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../message/screens/share_target_screen.dart';
import 'session_service.dart'; // navigatorKey

/// Content another app handed to us. Exactly what ShareTargetScreen sends.
class IncomingShare {
  /// Shared text or link (null when only files were shared).
  final String? text;

  /// Local file paths (images, videos, documents) already copied into our cache
  /// by the platform plugin.
  final List<String> filePaths;

  const IncomingShare({this.text, this.filePaths = const []});

  bool get isEmpty => (text == null || text!.trim().isEmpty) && filePaths.isEmpty;
}

class ShareTargetService {
  ShareTargetService._();
  static final ShareTargetService instance = ShareTargetService._();

  StreamSubscription<List<SharedMediaFile>>? _sub;
  bool _started = false;
  bool _flushing = false;
  IncomingShare? _pending;

  Future<void> init() async {
    if (_started) return;
    _started = true;
    // The share target only exists on mobile; other platforms just skip it.
    if (kIsWeb || !(Platform.isAndroid || Platform.isIOS)) return;
    try {
      // App was launched BY a share (cold start).
      final initial = await ReceiveSharingIntent.instance.getInitialMedia();
      _onShared(initial);
      await ReceiveSharingIntent.instance.reset(); // don't re-deliver on next launch
    } catch (e) {
      developer.log('ShareTarget initial media failed: $e');
    }
    // App already running when the share arrives.
    _sub = ReceiveSharingIntent.instance.getMediaStream().listen(
      (files) {
        _onShared(files);
        ReceiveSharingIntent.instance.reset();
      },
      onError: (Object e) => developer.log('ShareTarget stream error: $e'),
    );
  }

  void dispose() {
    _sub?.cancel();
    _sub = null;
    _started = false;
  }

  void _onShared(List<SharedMediaFile> items) {
    if (items.isEmpty) return;
    final texts = <String>[];
    final files = <String>[];
    for (final m in items) {
      switch (m.type) {
        case SharedMediaType.text:
        case SharedMediaType.url:
          if (m.path.trim().isNotEmpty) texts.add(m.path.trim());
          break;
        default:
          if (m.path.isNotEmpty) files.add(m.path);
      }
    }
    final share = IncomingShare(text: texts.isEmpty ? null : texts.join('\n'), filePaths: files);
    if (share.isEmpty) return;
    _pending = share;
    flushPending();
  }

  Future<bool> _isLoggedIn() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString('access_token'); // same check DeepLinkService uses
      return token != null && token.isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  /// Opens the pending share if the app is ready (Navigator mounted + logged in);
  /// otherwise keeps it for the next call. Safe to call any time, any number of times.
  Future<void> flushPending() async {
    final share = _pending;
    if (share == null || _flushing) return;
    _flushing = true;
    try {
      if (navigatorKey.currentState == null) return;
      if (!await _isLoggedIn()) return;
      if (!identical(_pending, share)) return; // a newer share replaced it while we awaited
      final nav = navigatorKey.currentState;
      if (nav == null) return;
      _pending = null;
      nav.push(MaterialPageRoute(builder: (_) => ShareTargetScreen(share: share)));
    } finally {
      _flushing = false;
    }
  }
}
