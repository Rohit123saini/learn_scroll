// ============================================================
// EXAMPLE — how to wire everything in this module together at the app
// root. This file is a reference, not meant to be dropped in as-is
// over an existing `main.dart` — merge the relevant pieces into
// whatever this project's real entry point already does (existing
// auth bootstrap, other feature modules, etc.).
// ============================================================

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import '../l10n/app_localizations.dart';

import 'api/liveclass_api.dart';
import 'state/liveclass_settings.dart';
import 'liveclass_home_shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final settings = LiveClassSettingsController();
  await settings.load();

  final api = LiveClassApi(
    baseUrl: 'https://your-api-host.example.com/liveclass', // TODO: real base URL
    // TODO: replace with this project's actual token source, e.g.
    // `() async => AuthService.instance.accessToken`.
    getToken: () async => null,
  );

  runApp(LiveClassExampleApp(settings: settings, api: api));
}

class LiveClassExampleApp extends StatelessWidget {
  final LiveClassSettingsController settings;
  final LiveClassApi api;
  const LiveClassExampleApp({super.key, required this.settings, required this.api});

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: settings,
      builder: (context, _) => MaterialApp(
        title: 'LearnScroll',
        debugShowCheckedModeBanner: false,
        locale: settings.locale,
        themeMode: settings.themeMode,
        // TODO: swap in this project's real ColorScheme-based themes —
        // these two are placeholders so the example compiles standalone.
        theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.deepPurple), useMaterial3: true),
        darkTheme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.deepPurple, brightness: Brightness.dark), useMaterial3: true),
        supportedLocales: AppLocalizations.supportedLocales,
        localizationsDelegates: const [
          ...AppLocalizations.localizationsDelegates,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: LiveClassHomeShell(api: api, settings: settings),
      ),
    );
  }
}
