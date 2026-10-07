import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'ls_ui.dart';

// ============================================================
// P13-FE — Achievements / badges UI (shared by profile.dart and
// target_profile.dart). Backend: P13-BE, `GET /profile/<username>/badges/`.
//
//   * `ProfileBadgesRow`        — "Badges" header + top-3 chips + "All" link
//   * `showAllBadgesSheet()`    — bottom sheet with every earned badge
//   * `BadgeCelebration`        — "new badge" popup, own profile only
//
// ⚠️ ONE LINE TO WIRE: `BadgeService.fetchJson` below. It must call the
// endpoint through the same authenticated client the rest of the app uses
// (ApiService's Bearer-token GET) — see the note on it.
// ============================================================

class UserBadge {
  final String code;
  final String title;
  final String description;
  final String icon;
  final String ruleType;
  final DateTime? earnedAt;

  const UserBadge({
    required this.code,
    required this.title,
    required this.description,
    required this.icon,
    required this.ruleType,
    required this.earnedAt,
  });

  factory UserBadge.fromJson(Map<String, dynamic> j) => UserBadge(
        code: (j['code'] ?? '').toString(),
        title: (j['title'] ?? '').toString(),
        description: (j['description'] ?? '').toString(),
        icon: (j['icon'] ?? '').toString(),
        ruleType: (j['rule_type'] ?? '').toString(),
        earnedAt: DateTime.tryParse((j['earned_at'] ?? '').toString())?.toLocal(),
      );

  /// Emoji / short text for the badge circle (backend `icon` is an emoji or
  /// an asset name — an asset name can't render as text, so fall back).
  String get glyph => (icon.isNotEmpty && icon.runes.length <= 4) ? icon : '🏅';
}

class BadgeService {
  /// Point this at the app's authenticated GET helper. It receives the
  /// username and must return the decoded JSON body of
  /// `GET <api-prefix>/profile/<username>/badges/`
  /// (`{"username", "count", "badges": [...]}`), throwing on a non-2xx.
  /// Example, once ApiService has a generic authed getter:
  ///   BadgeService.fetchJson = (u) => ApiService.getJson('/profile/$u/badges/');
  static Future<dynamic> Function(String username) fetchJson = (username) {
    throw StateError('BadgeService.fetchJson is not wired to the authed API client yet');
  };

  /// Newest first. Never throws — a failed call is "no badges to show",
  /// same silent-fail contract as the profile's other secondary loaders.
  static Future<List<UserBadge>> fetch(String username) async {
    try {
      final body = await fetchJson(username);
      final list = (body is Map ? body['badges'] : null) as List? ?? const [];
      return list
          .whereType<Map>()
          .map((e) => UserBadge.fromJson(Map<String, dynamic>.from(e)))
          .toList();
    } catch (_) {
      return const [];
    }
  }
}

String _fmtDate(DateTime? d) {
  if (d == null) return '';
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${d.day} ${m[d.month - 1]} ${d.year}';
}

Widget _badgeCircle(ColorScheme cs, UserBadge b, {double size = 48}) {
  return Container(
    width: size,
    height: size,
    alignment: Alignment.center,
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      color: cs.primaryContainer.withOpacity(.55),
      border: Border.all(color: cs.primary.withOpacity(.35), width: 1.5),
    ),
    child: Text(b.glyph, style: TextStyle(fontSize: size * .46)),
  );
}

// ------------------------------------------------------------
// Profile row: top 3 + "All badges"
// ------------------------------------------------------------
class ProfileBadgesRow extends StatelessWidget {
  final List<UserBadge> badges;
  final String ownerName; // shown in the sheet title on someone else's profile
  final bool isOwner;

  const ProfileBadgesRow({
    super.key,
    required this.badges,
    required this.ownerName,
    this.isOwner = false,
  });

