// message/widgets/notes_bar.dart
//
// 🔥 NAYA (M2-FE) — Notes row (Instagram-style status), inbox ke upar.
//
// Backend (M2-BE):
//   GET    /message/notes/     -> { results: [note...], my_note: note|null, next, count }
//   PUT    /message/notes/me/  {text<=60, emoji, audience: followers|close_friends}  (+24h)
//   DELETE /message/notes/me/
//
// Is file me:
//   * `NotesBar`            — horizontal row: "Your note" + doosron ke note bubbles
//   * compose sheet         — 60-char counter, emoji, audience chooser, suggestion chips
//   * reply sheet           — note ka reply = DM, jisme note ka quote hota hai
//
// `ConversationsScreen` isko `GlobalKey<NotesBarState>` se refresh karwati hai.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../services/message_api_service.dart';
import '../../theme_service.dart';

const int _kNoteMaxLen = 60; // backend `UserNote.MAX_TEXT_LENGTH` ke saath sync
const Color _kCloseFriendsGreen = Color(0xFF2EBD59);

// chip tap par text aur emoji alag-alag fields me jaate hain.
class _Suggestion {
  final String text;
  final String emoji;
  const _Suggestion(this.text, this.emoji);
}

const List<_Suggestion> _kSuggestions = [
  _Suggestion('Studying for JEE', '📚'),
  _Suggestion('Preparing for NEET', '🧬'),
  _Suggestion('Revising Physics', '⚡'),
  _Suggestion('Need a study buddy', '🤝'),
  _Suggestion('Exam tomorrow', '😬'),
  _Suggestion('Focus mode on', '🎧'),
  _Suggestion('Taking a break', '☕'),
  _Suggestion('Grinding mock tests', '🎯'),
];

const List<String> _kQuickEmojis = ['📚', '🔥', '🎯', '🧠', '💪', '☕', '🎧', '😴', '😎', '✨', '🙏', '😬'];

/// 60 characters (user-visible, yaani Unicode code points — Dart ke UTF-16
/// units nahi, warna 📚 jaise emoji 2 gine jaate aur backend se mismatch hota).
class _RuneLimitFormatter extends TextInputFormatter {
  final int max;
  const _RuneLimitFormatter(this.max);

  @override
  TextEditingValue formatEditUpdate(TextEditingValue oldValue, TextEditingValue newValue) {
    if (newValue.text.runes.length <= max) return newValue;
    final cut = String.fromCharCodes(newValue.text.runes.take(max));
    return TextEditingValue(text: cut, selection: TextSelection.collapsed(offset: cut.length));
  }
}

// ======================================================================
// NOTES BAR
// ======================================================================
class NotesBar extends StatefulWidget {
  const NotesBar({super.key});
  @override
  State<NotesBar> createState() => NotesBarState();
}

class NotesBarState extends State<NotesBar> {
  UserNoteModel? _myNote;
  final List<UserNoteModel> _notes = [];
  final ScrollController _scroll = ScrollController();
  Timer? _expiryTicker;

  int _page = 1;
  bool _hasMore = false;
  bool _isLoadingMore = false;

  @override
  void initState() {
    super.initState();
    refresh();
    _scroll.addListener(_onScroll);
    // Expired notes read time par server filter karta hai; screen khuli rahe
    // to bhi 24h cross hote hi bubble hat jaaye.
    _expiryTicker = Timer.periodic(const Duration(minutes: 1), (_) {
      if (mounted) setState(_dropExpired);
    });
  }

