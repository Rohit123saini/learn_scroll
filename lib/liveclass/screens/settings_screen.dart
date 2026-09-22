// ============================================================
// LIVECLASS — SETTINGS SCREEN (language + theme)
//
// Built from the shared `ls_ui.dart` kit so it matches the rest of
// LearnScroll (Sora headings via `LsType.head`, `LsCard` surfaces,
// theme-only colors) rather than introducing a one-off look. Every
// visible string comes from `AppLocalizations` — see the ARB additions
// shipped alongside this file for the new keys it needs.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart'; // adjust import to your project's generated path
import '../state/liveclass_settings.dart';
import '../../widgets/ls_ui.dart'; // LsCard, LsSectionHead, lsAppBar, LsType, lsBg
import '../../notifications/screens/notification_settings_screen.dart';

class LiveClassSettingsScreen extends StatelessWidget {
  final LiveClassSettingsController controller;
  const LiveClassSettingsScreen({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) => Scaffold(
        backgroundColor: lsBg(context),
        appBar: lsAppBar(context, title: t.settingsTitle),
        body: ListView(
          padding: const EdgeInsets.symmetric(vertical: 12),
          children: [
            LsSectionHead(title: t.languageSectionTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(
                children: [
                  _LanguageTile(
                    label: t.languageSystemDefault,
                    selected: controller.locale == null,
                    onTap: () => controller.setLocale(null),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  _LanguageTile(
                    label: t.languageEnglish,
                    selected: controller.locale?.languageCode == 'en',
                    onTap: () => controller.setLocale(const Locale('en')),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  _LanguageTile(
                    label: t.languageHindi,
                    selected: controller.locale?.languageCode == 'hi',
                    onTap: () => controller.setLocale(const Locale('hi')),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 22),
            LsSectionHead(title: t.themeSectionTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(
                children: [
                  _ThemeTile(
                    label: t.themeSystem,
                    icon: Icons.brightness_auto_rounded,
                    selected: controller.themeMode == ThemeMode.system,
                    onTap: () => controller.setThemeMode(ThemeMode.system),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  _ThemeTile(
                    label: t.themeLight,
                    icon: Icons.light_mode_rounded,
                    selected: controller.themeMode == ThemeMode.light,
                    onTap: () => controller.setThemeMode(ThemeMode.light),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  _ThemeTile(
                    label: t.themeDark,
                    icon: Icons.dark_mode_rounded,
                    selected: controller.themeMode == ThemeMode.dark,
                    onTap: () => controller.setThemeMode(ThemeMode.dark),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 22),
            // 🔥 FIX [Task 5] — notification channel/digest/mute preferences
            // (`core/notification-preferences/me/`) were fully built on the
            // backend but had no entry point anywhere in the app. This row
            // is that entry point.
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: InkWell(
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const NotificationSettingsScreen()),
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
                  child: Row(children: [
                    Icon(Icons.notifications_outlined, size: 18, color: cs.onSurfaceVariant),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(t.notificationSettingsTitle,
                          style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
                    ),
                    Icon(Icons.chevron_right_rounded, size: 20, color: cs.outline),
                  ]),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LanguageTile extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;
  const _LanguageTile({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(children: [
          Expanded(child: Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface))),
          if (selected) Icon(Icons.check_circle_rounded, size: 20, color: cs.primary),
        ]),
      ),
    );
  }
}

class _ThemeTile extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;
  const _ThemeTile({required this.label, required this.icon, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(children: [
          Icon(icon, size: 18, color: cs.onSurfaceVariant),
          const SizedBox(width: 12),
          Expanded(child: Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface))),
          if (selected) Icon(Icons.check_circle_rounded, size: 20, color: cs.primary),
        ]),
      ),
    );
  }
}
