// lib/support/screens/bug_report_screen.dart
// Bug report with optional screenshot. Device/app info is attached automatically.
// App version comes from --dart-define=APP_VERSION=1.2.3 (empty if not set).
// TODO(l10n): screen copy is English-only for now.

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../../widgets/ls_ui.dart';
import '../support_service.dart';

class BugReportScreen extends StatefulWidget {
  const BugReportScreen({super.key});
  @override
  State<BugReportScreen> createState() => _BugReportScreenState();
}

class _BugReportScreenState extends State<BugReportScreen> {
  final _title = TextEditingController();
  final _desc = TextEditingController();
  final _screen = TextEditingController();
  File? _shot;
  bool _busy = false;

  @override
  void dispose() {
    _title.dispose();
    _desc.dispose();
    _screen.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final f = await ImagePicker().pickImage(source: ImageSource.gallery, imageQuality: 80, maxWidth: 1600);
    if (f != null && mounted) setState(() => _shot = File(f.path));
  }

  Future<void> _submit() async {
    final title = _title.text.trim(), desc = _desc.text.trim();
    if (title.isEmpty || desc.isEmpty) {
      lsSnack(context, 'Please add a title and describe what happened.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await SupportService.reportBug(
        title: title,
        description: desc,
        screen: _screen.text.trim(),
        appVersion: const String.fromEnvironment('APP_VERSION'),
        platform: Platform.operatingSystem,
        deviceInfo: Platform.operatingSystemVersion,
        screenshot: _shot,
      );
      if (!mounted) return;
      lsSnack(context, 'Thanks! Your report has been sent.');
      Navigator.pop(context);
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        lsSnack(context, e.toString(), error: true);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Report a bug'),
      body: ListView(padding: const EdgeInsets.all(kLsPad), children: [
        TextField(
          controller: _title,
          maxLength: 120,
          decoration: const InputDecoration(labelText: 'What went wrong? (short)', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _desc,
          maxLength: 4000,
          minLines: 5,
          maxLines: 10,
          decoration: const InputDecoration(
              labelText: 'Steps to reproduce / what you expected', alignLabelWithHint: true, border: OutlineInputBorder()),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _screen,
          maxLength: 80,
          decoration: const InputDecoration(labelText: 'Which screen? (optional)', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 10),
        if (_shot != null)
          Stack(alignment: Alignment.topRight, children: [
            ClipRRect(borderRadius: BorderRadius.circular(12), child: Image.file(_shot!, height: 200, width: double.infinity, fit: BoxFit.cover)),
            IconButton(
              tooltip: 'Remove screenshot',
              onPressed: () => setState(() => _shot = null),
              icon: const Icon(Icons.cancel_rounded),
              color: Colors.white,
            ),
          ])
        else
          OutlinedButton.icon(
            onPressed: _pick,
            icon: const Icon(Icons.add_photo_alternate_outlined),
            label: const Text('Attach a screenshot'),
          ),
        const SizedBox(height: 8),
        Text('Your device type and app version are included automatically to help us fix it faster.',
            style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
        const SizedBox(height: 16),
        LsPrimaryButton(label: 'Send report', icon: Icons.send_rounded, loading: _busy, onPressed: _busy ? null : _submit),
      ]),
    );
  }
}
