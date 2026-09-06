// message/services/tts_service.dart
//
// Feature 10 — Text-to-speech for chat messages.
// Client-side only, no backend call needed — uses the device's own
// system TTS engine via `flutter_tts` (add to pubspec.yaml: flutter_tts: ^4.x).
//
// Singleton, one active "speaking" message at a time (starting a new
// one stops whichever was playing — matches how a voice-note bubble
// already behaves in most chat apps, incl. this one's own audio-message
// bubble pattern).

import 'package:flutter/foundation.dart';
import 'package:flutter_tts/flutter_tts.dart';

class TtsService {
  TtsService._() {
    _tts.setCompletionHandler(() {
      currentlySpeakingId.value = null;
    });
    _tts.setCancelHandler(() {
      currentlySpeakingId.value = null;
    });
    _tts.setErrorHandler((_) {
      currentlySpeakingId.value = null;
    });
  }
  static final TtsService instance = TtsService._();

  final FlutterTts _tts = FlutterTts();

  /// The message id currently being read aloud, or null if idle.
  /// UI (message bubble) listens to this to show a pause icon on the
  /// ONE bubble that's actually speaking, and a play icon everywhere else.
  final ValueNotifier<String?> currentlySpeakingId = ValueNotifier(null);

  /// messageId lets the bubble UI know whether THIS bubble is the one
  /// speaking (vs. some other message elsewhere in the same chat).
  /// languageCode: pass the message's detected/translated language if
  /// known (e.g. 'hi-IN'), else the device default is used.
  Future<void> speak(String messageId, String text, {String? languageCode}) async {
    if (text.trim().isEmpty) return;

    // Already reading this exact bubble -> treat as pause/stop toggle.
    if (currentlySpeakingId.value == messageId) {
      await stop();
      return;
    }

    // Switching from one bubble to another -> stop the old one first.
    if (currentlySpeakingId.value != null) {
      await _tts.stop();
    }

    if (languageCode != null) {
      try {
        await _tts.setLanguage(languageCode);
      } catch (_) {
        // Device may not have this language's voice installed — fall
        // back silently to whatever the default engine language is
        // rather than blocking playback entirely.
      }
    }

    currentlySpeakingId.value = messageId;
    await _tts.speak(text);
  }

  Future<void> stop() async {
    await _tts.stop();
    currentlySpeakingId.value = null;
  }
}