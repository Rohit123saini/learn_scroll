import 'dart:async';
import 'dart:developer' as developer;

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../campus/screens/parent_link_screen.dart';
import '../message/screens/parent_code_entry_screen.dart';
import '../profile/screens/target_profile.dart';
import '../referrals/referral_capture.dart'; // TASK 12 — ?ref= attribution
import 'share_target_service.dart'; // share-target: content shared FROM other apps
import 'session_service.dart'; // navigatorKey

// ============================================================
// TASK 11 — deep links + QR payloads (one parser for both).
//
// Supported (same payloads whether they come from a tapped link, the OS, or the in-app
// QR scanner):
//
//   Profile         learnscroll://u/<username>
//                   https://<webHost>/u/<username>                (share links + profile QR)
//   Parent invite   https://<webHost>/parent-link?code=XXXX[&campus=ID][&classroom=ID]
//                   learnscroll://parent-link?code=XXXX[&campus=ID][&classroom=ID]
//
//   DeepLinkService.profileLink('rahul')      // https link — what Share / QR use now
//   DeepLinkService.profileAppLink('rahul')   // learnscroll:// form (kept for old callers)
//   DeepLinkService.parse(uri) / parsePayload(text)  -> DeepLinkTarget? (pure, no UI)
//   DeepLinkService.instance.handlePayload(text)     -> bool  (scanner uses this)
//   DeepLinkService.instance.init()                  // main.dart, once
//   DeepLinkGate(child: ...)                         // main.dart: flushes a link that arrived early
//
// Auth rules when the link is opened:
//   • profile                      -> needs login (a logged-out user is never shown a profile)
//   • parent invite WITH campus    -> needs login (CampusParentLink is a real FK to the parent's account)
//   • parent invite WITHOUT campus -> NO login (Parent Mode is deliberately loginless:
//                                     code -> ParentToken). A parent usually has no account.
// A link that can't be opened yet (Navigator not mounted / login needed) is kept as "pending"
// and retried from DeepLinkGate, app resume, and the next link — never silently lost.
//
// Native registration (https App Links / Universal Links, camera permission) is in
// TASK_11_NATIVE_SETUP.md.
// ============================================================

sealed class DeepLinkTarget {
  const DeepLinkTarget();

  /// Does opening this need a logged-in session?
  bool get needsLogin;

  /// Stable id used to ignore the "same link delivered twice" echo.
  String get key;
}

class ProfileLinkTarget extends DeepLinkTarget {
  final String username;
  const ProfileLinkTarget(this.username);

  @override
  bool get needsLogin => true;

  @override
  String get key => 'u:$username';
}

class ParentInviteTarget extends DeepLinkTarget {
  /// Upper-cased ParentAccessCode.code.
  final String code;

  /// Present for campus invites (needs login + CampusParentLink confirm).
  final String? campusId;

  /// Present for tuition-class invites (informational — Parent Mode verifies the code only).
  final String? classroomId;

  const ParentInviteTarget({required this.code, this.campusId, this.classroomId});

  @override
  bool get needsLogin => campusId != null;

  @override
  String get key => 'p:$code:${campusId ?? ''}:${classroomId ?? ''}';
}

class DeepLinkService {
  DeepLinkService._();
  static final DeepLinkService instance = DeepLinkService._();

  static const String scheme = 'learnscroll';
  static const String profileHost = 'u'; // learnscroll://u/<username>
  static const String parentHost = 'parent-link'; // learnscroll://parent-link?code=..

  /// Public web host used for https links / QR codes. Must match the backend's
  /// PARENT_INVITE_LINK_BASE / APP_WEB_BASE_URL host. Override per build with
  /// `--dart-define=WEB_HOST=app.mydomain.com`.
  static const String webHost = String.fromEnvironment('WEB_HOST', defaultValue: 'learnscroll.app');

