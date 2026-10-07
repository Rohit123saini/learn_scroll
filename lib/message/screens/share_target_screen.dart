// message/screens/share_target_screen.dart
//
// Opened when the user shares something from ANOTHER app to LearnScroll.
// Shows what was shared (text / thumbnails / file names), lets the user pick
// one or more chats (+ optional caption), then sends it with the same
// upload + REST calls the normal chat screen uses.
//
// Sending model (WhatsApp-like): one message per shared item per chat. Files
// are uploaded ONCE and the resulting url is reused for every chosen chat.
// Caption (if any) rides on the first media message, or is sent as its own
// text message when only text/link was shared.

import 'dart:io';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../services/share_target_service.dart';
import '../../theme_service.dart';
import '../models/message_models.dart';
import '../services/message_api_service.dart';

class ShareTargetScreen extends StatefulWidget {
  final IncomingShare share;
  const ShareTargetScreen({super.key, required this.share});

  @override
  State<ShareTargetScreen> createState() => _ShareTargetScreenState();
}

class _ShareTargetScreenState extends State<ShareTargetScreen> {
  List<ConversationModel> _conversations = [];
  bool _loading = true;
  String? _error;
  String _search = '';
  final Set<String> _selected = {};
  bool _sending = false;
  double _progress = 0;
  late final TextEditingController _captionCtrl;

