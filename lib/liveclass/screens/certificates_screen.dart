// lib/liveclass/screens/certificates_screen.dart
//
// Screen 21 — Certificates (see LIVECLASS_SCREEN_ARCHITECTURE.md §21).
//
// Two modes, driven by [canIssue]:
//   - Teacher/staff (from Classroom Detail's manage sheet): sees every
//     certificate issued for the classroom (`GET certificates/?classroom=`)
//     plus an "Issue Certificate" action (`POST certificates/`, teacher/
//     co-teacher/moderator only — student id + optional file).
//   - Student (own list, [classroomId] omitted): `GET certificates/` scoped
//     to the caller's own certificates by the backend, read-only.
//
// Now on the shared LiveClass design system (liveclass_theme.dart) — local
// color/date constants and the hand-rolled loading/error blocks were
// replaced with the shared ones so this matches Holidays/Coin Wallet/etc.

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:file_selector/file_selector.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../../services/auth_service.dart';
import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// ===========================================================================
// SCREEN
// ===========================================================================
class CertificatesScreen extends StatefulWidget {
  /// Omit for "My Certificates" (own list, any user). Pass it (with
  /// [canIssue]) for a classroom's manage panel.
  final int? classroomId;
  final String classroomTitle;
  final bool canIssue;
  const CertificatesScreen({super.key, this.classroomId, this.classroomTitle = '', this.canIssue = false});

  @override
  State<CertificatesScreen> createState() => _CertificatesScreenState();
}

class _CertificatesScreenState extends State<CertificatesScreen> {
  List<Certificate> _certificates = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.certificates.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _certificates = res.results..sort((a, b) => b.issuedAt.compareTo(a.issuedAt));
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load certificates.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openIssueSheet() async {
    if (widget.classroomId == null) return;
    // FIX (memory leak): this controller used to be created here and never
    // disposed — every open+close of this sheet leaked one
    // TextEditingController (and its internal listeners) for the lifetime
    // of the app. Wrapped in try/finally so it's always released once the
    // sheet closes, however it closes (submitted, cancelled, or dismissed).
    final studentIdCtrl = TextEditingController();
    try {
      return await _showIssueSheet(studentIdCtrl);
    } finally {
      studentIdCtrl.dispose();
    }
  }

  Future<void> _showIssueSheet(TextEditingController studentIdCtrl) async {
    XFile? pickedFile;
    bool submitting = false;

    final issued = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          Future<void> pickFile() async {
            try {
              final file = await openFile(acceptedTypeGroups: [
                const XTypeGroup(label: 'certificate', extensions: ['pdf', 'png', 'jpg', 'jpeg'])
              ]);
              if (file != null) setSheetState(() => pickedFile = file);
            } catch (_) {
              _snack('Could not select file.');
            }
          }

          Future<void> submit() async {
            final studentId = int.tryParse(studentIdCtrl.text.trim());
            if (studentId == null) {
              _snack('Enter a valid numeric Student ID.');
              return;
            }
            setSheetState(() => submitting = true);
            try {
              await LiveClassApi.certificates.issue(
                classroomId: widget.classroomId!,
                studentId: studentId,
                certificateFilePath: pickedFile?.path,
              );
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on LiveClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Issue failed — please try again.');
            }
          }

          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 16),
                    decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const Text('Issue Certificate', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 4),
                Text("Get the student's Student/User ID from their app profile.", style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                const SizedBox(height: 14),
                TextField(
                  controller: studentIdCtrl,
                  keyboardType: TextInputType.number,
                  decoration: liveClassInputDecoration('Student ID'),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: pickFile,
                  style: OutlinedButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                    side: BorderSide(color: Colors.grey.shade300),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                  ),
                  icon: Icon(Icons.attach_file_rounded, size: 18, color: pickedFile == null ? Colors.grey.shade600 : LiveClassColors.navy),
                  label: Text(
                    pickedFile != null ? pickedFile!.name : 'Attach certificate file (optional)',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: pickedFile == null ? Colors.grey.shade600 : LiveClassColors.navy, fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: LiveClassColors.navy,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    onPressed: submitting ? null : submit,
                    child: submitting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Issue Certificate'),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (issued == true) {
      _snack('Certificate issued.');
      _load();
    }
  }

  Future<void> _openFile(Certificate c) async {
    if (c.certificateFile == null || c.certificateFile!.isEmpty) return;
    _snack('Downloading…');
    try {
      final token = await AuthService.getToken();
      final dir = await getTemporaryDirectory();
      final savePath = '${dir.path}/certificate_${c.certificateId}.pdf';
      final file = File(savePath);
      if (!await file.exists()) {
        // FIX (production readiness audit — corrupt-cache bug): Dio's
        // download() streams straight to `savePath`. If it fails partway
        // (dropped connection, server error mid-stream, app backgrounded
        // and killed), the outer catch below used to swallow the error and
        // leave a partial/corrupt file sitting at `savePath` — the next
        // `exists()` check here then returned true forever, so this method
        // never tried downloading again and just handed OpenFilex a broken
        // file every time. The certificate was then permanently
        // unopenable in-app (short of clearing app storage) even though
        // it's perfectly fine on the server. Now a failed download deletes
        // its own partial file before the error reaches the outer catch,
        // so the next tap retries a fresh download instead of replaying
        // the same corrupt one.
        try {
          await Dio().download(c.certificateFile!, savePath,
              options: Options(headers: token != null && token.isNotEmpty ? {'Authorization': 'Bearer $token'} : {}));
        } catch (_) {
          if (await file.exists()) await file.delete();
          rethrow;
        }
      }
      await OpenFilex.open(savePath);
    } catch (_) {
      _snack('Could not open certificate file.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.classroomTitle.isNotEmpty ? 'Certificates — ${widget.classroomTitle}' : 'Certificates';
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(title),
      floatingActionButton: widget.canIssue
          ? FloatingActionButton.extended(
              backgroundColor: LiveClassColors.navy,
              onPressed: _openIssueSheet,
              icon: const Icon(Icons.workspace_premium_rounded),
              label: const Text('Issue'),
            )
          : null,
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _certificates.isEmpty
                    ? LiveClassEmptyState(
                        icon: Icons.workspace_premium_outlined,
                        title: 'No certificates issued yet.',
                        subtitle: widget.canIssue ? 'Tap "Issue" to award the first one.' : null,
                      )
                    : ListView.builder(
                        padding: EdgeInsets.fromLTRB(16, 14, 16, widget.canIssue ? 90 : 24),
                        itemCount: _certificates.length,
                        itemBuilder: (_, i) => _certCard(_certificates[i]),
                      ),
      ),
    );
  }

  Widget _certCard(Certificate c) {
    return LiveClassCard(
      child: Row(
        children: [
          const LiveClassIconBadge(icon: Icons.workspace_premium_rounded, size: 44),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  widget.canIssue
                      ? (c.student.fullName.isNotEmpty ? c.student.fullName : c.student.username)
                      : (c.classroomTitle.isNotEmpty ? c.classroomTitle : 'Classroom'),
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text('ID: ${c.certificateId}', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                const SizedBox(height: 2),
                Text('Issued ${liveClassFmtDate(c.issuedAt)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
              ],
            ),
          ),
          if (c.certificateFile != null && c.certificateFile!.isNotEmpty)
            IconButton(
              onPressed: () => _openFile(c),
              icon: const Icon(Icons.download_rounded, color: LiveClassColors.navy),
              tooltip: 'Download',
            ),
        ],
      ),
    );
  }
}