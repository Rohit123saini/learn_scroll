// lib/liveclass/screens/liveclass_home_screen.dart
//
// LiveClass module entry point — its own Scaffold + bottom nav, separate
// from the app's main HomeScreen nav (which is already busy running the
// social feed). Wire this up from wherever "Live Classes" is opened from
// (home app-bar icon, etc.) INSTEAD OF pushing ExploreScreen directly:
//
//   IconButton(
//     icon: const Icon(Icons.cast_for_education_rounded),
//     onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const LiveClassHomeScreen())),
//   )
//
// Closes the gap noted in LIVECLASS_SCREEN_ARCHITECTURE.md: Wishlist, My
// Passes, My Requests, My Certificates and My Waitlist had no entry point
// anywhere in the screen set. They all live under the "My Learning" tab
// here now.
//
// FIX (screen-architecture audit): same gap, one more screen — ClassReminder
// had full backend + API-service support but no UI ever listed or let a
// user cancel the reminders they'd set (see the new "Remind me" bell on
// SessionsListScreen for the create side). Added "My Reminders" as a sixth
// tile below, same as the other five "my stuff" screens.
//
// Tabs:
//   0. Explore     — ExploreScreen (self-contained, own app bar + FAB)
//   1. My Learning — grid of the 5 previously-orphaned screens, plus a
//                    bell icon (unread badge) to Notifications, and (staff
//                    only) a flag icon into Classroom Reports
//   2. Wallet      — CoinWalletScreen (self-contained, own app bar)
//
// Now on the shared LiveClass design system (liveclass_theme.dart).
//
// FIX (screen-architecture audit): ClassroomReportsScreen (§22, the
// platform-staff report review queue) had zero references anywhere in the
// module — students could file a report via Classroom Detail's
// _openReportDialog(), but nothing ever let staff open the review screen,
// so every report vanished into a queue nobody could see. The screen's own
// header says to "gate access... behind whatever flag marks a user as
// platform staff" since the API/session layer isn't visible from inside
// this module — same reasoning `canManage`/`canIssue` (materials, doubts,
// assignments, certificates) already use for role gating elsewhere in
// this screen set. So [isPlatformStaff] is threaded in here from the
// caller (whoever pushes LiveClassHomeScreen already knows the signed-in
// user's role) and only then does a "Reports" icon appear in the My
// Learning app bar, next to the notifications bell.

import 'package:flutter/material.dart';

import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'explore_screen.dart';
import 'coin_wallet_screen.dart';
import 'notifications_screen.dart';
import 'my_passes_screen.dart';
import 'wishlist_screen.dart';
import 'waitlist_screen.dart';
import 'certificates_screen.dart';
import 'join_requests_screen.dart';
import 'classroom_reports_screen.dart';
import 'my_reminders_screen.dart';

// ===========================================================================
// SCREEN
// ===========================================================================
class LiveClassHomeScreen extends StatefulWidget {
  /// Which tab to land on — 0 Explore (default), 1 My Learning, 2 Wallet.
  final int initialIndex;

  /// Whether the signed-in user is platform staff (i.e. allowed to review
  /// classroom reports). The module has no session/user concept of its
  /// own, so — same as [CertificatesScreen.canIssue],
  /// [MaterialsScreen.canManage], [DoubtsScreen.canManage] etc. — this is
  /// supplied by whoever pushes this screen, from the app's own auth/user
  /// state. Defaults to false (no "Reports" entry point) so callers that
  /// haven't been updated yet don't accidentally expose it.
  final bool isPlatformStaff;

  const LiveClassHomeScreen({super.key, this.initialIndex = 0, this.isPlatformStaff = false});

  @override
  State<LiveClassHomeScreen> createState() => _LiveClassHomeScreenState();
}

