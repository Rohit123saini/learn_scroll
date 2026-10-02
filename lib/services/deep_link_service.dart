import 'dart:async';
import 'dart:developer' as developer;

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../profile/screens/target_profile.dart';
import 'session_service.dart'; // navigatorKey

// ============================================================
// P9-FE — deep links:   learnscroll://u/<username>   ->  that user's profile.
//
//   DeepLinkService.profileLink('rahul')   // builds the link (share sheet / QR use this)
//   DeepLinkService.instance.init()        // main.dart, once: cold-start link + live stream
//   DeepLinkGate(child: HomeScreen())      // main.dart: flushes a link that arrived early
//
// Why "pending": a link can land (a) before MaterialApp has mounted its Navigator (cold
// start) or (b) while the user is logged out. Both cases just remember the username and
// retry from the places where the app becomes ready (DeepLinkGate, app resume, next link) —
// a logged-out user is NEVER shown a profile, and the link isn't silently lost either.
// Native registration of the scheme (AndroidManifest / Info.plist) is in P9_NATIVE_SETUP.md.
// ============================================================

class DeepLinkService {
  DeepLinkService._();
  static final DeepLinkService instance = DeepLinkService._();

  static const String scheme = 'learnscroll';
  static const String profileHost = 'u';

  // Same character set Django allows for usernames (letters, digits, @ . + - _).
  static final RegExp _usernameRe = RegExp(r'^[A-Za-z0-9_.@+-]{1,150}$');

  /// learnscroll://u/<username>
  static String profileLink(String username) => '$scheme://$profileHost/${Uri.encodeComponent(username)}';

  /// Returns the username if [uri] is a profile link, else null. Accepts both
  /// `learnscroll://u/name` and the triple-slash form `learnscroll:///u/name`.
  static String? parseProfileUsername(Uri uri) {
    if (uri.scheme.toLowerCase() != scheme) return null;
    final segs = uri.pathSegments.where((s) => s.isNotEmpty).toList();
    String? name;
    if (uri.host.toLowerCase() == profileHost && segs.isNotEmpty) {
      name = segs.first;
    } else if (uri.host.isEmpty && segs.length >= 2 && segs.first == profileHost) {
      name = segs[1];
    }
    if (name == null || !_usernameRe.hasMatch(name)) return null;
    return name;
  }

  final AppLinks _appLinks = AppLinks();
  StreamSubscription<Uri>? _sub;
  bool _started = false;
  bool _flushing = false;
  String? _pending;
  String? _lastHandled;
  DateTime _lastHandledAt = DateTime.fromMillisecondsSinceEpoch(0);

  Future<void> init() async {
    if (_started) return;
    _started = true;
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
    final username = parseProfileUsername(uri);
    if (username == null) return;
    // Some platforms deliver the launch link twice (initial + stream) — ignore the echo.
    final now = DateTime.now();
    if (_lastHandled == username && now.difference(_lastHandledAt) < const Duration(seconds: 2)) return;
    _lastHandled = username;
    _lastHandledAt = now;
    _pending = username;
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

  /// Opens the pending profile if the app is ready (Navigator mounted + logged in);
  /// otherwise keeps it for the next call. Safe to call any time, any number of times.
  Future<void> flushPending() async {
    final username = _pending;
    if (username == null || _flushing) return;
    _flushing = true;
    try {
      if (navigatorKey.currentState == null) return;
      if (!await _isLoggedIn()) return;
      if (_pending != username) return; // a newer link replaced it while we awaited
      final nav = navigatorKey.currentState;
      if (nav == null) return;
      _pending = null;
      nav.push(MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)));
    } finally {
      _flushing = false;
    }
  }
}

/// Wrap the logged-in root (HomeScreen): once it's on screen, open any link that arrived
/// before the app was ready (cold start, or opened from the login screen).
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