  @override
  void dispose() {
    _expiryTicker?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  void _dropExpired() {
    _notes.removeWhere((n) => n.isExpired);
    if (_myNote?.isExpired ?? false) _myNote = null;
  }

  /// Page 1 dobara laao (pull-to-refresh / screen open). Fail ho to purana
  /// state waisa hi rehta hai — notes ek nice-to-have row hai, chat list nahi rokni.
  Future<void> refresh() async {
    try {
      final page = await MessageApiService.getNotes(page: 1);
      if (!mounted) return;
      setState(() {
        _myNote = page.myNote;
        _notes
          ..clear()
          ..addAll(page.items);
        _page = 1;
        _hasMore = page.hasMore;
        _dropExpired();
      });
    } catch (_) {}
  }

  void _onScroll() {
    if (!_scroll.hasClients || _isLoadingMore || !_hasMore) return;
    if (_scroll.position.extentAfter < 160) _loadMore();
  }

  Future<void> _loadMore() async {
    setState(() => _isLoadingMore = true);
    try {
      final page = await MessageApiService.getNotes(page: _page + 1);
      if (!mounted) return;
      setState(() {
        final known = _notes.map((n) => n.id).toSet();
        _notes.addAll(page.items.where((n) => !known.contains(n.id)));
        _page += 1;
        _hasMore = page.hasMore;
        _isLoadingMore = false;
      });
    } catch (_) {
      if (mounted) setState(() => _isLoadingMore = false);
    }
  }

  Future<void> _openCompose() async {
    final result = await showModalBottomSheet<_ComposeResult>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _NoteComposeSheet(existing: _myNote),
    );
    if (result == null || !mounted) return;
    setState(() => _myNote = result.deleted ? null : result.note);
  }

  Future<void> _openReply(UserNoteModel note) async {
    final sent = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _NoteReplySheet(note: note),
    );
    if (sent == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Reply ${note.displayName} ko bhej diya')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final visible = _notes.where((n) => !n.isExpired).toList();
    final itemCount = 1 + visible.length + (_isLoadingMore ? 1 : 0);

    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      height: 128,
      child: ListView.builder(
        controller: _scroll,
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        itemCount: itemCount,
        itemBuilder: (context, i) {
          if (i == 0) {
            return _NoteTile(
              note: _myNote,
              isMine: true,
              label: 'Your note',
              onTap: _openCompose,
            );
          }
          final idx = i - 1;
          if (idx >= visible.length) {
            return const SizedBox(
              width: 48,
              child: Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            );
          }
          final note = visible[idx];
          return _NoteTile(
            note: note,
            isMine: false,
            label: note.displayName,
            onTap: () => _openReply(note),
          );
        },
      ),
    );
  }
}

