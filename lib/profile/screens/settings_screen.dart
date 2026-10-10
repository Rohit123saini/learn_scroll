// lib/profile/screens/settings_screen.dart
//
// ============================================================
// APP SETTINGS — home.dart's own comments have been pointing at ("Theme
// aur language toggle home se hata diye gaye hain — dono ab sirf Settings
// screen ke andar hain") but that never actually got built. User report:
// notification-channel settings the wrong place (buried inside the
// Notifications list screen), settings not actually persisting, and no
// real, Instagram-style Settings screen anywhere reachable from Profile.
//
// Entry point: profile.dart's (⋯) button now opens this screen directly
// (see `_openSettings`) instead of an intermediate bottom-sheet menu —
// Task 10. Saved Posts and Parent Access, which used to live in that
// menu, are now the "General" section below; Logout stays at the bottom.
//
// 🔥 ROOT-CAUSE FIX (settings-persistence bug) — the actual bug was in
// `theme_service.dart`/`language_service.dart`: both only ever read/wrote
// on-device `SharedPreferences`, and never called the backend
// `UserPreference` endpoint (`GET/PATCH /profile/preferences/me/`) at all
// — which was already fully production-ready and simply never used from
// anywhere. That's now fixed at the service layer (see those two files +
// the new `services/user_preferences_api.dart`); this screen didn't need
// to change how it *calls* those services, only how it's laid out.
// `is_private` and the other toggles below were already correctly wired
// to their own backend endpoints from an earlier pass — no bug there,
// only restyled here.
//
// Har section already production-ready backend ke upar based hai:
//   • Account          -> `user_profile.UpdateProfileView` (edit profile,
//     is_private toggle)
//   • Privacy           -> `message/screens/read_receipt_privacy_screen.dart`,
//     `user_profile.BlockedUsersView`
//   • Notifications     -> `core/notification-preferences/me/`
//     (NotificationSettingsScreen)
//   • Security (NEW)    -> `login.ChangePasswordAPIView` — backend already
//     existed, had no UI anywhere in the app until this pass (see
//     `change_password_screen.dart`'s own header for a flagged gap in that
//     endpoint itself — out of scope for this pass to fix).
//   • Preferences       -> `ThemeService`/`LanguageService`, now synced
//     with `user_profile.UserPreference` (see above).
// ============================================================

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../../l10n/app_localizations.dart';
import '../../leaderboard/models/leaderboard_models.dart';
import '../../leaderboard/screens/leaderboard_screen.dart';
import '../../widgets/ls_ui.dart';
import '../../theme_service.dart';
import '../../accessibility_service.dart';
import '../../language_service.dart';
import '../../services/account_manager.dart'; // P15-FE
import '../../services/session_service.dart' show navigatorKey; // P15-FE
import '../../login/account_switcher_sheet.dart'; // P15-FE
import '../../utils/api.dart';
import '../../message/screens/read_receipt_privacy_screen.dart';
import '../../message/screens/manage_parent_access_screen.dart' show ManageParentAccessScreen;
import '../api_service.dart';
import '../model.dart';
import 'blocked_accounts_screen.dart';
import 'change_password_screen.dart';
import 'edit_profile.dart';
import 'weekly_recap_screen.dart';
import '../../support/screens/help_center_screen.dart'; // Task 5 — Help & feedback
import 'activity_screen.dart'; // P14-FE — Your activity
import '../../referrals/screens/referrals_screen.dart'; // Task G12 — app-wide Invite & Earn
import '../../notifications/screens/notification_settings_screen.dart';

class SettingsScreen extends StatefulWidget {
  final ProfileModel user;

  /// Private-account toggle change ke baad Profile screen ko turant pata
  /// chal jaaye (dobara fetch ka wait na karna pade) — optional hai,
  /// callback na diya to bhi ye screen khud consistent rehti hai.
  final ValueChanged<bool>? onPrivacyChanged;

  const SettingsScreen({super.key, required this.user, this.onPrivacyChanged});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late bool _isPrivate = widget.user.isPrivate;
  bool _savingPrivacy = false;

  @override
  void initState() {
    super.initState();
    // P15-FE — keep the saved-account row (name/photo) in sync.
    final u = widget.user;
    final photo = u.profilePhoto;
    AccountManager.instance.updateCurrentProfile(
      username: u.username,
      displayName: '${u.firstName} ${u.lastName}'.trim(),
      photo: photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo'),
    );
  }

