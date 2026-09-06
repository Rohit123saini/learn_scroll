// lib/liveclass/utils/liveclass_upload_limits.dart
//
// FIX (production readiness audit — file upload size/type enforcement).
// Client-side pre-upload validation, mirroring the backend's own
// MaxFileSizeValidator / FileExtensionValidator caps (models.py) exactly,
// so a too-big or wrong-type file is rejected instantly and locally —
// before a single byte goes over the network — instead of the previous
// behavior of accepting whatever the OS file picker returned and letting
// the user discover the backend's real limit only after a slow upload
// (or, for a truly huge file, a client-side stall/crash before the
// request even finishes).
//
// Kept in ONE shared file rather than duplicated inline per screen — same
// lesson as `_fmtRelative` (see architecture doc §11 item 8): three
// separate copies of size/extension-checking logic (Cover Image,
// Materials, Assignments) would drift out of sync with each other and
// with the backend just as easily as three copies of a date formatter did.
//
// ⚠️ Mirrors models.py's validators as read at audit time:
//   Classroom.cover_image   -> MaxFileSizeValidator(5)   (ImageField, Pillow-verified)
//   Material.file           -> MaxFileSizeValidator(100) + DOCUMENT_MEDIA_EXTENSIONS
//   Assignment.attachment   -> MaxFileSizeValidator(50)  + DOCUMENT_MEDIA_EXTENSIONS
//   Submission.file         -> MaxFileSizeValidator(50)  + DOCUMENT_MEDIA_EXTENSIONS
// If a future backend change adjusts any of these caps, update here too —
// in this one place, not per-screen.

import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:file_selector/file_selector.dart';

class LiveClassUploadLimits {
  LiveClassUploadLimits._();

  static const coverImageMaxMB = 5;
  static const coverImageExtensions = ['jpg', 'jpeg', 'png', 'webp'];

  static const materialMaxMB = 100;
  static const assignmentAttachmentMaxMB = 50;
  static const submissionMaxMB = 50;

  /// DOCUMENT_MEDIA_EXTENSIONS from models.py — shared safelist for every
  /// plain (non-image) upload above. Safelist, not a blocklist, so it
  /// matches the backend's own reasoning: a brand-new dangerous extension
  /// can't slip through just because nobody thought to blocklist it.
  static const documentExtensions = [
    'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx',
    'png', 'jpg', 'jpeg', 'gif', 'webp',
    'mp4', 'mov', 'webm',
    'zip',
  ];

  static String _extensionOf(String fileName) =>
      fileName.contains('.') ? fileName.split('.').last.toLowerCase() : '';

  /// Core check — returns a user-facing error message, or null if the file
  /// is within [maxMB] and its extension is in [allowedExtensions].
  static String? check({
    required int sizeBytes,
    required String fileName,
    required int maxMB,
    required List<String> allowedExtensions,
  }) {
    final ext = _extensionOf(fileName);
    if (ext.isEmpty || !allowedExtensions.contains(ext)) {
      return 'That file type isn\'t supported. Allowed: ${allowedExtensions.join(', ')}.';
    }
    final maxBytes = maxMB * 1024 * 1024;
    if (sizeBytes > maxBytes) {
      final mb = (sizeBytes / (1024 * 1024)).toStringAsFixed(1);
      return 'File is ${mb}MB — max allowed is ${maxMB}MB.';
    }
    return null;
  }

  /// For file_selector's `XFile` (used by the openFile()-based pickers —
  /// cover image, materials, assignment submission). XFile doesn't carry
  /// its size directly, so this reads it off disk.
  static Future<String?> checkXFile(
    XFile file, {
    required int maxMB,
    required List<String> allowedExtensions,
  }) async {
    final size = await File(file.path).length();
    return check(sizeBytes: size, fileName: file.name, maxMB: maxMB, allowedExtensions: allowedExtensions);
  }

  /// For file_picker's `PlatformFile` (used by the FilePicker.platform
  /// .pickFiles()-based pickers). PlatformFile already carries its size,
  /// so no disk I/O needed.
  static String? checkPlatformFile(
    PlatformFile file, {
    required int maxMB,
    required List<String> allowedExtensions,
  }) {
    return check(sizeBytes: file.size, fileName: file.name, maxMB: maxMB, allowedExtensions: allowedExtensions);
  }
}