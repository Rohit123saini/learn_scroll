// lib/services/account_manager.dart
//
// ============================================================
// P15-FE — MULTI-ACCOUNT (max 3) ON ONE DEVICE
//
// Design (kept deliberately small so login/logout risk stays contained):
//
//  • SharedPreferences ("access"/"refresh"/"user_id") REMAINS the live
//    session for the ACTIVE account. Every existing service
//    (AuthService.getToken/getValidToken, http interceptor, sockets)
//    keeps reading from there — none of them need to change.
//  • This class adds an encrypted VAULT (flutter_secure_storage) that holds
//    every saved account's tokens. Switching = snapshot current tokens into
//    the vault -> verify target's refresh token -> tear down per-user state
//    -> write target's tokens into prefs -> re-init.
//  • The target is VERIFIED FIRST (before anything is torn down). A dead
//    session (server has a 10-day inactivity sliding-expiry!) therefore
//    never leaves the user half-logged-out.
//  • Per-user singletons I can't see (FCM, chat/inbox sockets, caches)
//    plug in through addLeaveHook / addEnterHook — see main.dart.
// ============================================================

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/painting.dart' show PaintingBinding;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../message/services/call_manager.dart';
import 'auth_service.dart';
import 'crash_reporting_service.dart';
import 'session_service.dart';

enum SwitchResult { ok, expired, network, inCall, busy, failed }

class SavedAccount {
  String id;
  String username;
  String displayName;
  String photo; // full URL or ''
  String access;
  String refresh;
  bool expired;

  SavedAccount({
    required this.id,
    this.username = '',
    this.displayName = '',
    this.photo = '',
    this.access = '',
    this.refresh = '',
    this.expired = false,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'username': username,
        'displayName': displayName,
        'photo': photo,
        'access': access,
        'refresh': refresh,
        'expired': expired,
      };

  factory SavedAccount.fromJson(Map<String, dynamic> j) => SavedAccount(
        id: (j['id'] ?? '').toString(),
        username: (j['username'] ?? '').toString(),
        displayName: (j['displayName'] ?? '').toString(),
        photo: (j['photo'] ?? '').toString(),
        access: (j['access'] ?? '').toString(),
        refresh: (j['refresh'] ?? '').toString(),
        expired: j['expired'] == true,
      );
}

typedef AccountHook = Future<void> Function();

class AccountManager extends ChangeNotifier {
  AccountManager._();
  static final AccountManager instance = AccountManager._();

  static const int maxAccounts = 3;
  static const String _storeKey = 'accounts_v1';

  /// SharedPreferences keys that are DEVICE-level (not per-user) and must
  /// survive a switch — e.g. theme / language keys from ThemeService and
  /// LanguageService. Switching wipes prefs exactly like the existing
  /// logout does (AuthService.logout -> prefs.clear()), minus this set.
  /// Fill in the real key names.
  static const Set<String> preservedPrefKeys = <String>{
    'theme_mode', // ThemeService._prefsKey
    'app_locale', // LanguageService._prefsKey
  };

  /// Parent/Guardian mode uses its own prefs keys (parent_service.dart) and
  /// is deliberately independent of the student login, so switching or
  /// adding an account must not wipe it. A real LOGOUT still does, exactly
  /// like before.
  static const Set<String> _parentModeKeys = <String>{
    'parent_token',
    'parent_student_name',
    'parent_label',
  };

  final FlutterSecureStorage _storage = const FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  final List<SavedAccount> _accounts = [];
  String? _activeId;
  String? _addingFrom; // set while the "Add account" login screen is open
  bool _busy = false;
  bool _loaded = false;

  final List<AccountHook> _leaveHooks = [];
  final List<AccountHook> _enterHooks = [];

  // ---------------- public read API ----------------
  List<SavedAccount> get accounts => List.unmodifiable(_accounts);
  String? get activeId => _activeId;
  bool get isAddingAccount => _addingFrom != null;
  bool get busy => _busy;
  bool get canAddMore => _accounts.length < maxAccounts;
  bool get hasOthers => _accounts.any((a) => a.id != _activeId);
  SavedAccount? get active => _byId(_activeId);

  SavedAccount? _byId(String? id) {
    if (id == null) return null;
    for (final a in _accounts) {
      if (a.id == id) return a;
    }
    return null;
  }

  /// Runs BEFORE the outgoing account's session is wiped (its token is
  /// still valid here — use it to unregister the FCM token server-side,
  /// disconnect sockets, drop per-user caches).
  void addLeaveHook(AccountHook h) => _leaveHooks.add(h);

  /// Runs AFTER the incoming account's tokens are installed (re-register
  /// FCM, reconnect sockets, re-pull preferences).
  void addEnterHook(AccountHook h) => _enterHooks.add(h);