class _LiveClassHomeScreenState extends State<LiveClassHomeScreen> {
  late int _selected = widget.initialIndex;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      body: IndexedStack(
        index: _selected,
        children: [
          const ExploreScreen(),
          _MyLearningTab(isPlatformStaff: widget.isPlatformStaff),
          const CoinWalletScreen(),
        ],
      ),
      bottomNavigationBar: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.06), blurRadius: 12, offset: const Offset(0, -2))],
        ),
        child: SafeArea(
          child: BottomNavigationBar(
            currentIndex: _selected,
            onTap: (i) => setState(() => _selected = i),
            backgroundColor: Colors.white,
            elevation: 0,
            selectedItemColor: LiveClassColors.navy,
            unselectedItemColor: Colors.grey.shade500,
            type: BottomNavigationBarType.fixed,
            selectedLabelStyle: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.bold),
            unselectedLabelStyle: const TextStyle(fontSize: 11.5),
            items: const [
              BottomNavigationBarItem(icon: Icon(Icons.explore_outlined), activeIcon: Icon(Icons.explore), label: 'Explore'),
              BottomNavigationBarItem(icon: Icon(Icons.school_outlined), activeIcon: Icon(Icons.school), label: 'My Learning'),
              BottomNavigationBarItem(icon: Icon(Icons.monetization_on_outlined), activeIcon: Icon(Icons.monetization_on), label: 'Wallet'),
            ],
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// TAB 1 — My Learning: the 5 previously-orphaned "my stuff" screens
// ===========================================================================
class _MyLearningTab extends StatefulWidget {
  final bool isPlatformStaff;
  const _MyLearningTab({required this.isPlatformStaff});

  @override
  State<_MyLearningTab> createState() => _MyLearningTabState();
}

class _MyLearningTabState extends State<_MyLearningTab> {
  int? _unread;

  @override
  void initState() {
    super.initState();
    _loadUnread();
  }

  Future<void> _loadUnread() async {
    try {
      final c = await LiveClassApi.notifications.unreadCount();
      if (mounted) setState(() => _unread = c);
    } catch (_) {
      // Non-fatal — bell just shows without a badge.
    }
  }

  void _open(Widget screen) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => screen)).then((_) => _loadUnread());
  }

  @override
  Widget build(BuildContext context) {
    final tiles = <_LearningTile>[
      _LearningTile(
        icon: Icons.card_membership_rounded,
        label: 'My Passes',
        subtitle: 'Purchase history & status',
        onTap: () => _open(const MyPassesScreen()),
      ),
      _LearningTile(
        icon: Icons.mail_outline_rounded,
        label: 'My Requests',
        subtitle: 'Join requests you sent',
        onTap: () => _open(const JoinRequestsScreen.mine()),
      ),
      _LearningTile(
        icon: Icons.favorite_border_rounded,
        label: 'Wishlist',
        subtitle: 'Classrooms saved for later',
        onTap: () => _open(const WishlistScreen()),
      ),
      _LearningTile(
        icon: Icons.hourglass_top_rounded,
        label: 'My Waitlist',
        subtitle: 'Sessions you\'re waiting on',
        onTap: () => _open(const WaitlistScreen()),
      ),
      _LearningTile(
        icon: Icons.workspace_premium_rounded,
        label: 'My Certificates',
        subtitle: 'Issued course completions',
        onTap: () => _open(const CertificatesScreen()),
      ),
      _LearningTile(
        icon: Icons.notifications_active_rounded,
        label: 'My Reminders',
        subtitle: 'Session alerts you\'ve set',
        onTap: () => _open(const MyRemindersScreen()),
      ),
    ];

    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        'My Learning',
        actions: [
          if (widget.isPlatformStaff)
            IconButton(
              onPressed: () => _open(const ClassroomReportsScreen()),
              icon: const Icon(Icons.flag_outlined),
              tooltip: 'Classroom Reports',
            ),
          Stack(
            alignment: Alignment.center,
            children: [
              IconButton(
                onPressed: () => _open(const NotificationsScreen()),
                icon: const Icon(Icons.notifications_outlined),
                tooltip: 'Notifications',
              ),
              if (_unread != null && _unread! > 0)
                Positioned(
                  top: 10,
                  right: 8,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                    decoration: BoxDecoration(color: const Color(0xFFEE0979), borderRadius: BorderRadius.circular(10)),
                    constraints: const BoxConstraints(minWidth: 16),
                    child: Text(
                      _unread! > 99 ? '99+' : '$_unread',
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.bold),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: GridView.builder(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 24),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 2,
          crossAxisSpacing: 12,
          mainAxisSpacing: 12,
          childAspectRatio: 1.05,
        ),
        itemCount: tiles.length,
        itemBuilder: (_, i) => tiles[i],
      ),
    );
  }
}

class _LearningTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final String subtitle;
  final VoidCallback onTap;
  const _LearningTile({required this.icon, required this.label, required this.subtitle, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            boxShadow: const [LiveClassColors.cardShadow],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LiveClassIconBadge(icon: icon, size: 40),
              const Spacer(),
              Text(label, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: LiveClassColors.navy)),
              const SizedBox(height: 3),
              Text(subtitle, style: TextStyle(fontSize: 11, color: Colors.grey.shade600), maxLines: 2, overflow: TextOverflow.ellipsis),
            ],
          ),
        ),
      ),
    );
  }
}