// ======================================================================
// ONE TILE: bubble (text) avatar ke upar
// ======================================================================
class _NoteTile extends StatelessWidget {
  final UserNoteModel? note; // null sirf "Your note" me (abhi koi note nahi)
  final bool isMine;
  final String label;
  final VoidCallback onTap;
  const _NoteTile({required this.note, required this.isMine, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    final n = note;
    final photo = n?.photoUrl;
    final hasPhoto = photo != null && photo.isNotEmpty;
    final closeFriends = n?.isCloseFriends ?? false;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: SizedBox(
        width: 84,
        height: 120,
        child: Stack(
          alignment: Alignment.topCenter,
          children: [
            // avatar
            Positioned(
              top: 42,
              child: Stack(clipBehavior: Clip.none, children: [
                Container(
                  padding: const EdgeInsets.all(2),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: closeFriends ? _kCloseFriendsGreen : cs.outlineVariant,
                      width: closeFriends ? 2 : 1,
                    ),
                  ),
                  child: CircleAvatar(
                    radius: 27,
                    backgroundColor: tokens.surface2,
                    backgroundImage: hasPhoto ? CachedNetworkImageProvider(photo) : null,
                    child: !hasPhoto ? Icon(Icons.person_rounded, color: cs.onSurfaceVariant) : null,
                  ),
                ),
                if (isMine && n == null)
                  Positioned(
                    right: -2, bottom: -2,
                    child: Container(
                      width: 20, height: 20,
                      decoration: BoxDecoration(
                        color: cs.primary,
                        shape: BoxShape.circle,
                        border: Border.all(color: cs.surface, width: 2),
                      ),
                      child: Icon(Icons.add_rounded, size: 13, color: cs.onPrimary),
                    ),
                  ),
              ]),
            ),
            // bubble (avatar ke upar, thoda overlap)
            Positioned(
              top: 0, left: 2, right: 2, height: 50,
              child: Align(
                alignment: Alignment.bottomCenter,
                child: _bubble(context, cs),
              ),
            ),
            // naam
            Positioned(
              top: 104, left: 2, right: 2,
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant, fontWeight: FontWeight.w500),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _bubble(BuildContext context, ColorScheme cs) {
    final n = note;
    final placeholder = n == null;
    final text = placeholder ? 'Share a note' : (n?.text ?? '');
    final emoji = placeholder ? '' : (n?.emoji ?? '');
    final emojiOnly = !placeholder && text.isEmpty && emoji.isNotEmpty;

    return Stack(clipBehavior: Clip.none, children: [
      Container(
        constraints: const BoxConstraints(maxWidth: 80),
        padding: EdgeInsets.symmetric(horizontal: emojiOnly ? 12 : 8, vertical: 5),
        decoration: BoxDecoration(
          color: placeholder ? AppThemeTokens.of(context).surface2 : cs.surface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: placeholder ? Colors.transparent : cs.outlineVariant),
          boxShadow: placeholder
              ? null
              : [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 4, offset: const Offset(0, 1))],
        ),
        child: emojiOnly
            ? Text(emoji, style: const TextStyle(fontSize: 20))
            : Text(
                emoji.isEmpty ? text : '$text $emoji',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 10.5,
                  height: 1.2,
                  color: placeholder ? cs.onSurfaceVariant : cs.onSurface,
                  fontStyle: placeholder ? FontStyle.italic : FontStyle.normal,
                ),
              ),
      ),
      if (n?.isCloseFriends ?? false)
        Positioned(
          right: -4, top: -4,
          child: Container(
            width: 16, height: 16,
            decoration: BoxDecoration(
              color: _kCloseFriendsGreen,
              shape: BoxShape.circle,
              border: Border.all(color: cs.surface, width: 1.5),
            ),
            child: const Icon(Icons.star_rounded, size: 10, color: Colors.white),
          ),
        ),
    ]);
  }
}

// ======================================================================
// COMPOSE SHEET
// ======================================================================
class _ComposeResult {
  final UserNoteModel? note;
  final bool deleted;
  const _ComposeResult.saved(UserNoteModel n) : note = n, deleted = false;
  const _ComposeResult.deleted() : note = null, deleted = true;
}

class _NoteComposeSheet extends StatefulWidget {
  final UserNoteModel? existing;
  const _NoteComposeSheet({required this.existing});
  @override
  State<_NoteComposeSheet> createState() => _NoteComposeSheetState();
}

class _NoteComposeSheetState extends State<_NoteComposeSheet> {
  late final TextEditingController _text;
  String _emoji = '';
  String _audience = 'followers';
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _text = TextEditingController(text: e?.text ?? '');
    _emoji = e?.emoji ?? '';
    _audience = e?.audience ?? 'followers';
    _text.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  int get _len => _text.text.runes.length;
  bool get _canShare => !_busy && (_text.text.trim().isNotEmpty || _emoji.isNotEmpty);

  void _applySuggestion(_Suggestion s) {
    _text.value = TextEditingValue(text: s.text, selection: TextSelection.collapsed(offset: s.text.length));
    setState(() => _emoji = s.emoji);
  }

  Future<void> _share() async {
    setState(() { _busy = true; _error = null; });
    try {
      final note = await MessageApiService.putMyNote(
        text: _text.text.trim(),
        emoji: _emoji,
        audience: _audience,
      );
      if (mounted) Navigator.pop(context, _ComposeResult.saved(note));
    } catch (e) {
      if (!mounted) return;
      setState(() { _busy = false; _error = e.toString(); });
    }
  }