  // ---------------- lifecycle ----------------
  Future<void> init() async {
    if (_loaded) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString('access') ?? prefs.getString('access_token');
      final hasToken = token != null && token.isNotEmpty;

      if (!hasToken && prefs.getKeys().isEmpty) {
        // Fresh install. iOS Keychain survives uninstall, so a vault with
        // no matching app data is stale — never resurrect it.
        await _storage.delete(key: _storeKey);
      } else {
        final raw = await _storage.read(key: _storeKey);
        if (raw != null) {
          final m = jsonDecode(raw) as Map<String, dynamic>;
          _addingFrom = m['adding'] as String?;
          for (final j in (m['accounts'] as List? ?? const [])) {
            _accounts.add(SavedAccount.fromJson(Map<String, dynamic>.from(j as Map)));
          }
        }
      }

      if (hasToken) {
        // Adopt the live session (also migrates users who were already
        // logged in before this feature shipped).
        final uid = prefs.getString('user_id');
        final id = (uid != null && uid.isNotEmpty) ? uid : 'legacy';
        var acct = _byId(id);
        acct ??= SavedAccount(id: id);
        acct.access = token;
        acct.refresh = prefs.getString('refresh') ?? acct.refresh;
        acct.expired = false;
        if (_byId(id) == null) _accounts.add(acct);
        _activeId = id;
        _addingFrom = null;
        await _persist();
      }
    } catch (e, st) {
      CrashReportingService.logError('AccountManager.init', e, stackTrace: st);
    } finally {
      _loaded = true;
      notifyListeners();
    }
  }

  /// App was killed while the "Add account" login screen was open (prefs
  /// are empty, vault still has the previous account) -> put it back.
  Future<bool> restoreIfInterrupted() async {
    await init();
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access') ?? prefs.getString('access_token');
    if (token != null && token.isNotEmpty) return false;
    final a = _byId(_addingFrom);
    if (a == null) return false;
    await _enter(a, runHooks: false);
    return true;
  }

  // ---------------- called from ApiService._saveSession ----------------
  /// A login/signup/google response just wrote new tokens into prefs.
  /// Register (or refresh) that account in the vault and mark it active.
  Future<void> onSessionSaved(Map<String, dynamic> data) async {
    try {
      await init();
      final tok = data['token'];
      final access = (tok is Map ? tok['access'] : data['access']) as String?;
      final refresh = (tok is Map ? tok['refresh'] : data['refresh']) as String?;
      if (access == null || access.isEmpty) return;

      final user = data['user'] is Map ? Map<String, dynamic>.from(data['user'] as Map) : <String, dynamic>{};
      final prefs = await SharedPreferences.getInstance();
      final uid = (user['id'] ?? user['user_id'] ?? data['id'] ?? prefs.getString('user_id') ?? '').toString();
      final username = (user['username'] ?? '').toString();
      final id = uid.isNotEmpty ? uid : (username.isNotEmpty ? 'u:$username' : 'legacy');

      var acct = _byId(id);
      // Same person re-logging in (expired session) or a pre-feature
      // 'legacy' entry: match by username so we never store duplicates.
      acct ??= _accounts.cast<SavedAccount?>().firstWhere(
            (a) => a!.id == 'legacy' || (username.isNotEmpty && a.username.toLowerCase() == username.toLowerCase()),
            orElse: () => null,
          );
      if (acct == null) {
        acct = SavedAccount(id: id);
        _accounts.add(acct);
      }
      acct
        ..id = id
        ..access = access
        ..refresh = refresh ?? acct.refresh
        ..expired = false;
      if (username.isNotEmpty) acct.username = username;
      final fn = '${user['first_name'] ?? ''} ${user['last_name'] ?? ''}'.trim();
      if (fn.isNotEmpty) acct.displayName = fn;

      while (_accounts.length > maxAccounts) {
        final drop = _accounts.firstWhere((a) => a.id != id);
        _accounts.remove(drop);
      }

      _addingFrom = null;
      _activeId = id;
      await _persist();
      notifyListeners();
      // Every fresh login (not only add-account): registers the FCM token
      // for this user and (re)connects the inbox socket.
      await _runHooks(_enterHooks);
    } catch (e, st) {
      CrashReportingService.logError('AccountManager.onSessionSaved', e, stackTrace: st);
    }
  }

  /// Settings/Profile know the current user's display data — keep the
  /// switcher rows in sync (name/photo can change after login).
  Future<void> updateCurrentProfile({String? username, String? displayName, String? photo}) async {
    final a = active;
    if (a == null) return;
    var changed = false;
    if (username != null && username.isNotEmpty && a.username != username) { a.username = username; changed = true; }
    if (displayName != null && a.displayName != displayName) { a.displayName = displayName; changed = true; }
    if (photo != null && a.photo != photo) { a.photo = photo; changed = true; }
    if (changed) {
      await _persist();
      notifyListeners();
    }
  }

