// lib/liveclass/services/pip_service.dart
//
// Thin platform-channel bridge for OS-level Picture-in-Picture.
// Android: real PictureInPictureParams-based PiP (API 26+, native side in
// MainActivity.kt shrinks the whole Activity surface into the floating
// window — no frame bridging needed there).
// iOS: AVPictureInPictureController-based PiP (iOS 15+, PipManager.swift) —
// window/lifecycle mechanics are wired; the actual video frame feed into
// the PiP window still needs a companion patch inside the livekit_client
// iOS plugin (see PipManager.swift's header comment) before real video
// shows up in the floating window there. Android does not have this gap.
import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class PipService {
  PipService._();
  static final PipService instance = PipService._();

  static const _channel = MethodChannel('learnscroll/pip');

  final _modeController = StreamController<bool>.broadcast();
  /// Emits true the instant the OS actually puts the app into PiP, false
  /// when it leaves PiP (user tapped back in, or closed the PiP window).
  /// LiveSessionScreen listens to this to swap into a chrome-free,
  /// video-only layout while true.
  Stream<bool> get onPipModeChanged => _modeController.stream;

  bool _listenerAttached = false;

  void _ensureListener() {
    if (_listenerAttached) return;
    _listenerAttached = true;
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'onPipModeChanged') {
        _modeController.add(call.arguments as bool);
      }
    });
  }

  /// Call with true right after a successful join (see _afterJoined in
  /// live_session_screen.dart), and false on leave/dispose. Native side
  /// only auto-enters PiP on backgrounding while this is true — every other
  /// screen in the app is never PiP-eligible.
  Future<void> setPipEnabled(bool enabled) async {
    _ensureListener();
    try {
      await _channel.invokeMethod('setPipEnabled', {'enabled': enabled});
    } catch (e) {
      debugPrint('PipService.setPipEnabled failed: $e');
    }
  }

  /// Manual trigger — wire this to the existing mini-view button so users
  /// can pop into real PiP without backgrounding the app at all.
  Future<bool> enterPip() async {
    try {
      final ok = await _channel.invokeMethod<bool>('enterPip');
      return ok ?? false;
    } catch (e) {
      debugPrint('PipService.enterPip failed: $e');
      return false;
    }
  }

  Future<bool> isSupported() async {
    try {
      final ok = await _channel.invokeMethod<bool>('isPipSupported');
      return ok ?? false;
    } catch (_) {
      return false;
    }
  }
}