  @override
  Widget build(BuildContext context) {
    if (badges.isEmpty) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;
    final top = badges.take(3).toList();

    return Semantics(
      button: true,
      label: '${badges.length} badges, tap to see all',
      child: InkWell(
        borderRadius: BorderRadius.circular(kLsRadius),
        onTap: () => showAllBadgesSheet(context, badges, ownerName: ownerName, isOwner: isOwner),
        child: Container(
          padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: BorderRadius.circular(kLsRadius),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Row(children: [
            Expanded(
              child: Row(children: [
                for (final b in top)
                  Expanded(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      _badgeCircle(cs, b, size: 38),
                      const SizedBox(height: 4),
                      Text(
                        b.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.center,
                        style: LsType.caption(context, size: 10.5, weight: FontWeight.w600, color: cs.onSurface),
                      ),
                    ]),
                  ),
                // Keep 1–2 badges left-aligned at the same width as a full row.
                for (var i = top.length; i < 3; i++) const Expanded(child: SizedBox.shrink()),
              ]),
            ),
            Column(mainAxisSize: MainAxisSize.min, children: [
              Text('All', style: LsType.caption(context, weight: FontWeight.w700, color: cs.primary)),
              Text('${badges.length}', style: LsType.caption(context, weight: FontWeight.w400, color: cs.onSurfaceVariant)),
            ]),
            Icon(Icons.chevron_right_rounded, size: 18, color: cs.onSurfaceVariant),
          ]),
        ),
      ),
    );
  }
}

// ------------------------------------------------------------
// "All badges" sheet
// ------------------------------------------------------------
Future<void> showAllBadgesSheet(
  BuildContext context,
  List<UserBadge> badges, {
  required String ownerName,
  bool isOwner = false,
}) {
  final cs = Theme.of(context).colorScheme;
  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: .6,
      minChildSize: .35,
      maxChildSize: .92,
      builder: (ctx, controller) => SafeArea(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 6),
            child: Text(
              isOwner ? 'Your badges (${badges.length})' : '@$ownerName\'s badges (${badges.length})',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: cs.onSurface),
            ),
          ),
          Expanded(
            child: ListView.separated(
              controller: controller,
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
              itemCount: badges.length,
              separatorBuilder: (_, __) => const SizedBox(height: 14),
              itemBuilder: (_, i) {
                final b = badges[i];
                return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _badgeCircle(cs, b, size: 52),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(b.title, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: cs.onSurface)),
                      if (b.description.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(b.description, style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
                      ],
                      if (b.earnedAt != null) ...[
                        const SizedBox(height: 4),
                        Text('Earned ${_fmtDate(b.earnedAt)}',
                            style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant.withOpacity(.8))),
                      ],
                    ]),
                  ),
                ]);
              },
            ),
          ),
        ]),
      ),
    ),
  );
}

// ------------------------------------------------------------
// Celebration popup — own profile only
// ------------------------------------------------------------
class BadgeCelebration {
  static const _prefsKey = 'seen_badge_codes_v1';

  /// Call with the freshly loaded list of MY badges. Shows one small popup
  /// per badge that wasn't in the previously-seen set (max 3 per call).
  ///
  /// Seen-state is local (the API has no "seen" flag). The very first call
  /// on a device only records a baseline and shows nothing, so existing
  /// users — and everyone the backend's silent backfill awarded — don't get
  /// a burst of popups for old achievements.
  static Future<void> checkAndShow(BuildContext context, List<UserBadge> mine) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final stored = prefs.getStringList(_prefsKey);
      final current = mine.map((b) => b.code).toSet();

      if (stored == null) {
        await prefs.setStringList(_prefsKey, current.toList());
        return;
      }
      final fresh = mine.where((b) => !stored.contains(b.code)).toList();
      if (fresh.isEmpty) return;

      // Record BEFORE showing: if the popup is interrupted the user isn't
      // celebrated twice for the same badge.
      await prefs.setStringList(_prefsKey, {...stored, ...current}.toList());

      for (final b in fresh.take(3)) {
        if (!context.mounted) return;
        await showDialog<void>(
          context: context,
          builder: (_) => _CelebrationDialog(badge: b),
        );
      }
    } catch (_) {
      // Purely cosmetic — never let it surface as an error.
    }
  }
}

class _CelebrationDialog extends StatelessWidget {
  final UserBadge badge;
  const _CelebrationDialog({required this.badge});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Dialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 22, 24, 16),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text('🎉  New badge!  🎉',
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: cs.primary)),
          const SizedBox(height: 14),
          TweenAnimationBuilder<double>(
            tween: Tween(begin: .3, end: 1),
            duration: const Duration(milliseconds: 600),
            curve: Curves.elasticOut,
            builder: (_, v, child) => Transform.scale(scale: v, child: child),
            child: _badgeCircle(cs, badge, size: 84),
          ),
          const SizedBox(height: 14),
          Text(badge.title,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: cs.onSurface)),
          if (badge.description.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(badge.description,
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant)),
          ],
          const SizedBox(height: 12),
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Nice!')),
        ]),
      ),
    );
  }
}
