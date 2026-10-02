// lib/widgets/app_bottom_nav.dart
//
// 🔥 FIX [Settings/Nav pass] — "message/campus me ek alag navigator hai,
// home jaisa nahi" (user report):
//
// `message/screens/app_bottom_nav.dart`'s purana `AppBottomNav` sirf 4
// tabs dikhata tha — Home / Search / Chats / Profile — LsBottomNav ka
// wohi gradient LOOK use karke (Task 6 ne bas itna hi fix kiya tha).
// Lekin `home.dart` ka *asli* bottom nav 5 tabs ka hai aur bilkul alag
// set hai — Home / Campus / Classes / Chat / Profile — na ki Search.
// (Search home.dart me bottom-tab hai hi nahi, top search-bar se khulta
// hai.) Matlab tabs ka SET hi mismatch tha, sirf visual style nahi —
// isliye message screens se app ka "Campus" ya "Classes" tab seedha
// pahunch hi nahi paata tha, aur ek "Search" tab dikhta tha jo home me
// kahin hai hi nahi.
//
// Ye widget ab home.dart ke bottom nav ka EXACT mirror hai (same 5 items,
// same icons/labels/order, same tap-behaviour) — home.dart, message
// module (`conversations_screen.dart`), aur campus module
// (`campus_screen.dart`) — teeno ab isi ek widget se apna bottom nav
// banate hain. Naya top-level section (jaisa Campus/Chat) add ho jaye
// aage chal ke, to bas yahin ek jagah update karna hoga.
//
// home.dart khud apna inline `LsBottomNav(...)` rakhta hai (single
// source of truth ke tor par) — is widget ko import nahi karta, taaki
// "asli" nav definition ek hi, sabse zyada dekhe jaane wale screen
// (home) me rahe. Yahan bas usi ko replicate kiya gaya hai.

import 'package:flutter/material.dart';

import '../home.dart';
import '../post/screens/reels_screen.dart';
import '../l10n/app_localizations.dart';
import '../campus/screens/campus_screen.dart';
import '../tuitionclass/screens/explore_screen.dart';
import '../message/screens/conversations_screen.dart';
import 'ls_ui.dart';

/// home.dart ke 5 tabs ke exact order me — index isi order se match hona
/// chahiye (LsBottomNav ke `items` list se).
enum AppTab { home, reels, campus, classes, chat, profile }

class AppBottomNav extends StatelessWidget {
  final AppTab current;

  /// Chats tab ka unread-message badge — home.dart ke `_unreadMessages`
  /// jaisa hi count yahan bhi pass karo taaki dono jagah number match ho.
  final int chatBadge;

  const AppBottomNav({super.key, required this.current, this.chatBadge = 0});

  void _handleTap(BuildContext context, int index) {
    // Already usi tab pe ho to kuch mat karo (jaise Chats screen pe hote
    // hue Chats phir se tap karna).
    final tapped = AppTab.values[index];
    if (tapped == current) return;

    switch (tapped) {
      case AppTab.home:
        // Home ke IndexedStack me wapas jaana ho to purana stack hata do —
        // warna Home > Campus > (Chat se yahan) Home taap karne pe screens
        // ka dher lagta jaata.
        Navigator.of(context).pushAndRemoveUntil(
          MaterialPageRoute(builder: (_) => const HomeScreen(initialIndex: 0)),
          (route) => false,
        );
      case AppTab.reels:
        ReelsScreen.open(context);
      case AppTab.campus:
        Navigator.push(context, MaterialPageRoute(builder: (_) => const CampusScreen()));
      case AppTab.classes:
        Navigator.push(context, MaterialPageRoute(builder: (_) => const ExploreScreen()));
      case AppTab.chat:
        Navigator.push(context, MaterialPageRoute(builder: (_) => const ConversationsScreen()));
      case AppTab.profile:
        Navigator.of(context).pushAndRemoveUntil(
          MaterialPageRoute(builder: (_) => const HomeScreen(initialIndex: 2)),
          (route) => false,
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return LsBottomNav(
      items: [
        LsBottomNavItemData(icon: Icons.home_outlined, activeIcon: Icons.home_rounded, label: l10n.homeTab),
        LsBottomNavItemData(icon: Icons.movie_outlined, activeIcon: Icons.movie_rounded, label: l10n.reelsTab),
        LsBottomNavItemData(icon: Icons.school_outlined, activeIcon: Icons.school_rounded, label: l10n.campusTab),
        LsBottomNavItemData(
          icon: Icons.smart_display_outlined,
          activeIcon: Icons.smart_display_rounded,
          label: l10n.classesTab,
        ),
        LsBottomNavItemData(
          icon: Icons.chat_bubble_outline_rounded,
          activeIcon: Icons.chat_bubble_rounded,
          label: l10n.chatTab,
          badge: chatBadge,
        ),
        LsBottomNavItemData(icon: Icons.person_outline_rounded, activeIcon: Icons.person_rounded, label: l10n.profileTab),
      ],
      activeIndex: current.index,
      onTap: (i) => _handleTap(context, i),
    );
  }
}
