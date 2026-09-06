// message/widgets/translatable_message_widgets.dart
//
// Features 9 & 10 — drop these two widgets into the EXISTING message
// bubble widget (not included here — wasn't among the uploaded files).
// Both are self-contained; they only need the message's id + text.
//
// Typical wiring inside your bubble's build():
//
//   if (message.type == MessageType.text) ...[
//     Text(message.text),
//     Row(
//       children: [
//         ListenButton(messageId: message.id, text: message.text),
//         const SizedBox(width: 4),
//         TranslateToggle(messageId: message.id, text: message.text),
//       ],
//     ),
//   ]
//
// Both widgets are small (icon-button-sized) so they fit naturally in
// a bubble's existing action row (next to reply/react/star icons) or a
// long-press context menu — whichever pattern this app's bubble already
// uses for per-message actions.

import 'package:flutter/material.dart';
import '../services/translate_service.dart';
import '../services/tts_service.dart';
import 'language_picker_sheet.dart';

// ============================================================
// Feature 10 — Listen (TTS) button
// ============================================================
class ListenButton extends StatelessWidget {
  final String messageId;
  final String text;
  /// Optional — pass the currently-displayed language's code (e.g. if
  /// the user is viewing a translated version, read THAT language aloud
  /// instead of the original). BCP-47 form, e.g. 'hi-IN'.
  final String? languageCode;

  const ListenButton({
    super.key,
    required this.messageId,
    required this.text,
    this.languageCode,
  });

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String?>(
      valueListenable: TtsService.instance.currentlySpeakingId,
      builder: (context, speakingId, _) {
        final isSpeaking = speakingId == messageId;
        return IconButton(
          padding: EdgeInsets.zero,
          constraints: const BoxConstraints(),
          iconSize: 18,
          icon: Icon(
            isSpeaking ? Icons.pause_circle_outline : Icons.volume_up_outlined,
            color: Colors.white54,
          ),
          onPressed: () => TtsService.instance.speak(messageId, text, languageCode: languageCode),
        );
      },
    );
  }
}

// ============================================================
// Feature 9 — Inline translate toggle
// ============================================================
/// Shows a small "Translate" text button. First tap: uses the saved
/// preferred language (asks via the language picker sheet if none set
/// yet), fetches the translation, and shows it inline below the
/// original — same bubble, no navigation. Tap "Change language" to pick
/// a different target on the fly.
class TranslateToggle extends StatefulWidget {
  final String messageId;
  final String text;

  const TranslateToggle({super.key, required this.messageId, required this.text});

  @override
  State<TranslateToggle> createState() => _TranslateToggleState();
}

class _TranslateToggleState extends State<TranslateToggle> {
  bool _expanded = false;
  bool _loading = false;
  String? _error;
  TranslationResult? _result;

  Future<void> _handleTap() async {
    if (_expanded) {
      setState(() => _expanded = false);
      return;
    }

    String? lang = await getPreferredTranslateLang();
    if (lang == null) {
      if (!mounted) return;
      lang = await showLanguagePickerSheet(context);
      if (lang == null) return; // user dismissed the sheet
    }

    await _fetchTranslation(lang);
  }

  Future<void> _changeLanguage() async {
    if (!mounted) return;
    final lang = await showLanguagePickerSheet(context);
    if (lang == null) return;
    await _fetchTranslation(lang);
  }

  Future<void> _fetchTranslation(String lang) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await TranslateService.instance.translate(
        messageId: widget.messageId,
        targetLang: lang,
      );
      if (!mounted) return;
      setState(() {
        _result = result;
        _expanded = true;
      });
    } on TranslateException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        InkWell(
          onTap: _loading ? null : _handleTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.translate, size: 14, color: Colors.white54),
                const SizedBox(width: 4),
                Text(
                  _loading
                      ? 'Translating...'
                      : (_expanded ? 'Hide translation' : 'Translate'),
                  style: const TextStyle(color: Colors.white54, fontSize: 12),
                ),
              ],
            ),
          ),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(_error!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
          ),
        if (_expanded && _result != null)
          Container(
            margin: const EdgeInsets.only(top: 4),
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.05),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _result!.translatedText,
                  style: const TextStyle(color: Colors.white, fontSize: 14, fontStyle: FontStyle.italic),
                ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Text(
                      kTranslateLanguages[_result!.targetLang] ?? _result!.targetLang,
                      style: const TextStyle(color: Colors.white38, fontSize: 11),
                    ),
                    const SizedBox(width: 10),
                    InkWell(
                      onTap: _changeLanguage,
                      child: const Text(
                        'Change language',
                        style: TextStyle(color: Colors.blueAccent, fontSize: 11),
                      ),
                    ),
                    const SizedBox(width: 10),
                    ListenButton(
                      messageId: '${widget.messageId}_translated',
                      text: _result!.translatedText,
                    ),
                  ],
                ),
              ],
            ),
          ),
      ],
    );
  }
}