  Future<void> _togglePrivate(bool value) async {
    final previous = _isPrivate;
    setState(() {
      _isPrivate = value;
      _savingPrivacy = true;
    });
    try {
      await ApiService.updateProfile(isPrivate: value);
      if (!mounted) return;
      setState(() => _savingPrivacy = false);
      widget.onPrivacyChanged?.call(value);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isPrivate = previous;
        _savingPrivacy = false;
      });
      lsSnack(context, e.toString(), error: true);
    }
  }

  Future<void> _logout(AppLocalizations t) async {
    final cs = Theme.of(context).colorScheme;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(t.settingsLogout),
        content: Text(t.settingsLogoutConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(t.settingsLogout, style: TextStyle(color: cs.error)),
          ),
        ],
      ),
    );
    if (confirm != true) return;
    // P15-FE — logs out ONLY this account. If another saved account is
    // still valid we land in it; otherwise the login screen. (Per-user
    // cache clearing now happens in AccountManager's leave hooks.)
    final switched = await AccountManager.instance.logoutCurrent();
    navigatorKey.currentState?.pushNamedAndRemoveUntil(switched ? '/home' : '/login', (_) => false);
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return AnimatedBuilder(
      animation: Listenable.merge([ThemeService.instance.themeMode, LanguageService.instance.locale]),
      builder: (context, _) => Scaffold(
        backgroundColor: lsBg(context),
        appBar: lsAppBar(context, title: t.settingsTitle),
        body: ListView(
          padding: const EdgeInsets.symmetric(vertical: 12),
          children: [
            _accountHeader(cs),
            const SizedBox(height: 22),

            // P15-FE — account switching
            LsSectionHead(title: t.acctSectionTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: AnimatedBuilder(
                animation: AccountManager.instance,
                builder: (context, _) => _navTile(
                  cs,
                  icon: Icons.switch_account_rounded,
                  label: t.acctSwitchAccount,
                  subtitle: t.acctCountSubtitle(AccountManager.instance.accounts.length, AccountManager.maxAccounts),
                  onTap: () => showAccountSwitcherSheet(context),
                ),
              ),
            ),
            const SizedBox(height: 22),

            LsSectionHead(title: t.helpFeedbackTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: _navTile(
                cs,
                icon: Icons.support_agent_rounded,
                label: t.helpFeedbackTitle,
                subtitle: t.helpFeedbackSub,
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const HelpCenterScreen())),
              ),
            ),
            const SizedBox(height: 22),

            LsSectionHead(title: t.settingsAccountSection),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                _navTile(
                  cs,
                  icon: Icons.person_outline_rounded,
                  label: t.editProfileButton,
                  subtitle: t.settingsEditProfileSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => EditProfileScreen(user: widget.user)),
                  ),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _switchRow(
                  cs,
                  icon: Icons.lock_outline_rounded,
                  label: t.settingsPrivateAccount,
                  subtitle: t.settingsPrivateAccountSub,
                  value: _isPrivate,
                  saving: _savingPrivacy,
                  onChanged: _togglePrivate,
                ),
              ]),
            ),

            const SizedBox(height: 22),
            // 🔧 TASK 10 — this used to live one tap away, inside the
            // profile screen's (⋯) bottom-sheet menu, before that menu was
            // removed in favour of jumping straight to Settings. It now
            // lives here as an ordinary sub-item instead of disappearing.
            //
            // 🔧 TASK 11 — "Saved Posts" used to sit right above this in
            // the same section (added by Task 10). It's gone now: the
            // profile screen already has its own dedicated "Saved" segmented
            // tab (Instagram-style, inside profile.dart's own grid) that
            // does the exact same job, so this menu-level shortcut was a
            // second path to the same posts, not a second feature — one
            // menu slot doing double duty for no reason. Removing it is
            // what frees the slot Parent Access now has to itself. Nothing
            // a person could already save/view is gone: the bookmark icon
            // on posts and the profile's "Saved" tab are both untouched.
            LsSectionHead(title: t.settingsGeneralSection),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                _navTile(
                  cs,
                  icon: Icons.family_restroom_rounded,
                  label: t.parentAccessTitle,
                  subtitle: t.settingsParentAccessSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const ManageParentAccessScreen()),
                  ),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                // Task G12 (growth list) — referral program now reachable
                // from Settings too, not just the Profile-header row and
                // the Tuition Class module.
                _navTile(
                  cs,
                  icon: Icons.card_giftcard_rounded,
                  label: t.inviteEarn,
                  subtitle: t.settingsInviteEarnSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const ReferralsScreen()),
                  ),
                ),
              ]),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.settingsActivitySection),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                // P14-FE — time spent (7 days), liked / comments / saved.
                _navTile(
                  cs,
                  icon: Icons.insights_rounded,
                  label: t.settingsYourActivity,
                  subtitle: t.settingsYourActivitySub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const ActivityScreen()),
                  ),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _navTile(
                  cs,
                  icon: Icons.bar_chart_rounded,
                  label: t.settingsYourWeek,
                  subtitle: t.settingsYourWeekSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const WeeklyRecapScreen()),
                  ),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                // TASK G7 (growth_and_feature_tasks.md — Leaderboards) —
                // the app-wide engagement board. Test-series/campus-
                // section boards are reached contextually (test result
                // screen / section academics screen) since those need a
                // scope_id; this one has none, so it belongs somewhere
                // global — same "Activity" section as the recap above.
                _navTile(
                  cs,
                  icon: Icons.leaderboard_rounded,
                  label: t.settingsLeaderboard,
                  subtitle: t.settingsLeaderboardSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => LeaderboardScreen(
                        scope: LeaderboardScope.engagement,
                        title: t.settingsLeaderboard,
                      ),
                    ),
                  ),
                ),
              ]),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.settingsPrivacySection),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                _navTile(
                  cs,
                  icon: Icons.visibility_outlined,
                  label: t.settingsReadReceipts,
                  subtitle: t.settingsReadReceiptsSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const ReadReceiptPrivacyScreen()),
                  ),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _navTile(
                  cs,
                  icon: Icons.block_rounded,
                  label: t.settingsBlockedAccounts,
                  subtitle: t.settingsBlockedAccountsSub,
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const BlockedAccountsScreen()),
                  ),
                ),
              ]),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.notificationSettingsTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: _navTile(
                cs,
                icon: Icons.notifications_outlined,
                label: t.notificationSettingsTitle,
                subtitle: t.settingsNotificationsSub,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const NotificationSettingsScreen()),
                ),
              ),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.settingsSecuritySection),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: _navTile(
                cs,
                icon: Icons.password_rounded,
                label: t.settingsChangePassword,
                subtitle: t.settingsChangePasswordSub,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const ChangePasswordScreen()),
                ),
              ),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.languageSectionTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                _radioTile(
                  cs,
                  label: t.languageEnglish,
                  selected: LanguageService.instance.locale.value.languageCode == 'en',
                  onTap: () => LanguageService.instance.setLocale(const Locale('en')),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _radioTile(
                  cs,
                  label: t.languageHindi,
                  selected: LanguageService.instance.locale.value.languageCode == 'hi',
                  onTap: () => LanguageService.instance.setLocale(const Locale('hi')),
                ),
              ]),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.themeSectionTitle),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: Column(children: [
                _themeTile(
                  cs,
                  label: t.themeSystem,
                  icon: Icons.brightness_auto_rounded,
                  selected: ThemeService.instance.themeMode.value == ThemeMode.system,
                  onTap: () => ThemeService.instance.setThemeMode(ThemeMode.system),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _themeTile(
                  cs,
                  label: t.themeLight,
                  icon: Icons.light_mode_rounded,
                  selected: ThemeService.instance.themeMode.value == ThemeMode.light,
                  onTap: () => ThemeService.instance.setThemeMode(ThemeMode.light),
                ),
                Divider(height: 1, color: cs.outlineVariant),
                _themeTile(
                  cs,
                  label: t.themeDark,
                  icon: Icons.dark_mode_rounded,
                  selected: ThemeService.instance.themeMode.value == ThemeMode.dark,
                  onTap: () => ThemeService.instance.setThemeMode(ThemeMode.dark),
                ),
              ]),
            ),

            const SizedBox(height: 22),
            LsSectionHead(title: t.textSizeSectionTitle),
            ValueListenableBuilder<FontScaleStep>(
              valueListenable: AccessibilityService.instance.fontScale,
              builder: (context, current, _) {
                final labels = <FontScaleStep, String>{
                  FontScaleStep.small: t.textSizeSmall,
                  FontScaleStep.normal: t.textSizeNormal,
                  FontScaleStep.large: t.textSizeLarge,
                  FontScaleStep.xlarge: t.textSizeXLarge,
                };
                return LsCard(
                  margin: const EdgeInsets.symmetric(horizontal: kLsPad),
                  padding: EdgeInsets.zero,
                  child: Column(children: [
                    for (final step in FontScaleStep.values) ...[
                      _themeTile(
                        cs,
                        label: labels[step]!,
                        icon: Icons.text_fields_rounded,
                        selected: step == current,
                        onTap: () => AccessibilityService.instance.setFontScale(step),
                      ),
                      Divider(height: 1, color: cs.outlineVariant),
                    ],
                    // Live preview: this text already scales with the chosen step.
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(
                        t.textSizePreview,
                        style: TextStyle(fontSize: 14, color: cs.onSurfaceVariant),
                      ),
                    ),
                  ]),
                );
              },
            ),

            const SizedBox(height: 22),
            LsCard(
              margin: const EdgeInsets.symmetric(horizontal: kLsPad),
              padding: EdgeInsets.zero,
              child: InkWell(
                onTap: () => _logout(t),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
                  child: Row(children: [
                    Icon(Icons.logout_rounded, size: 18, color: cs.error),
                    const SizedBox(width: 12),
                    Text(t.settingsLogout, style: TextStyle(fontSize: 13.5, color: cs.error, fontWeight: FontWeight.w700)),
                  ]),
                ),
              ),
            ),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }

  /// Instagram-style small identity strip at the top of Settings — avatar
  /// + name/username, tap to jump straight into Edit Profile. Purely a
  /// visual/nav convenience (no new data — everything here already comes
  /// from `widget.user`, same object the rest of this screen already had).
  Widget _accountHeader(ColorScheme cs) {
    final photo = widget.user.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    final fullName = '${widget.user.firstName} ${widget.user.lastName}'.trim();

    return GestureDetector(
      behavior: HitTestBehavior.translucent,
      onLongPress: () => showAccountSwitcherSheet(context), // P15-FE
      child: LsCard(
      margin: const EdgeInsets.symmetric(horizontal: kLsPad),
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => EditProfileScreen(user: widget.user)),
      ),
      child: Row(children: [
        Container(
          width: 52,
          height: 52,
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1.5)),
          child: ClipOval(
            child: photo.isEmpty
                ? Container(
                    color: cs.surfaceVariant,
                    child: Icon(Icons.person_rounded, size: 26, color: cs.onSurfaceVariant),
                  )
                : CachedNetworkImage(
                    imageUrl: url,
                    width: 48,
                    height: 48,
                    fit: BoxFit.cover,
                    placeholder: (c, u) => Container(color: cs.surfaceVariant),
                    errorWidget: (c, u, e) => Container(
                      color: cs.surfaceVariant,
                      child: Icon(Icons.person_rounded, size: 26, color: cs.onSurfaceVariant),
                    ),
                  ),
          ),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              fullName.isEmpty ? widget.user.username : fullName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: cs.onSurface),
            ),
            const SizedBox(height: 2),
            Text(
              '@${widget.user.username}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
            ),
          ]),
        ),
        Icon(Icons.chevron_right_rounded, size: 20, color: cs.outline),
      ]),
    ),
    );
  }

  Widget _navTile(
    ColorScheme cs, {
    required IconData icon,
    required String label,
    String? subtitle,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(children: [
          Icon(icon, size: 18, color: cs.onSurfaceVariant),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
              if (subtitle != null) ...[
                const SizedBox(height: 2),
                Text(subtitle, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
              ],
            ]),
          ),
          Icon(Icons.chevron_right_rounded, size: 20, color: cs.outline),
        ]),
      ),
    );
  }

  Widget _switchRow(
    ColorScheme cs, {
    required IconData icon,
    required String label,
    required String subtitle,
    required bool value,
    required bool saving,
    required ValueChanged<bool> onChanged,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(children: [
        Icon(icon, size: 18, color: cs.onSurfaceVariant),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
            Text(subtitle, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          ]),
        ),
        if (saving)
          SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary),
          )
        else
          Switch(value: value, onChanged: onChanged),
      ]),
    );
  }

  Widget _radioTile(ColorScheme cs, {required String label, required bool selected, required VoidCallback onTap}) {
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

  Widget _themeTile(
    ColorScheme cs, {
    required String label,
    required IconData icon,
    required bool selected,
    required VoidCallback onTap,
  }) {
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
