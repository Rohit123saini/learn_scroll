import 'dart:io';
import 'dart:ui' as ui;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:share_plus/share_plus.dart';

import '../l10n/app_localizations.dart';
import '../services/deep_link_service.dart';

// ============================================================
// P9-FE — "Share profile" sheet.
//
//   showShareProfileSheet(context, username: .., fullName: .., photoUrl: ..)
//
//   • a card (avatar, name, @username, QR of learnscroll://u/<username>)
//   • Share card  -> card rendered to a PNG (RepaintBoundary) + system share sheet
//   • Share link  -> system share with the link as text
//   • Copy link   -> clipboard
// ============================================================

Future<void> showShareProfileSheet(
  BuildContext context, {
  required String username,
  String fullName = '',
  String photoUrl = '',
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (_) => _ShareProfileSheet(username: username, fullName: fullName, photoUrl: photoUrl),
  );
}

class _ShareProfileSheet extends StatefulWidget {
  final String username;
  final String fullName;
  final String photoUrl;
  const _ShareProfileSheet({required this.username, required this.fullName, required this.photoUrl});

  @override
  State<_ShareProfileSheet> createState() => _ShareProfileSheetState();
}

class _ShareProfileSheetState extends State<_ShareProfileSheet> {
  final GlobalKey _cardKey = GlobalKey();
  bool _avatarReady = false;
  bool _busy = false;
  bool _didPrecache = false;

  String get _link => DeepLinkService.profileLink(widget.username);

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_didPrecache) return;
    _didPrecache = true;
    _precacheAvatar();
  }

  // The PNG can only contain the avatar if it has finished loading, so warm the cache
  // first and keep "Share card" disabled until then (6 s cap so a dead network can't
  // block sharing forever — the card then just shows the initial letter).
  Future<void> _precacheAvatar() async {
    if (widget.photoUrl.isEmpty) {
      setState(() => _avatarReady = true);
      return;
    }
    try {
      await precacheImage(CachedNetworkImageProvider(widget.photoUrl), context)
          .timeout(const Duration(seconds: 6));
    } catch (_) {}
    if (mounted) setState(() => _avatarReady = true);
  }

  Rect? _origin() {
    // iPad needs an anchor rect for the share popover.
    final box = context.findRenderObject() as RenderBox?;
    if (box == null || !box.hasSize) return null;
    return box.localToGlobal(Offset.zero) & box.size;
  }

  String _message() => AppLocalizations.of(context)!.shareProfileMessage(widget.username, _link);

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: _link));
    if (!mounted) return;
    HapticFeedback.selectionClick();
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Link copied')));
  }

  Future<void> _shareLink() async {
    await SharePlus.instance.share(ShareParams(text: _message(), sharePositionOrigin: _origin()));
  }

  Future<File?> _renderCard() async {
    final ctx = _cardKey.currentContext;
    if (ctx == null) return null;
    final boundary = ctx.findRenderObject() as RenderRepaintBoundary;
    final ui.Image image = await boundary.toImage(pixelRatio: 3.0);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    image.dispose();
    if (bytes == null) return null;
    final dir = await getTemporaryDirectory();
    final safe = widget.username.replaceAll(RegExp(r'[^A-Za-z0-9_-]'), '_');
    final file = File('${dir.path}/learnscroll_${safe}_profile.png');
    await file.writeAsBytes(bytes.buffer.asUint8List(), flush: true);
    return file;
  }

  Future<void> _shareCard() async {
    if (_busy) return;
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final file = await _renderCard();
      if (file == null) throw Exception('render failed');
      await SharePlus.instance.share(ShareParams(
        files: [XFile(file.path, mimeType: 'image/png')],
        text: _message(),
        sharePositionOrigin: _origin(),
      ));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text("Couldn't create the card. Try again.")));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(l10n.shareProfileButton, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
            const SizedBox(height: 14),
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 320),
                child: RepaintBoundary(key: _cardKey, child: _card(context)),
              ),
            ),
            const SizedBox(height: 18),
            Row(children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _copy,
                  icon: const Icon(Icons.link_rounded, size: 18),
                  label: const Text('Copy link'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _shareLink,
                  icon: const Icon(Icons.ios_share_rounded, size: 18),
                  label: const Text('Share link'),
                ),
              ),
            ]),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: (_avatarReady && !_busy) ? _shareCard : null,
                icon: _busy
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.image_outlined, size: 18),
                label: const Text('Share card image'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // The card is painted with FIXED colours (not theme surfaces) so the PNG looks the same
  // in light/dark mode — and the QR is always dark-on-white, which is what scanners need.
  Widget _card(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final name = widget.fullName.trim().isEmpty ? widget.username : widget.fullName.trim();
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 28, 24, 22),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [cs.primary, Color.lerp(cs.primary, Colors.black, 0.4)!],
        ),
      ),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          padding: const EdgeInsets.all(3),
          decoration: const BoxDecoration(color: Colors.white, shape: BoxShape.circle),
          child: CircleAvatar(
            radius: 38,
            backgroundColor: const Color(0xFFE6E6EC),
            backgroundImage: widget.photoUrl.isEmpty ? null : CachedNetworkImageProvider(widget.photoUrl),
            child: widget.photoUrl.isEmpty
                ? Text(widget.username.isEmpty ? '?' : widget.username[0].toUpperCase(),
                    style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, color: Color(0xFF555566)))
                : null,
          ),
        ),
        const SizedBox(height: 12),
        Text(name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w800, color: Colors.white)),
        const SizedBox(height: 2),
        Text('@${widget.username}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 13.5, color: Colors.white70)),
        const SizedBox(height: 18),
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(18)),
          child: QrImageView(
            data: _link,
            version: QrVersions.auto,
            size: 180,
            backgroundColor: Colors.white,
            errorCorrectionLevel: QrErrorCorrectLevel.M,
          ),
        ),
        const SizedBox(height: 14),
        const Text('Scan to open on LearnScroll',
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Colors.white70)),
      ]),
    );
  }
}
