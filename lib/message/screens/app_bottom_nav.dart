// message/screens/app_bottom_nav.dart
//
// 🔥 NAYA — same bottom navigation bar jo HomeScreen me hai (Home / Search /
// Profile), ab yahan ek "Chats" tab ke saath reuse ho rahi hai, taaki
// conversation list bhi ek real tab jaisa feel de — bilkul WhatsApp/
// Instagram jaisa (jaise Insta ka DM inbox apna khud ka top-level screen
// hota hai lekin bottom bar wahi rehta hai).
//
// NOTE: import neeche '../../home/home.dart' se hai — ye home.dart ke
// andar ke relative imports (jaise '../message/screens/conversations_screen.dart')
// se inferred hai. Agar tumhare project me HomeScreen kisi aur path pe hai,
// to bas ye ek import line update kar dena, baaki sab same rahega.

import 'package:flutter/material.dart';
import '../../home.dart';

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
    return BottomNavigationBar(
      currentIndex: current.index,
      onTap: (i) => _handleTap(context, i),
      backgroundColor: const Color(0xFF030F27),
      selectedItemColor: Colors.white,
      unselectedItemColor: Colors.white60,
      type: BottomNavigationBarType.fixed,
      items: const [
        BottomNavigationBarItem(
          icon: Icon(Icons.home_outlined),
          activeIcon: Icon(Icons.home),
          label: "Home",
        ),
        BottomNavigationBarItem(
          icon: Icon(Icons.search),
          label: "Search",
        ),
        BottomNavigationBarItem(
          icon: Icon(Icons.chat_bubble_outline),
          activeIcon: Icon(Icons.chat_bubble),
          label: "Chats",
        ),
        BottomNavigationBarItem(
          icon: Icon(Icons.person_outline),
          activeIcon: Icon(Icons.person),
          label: "Profile",
        ),
      ],
    );
  }
}