  Future<void> _delete() async {
    setState(() { _busy = true; _error = null; });
    try {
      await MessageApiService.deleteMyNote();
      if (mounted) Navigator.pop(context, const _ComposeResult.deleted());
    } catch (e) {
      if (!mounted) return;
      setState(() { _busy = false; _error = e.toString(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final atLimit = _len >= _kNoteMaxLen;
    final isEdit = widget.existing != null;

    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40, height: 4,
                decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 12),
            Row(children: [
              TextButton(
                onPressed: _busy ? null : () => Navigator.pop(context),
                child: const Text('Cancel'),
              ),
              Expanded(
                child: Text(
                  isEdit ? 'Edit note' : 'New note',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: cs.onSurface),
                ),
              ),
              TextButton(
                onPressed: _canShare ? _share : null,
                child: _busy
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Share', style: TextStyle(fontWeight: FontWeight.w700)),
              ),
            ]),
            const SizedBox(height: 8),

            // text + emoji preview
            TextField(
              controller: _text,
              autofocus: true,
              enabled: !_busy,
              maxLines: 1,
              textInputAction: TextInputAction.done,
              inputFormatters: [
                FilteringTextInputFormatter.deny(RegExp(r'[\n\r]')),
                const _RuneLimitFormatter(_kNoteMaxLen),
              ],
              onSubmitted: (_) {
                if (_canShare) _share();
              },
              decoration: InputDecoration(
                hintText: 'Share a thought...',
                prefixIcon: _emoji.isEmpty
                    ? null
                    : Padding(
                        padding: const EdgeInsets.only(left: 12, right: 4),
                        child: Center(widthFactor: 1, child: Text(_emoji, style: const TextStyle(fontSize: 20))),
                      ),
                suffixIcon: _emoji.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.close_rounded, size: 18),
                        tooltip: 'Remove emoji',
                        onPressed: _busy ? null : () => setState(() => _emoji = ''),
                      ),
                counterText: '', // apna counter neeche
                filled: true,
                fillColor: AppThemeTokens.of(context).surface2,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
              ),
            ),
            Padding(
              padding: const EdgeInsets.only(top: 4, right: 4),
              child: Align(
                alignment: Alignment.centerRight,
                child: Text(
                  '$_len/$_kNoteMaxLen',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: atLimit ? FontWeight.w700 : FontWeight.normal,
                    color: atLimit ? cs.error : cs.onSurfaceVariant,
                  ),
                ),
              ),
            ),

