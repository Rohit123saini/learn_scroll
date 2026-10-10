// lib/post/widgets/story_sticker_pickers.dart
//
// Stories upgrade, Part 2 — the "add a sticker" flow of the story composer.
//
//   pickStickerDraft()  ->  tray (Mention / Link / Poll / Question)
//                       ->  the matching input sheet
//                       ->  a [StickerDraft] (or null if the user backed out)
//
// The draft is placed / dragged / scaled by `story_caption_sheet.dart`; this
// file only collects the content. Limits mirror `post/story_stickers.py` (the
// constants live in `story_model.dart`); the server re-validates everything,
// these checks just save a round trip.

import 'dart:async';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../models/story_model.dart';
import '../services/story_service.dart';

const Color _kAccent = Color(0xFF8B7CFF);
const Color _kSheetBg = Color(0xFF1C1C1E);
const RoundedRectangleBorder _kSheetShape =
    RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22)));

/// Opens the sticker tray, then the input sheet for whatever was chosen.
/// [existing] is what is already on the story (limits are counted from it);
/// [audience] decides who may be @mentioned (Close Friends story -> only
/// Close Friends).
Future<StickerDraft?> pickStickerDraft(
  BuildContext context, {
  required List<StickerDraft> existing,
  required String audience,
}) async {
  int count(String kind) => existing.where((d) => d.kind == kind).length;
  final full = existing.length >= kStoryMaxStickers;
  final available = <String, bool>{
    StorySticker.kMention: !full && count(StorySticker.kMention) < kStoryMaxMentions,
    StorySticker.kLink: !full && count(StorySticker.kLink) < kStoryMaxLinks,
    StorySticker.kPoll: !full && count(StorySticker.kPoll) < kStoryMaxPolls,
    StorySticker.kQuestion: !full && count(StorySticker.kQuestion) < kStoryMaxQuestions,
  };

  final kind = await showModalBottomSheet<String>(
    context: context,
    backgroundColor: _kSheetBg,
    shape: _kSheetShape,
    builder: (_) => _StickerTray(available: available),
  );
  if (kind == null || !context.mounted) return null;

  Future<StickerDraft?> sheet(Widget Function(BuildContext) builder) => showModalBottomSheet<StickerDraft>(
        context: context,
        isScrollControlled: true,
        backgroundColor: _kSheetBg,
        shape: _kSheetShape,
        builder: builder,
      );

  switch (kind) {
    case StorySticker.kMention:
      final taken = existing.where((d) => d.kind == StorySticker.kMention).map((d) => d.userId).whereType<int>().toSet();
      return sheet((_) => _MentionPickerSheet(audience: audience, takenIds: taken));
    case StorySticker.kLink:
      return sheet((_) => const _LinkSheet());
    case StorySticker.kPoll:
      return sheet((_) => const _PollSheet());
    case StorySticker.kQuestion:
      return sheet((_) => const _QuestionSheet());
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────
// Tray
// ─────────────────────────────────────────────────────────────────────────
class _StickerTray extends StatelessWidget {
  final Map<String, bool> available;
  const _StickerTray({required this.available});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    Widget tile(String kind, IconData icon, String label) {
      final enabled = available[kind] ?? false;
      return Opacity(
        opacity: enabled ? 1 : 0.4,
        child: Material(
          color: Colors.white.withOpacity(0.08),
          borderRadius: BorderRadius.circular(14),
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: enabled ? () => Navigator.of(context).pop(kind) : null,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
              child: Row(
                children: [
                  Icon(icon, color: _kAccent, size: 24),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 14)),
                        if (!enabled)
                          Text(l10n.stickerLimitReached, style: const TextStyle(color: Colors.white54, fontSize: 11)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const _SheetHandle(),
            Padding(
              padding: const EdgeInsets.only(top: 14, bottom: 14),
              child: Text(l10n.storyAddSticker, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
            ),
            Row(children: [
              Expanded(child: tile(StorySticker.kMention, Icons.alternate_email_rounded, l10n.stickerMention)),
              const SizedBox(width: 10),
              Expanded(child: tile(StorySticker.kLink, Icons.link_rounded, l10n.stickerLink)),
            ]),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(child: tile(StorySticker.kPoll, Icons.poll_outlined, l10n.stickerPoll)),
              const SizedBox(width: 10),
              Expanded(child: tile(StorySticker.kQuestion, Icons.help_outline_rounded, l10n.stickerQuestion)),
            ]),
          ],
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Mention picker
// ─────────────────────────────────────────────────────────────────────────
class _MentionPickerSheet extends StatefulWidget {
  final String audience;
  final Set<int> takenIds;
  const _MentionPickerSheet({required this.audience, required this.takenIds});

  @override
  State<_MentionPickerSheet> createState() => _MentionPickerSheetState();
}

class _MentionPickerSheetState extends State<_MentionPickerSheet> {
  final _controller = TextEditingController();
  Timer? _debounce;
  List<StoryMentionCandidate> _items = const [];
  bool _loading = true;
  bool _error = false;
  int _requestId = 0; // drops answers of superseded searches

  @override
  void initState() {
    super.initState();
    _load('');
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  Future<void> _load(String q) async {
    final id = ++_requestId;
    try {
      final result = await StoryService.getMentionCandidates(q: q, audience: widget.audience);
      if (!mounted || id != _requestId) return;
      setState(() {
        _items = result;
        _loading = false;
        _error = false;
      });
    } catch (_) {
      if (!mounted || id != _requestId) return;
      setState(() {
        _items = const [];
        _loading = false;
        _error = true;
      });
    }
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    setState(() {
      _loading = true;
      _error = false;
    });
    _debounce = Timer(const Duration(milliseconds: 300), () => _load(value));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final inset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: inset),
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.68,
        child: SafeArea(
          child: Column(
            children: [
              const SizedBox(height: 10),
              const _SheetHandle(),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
                child: Row(children: [
                  const Icon(Icons.alternate_email_rounded, color: _kAccent, size: 18),
                  const SizedBox(width: 8),
                  Text(l10n.stickerMention, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
                ]),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: TextField(
                  controller: _controller,
                  onChanged: _onChanged,
                  style: const TextStyle(color: Colors.white),
                  cursorColor: _kAccent,
                  textInputAction: TextInputAction.search,
                  decoration: _fieldDecoration(l10n.stickerMentionSearchHint, prefix: Icons.search_rounded),
                ),
              ),
              if (widget.audience == 'close_friends')
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
                  child: Text(l10n.stickerMentionCloseFriendsNote, style: const TextStyle(color: Colors.white54, fontSize: 11.5)),
                ),
              const Divider(color: Colors.white12, height: 1),
              Expanded(child: _buildBody(l10n)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildBody(AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: _kAccent));
    if (_error) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(l10n.stickerLoadFailed, style: const TextStyle(color: Colors.white54)),
          TextButton(
            onPressed: () {
              setState(() {
                _loading = true;
                _error = false;
              });
              _load(_controller.text);
            },
            child: Text(l10n.retry),
          ),
        ]),
      );
    }
    if (_items.isEmpty) {
      return Center(child: Text(l10n.stickerMentionEmpty, style: const TextStyle(color: Colors.white54)));
    }
    return ListView.builder(
      itemCount: _items.length,
      itemBuilder: (context, i) {
        final c = _items[i];
        final taken = widget.takenIds.contains(c.id);
        return ListTile(
          enabled: !taken,
          leading: _Avatar(url: c.profilePicture, fallback: c.username),
          title: Text(c.username, style: TextStyle(color: taken ? Colors.white38 : Colors.white, fontWeight: FontWeight.w700)),
          subtitle: c.name.isNotEmpty && c.name != c.username
              ? Text(c.name, style: const TextStyle(color: Colors.white54, fontSize: 12))
              : null,
          trailing: taken ? const Icon(Icons.check_rounded, color: Colors.white38) : null,
          onTap: taken
              ? null
              : () => Navigator.of(context).pop(StickerDraft.mention(userId: c.id, username: c.username, profilePicture: c.profilePicture)),
        );
      },
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Link
// ─────────────────────────────────────────────────────────────────────────
class _LinkSheet extends StatefulWidget {
  const _LinkSheet();

  @override
  State<_LinkSheet> createState() => _LinkSheetState();
}

class _LinkSheetState extends State<_LinkSheet> {
  final _url = TextEditingController();
  final _label = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _url.dispose();
    _label.dispose();
    super.dispose();
  }

  static final RegExp _ipv4 = RegExp(r'^\d{1,3}(\.\d{1,3}){3}$');

  void _submit() {
    final l10n = AppLocalizations.of(context)!;
    var raw = _url.text.trim();
    if (raw.isEmpty) {
      setState(() => _error = l10n.stickerLinkInvalid);
      return;
    }
    if (!raw.contains('://')) raw = 'https://$raw';
    final uri = Uri.tryParse(raw);
    final ok = uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.host.contains('.') &&
        !_ipv4.hasMatch(uri.host) &&
        uri.userInfo.isEmpty;
    if (!ok) {
      setState(() => _error = l10n.stickerLinkInvalid);
      return;
    }
    Navigator.of(context).pop(StickerDraft.link(url: raw, label: _label.text));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return _SheetFrame(
      icon: Icons.link_rounded,
      title: l10n.stickerLink,
      actionLabel: l10n.stickerAddButton,
      onAction: _submit,
      children: [
        TextField(
          controller: _url,
          autofocus: true,
          keyboardType: TextInputType.url,
          autocorrect: false,
          style: const TextStyle(color: Colors.white),
          cursorColor: _kAccent,
          onChanged: (_) {
            if (_error != null) setState(() => _error = null);
          },
          decoration: _fieldDecoration(l10n.stickerLinkUrlHint, prefix: Icons.link_rounded),
        ),
        const SizedBox(height: 10),
        TextField(
          controller: _label,
          maxLength: kStoryLinkLabelMax,
          style: const TextStyle(color: Colors.white),
          cursorColor: _kAccent,
          decoration: _fieldDecoration(l10n.stickerLinkLabelHint),
        ),
        if (_error != null) _ErrorText(_error!),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Poll
// ─────────────────────────────────────────────────────────────────────────
class _PollSheet extends StatefulWidget {
  const _PollSheet();

  @override
  State<_PollSheet> createState() => _PollSheetState();
}

class _PollSheetState extends State<_PollSheet> {
  final _question = TextEditingController();
  final List<TextEditingController> _options = [TextEditingController(), TextEditingController()];
  String? _error;

  @override
  void dispose() {
    _question.dispose();
    for (final c in _options) {
      c.dispose();
    }
    super.dispose();
  }

  void _addOption() {
    if (_options.length >= kStoryPollMaxOptions) return;
    setState(() => _options.add(TextEditingController()));
  }

  void _removeOption(int index) {
    if (_options.length <= kStoryPollMinOptions) return;
    final removed = _options[index];
    setState(() => _options.removeAt(index));
    // Dispose only after the frame that stops using it has been built.
    WidgetsBinding.instance.addPostFrameCallback((_) => removed.dispose());
  }

  void _submit() {
    final l10n = AppLocalizations.of(context)!;
    final question = _question.text.trim();
    final options = _options.map((c) => c.text.trim()).where((t) => t.isNotEmpty).toList();
    final distinct = options.map((o) => o.toLowerCase()).toSet().length == options.length;
    if (question.isEmpty || options.length < kStoryPollMinOptions || !distinct) {
      setState(() => _error = l10n.stickerPollInvalid);
      return;
    }
    Navigator.of(context).pop(StickerDraft.poll(question: question, options: options));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return _SheetFrame(
      icon: Icons.poll_outlined,
      title: l10n.stickerPoll,
      actionLabel: l10n.stickerAddButton,
      onAction: _submit,
      children: [
        TextField(
          controller: _question,
          autofocus: true,
          maxLength: kStoryPollQuestionMax,
          style: const TextStyle(color: Colors.white),
          cursorColor: _kAccent,
          textCapitalization: TextCapitalization.sentences,
          onChanged: (_) {
            if (_error != null) setState(() => _error = null);
          },
          decoration: _fieldDecoration(l10n.stickerPollQuestionHint),
        ),
        const SizedBox(height: 6),
        for (int i = 0; i < _options.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _options[i],
                  maxLength: kStoryPollOptionMax,
                  style: const TextStyle(color: Colors.white),
                  cursorColor: _kAccent,
                  onChanged: (_) {
                    if (_error != null) setState(() => _error = null);
                  },
                  decoration: _fieldDecoration(l10n.stickerPollOptionHint(i + 1)),
                ),
              ),
              if (_options.length > kStoryPollMinOptions)
                IconButton(tooltip: 'Close', 
                  onPressed: () => _removeOption(i),
                  icon: const Icon(Icons.close_rounded, color: Colors.white54, size: 20),
                ),
            ]),
          ),
        if (_options.length < kStoryPollMaxOptions)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _addOption,
              icon: const Icon(Icons.add_rounded, size: 18),
              label: Text(l10n.stickerPollAddOption),
              style: TextButton.styleFrom(foregroundColor: _kAccent),
            ),
          ),
        if (_error != null) _ErrorText(_error!),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Question
// ─────────────────────────────────────────────────────────────────────────
class _QuestionSheet extends StatefulWidget {
  const _QuestionSheet();

  @override
  State<_QuestionSheet> createState() => _QuestionSheetState();
}

class _QuestionSheetState extends State<_QuestionSheet> {
  final _prompt = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _prompt.text.trim();
    if (text.isEmpty) {
      setState(() => _error = AppLocalizations.of(context)!.stickerQuestionInvalid);
      return;
    }
    Navigator.of(context).pop(StickerDraft.question(prompt: text));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return _SheetFrame(
      icon: Icons.help_outline_rounded,
      title: l10n.stickerQuestion,
      actionLabel: l10n.stickerAddButton,
      onAction: _submit,
      children: [
        TextField(
          controller: _prompt,
          autofocus: true,
          maxLength: kStoryQuestionPromptMax,
          maxLines: 2,
          minLines: 1,
          style: const TextStyle(color: Colors.white),
          cursorColor: _kAccent,
          textCapitalization: TextCapitalization.sentences,
          onChanged: (_) {
            if (_error != null) setState(() => _error = null);
          },
          decoration: _fieldDecoration(l10n.stickerQuestionPromptHint),
        ),
        if (_error != null) _ErrorText(_error!),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Shared bits
// ─────────────────────────────────────────────────────────────────────────
InputDecoration _fieldDecoration(String hint, {IconData? prefix}) {
  return InputDecoration(
    hintText: hint,
    hintStyle: const TextStyle(color: Colors.white54),
    counterStyle: const TextStyle(color: Colors.white38, fontSize: 11),
    prefixIcon: prefix == null ? null : Icon(prefix, color: Colors.white54, size: 20),
    filled: true,
    fillColor: Colors.white.withOpacity(0.08),
    isDense: true,
    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
    border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
  );
}

class _SheetHandle extends StatelessWidget {
  const _SheetHandle();

  @override
  Widget build(BuildContext context) =>
      Center(child: Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))));
}

class _ErrorText extends StatelessWidget {
  final String text;
  const _ErrorText(this.text);

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text(text, style: const TextStyle(color: Color(0xFFFF6B6B), fontSize: 12.5, fontWeight: FontWeight.w600)),
      );
}

/// Title row + scrollable fields + primary button, lifted above the keyboard.
class _SheetFrame extends StatelessWidget {
  final IconData icon;
  final String title;
  final String actionLabel;
  final VoidCallback onAction;
  final List<Widget> children;

  const _SheetFrame({
    required this.icon,
    required this.title,
    required this.actionLabel,
    required this.onAction,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const _SheetHandle(),
              Padding(
                padding: const EdgeInsets.only(top: 14, bottom: 12),
                child: Row(children: [
                  Icon(icon, color: _kAccent, size: 18),
                  const SizedBox(width: 8),
                  Text(title, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
                ]),
              ),
              ...children,
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: onAction,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _kAccent,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                ),
                child: Text(actionLabel, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 14)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Avatar extends StatelessWidget {
  final String? url;
  final String fallback;
  const _Avatar({required this.url, required this.fallback});

  @override
  Widget build(BuildContext context) {
    final hasPic = (url ?? '').isNotEmpty;
    return CircleAvatar(
      radius: 18,
      backgroundColor: Colors.white24,
      backgroundImage: hasPic ? CachedNetworkImageProvider(url!) : null,
      child: hasPic ? null : Text(fallback.isNotEmpty ? fallback[0].toUpperCase() : '?', style: const TextStyle(color: Colors.white)),
    );
  }
}
