import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:permission_handler/permission_handler.dart';

import '../l10n/app_localizations.dart';
import '../services/deep_link_service.dart';

// ============================================================
// TASK 11.1 — QR scanner.
//
//   // Normal use (profile QR / parent QR / any LearnScroll link): scan -> close -> open it.
//   Navigator.push(context, MaterialPageRoute(builder: (_) => const ScanQrScreen()));
//
//   // Parent screens that only want the text back (to pre-fill a code field):
//   final raw = await Navigator.push<String>(
//       context, MaterialPageRoute(builder: (_) => const ScanQrScreen(returnRaw: true)));
//
// • Camera scan + "pick from gallery" (decodes a saved/screenshotted QR).
// • Payload routing is DeepLinkService.handlePayload — the exact parser used for tapped links,
//   so a QR and a link always behave the same (incl. the login rules).
// • Non-LearnScroll QR codes are rejected with a message; scanning keeps going.
// • Camera permission is requested here so a denial shows a clear screen (+ "Open settings").
// ============================================================

class ScanQrScreen extends StatefulWidget {
  /// true  -> pop with the raw QR text; caller decides what to do (must be a LearnScroll
  ///          payload — others are still rejected here).
  /// false -> route through [DeepLinkService.handlePayload] after closing.
  final bool returnRaw;
  const ScanQrScreen({super.key, this.returnRaw = false});

  @override
  State<ScanQrScreen> createState() => _ScanQrScreenState();
}

enum _Perm { checking, granted, denied, permanentlyDenied }

class _ScanQrScreenState extends State<ScanQrScreen> with WidgetsBindingObserver {
  final MobileScannerController _controller = MobileScannerController(formats: const [BarcodeFormat.qrCode]);
  _Perm _perm = _Perm.checking;
  bool _handled = false; // a valid code was found; ignore further frames
  bool _torchOn = false;
  bool _busy = false; // gallery decode in progress
  DateTime _lastRejectAt = DateTime.fromMillisecondsSinceEpoch(0);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _ensurePermission();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller.dispose();
    super.dispose();
  }

  // Coming back from system settings after granting the permission.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _perm != _Perm.granted) _ensurePermission();
  }

  Future<void> _ensurePermission() async {
    var status = await Permission.camera.status;
    if (!status.isGranted) status = await Permission.camera.request();
    if (!mounted) return;
    setState(() {
      if (status.isGranted) {
        _perm = _Perm.granted;
      } else if (status.isPermanentlyDenied || status.isRestricted) {
        _perm = _Perm.permanentlyDenied;
      } else {
        _perm = _Perm.denied;
      }
    });
  }

  void _toast(String msg) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(msg)));
  }

  /// Returns true when the text was a valid LearnScroll payload and we're closing.
  Future<bool> _accept(String raw) async {
    if (_handled) return true;
    final l10n = AppLocalizations.of(context)!;
    if (DeepLinkService.parsePayload(raw) == null) {
      // Camera fires many frames/second — don't spam the snackbar.
      final now = DateTime.now();
      if (now.difference(_lastRejectAt) > const Duration(seconds: 2)) {
        _lastRejectAt = now;
        _toast(l10n.scanQrUnsupported);
      }
      return false;
    }
    _handled = true;
    final nav = Navigator.of(context);
    if (widget.returnRaw) {
      nav.pop(raw.trim());
    } else {
      nav.pop();
      await DeepLinkService.instance.handlePayload(raw);
    }
    return true;
  }

  void _onDetect(BarcodeCapture capture) {
    if (_handled) return;
    for (final b in capture.barcodes) {
      final raw = b.rawValue;
      if (raw != null && raw.isNotEmpty) {
        _accept(raw);
        break;
      }
    }
  }

  Future<void> _pickFromGallery() async {
    if (_busy || _handled) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      final picked = await ImagePicker().pickImage(source: ImageSource.gallery);
      if (picked == null) return;
      final capture = await _controller.analyzeImage(picked.path);
      if (!mounted) return;
      final codes = capture?.barcodes.map((b) => b.rawValue).whereType<String>().where((s) => s.isNotEmpty).toList() ??
          const <String>[];
      if (codes.isEmpty) {
        _toast(l10n.scanQrNoCodeInImage);
        return;
      }
      for (final c in codes) {
        if (await _accept(c)) return;
      }
    } catch (_) {
      if (mounted) _toast(l10n.scanQrNoCodeInImage);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggleTorch() async {
    try {
      await _controller.toggleTorch();
      if (mounted) setState(() => _torchOn = !_torchOn);
    } catch (_) {/* device without a torch */}
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 0,
        title: Text(l10n.scanQrTitle),
        actions: [
          if (_perm == _Perm.granted)
            IconButton(
              tooltip: l10n.scanQrTorch,
              icon: Icon(_torchOn ? Icons.flash_on_rounded : Icons.flash_off_rounded),
              onPressed: _toggleTorch,
            ),
        ],
      ),
      body: _body(l10n),
    );
  }

  Widget _body(AppLocalizations l10n) {
    switch (_perm) {
      case _Perm.checking:
        return const Center(child: CircularProgressIndicator());
      case _Perm.denied:
      case _Perm.permanentlyDenied:
        return _permissionDenied(l10n);
      case _Perm.granted:
        return Stack(fit: StackFit.expand, children: [
          MobileScanner(controller: _controller, onDetect: _onDetect),
          // Viewfinder frame (purely visual — the whole preview is scanned).
          Center(
            child: Container(
              width: 250,
              height: 250,
              decoration: BoxDecoration(
                border: Border.all(color: Colors.white, width: 3),
                borderRadius: BorderRadius.circular(24),
              ),
            ),
          ),
          Positioned(
            left: 24,
            right: 24,
            bottom: 28,
            child: SafeArea(
              top: false,
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text(
                  l10n.scanQrHint,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 14),
                FilledButton.tonalIcon(
                  onPressed: _busy ? null : _pickFromGallery,
                  icon: _busy
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.photo_library_outlined, size: 18),
                  label: Text(l10n.scanQrGallery),
                ),
              ]),
            ),
          ),
        ]);
    }
  }

  Widget _permissionDenied(AppLocalizations l10n) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.no_photography_outlined, color: Colors.white70, size: 56),
          const SizedBox(height: 14),
          Text(l10n.scanQrPermissionDenied,
              textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 15)),
          const SizedBox(height: 20),
          if (_perm == _Perm.permanentlyDenied)
            FilledButton(onPressed: openAppSettings, child: Text(l10n.scanQrOpenSettings))
          else
            FilledButton(onPressed: _ensurePermission, child: Text(l10n.scanQrGrantAccess)),
          const SizedBox(height: 10),
          // The gallery path needs no camera permission, so offer it as the fallback.
          TextButton.icon(
            onPressed: _busy ? null : _pickFromGallery,
            icon: const Icon(Icons.photo_library_outlined, size: 18),
            label: Text(l10n.scanQrGallery),
          ),
        ]),
      ),
    );
  }
}