            // emoji
            const SizedBox(height: 4),
            Text('Emoji', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final e in _kQuickEmojis)
                ChoiceChip(
                  label: Text(e, style: const TextStyle(fontSize: 18)),
                  selected: _emoji == e,
                  showCheckmark: false,
                  visualDensity: VisualDensity.compact,
                  onSelected: _busy ? null : (sel) => setState(() => _emoji = sel ? e : ''),
                ),
            ]),

            // suggestions
            const SizedBox(height: 14),
            Text('Suggestions', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
            const SizedBox(height: 6),
            Wrap(spacing: 8, runSpacing: 4, children: [
              for (final s in _kSuggestions)
                ActionChip(
                  label: Text('${s.text} ${s.emoji}', style: const TextStyle(fontSize: 12.5)),
                  backgroundColor: cs.surface,
                  side: BorderSide(color: cs.outlineVariant),
                  onPressed: _busy ? null : () => _applySuggestion(s),
                ),
            ]),

            // audience
            const SizedBox(height: 14),
            Text('Share with', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
            const SizedBox(height: 6),
            Wrap(spacing: 8, children: [
              ChoiceChip(
                avatar: const Icon(Icons.people_alt_outlined, size: 18),
                label: const Text('Followers'),
                selected: _audience == 'followers',
                onSelected: _busy ? null : (_) => setState(() => _audience = 'followers'),
              ),
              ChoiceChip(
                avatar: const Icon(Icons.star_rounded, size: 18, color: _kCloseFriendsGreen),
                label: const Text('Close friends'),
                selected: _audience == 'close_friends',
                onSelected: _busy ? null : (_) => setState(() => _audience = 'close_friends'),
              ),
            ]),
            const SizedBox(height: 10),
            Text(
              'Note 24 ghante tak dikhega, phir apne aap hat jaayega. Naya note purane ko replace kar deta hai.',
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
            ),

            if (_error != null) ...[
              const SizedBox(height: 10),
              Text(_error!, style: TextStyle(fontSize: 12.5, color: cs.error)),
            ],

            if (isEdit) ...[
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _busy ? null : _delete,
                  style: TextButton.styleFrom(foregroundColor: cs.error),
                  icon: const Icon(Icons.delete_outline_rounded, size: 18),
                  label: const Text('Delete note'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ======================================================================
// REPLY SHEET — note ka reply ek normal DM hai, jisme note ka quote hota hai
// ======================================================================
class _NoteReplySheet extends StatefulWidget {
  final UserNoteModel note;
  const _NoteReplySheet({required this.note});
  @override
  State<_NoteReplySheet> createState() => _NoteReplySheetState();
}

class _NoteReplySheetState extends State<_NoteReplySheet> {
  final _controller = TextEditingController();
  bool _sending = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _controller.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final reply = _controller.text.trim();
    if (reply.isEmpty || _sending) return;
    final note = widget.note;
    setState(() { _sending = true; _error = null; });
    try {
      final convo = await MessageApiService.startPrivateChat(note.userId);
      // Quote text me bhi hai (aaj ke chat bubble me bina kisi change ke
      // dikhta hai) aur `meta.note_reply` me structured bhi (future rich
      // bubble ke liye).
      await MessageApiService.sendMessageRest(
        convo.id,
        type: 'text',
        text: '↪ Replied to your note: “${note.label}”\n\n$reply',
        meta: {
          'note_reply': {
            'note_id': note.id,
            'author_id': note.userId,
            'text': note.text,
            'emoji': note.emoji,
          },
        },
      );
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      setState(() { _sending = false; _error = 'Reply nahi gaya: $e'; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    final note = widget.note;
    final photo = note.photoUrl;
    final hasPhoto = photo != null && photo.isNotEmpty;

    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 40, height: 4,
                decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 16),
            Row(children: [
              CircleAvatar(
                radius: 22,
                backgroundColor: tokens.surface2,
                backgroundImage: hasPhoto ? CachedNetworkImageProvider(photo) : null,
                child: !hasPhoto ? Icon(Icons.person_rounded, color: cs.onSurfaceVariant) : null,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(note.displayName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
                  Row(children: [
                    Text(timeago.format(note.postedAt), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                    if (note.isCloseFriends) ...[
                      const SizedBox(width: 6),
                      const Icon(Icons.star_rounded, size: 13, color: _kCloseFriendsGreen),
                      const SizedBox(width: 2),
                      const Text('Close friends',
                          style: TextStyle(fontSize: 12, color: _kCloseFriendsGreen, fontWeight: FontWeight.w600)),
                    ],
                  ]),
                ]),
              ),
            ]),
            const SizedBox(height: 14),
            // note ka quote
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: AppThemeTokens.of(context).surface2,
                borderRadius: BorderRadius.circular(14),
                border: Border(left: BorderSide(color: cs.primary, width: 4)),
              ),
              child: Text(
                note.label,
                style: TextStyle(fontSize: 16, height: 1.3, color: cs.onSurface),
              ),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _controller,
              autofocus: true,
              enabled: !_sending,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(
                hintText: 'Reply to ${note.displayName}...',
                filled: true,
                fillColor: AppThemeTokens.of(context).surface2,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                suffixIcon: _sending
                    ? const Padding(
                        padding: EdgeInsets.all(12),
                        child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                      )
                    : IconButton(
                        icon: Icon(Icons.send_rounded, color: _controller.text.trim().isEmpty ? cs.onSurfaceVariant : cs.primary),
                        tooltip: 'Send',
                        onPressed: _controller.text.trim().isEmpty ? null : _send,
                      ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              'Reply ek private message ki tarah ${note.displayName} ko jaayega.',
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!, style: TextStyle(fontSize: 12.5, color: cs.error)),
            ],
          ],
        ),
      ),
    );
  }
}