  // Same character set Django allows for usernames (letters, digits, @ . + - _).
  static final RegExp _usernameRe = RegExp(r'^[A-Za-z0-9_.@+-]{1,150}$');
  // ParentAccessCode.code is max_length=12, generated from A-Z2-9; accept any alnum 4..12.
  static final RegExp _codeRe = RegExp(r'^[A-Za-z0-9]{4,12}$');
  static final RegExp _idRe = RegExp(r'^[A-Za-z0-9_-]{1,64}$');

  // ---------- builders ----------

  /// https://<webHost>/u/<username>  — share sheet, copy link and the profile QR.
  static String profileLink(String username) => 'https://$webHost/$profileHost/${Uri.encodeComponent(username)}';

  /// https://<webHost>/parent-link?code=XXXX — fallback when the server didn't send a `link`.
  static String parentInviteLink(String code, {String? campusId, String? classroomId}) {
    final q = <String, String>{
      'code': code,
      if (campusId != null) 'campus': campusId,
      if (classroomId != null) 'classroom': classroomId,
    };
    return Uri.https(webHost, '/$parentHost', q).toString();
  }

  /// learnscroll://u/<username>  (custom-scheme form; still parsed everywhere).
  static String profileAppLink(String username) => '$scheme://$profileHost/${Uri.encodeComponent(username)}';

  // ---------- parsing (pure) ----------

  static bool _isOurWebHost(String host) {
    final h = host.toLowerCase();
    return h == webHost.toLowerCase() || h == 'www.${webHost.toLowerCase()}';
  }

  /// Turns any supported link / QR text into a target, or null if it isn't ours.
  static DeepLinkTarget? parse(Uri uri) {
    final s = uri.scheme.toLowerCase();
    final segs = uri.pathSegments.where((e) => e.isNotEmpty).toList();

    String? first; // "u" | "parent-link"
    List<String> rest = const [];

    if (s == scheme) {
      // learnscroll://u/name  (host=u)   or   learnscroll:///u/name  (host empty)
      if (uri.host.isNotEmpty) {
        first = uri.host.toLowerCase();
        rest = segs;
      } else if (segs.isNotEmpty) {
        first = segs.first.toLowerCase();
        rest = segs.sublist(1);
      }
    } else if ((s == 'https' || s == 'http') && _isOurWebHost(uri.host)) {
      if (segs.isNotEmpty) {
        first = segs.first.toLowerCase();
        rest = segs.sublist(1);
      }
    } else {
      return null;
    }

    if (first == profileHost && rest.isNotEmpty) {
      final name = rest.first;
      if (!_usernameRe.hasMatch(name)) return null;
      return ProfileLinkTarget(name);
    }

    if (first == parentHost) {
      final code = (uri.queryParameters['code'] ?? uri.queryParameters['token'] ?? '').trim();
      if (!_codeRe.hasMatch(code)) return null;
      String? campus = uri.queryParameters['campus']?.trim();
      String? classroom = uri.queryParameters['classroom']?.trim();
      if (campus != null && !_idRe.hasMatch(campus)) campus = null;
      if (classroom != null && !_idRe.hasMatch(classroom)) classroom = null;
      if (campus != null && campus.isEmpty) campus = null;
      if (classroom != null && classroom.isEmpty) classroom = null;
      return ParentInviteTarget(code: code.toUpperCase(), campusId: campus, classroomId: classroom);
    }
    return null;
  }

  /// Same as [parse] for raw text (QR content, pasted link).
  static DeepLinkTarget? parsePayload(String raw) {
    final text = raw.trim();
    if (text.isEmpty || text.length > 600) return null;
    final uri = Uri.tryParse(text);
    if (uri == null) return null;
    return parse(uri);
  }

  /// Back-compat helper (old signature): username if [uri] is a profile link, else null.
  static String? parseProfileUsername(Uri uri) {
    final t = parse(uri);
    return t is ProfileLinkTarget ? t.username : null;
  }

  // ---------- runtime ----------

  final AppLinks _appLinks = AppLinks();
  StreamSubscription<Uri>? _sub;
  bool _started = false;
  bool _flushing = false;
  DeepLinkTarget? _pending;
  String? _lastHandled;
  DateTime _lastHandledAt = DateTime.fromMillisecondsSinceEpoch(0);

