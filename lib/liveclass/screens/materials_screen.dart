// lib/liveclass/screens/materials_screen.dart
//
// Screen 11 — Materials (see LIVECLASS_SCREEN_ARCHITECTURE.md §11).
// Reached from Classroom Detail's manage sheet (teacher/staff) or the
// Materials tab shortcut (student, read-only).
//
// API: `materials/?classroom=` GET, `POST materials/` (multipart file OR
// external_link), `PATCH materials/{id}/`, `DELETE materials/{id}/` —
// upload/edit/delete restricted server-side to teacher/co-teacher/
// moderator (ClassMaterialViewSet.perform_update/destroy).
//
// Downloading: files stream through Dio with the auth token attached (same
// pattern as Certificates), external links are copied to the clipboard
// since there's no in-app browser wired up yet.
//
// Now on the shared LiveClass design system (liveclass_theme.dart).
//
// NOTE: Flutter's material.dart exports its own `MaterialType` enum (used
// internally by the Material widget's `type:` property — canvas/card/
// circle/button/transparency). This file's `MaterialType` (pdf/ppt/doc/
// image/video/link) comes from liveclass_models.dart. The two collide, so
// Flutter's version is hidden below — it isn't used anywhere in this file.

import 'dart:io';
import 'package:flutter/material.dart' hide MaterialType;
import 'package:flutter/services.dart';
import 'package:file_selector/file_selector.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../../services/auth_service.dart';
import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

Widget _dropdown({required String value, required Map<String, String> items, required ValueChanged<String?> onChanged}) {
  return DropdownButtonFormField<String>(
    value: value,
    decoration: liveClassInputDecoration(''),
    items: items.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
    onChanged: onChanged,
  );
}

