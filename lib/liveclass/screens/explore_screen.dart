// ============================================================
// EXPLORE (live classes entry point)
//
// `home.dart` imports and opens `ExploreScreen()` (no arguments) from the
// "join classes" / "explore" actions, but the file didn't exist. It hosts the
// full live-class shell — dashboard, browse classes, coin wallet, settings —
// wired to the app-wide `LiveClass.api` / `LiveClass.settings`, and lands on
// the browse-classes tab because that is what "explore" means.
// ============================================================

import 'package:flutter/material.dart';

import '../liveclass_bootstrap.dart';
import '../liveclass_home_shell.dart';

class ExploreScreen extends StatelessWidget {
  /// 0 dashboard · 1 browse classes (default) · 2 wallet · 3 settings.
  final int initialTab;
  const ExploreScreen({super.key, this.initialTab = 1});

  @override
  Widget build(BuildContext context) {
    return LiveClassHomeShell(
      api: LiveClass.api,
      settings: LiveClass.settings,
      initialIndex: initialTab,
    );
  }
}
