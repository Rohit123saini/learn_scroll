// ============================================================
// LIVECLASS — HOME SHELL
//
// Ties the top-level (no id required) screens into one bottom-nav
// entry point: Dashboard / Classrooms / Wallet / Settings.
//
// Screens that need a `classroomId` (join requests, materials,
// moderation, assignments, notices/holidays/doubts, certificates,
// pass management, recordings) aren't tabs here — they only make
// sense once a classroom is open, so they're reached from
// `ClassroomDetailScreen` itself. `ReferralsScreen` and
// `WishlistScreen` don't need an id but also aren't primary-nav
// material, so they're one tap away from the profile/menu icon on
// the Dashboard tab — wire that icon to whatever this project's
// existing profile/menu affordance is.
// ============================================================

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';

import '../widgets/ls_ui.dart';
import 'api/liveclass_api.dart';
import 'state/liveclass_settings.dart';
import 'screens/dashboard_screen.dart';
import 'screens/classrooms_list_screen.dart';
import 'screens/wallet_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/wishlist_screen.dart';
import 'screens/referrals_screen.dart';

class LiveClassHomeShell extends StatefulWidget {
  final LiveClassApi api;
  final LiveClassSettingsController settings;
  /// Which bottom tab to open on (0 dashboard, 1 browse classes, 2 wallet, 3 settings).
  final int initialIndex;
  const LiveClassHomeShell({super.key, required this.api, required this.settings, this.initialIndex = 0});

  @override
  State<LiveClassHomeShell> createState() => _LiveClassHomeShellState();
}

class _LiveClassHomeShellState extends State<LiveClassHomeShell> {
  late int _index = widget.initialIndex.clamp(0, 3).toInt();

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;

    final pages = [
      DashboardScreenWithMenu(api: widget.api),
      ClassroomsListScreen(api: widget.api),
      WalletScreen(api: widget.api),
      LiveClassSettingsScreen(controller: widget.settings),
    ];

    return Scaffold(
      backgroundColor: lsBg(context),
      body: IndexedStack(index: _index, children: pages),
      // 🔥 FIX [Task 6] — pehle Flutter ka stock Material 3 `NavigationBar`
      // tha (pill indicator, alag typography) — home.dart ke gradient nav
      // se bilkul alag dikhta tha. Ab wahi shared `LsBottomNav` widget.
      bottomNavigationBar: LsBottomNav(
        items: [
          LsBottomNavItemData(icon: Icons.home_outlined, activeIcon: Icons.home_rounded, label: t.dashboardTitle),
          LsBottomNavItemData(icon: Icons.school_outlined, activeIcon: Icons.school_rounded, label: t.liveClassesTitle),
          LsBottomNavItemData(
              icon: Icons.account_balance_wallet_outlined,
              activeIcon: Icons.account_balance_wallet_rounded,
              label: t.walletTitle),
          LsBottomNavItemData(icon: Icons.settings_outlined, activeIcon: Icons.settings_rounded, label: t.settingsTitle),
        ],
        activeIndex: _index,
        onTap: (i) => setState(() => _index = i),
      ),
    );
  }
}

/// Thin wrapper so the Dashboard tab can carry a menu button to the two
/// screens that don't need a classroom id but also aren't primary nav:
/// Wishlist and Referrals.
class DashboardScreenWithMenu extends StatelessWidget {
  final LiveClassApi api;
  const DashboardScreenWithMenu({super.key, required this.api});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Stack(children: [
      LiveClassDashboardScreen(api: api),
      Positioned(
        top: MediaQuery.of(context).padding.top + 4,
        right: 4,
        child: PopupMenuButton<String>(
          icon: const Icon(Icons.more_vert_rounded),
          onSelected: (v) {
            if (v == 'wishlist') {
              Navigator.of(context).push(MaterialPageRoute(builder: (_) => WishlistScreen(api: api)));
            } else if (v == 'referrals') {
              Navigator.of(context).push(MaterialPageRoute(builder: (_) => ReferralsScreen(api: api)));
            }
          },
          itemBuilder: (context) => [
            PopupMenuItem(value: 'wishlist', child: Text(t.wishlistTitle)),
            PopupMenuItem(value: 'referrals', child: Text(t.referAndEarnTitle)),
          ],
        ),
      ),
    ]);
  }
}