  static const _imageExt = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'bmp'};

  @override
  void initState() {
    super.initState();
    // Shared text/link is editable before sending, files get an optional caption.
    _captionCtrl = TextEditingController(text: widget.share.text ?? '');
    _load();
  }

  @override
  void dispose() {
    _captionCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final list = await MessageApiService.getConversations();
      if (!mounted) return;
      setState(() {
        _conversations = list;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  List<ConversationModel> get _filtered {
    final q = _search.trim().toLowerCase();
    if (q.isEmpty) return _conversations;
    return _conversations.where((c) => c.displayTitle.toLowerCase().contains(q)).toList();
  }

  bool _isImage(String path) => _imageExt.contains(path.split('.').last.toLowerCase());

  Future<void> _send() async {
    if (_selected.isEmpty || _sending) return;
    final l10n = AppLocalizations.of(context)!;
    final files = widget.share.filePaths;
    final caption = _captionCtrl.text.trim();
    setState(() {
      _sending = true;
      _progress = 0;
    });
    try {
      // 1) Upload every file once.
      final uploaded = <UploadedFileResult>[];
      for (var i = 0; i < files.length; i++) {
        final f = File(files[i]);
        if (!await f.exists()) {
          throw MessageApiException(l10n.shareTargetFileMissing, code: 'local_file_missing');
        }
        uploaded.add(await MessageApiService.uploadFile(f, onProgress: (v) {
          if (mounted) setState(() => _progress = (i + v) / files.length);
        }));
      }

      // 2) Send into each chosen chat; collect failures instead of aborting
      //    on the first one so a single blocked group doesn't drop the rest.
      var failed = 0;
      for (final conversationId in _selected) {
        try {
          if (uploaded.isEmpty) {
            if (caption.isNotEmpty) {
              await MessageApiService.sendMessageRest(conversationId, type: 'text', text: caption);
            }
          } else {
            for (var i = 0; i < uploaded.length; i++) {
              final u = uploaded[i];
              await MessageApiService.sendMessageRest(
                conversationId,
                type: u.fileType.isEmpty ? 'file' : u.fileType,
                text: (i == 0 && caption.isNotEmpty) ? caption : null,
                fileUrl: u.fileUrl,
                meta: {'file_name': u.fileName, 'size': u.fileSize, 'mime_type': u.mimeType},
              );
            }
          }
        } catch (_) {
          failed++;
        }
      }

      if (!mounted) return;
      if (failed == _selected.length) {
        setState(() => _sending = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.shareTargetSendFailed)));
        return;
      }
      Navigator.of(context).pop();
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(failed == 0
            ? l10n.shareTargetSent(_selected.length)
            : l10n.shareTargetSentPartial(_selected.length - failed, _selected.length)),
      ));
    } catch (e) {
      if (!mounted) return;
      setState(() => _sending = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.shareTargetSendFailed)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final hasFiles = widget.share.filePaths.isNotEmpty;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        iconTheme: IconThemeData(color: cs.onPrimary),
        title: Text(l10n.shareTargetTitle, style: TextStyle(color: cs.onPrimary, fontSize: 16.5)),
      ),
      body: Column(children: [
        if (hasFiles) _buildFilePreview(cs),
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
          child: TextField(
            controller: _captionCtrl,
            maxLines: 4,
            minLines: 1,
            decoration: InputDecoration(hintText: hasFiles ? l10n.shareTargetCaptionHint : l10n.shareTargetMessageHint),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 6),
          child: TextField(
            onChanged: (v) => setState(() => _search = v),
            decoration: InputDecoration(hintText: l10n.shareTargetSearchHint, prefixIcon: const Icon(Icons.search, size: 20)),
          ),
        ),
        Expanded(child: _buildList(l10n, cs)),
      ]),
      bottomNavigationBar: _selected.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: SizedBox(
                  width: double.infinity,
                  height: 46,
                  child: ElevatedButton.icon(
                    onPressed: _sending ? null : _send,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: cs.primary,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    icon: _sending
                        ? SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: cs.onPrimary, value: _progress > 0 && _progress < 1 ? _progress : null),
                          )
                        : Icon(Icons.send, size: 18, color: cs.onPrimary),
                    label: Text(
                      _sending ? l10n.shareTargetSending : l10n.shareTargetSendTo(_selected.length),
                      style: TextStyle(color: cs.onPrimary, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ),
            ),
    );
  }

  Widget _buildFilePreview(ColorScheme cs) {
    final files = widget.share.filePaths;
    return SizedBox(
      height: 84,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
        itemCount: files.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (_, i) {
          final path = files[i];
          return ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Container(
              width: 72,
              height: 72,
              color: AppThemeTokens.of(context).surface2,
              child: _isImage(path)
                  ? Image.file(File(path), fit: BoxFit.cover, errorBuilder: (_, __, ___) => const Icon(Icons.image_not_supported_outlined))
                  : Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                      Icon(Icons.insert_drive_file_outlined, color: cs.onSurfaceVariant),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        child: Text(path.split('/').last,
                            maxLines: 2, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center, style: const TextStyle(fontSize: 9.5)),
                      ),
                    ]),
            ),
          );
        },
      ),
    );
  }

  Widget _buildList(AppLocalizations l10n, ColorScheme cs) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(l10n.shareTargetLoadFailed, textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
            const SizedBox(height: 10),
            TextButton(onPressed: _load, child: Text(l10n.retry)),
          ]),
        ),
      );
    }
    final list = _filtered;
    if (list.isEmpty) {
      return Center(child: Text(l10n.shareTargetNoChats, style: TextStyle(color: cs.onSurfaceVariant)));
    }
    return ListView.builder(
      itemCount: list.length,
      itemBuilder: (context, index) {
        final c = list[index];
        final selected = _selected.contains(c.id);
        return ListTile(
          onTap: _sending
              ? null
              : () => setState(() {
                    selected ? _selected.remove(c.id) : _selected.add(c.id);
                  }),
          leading: CircleAvatar(
            radius: 21,
            backgroundColor: AppThemeTokens.of(context).surface2,
            backgroundImage: c.displayPhoto != null && c.displayPhoto!.isNotEmpty ? CachedNetworkImageProvider(c.displayPhoto!) : null,
            child: c.displayPhoto == null || c.displayPhoto!.isEmpty
                ? Icon(c.isGroup ? Icons.group : Icons.person, color: cs.onSurfaceVariant)
                : null,
          ),
          title: Text(c.displayTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: Icon(selected ? Icons.check_circle : Icons.radio_button_unchecked,
              color: selected ? cs.primary : cs.onSurfaceVariant),
        );
      },
    );
  }
}