  // ---------------- switching ----------------
  Future<SwitchResult> switchTo(String id) async {
    if (_busy) return SwitchResult.busy;
    final target = _byId(id);
    if (target == null || id == _activeId) return SwitchResult.failed;
    if (CallManager.instance.isActive) return SwitchResult.inCall;

    _busy = true;
    notifyListeners();
    try {
      await _snapshotActive();

      // 1) Verify BEFORE tearing anything down.
      RefreshedTokens? fresh;
      try {
        fresh = await AuthService.exchangeRefresh(target.refresh);
      } catch (_) {
        return SwitchResult.network;
      }
      if (fresh == null) {
        target.expired = true;
        await _persist();
        return SwitchResult.expired;
      }
      target.access = fresh.access;
      if (fresh.refresh != null) target.refresh = fresh.refresh!;

      // 2) Tear down outgoing account, 3) install incoming.
      await _leaveCurrent();
      await _enter(target);
      return SwitchResult.ok;
    } catch (e, st) {
      CrashReportingService.logError('AccountManager.switchTo', e, stackTrace: st);
      return SwitchResult.failed;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  /// "Add account" (or re-login of an expired one): park the current
  /// account in the vault, wipe the live session, caller then shows the
  /// normal LoginScreen. `isAddingAccount` lets LoginScreen show a Cancel.
  Future<bool> beginAddAccount({bool relogin = false}) async {
    if (_busy || (!relogin && !canAddMore)) return false;
    if (CallManager.instance.isActive) return false;
    _busy = true;
    try {
      await _snapshotActive();
      _addingFrom = _activeId;
      await _persist();
      await _leaveCurrent();
      _activeId = null;
      return true;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  Future<bool> cancelAddAccount() async {
    final a = _byId(_addingFrom);
    if (a == null) return false;
    await _enter(a);
    return true;
  }

  /// Log out of the CURRENT account only. If another saved account is
  /// still alive, switch into it and return true (caller -> '/home');
  /// otherwise return false (caller -> '/login').
  Future<bool> logoutCurrent() async {
    final cur = _activeId;
    await _leaveCurrent(keepParentMode: false);
    _accounts.removeWhere((a) => a.id == cur);
    _activeId = null;
    await _persist();

    for (final a in List<SavedAccount>.of(_accounts.where((a) => !a.expired))) {
      try {
        final fresh = await AuthService.exchangeRefresh(a.refresh);
        if (fresh == null) {
          a.expired = true;
          continue;
        }
        a.access = fresh.access;
        if (fresh.refresh != null) a.refresh = fresh.refresh!;
        await _enter(a);
        return true;
      } catch (_) {
        break; // offline — don't mark anything expired, just go to login
      }
    }
    await _persist();
    notifyListeners();
    return false;
  }

  // ---------------- internals ----------------
  Future<void> _snapshotActive() async {
    final a = active;
    if (a == null) return;
    final prefs = await SharedPreferences.getInstance();
    final acc = prefs.getString('access') ?? prefs.getString('access_token');
    final ref = prefs.getString('refresh');
    if (acc != null && acc.isNotEmpty) a.access = acc; // may have been silently refreshed
    if (ref != null && ref.isNotEmpty) a.refresh = ref;
    await _persist();
  }

  Future<void> _leaveCurrent({bool keepParentMode = true}) async {
    await _runHooks(_leaveHooks);

    final prefs = await SharedPreferences.getInstance();
    final keep = <String, Object>{};
    for (final k in {...preservedPrefKeys, if (keepParentMode) ..._parentModeKeys}) {
      final v = prefs.get(k);
      if (v != null) keep[k] = v;
    }
    await AuthService.logout(); // prefs.clear() — same as today's logout
    final p2 = await SharedPreferences.getInstance();
    for (final e in keep.entries) {
      final v = e.value;
      if (v is bool) await p2.setBool(e.key, v);
      else if (v is int) await p2.setInt(e.key, v);
      else if (v is double) await p2.setDouble(e.key, v);
      else if (v is String) await p2.setString(e.key, v);
      else if (v is List<String>) await p2.setStringList(e.key, v);
    }

    PaintingBinding.instance.imageCache
      ..clear()
      ..clearLiveImages();
    SessionService.reset();
  }

  Future<void> _enter(SavedAccount a, {bool runHooks = true}) async {
    await AuthService.saveToken(a.access, refreshToken: a.refresh);
    if (a.id != 'legacy' && !a.id.startsWith('u:')) {
      await AuthService.saveUserId(a.id);
    }
    a.expired = false;
    _activeId = a.id;
    _addingFrom = null;
    await _persist();
    SessionService.reset();
    notifyListeners();
    if (runHooks) await _runHooks(_enterHooks);
  }

  Future<void> _runHooks(List<AccountHook> hooks) async {
    for (final h in hooks) {
      try {
        await h().timeout(const Duration(seconds: 5));
      } catch (e, st) {
        CrashReportingService.logError('AccountManager.hook', e, stackTrace: st);
      }
    }
  }

  Future<void> _persist() async {
    try {
      await _storage.write(
        key: _storeKey,
        value: jsonEncode({
          'adding': _addingFrom,
          'accounts': _accounts.map((a) => a.toJson()).toList(),
        }),
      );
    } catch (e, st) {
      CrashReportingService.logError('AccountManager.persist', e, stackTrace: st);
    }
  }
}