  Future<void> init() async {
    if (_started) return;
    _started = true;
    unawaited(ShareTargetService.instance.init()); // other apps' Share -> LearnScroll
    try {
      final initial = await _appLinks.getInitialLink();
      if (initial != null) _onUri(initial);
    } catch (e) {
      developer.log('DeepLink initial link failed: $e');
    }
    _sub = _appLinks.uriLinkStream.listen(
      _onUri,
      onError: (Object e) => developer.log('DeepLink stream error: $e'),
    );
  }

  void dispose() {
    _sub?.cancel();
    _sub = null;
    _started = false;
  }

  void _onUri(Uri uri) {
    // TASK 12: any link (profile, classroom, test series, https...) may carry
    // ?ref=<code>. Store it and report it as soon as the user is logged in.
    ReferralCapture.captureFromUri(uri).then((_) => ReferralCapture.flush());
    final target = parse(uri);
    if (target != null) _accept(target);
  }

  /// Used by the QR scanner / paste boxes. Returns false when [raw] isn't a LearnScroll
  /// payload (the caller shows an "unsupported QR" message).
  Future<bool> handlePayload(String raw) async {
    final target = parsePayload(raw);
    if (target == null) return false;
    _accept(target, force: true);
    return true;
  }

  void _accept(DeepLinkTarget target, {bool force = false}) {
    // Some platforms deliver the launch link twice (initial + stream) — ignore the echo.
    // (A deliberate scan/paste is never an echo, so [force] skips this.)
    final now = DateTime.now();
    if (!force && _lastHandled == target.key && now.difference(_lastHandledAt) < const Duration(seconds: 2)) {
      return;
    }
    _lastHandled = target.key;
    _lastHandledAt = now;
    _pending = target;
    flushPending();
  }

  Future<bool> _isLoggedIn() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString('access_token'); // same check main.dart's _checkAuth() uses
      return token != null && token.isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  /// Opens the pending target if the app is ready (Navigator mounted, and logged in when the
  /// target needs it); otherwise keeps it for the next call. Safe to call any time.
  Future<void> flushPending() async {
    ReferralCapture.flush(); // TASK 12 — no-op unless a ref code is waiting
    ShareTargetService.instance.flushPending(); // content shared from another app, if any
    final target = _pending;
    if (target == null || _flushing) return;
    _flushing = true;
    try {
      if (navigatorKey.currentState == null) return;
      if (target.needsLogin && !await _isLoggedIn()) return;
      if (!identical(_pending, target)) return; // a newer link replaced it while we awaited
      final nav = navigatorKey.currentState;
      if (nav == null) return;
      _pending = null;
      nav.push(MaterialPageRoute(builder: (_) => _screenFor(target)));
    } finally {
      _flushing = false;
    }
  }

  Widget _screenFor(DeepLinkTarget target) {
    switch (target) {
      case ProfileLinkTarget(:final username):
        return TargetProfilePage(username: username);
      case ParentInviteTarget(:final code, :final campusId):
        if (campusId != null) {
          // Campus invite: the logged-in user becomes the parent (CampusParentLink).
          return ParentLinkScreen(initialCampusId: campusId, initialCode: code);
        }
        // Tuition-class / student-generated code: loginless Parent Mode.
        return ParentCodeEntryScreen(initialCode: code, autoSubmit: true);
    }
  }
}

/// Wrap a root screen (HomeScreen AND LoginScreen): once it's on screen, open any link that
/// arrived before the app was ready. Login-gated targets simply stay pending while logged out.
class DeepLinkGate extends StatefulWidget {
  final Widget child;
  const DeepLinkGate({super.key, required this.child});

  @override
  State<DeepLinkGate> createState() => _DeepLinkGateState();
}

class _DeepLinkGateState extends State<DeepLinkGate> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => DeepLinkService.instance.flushPending());
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
