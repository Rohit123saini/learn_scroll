// message/services/media_download_service.dart
//
// SETUP:
//   pubspec.yaml me add karo:
//     dio: ^5.7.0
//     gal: ^2.3.0                 (image/video ko gallery me save karne ke liye)
//     path_provider: ^2.1.4       (file/pdf/audio ke liye)
//     open_filex: ^4.5.0          (downloaded file ko open karne ke liye)
//     permission_handler: ^11.3.1 (already tere project me hai)
//     device_info_plus: ^10.1.0   (🔥 NAYA — Android version ke hisaab se sahi storage permission maangne ke liye)
//
//   Android — android/app/src/main/AndroidManifest.xml me:
//   (poora manifest snippet neeche chat me diya hai, "Downloads/AppName"
//   wale public folder access ke liye MANAGE_EXTERNAL_STORAGE zaroori hai,
//   bilkul WhatsApp jis wajah se ye permission maangta hai)

import 'dart:io';
import 'package:dio/dio.dart';
import 'package:gal/gal.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:device_info_plus/device_info_plus.dart';

enum DownloadKind { image, video, other }

class MediaDownloadService {
  static final Dio _dio = Dio();

  /// Isi naam ka folder "Download/" ke andar banega — Files app / kisi
  /// bhi file manager se seedha dikhega, jaise WhatsApp apna "WhatsApp"
  /// folder banata hai.
  static const _appFolderName = "LearnScroll";

  // ------------------------------------------------------------------
  // 🔥 NAYA: STORAGE PERMISSION
  // Android 11+ (API 30+) me kisi arbitrary file (pdf/doc/audio) ko
  // seedhe public "Download" folder me likhne ke liye "All files access"
  // (MANAGE_EXTERNAL_STORAGE) chahiye — WhatsApp bhi isi liye ye
  // permission maangta hai. Purane Android (10 aur niche) pe normal
  // storage permission kaafi hai.
  // ------------------------------------------------------------------
  static Future<bool> _ensureStorageAccess() async {
    if (!Platform.isAndroid) return true;
    final sdkInt = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
    if (sdkInt >= 30) {
      var status = await Permission.manageExternalStorage.status;
      if (!status.isGranted) status = await Permission.manageExternalStorage.request();
      return status.isGranted;
    }
    var status = await Permission.storage.status;
    if (!status.isGranted) status = await Permission.storage.request();
    return status.isGranted;
  }

  static Future<Directory> _publicDownloadsDir() async {
    final dir = Directory("/storage/emulated/0/Download/$_appFolderName");
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  /// Agar public storage access na mile (permission deny, ya koi OEM
  /// restriction), to app ke apne sandboxed folder pe fallback — taaki
  /// download kabhi fail na ho, sirf "Files" app se dikhna band ho jaaye.
  static Future<Directory> _fallbackDownloadsDir() async {
    final docsDir = await getApplicationDocumentsDirectory();
    final downloadsDir = Directory("${docsDir.path}/Downloads");
    if (!await downloadsDir.exists()) await downloadsDir.create(recursive: true);
    return downloadsDir;
  }

  /// Non-media files (pdf/audio/doc/presentation) jahan save hote hain
  /// — download aur "already downloaded?" check dono isi ek folder ko
  /// use karte hain, taaki state hamesha sahi rahe.
  static Future<Directory> _docsDestinationDir() async {
    if (Platform.isAndroid) {
      final hasAccess = await _ensureStorageAccess();
      if (hasAccess) {
        try {
          return await _publicDownloadsDir();
        } catch (_) {
          // kabhi kabhi permission granted dikhata hai par write fail ho
          // jaata hai (OEM quirk) — safe fallback.
        }
      }
    }
    return _fallbackDownloadsDir();
  }

  /// 🔥 NAYA: WhatsApp jaisa "Open" vs "Download" state — file pehle se
  /// downloaded hai kya, ye deterministic path check karke pata karta hai
  /// (koi extra DB/network call nahi). Sirf doc/audio/presentation/file
  /// type ke liye kaam karta hai — image/video seedhe gallery me jaate
  /// hain, unka koi fixed app-accessible path nahi hota, isliye unke
  /// downloaded-state ko ChatScreen apni in-session memory me track karta
  /// hai (naya message ke liye hamesha download available rehna chahiye).
  static Future<String?> alreadyDownloadedPath(String fileName) async {
    try {
      final dir = await _docsDestinationDir();
      final file = File("${dir.path}/$fileName");
      if (await file.exists()) return file.path;
    } catch (_) {}
    return null;
  }

  /// URL se file download karke device me save karta hai.
  /// Image/video -> gallery ("LearnScroll" album) me jaata hai.
  /// File/audio/presentation -> "Download/LearnScroll" (public) folder me
  /// jaata hai, jahan se dobara "Open" milta hai bina dobara download kiye.
  ///
  /// `onProgress` 0.0 se 1.0 tak progress deta hai — UI me progress bar
  /// dikhane ke liye.
  static Future<String> download({
    required String url,
    required DownloadKind kind,
    String? fileName,
    void Function(double progress)? onProgress,
  }) async {
    if (kind == DownloadKind.image || kind == DownloadKind.video) {
      var hasAccess = await Gal.hasAccess();
      if (!hasAccess) {
        hasAccess = await Gal.requestAccess();
      }
      if (!hasAccess) {
        throw Exception("Gallery permission denied — Settings me jaake Photos/Media access allow karo.");
      }
    }

    final name = fileName ?? url.split('/').last.split('?').first;

    // 🔥 NAYA: non-media files ke liye — agar pehle se downloaded hai to
    // dobara internet call hi mat karo, seedha existing path wapas kar do.
    if (kind == DownloadKind.other) {
      final existing = await alreadyDownloadedPath(name);
      if (existing != null) {
        onProgress?.call(1.0);
        return existing;
      }
    }

    final tempDir = await getTemporaryDirectory();
    final tempPath = "${tempDir.path}/$name";

    await _dio.download(
      url,
      tempPath,
      onReceiveProgress: (received, total) {
        if (total > 0) onProgress?.call(received / total);
      },
    );

    if (kind == DownloadKind.image) {
      await Gal.putImage(tempPath, album: _appFolderName);
      return tempPath;
    }
    if (kind == DownloadKind.video) {
      await Gal.putVideo(tempPath, album: _appFolderName);
      return tempPath;
    }

    final downloadsDir = await _docsDestinationDir();
    final finalPath = "${downloadsDir.path}/$name";
    await File(tempPath).copy(finalPath);
    try {
      await File(tempPath).delete();
    } catch (_) {}
    return finalPath;
  }

  /// Downloaded file ko device ke default app se open karo (PDF viewer,
  /// audio player, waghera).
  static Future<void> openFile(String path) async {
    await OpenFilex.open(path);
  }
}