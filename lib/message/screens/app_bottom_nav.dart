// message/screens/app_bottom_nav.dart
//
// Same bottom navigation bar jo HomeScreen me hai (Home / Search / Profile),
// ab yahan ek "Chats" tab ke saath reuse ho rahi hai, taaki conversation
// list bhi ek real tab jaisa feel de — bilkul WhatsApp/Instagram jaisa
// (jaise Insta ka DM inbox apna khud ka top-level screen hota hai lekin
// bottom bar wahi rehta hai).
//
// 🔥 FIX [Task 6] — pehle ye apna alag stock `BottomNavigationBar` banata
// tha (Material 2 look), jo iske apne purane comment me hi documented tha:
// "isn't a visual match for home.dart's actual bottom nav ... would need
// `_LsNavItem`/`_LsBottomNav` exposed as a shared widget". Wahi ab
// `widgets/ls_ui.dart` me public `LsBottomNav` ban chuka hai — yahan use
// karo, pixel-for-pixel wahi gradient look home.dart jaisa.
//
// NOTE: import neeche '../../home.dart' se hai — ye home.dart ke andar ke
// relative imports (jaise '../message/screens/conversations_screen.dart')
// se inferred hai. Agar tumhare project me HomeScreen kisi aur path pe hai,
// to bas ye ek import line update kar dena, baaki sab same rahega.

import 'package:flutter/material.dart';
import '../../home.dart';
import '../../widgets/ls_ui.dart';

enum AppTab { home, search, chats, profile }

class AppBottomNav extends StatelessWidget {
  final AppTab current;
  const AppBottomNav({super.key, required this.current});

  void _handleTap(BuildContext context, int index) {
    // Chats tab pe tap karke agar already chats screen pe ho, to kuch mat karo.
    if (current == AppTab.chats && index == 2) return;

    // Home ke IndexedStack me sirf 3 tabs hain: Home(0), Search(1), Profile(2).
    // "Chats" apna alag pushed screen hai, isliye is bottom bar me tap hote
    // hi Chats se seedha Home/Search/Profile pe navigate ho jaata hai.
    final homeIndex = index == 2 ? 0 : (index == 3 ? 2 : index);

    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => HomeScreen(initialIndex: homeIndex)),
      (route) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    return LsBottomNav(
      items: const [
        LsBottomNavItemData(icon: Icons.home_outlined, activeIcon: Icons.home_rounded, label: 'Home'),
        LsBottomNavItemData(icon: Icons.search_rounded, activeIcon: Icons.search_rounded, label: 'Search'),
        LsBottomNavItemData(
            icon: Icons.chat_bubble_outline_rounded, activeIcon: Icons.chat_bubble_rounded, label: 'Chats'),
        LsBottomNavItemData(icon: Icons.person_outline_rounded, activeIcon: Icons.person_rounded, label: 'Profile'),
      ],
      activeIndex: current.index,
      onTap: (i) => _handleTap(context, i),
    );
  }
}