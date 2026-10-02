// ============================================================
// REELS — global sound flag.
//
// One app-wide "muted" switch for the Reels screen, persisted in
// SharedPreferences. Default = sound ON (muted == false). Home's feed videos are
// unrelated to this flag and stay muted by default.
//
//   await ReelsSoundService.instance.load();          // before building players
//   ReelsSoundService.instance.muted                  // ValueListenable<bool>
//   ReelsSoundService.instance.toggle();              // speaker button
// ============================================================

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ReelsSoundService {
  ReelsSoundService._();
  static final ReelsSoundService instance = ReelsSoundService._();

  static const String _prefsKey = 'reels_muted';

  final ValueNotifier<bool> _muted = ValueNotifier<bool>(false); // default: sound ON
  Future<void>? _loading;
  bool _touched = false; // user toggled before the stored value was read

  ValueListenable<bool> get muted => _muted;
  bool get isMuted => _muted.value;

  /// Reads the stored flag once (later calls return the same future). Never throws.
  Future<void> load() => _loading ??= _load();

  Future<void> _load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!_touched) _muted.value = prefs.getBool(_prefsKey) ?? false;
    } catch (_) {
      // storage unavailable -> keep default (sound on)
    }
  }

  Future<void> setMuted(bool value) async {
    _touched = true;
    _muted.value = value;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_prefsKey, value);
    } catch (_) {
      // best effort; the in-memory flag still applies for this session
    }
  }

  Future<void> toggle() => setMuted(!_muted.value);
}