IconData _iconFor(String type) {
  switch (type) {
    case MaterialType.pdf:
      return Icons.picture_as_pdf_rounded;
    case MaterialType.ppt:
      return Icons.slideshow_rounded;
    case MaterialType.doc:
      return Icons.description_rounded;
    case MaterialType.image:
      return Icons.image_rounded;
    case MaterialType.video:
      return Icons.videocam_rounded;
    default:
      return Icons.link_rounded;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class MaterialsScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  final bool canManage;
  const MaterialsScreen({super.key, required this.classroomId, this.classroomTitle = '', this.canManage = false});

  @override
  State<MaterialsScreen> createState() => _MaterialsScreenState();
}

class _MaterialsScreenState extends State<MaterialsScreen> {
  List<ClassMaterial> _items = [];
  bool _loading = true;
  String? _error;
  bool _uploading = false;

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
      final res = await LiveClassApi.materials.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _items = res.results..sort((a, b) => b.uploadedAt.compareTo(a.uploadedAt));
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load materials.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openMaterial(ClassMaterial m) async {
    if (m.file != null && m.file!.isNotEmpty) {
      await _downloadAndOpen(m.file!, m.title);
    } else if (m.externalLink.isNotEmpty) {
      await Clipboard.setData(ClipboardData(text: m.externalLink));
      _snack('Link copied — paste it in your browser.');
    }
  }

  Future<void> _downloadAndOpen(String url, String name) async {
    _snack('Downloading…');
    try {
      final token = await AuthService.getToken();
      final dir = await getTemporaryDirectory();
      final savePath = '${dir.path}/${name.replaceAll(' ', '_')}';
      final file = File(savePath);
      if (!await file.exists()) {
        // FIX (production readiness audit — corrupt-cache bug, same as
        // certificates_screen.dart/submission_grading_screen.dart): a
        // download that fails partway used to leave a partial/corrupt file
        // at `savePath`, which the `exists()` check above then treated as
        // already-cached forever — the material became permanently
        // unopenable in-app. A failed download now deletes its own partial
        // file so the next tap retries instead of replaying the corrupt one.
        try {
          await Dio().download(url, savePath,
              options: Options(headers: token != null && token.isNotEmpty ? {'Authorization': 'Bearer $token'} : {}));
        } catch (_) {
          if (await file.exists()) await file.delete();
          rethrow;
        }
      }
      await OpenFilex.open(savePath);
    } catch (_) {
      _snack('Could not open file.');
    }
  }

  Future<void> _openUploadSheet() async {
    // FIX (memory leak): both controllers were created here and never
    // disposed. Their values are read again after the sheet closes (for
    // the upload call below), so disposal happens at the very end of this
    // method via try/finally — covers the sheet-cancelled path too, since
    // an early `return` still runs the finally block.
    final titleCtrl = TextEditingController();
    final linkCtrl = TextEditingController();
    try {
      await _runUploadFlow(titleCtrl, linkCtrl);
    } finally {
      titleCtrl.dispose();
      linkCtrl.dispose();
    }
  }

  Future<void> _runUploadFlow(TextEditingController titleCtrl, TextEditingController linkCtrl) async {
    String type = MaterialType.pdf;
    XFile? picked;

    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
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
                const Text('Upload Material', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 14),
                TextField(controller: titleCtrl, decoration: liveClassInputDecoration('Title')),
                const SizedBox(height: 12),
                _dropdown(
                  value: type,
                  items: const {
                    MaterialType.pdf: 'PDF',
                    MaterialType.ppt: 'Presentation',
                    MaterialType.doc: 'Document',
                    MaterialType.image: 'Image',
                    MaterialType.video: 'Video',
                    MaterialType.link: 'External Link',
                  },
                  onChanged: (v) => setSheetState(() => type = v!),
                ),
                const SizedBox(height: 12),
                if (type == MaterialType.link)
                  TextField(controller: linkCtrl, decoration: liveClassInputDecoration('https://…'))
                else
                  OutlinedButton.icon(
                    onPressed: () async {
                      final f = await openFile();
                      if (f != null) setSheetState(() => picked = f);
                    },
                    style: OutlinedButton.styleFrom(
                      alignment: Alignment.centerLeft,
                      padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                      side: BorderSide(color: Colors.grey.shade300),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    icon: Icon(Icons.attach_file_rounded, size: 18, color: picked == null ? Colors.grey.shade600 : LiveClassColors.navy),
                    label: Text(
                      picked == null ? 'Choose file' : picked!.name,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: picked == null ? Colors.grey.shade600 : LiveClassColors.navy, fontWeight: FontWeight.w600),
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
                    onPressed: () {
                      if (titleCtrl.text.trim().isEmpty) {
                        _snack('Enter a title.');
                        return;
                      }
                      if (type != MaterialType.link && picked == null) {
                        _snack('Select a file.');
                        return;
                      }
                      if (type == MaterialType.link && linkCtrl.text.trim().isEmpty) {
                        _snack('Enter a link.');
                        return;
                      }
                      Navigator.pop(ctx, true);
                    },
                    child: const Text('Upload'),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );

    if (ok != true) return;
    setState(() => _uploading = true);
    try {
      await LiveClassApi.materials.upload(
        classroomId: widget.classroomId,
        title: titleCtrl.text.trim(),
        materialType: type,
        filePath: picked?.path,
        externalLink: linkCtrl.text.trim(),
      );
      _snack('Material uploaded.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Upload failed.');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  Future<void> _confirmDelete(ClassMaterial m) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Remove Material?'),
        content: Text('"${m.title}" will be permanently removed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: LiveClassColors.danger),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final previous = List<ClassMaterial>.from(_items);
    setState(() => _items.removeWhere((x) => x.id == m.id));
    try {
      await LiveClassApi.materials.delete(m.id);
    } catch (e) {
      if (!mounted) return;
      setState(() => _items = previous);
      _snack(e is LiveClassApiException ? e.message : 'Delete failed.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? 'Materials — ${widget.classroomTitle}' : 'Materials'),
      floatingActionButton: widget.canManage
          ? FloatingActionButton.extended(
              backgroundColor: LiveClassColors.navy,
              onPressed: _uploading ? null : _openUploadSheet,
              icon: _uploading
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.upload_rounded),
              label: const Text('Upload'),
            )
          : null,
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _items.isEmpty
                    ? const LiveClassEmptyState(icon: Icons.folder_open_outlined, title: 'No materials yet.')
                    : ListView.builder(
                        padding: EdgeInsets.fromLTRB(16, 14, 16, widget.canManage ? 90 : 24),
                        itemCount: _items.length,
                        itemBuilder: (_, i) => _materialCard(_items[i]),
                      ),
      ),
    );
  }

  Widget _materialCard(ClassMaterial m) {
    return LiveClassCard(
      padding: EdgeInsets.zero,
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
        leading: LiveClassIconBadge(icon: _iconFor(m.materialType), size: 40),
        title: Text(m.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5), maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text('${m.uploadedBy.fullName} · ${liveClassFmtDate(m.uploadedAt)}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
        trailing: widget.canManage
            ? IconButton(icon: const Icon(Icons.delete_outline_rounded, color: LiveClassColors.danger), onPressed: () => _confirmDelete(m))
            : Icon(Icons.chevron_right_rounded, color: Colors.grey.shade400),
        onTap: () => _openMaterial(m),
      ),
    );
  }
}