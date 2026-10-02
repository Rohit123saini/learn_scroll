// lib/login/account_switcher_sheet.dart
//
// P15-FE — bottom sheet listing saved accounts. Reusable from anywhere:
//   onLongPress: () => showAccountSwitcherSheet(context)
// (Settings account header uses it; profile.dart's title can too.)

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../services/account_manager.dart';
import '../services/session_service.dart'; // navigatorKey

Future<void> showAccountSwitcherSheet(BuildContext context) {
  return showModalBottomSheet<void>(
    context: context,
    useRootNavigator: true,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (_) => const _AccountSwitcherSheet(),
  );
}

class _AccountSwitcherSheet extends StatefulWidget {
  const _AccountSwitcherSheet();

  @override
  State<_AccountSwitcherSheet> createState() => _AccountSwitcherSheetState();
}

class _AccountSwitcherSheetState extends State<_AccountSwitcherSheet> {
  String? _busyId;

  // Text is resolved lazily from the root navigator's context, so it
  // follows the current app language even after the sheet itself is gone.
  void _snack(String Function(AppLocalizations t) msg) {
    final ctx = navigatorKey.currentContext;
    if (ctx == null) return;
    final t = AppLocalizations.of(ctx);
    if (t == null) return;
    ScaffoldMessenger.maybeOf(ctx)?.showSnackBar(SnackBar(content: Text(msg(t))));
  }

  Future<void> _tap(SavedAccount a) async {
    final mgr = AccountManager.instance;
    if (_busyId != null || mgr.busy) return;
    if (a.id == mgr.activeId) {
      Navigator.pop(context);
      return;
    }
    if (a.expired) {
      await _startAdd(relogin: true);
      return;
    }

    setState(() => _busyId = a.id);
    final res = await mgr.switchTo(a.id);
    switch (res) {
      case SwitchResult.ok:
        // Fresh route stack => every screen of the old account is disposed
        // (and its streams/sockets with it). Removes this sheet as well.
        navigatorKey.currentState?.pushNamedAndRemoveUntil('/home', (_) => false);
        return;
      case SwitchResult.expired:
        _snack((t) => t.acctSwitchExpired(a.username));
        break;
      case SwitchResult.network:
        _snack((t) => t.acctSwitchNoConnection);
        break;
      case SwitchResult.inCall:
        _snack((t) => t.acctSwitchInCall);
        break;
      case SwitchResult.busy:
        break;
      case SwitchResult.failed:
        _snack((t) => t.acctSwitchFailed);
        break;
    }
    if (mounted) setState(() => _busyId = null);
  }

  Future<void> _startAdd({bool relogin = false}) async {
    final ok = await AccountManager.instance.beginAddAccount(relogin: relogin);
    if (!ok) {
      _snack((t) => t.acctAddFailed);
      return;
    }
    navigatorKey.currentState?.pushNamedAndRemoveUntil('/login', (_) => false);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = AppLocalizations.of(context)!;
    final mgr = AccountManager.instance;

    return SafeArea(
      child: AnimatedBuilder(
        animation: mgr,
        builder: (context, _) {
          final accounts = mgr.accounts;
          return Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(t.acctSwitchAccount,
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800, color: cs.onSurface)),
                const SizedBox(height: 10),
                for (final a in accounts) _row(cs, t, a, mgr),
                const Divider(height: 20),
                if (mgr.canAddMore)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: CircleAvatar(
                      backgroundColor: cs.surfaceVariant,
                      child: Icon(Icons.add_rounded, color: cs.onSurfaceVariant),
                    ),
                    title: Text(t.acctAddAccount),
                    onTap: _busyId != null ? null : () => _startAdd(),
                  )
                else
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Text(
                      t.acctMaxReached(AccountManager.maxAccounts),
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _row(ColorScheme cs, AppLocalizations t, SavedAccount a, AccountManager mgr) {
    final isActive = a.id == mgr.activeId;
    final title = a.displayName.isNotEmpty ? a.displayName : (a.username.isNotEmpty ? a.username : t.settingsAccountSection);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: _avatar(cs, a),
      title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        a.expired ? t.acctSessionExpiredTap : (a.username.isEmpty ? '' : '@${a.username}'),
        style: TextStyle(color: a.expired ? cs.error : cs.onSurfaceVariant, fontSize: 12),
      ),
      trailing: _busyId == a.id
          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
          : (isActive ? Icon(Icons.check_circle_rounded, color: cs.primary) : null),
      onTap: () => _tap(a),
    );
  }

  Widget _avatar(ColorScheme cs, SavedAccount a) {
    final fallback = CircleAvatar(
      backgroundColor: cs.surfaceVariant,
      child: Text(
        (a.username.isNotEmpty ? a.username[0] : '?').toUpperCase(),
        style: TextStyle(color: cs.onSurfaceVariant, fontWeight: FontWeight.w700),
      ),
    );
    if (a.photo.isEmpty) return fallback;
    return ClipOval(
      child: CachedNetworkImage(
        imageUrl: a.photo,
        width: 40,
        height: 40,
        fit: BoxFit.cover,
        placeholder: (c, u) => fallback,
        errorWidget: (c, u, e) => fallback,
      ),
    );
  }
}
