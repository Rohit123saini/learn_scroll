// lib/liveclass/theme/liveclass_theme.dart
//
// Shared design tokens for the LiveClass module. Pulled out so every screen
// (Assignments, Doubts, Holidays, Certificates, Coin Wallet, Home) renders
// with the same palette, spacing, card style and empty/error/loading states
// instead of each screen redefining its own local constants (which is what
// caused Assignments/Doubts to visually drift from the rest of the module).
//
// Screens should now do:
//   import '../theme/liveclass_theme.dart';
// and use LiveClassColors / LiveClassSpacing / liveClassAppBar / LiveClassCard
// / LiveClassEmptyState / LiveClassErrorState / LiveClassLoading etc. instead
// of hand-rolling their own Container+BoxDecoration+ListView boilerplate.

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

class LiveClassColors {
  LiveClassColors._();

  static const navy = Color(0xFF030F27);
  static const bg = Color(0xFFF0F2F5);
  static const gradient = LinearGradient(colors: [Color(0xFFFF6A00), Color(0xFFEE0979)]);

  static const success = Color(0xFF2E7D32);
  static const successBg = Color(0xFFE8F5E9);
  static const warning = Color(0xFFED6C02);
  static const warningBg = Color(0xFFFFF3E0);
  static const danger = Color(0xFFC62828);
  static const dangerBg = Color(0xFFFDECEA);

  static const cardShadow = BoxShadow(
    color: Color(0x0A000000),
    blurRadius: 8,
    offset: Offset(0, 2),
  );
}

class LiveClassSpacing {
  LiveClassSpacing._();
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 20.0;
  static const xxl = 24.0;
}

class LiveClassRadius {
  LiveClassRadius._();
  static const card = 14.0;
  static const chip = 10.0;
  static const sheet = 18.0;
}

/// Shared AppBar look so every LiveClass screen matches — white bar, navy
/// icons/text, hairline elevation, single-line ellipsized title.
AppBar liveClassAppBar(String title, {List<Widget>? actions, Widget? leading}) {
  return AppBar(
    backgroundColor: Colors.white,
    foregroundColor: LiveClassColors.navy,
    elevation: 0.5,
    leading: leading,
    title: Text(
      title,
      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    ),
    actions: actions,
  );
}

InputDecoration liveClassInputDecoration(String hint, {String? label}) => InputDecoration(
      hintText: hint,
      labelText: label,
      filled: true,
      fillColor: Colors.grey.shade50,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip), borderSide: BorderSide.none),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(LiveClassRadius.chip),
        borderSide: BorderSide(color: Colors.grey.shade300),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(LiveClassRadius.chip),
        borderSide: const BorderSide(color: LiveClassColors.navy, width: 1.4),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
      isDense: true,
    );

/// Card container with the consistent white/rounded/shadow treatment used
/// across every list item in the module.
class LiveClassCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;
  final VoidCallback? onTap;
  const LiveClassCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.margin = const EdgeInsets.only(bottom: LiveClassSpacing.md),
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final content = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(LiveClassRadius.card),
        boxShadow: const [LiveClassColors.cardShadow],
      ),
      child: child,
    );
    return Padding(
      padding: margin,
      child: onTap == null
          ? content
          : Material(
              color: Colors.transparent,
              borderRadius: BorderRadius.circular(LiveClassRadius.card),
              child: InkWell(
                onTap: onTap,
                borderRadius: BorderRadius.circular(LiveClassRadius.card),
                child: content,
              ),
            ),
    );
  }
}

/// Small rounded icon badge, e.g. leading icon on a list card.
class LiveClassIconBadge extends StatelessWidget {
  final IconData icon;
  final double size;
  final Gradient? gradient;
  final Color? color;
  const LiveClassIconBadge({super.key, required this.icon, this.size = 42, this.gradient, this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        gradient: gradient ?? (color == null ? LiveClassColors.gradient : null),
        color: gradient == null ? color : null,
        borderRadius: BorderRadius.circular(size * 0.28),
      ),
      child: Icon(icon, color: Colors.white, size: size * 0.48),
    );
  }
}

/// Status pill, e.g. "OPEN"/"ANSWERED", "Past due".
class LiveClassStatusChip extends StatelessWidget {
  final String label;
  final Color color;
  final Color background;
  const LiveClassStatusChip({super.key, required this.label, required this.color, required this.background});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(8)),
      child: Text(label, style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.bold, color: color, letterSpacing: 0.3)),
    );
  }
}

/// Centered empty state with icon + message (+ optional action), used
/// whenever a list loads successfully but has zero items. Wrapped in a
/// ListView so RefreshIndicator / pull-to-refresh still works over it.
class LiveClassEmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;
  const LiveClassEmptyState({
    super.key,
    this.icon = Icons.inbox_outlined,
    required this.title,
    this.subtitle,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        const SizedBox(height: 90),
        Icon(icon, size: 46, color: Colors.grey.shade400),
        const SizedBox(height: 14),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(title, textAlign: TextAlign.center, style: TextStyle(color: Colors.grey.shade700, fontSize: 14, fontWeight: FontWeight.w600)),
        ),
        if (subtitle != null) ...[
          const SizedBox(height: 4),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(subtitle!, textAlign: TextAlign.center, style: TextStyle(color: Colors.grey.shade500, fontSize: 12.5)),
          ),
        ],
        if (actionLabel != null && onAction != null) ...[
          const SizedBox(height: 16),
          Center(child: OutlinedButton(onPressed: onAction, child: Text(actionLabel!))),
        ],
      ],
    );
  }
}

/// Centered error state with icon + message + retry, used across every
/// screen that loads from the API. Wrapped in a ListView so pull-to-refresh
/// still works while an error is showing.
class LiveClassErrorState extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const LiveClassErrorState({super.key, required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        const SizedBox(height: 90),
        Icon(Icons.error_outline_rounded, size: 42, color: Theme.of(context).colorScheme.error),
        const SizedBox(height: 10),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(message, textAlign: TextAlign.center, style: const TextStyle(fontSize: 13.5)),
        ),
        const SizedBox(height: 14),
        Center(
          child: OutlinedButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh_rounded, size: 18),
            label: const Text('Retry'),
          ),
        ),
      ],
    );
  }
}

/// Full-screen centered loading spinner in the module's navy accent.
class LiveClassLoading extends StatelessWidget {
  const LiveClassLoading({super.key});
  @override
  Widget build(BuildContext context) => const Center(child: CircularProgressIndicator(color: LiveClassColors.navy));
}

// FIX (i18n / timezone audit — see utils/liveclass_datetime.dart for the
// full writeup): these used to be a hardcoded English month/weekday array
// with hand-built strings, and never called `.toLocal()` before reading
// clock fields off a `DateTime` — so every date/time in the module both
// rendered in English only *and* showed the UTC clock time rather than
// the viewer's own. Now backed by `intl`'s locale-aware `DateFormat` and
// always normalised to local time first.
//
// The optional [context] picks up the live app/device locale via
// `Localizations`; omit it and these fall back to `intl`'s default locale
// (still gets the `.toLocal()` fix either way). Kept as free functions
// (rather than requiring `LiveClassDateTime.of(context)` everywhere) so
// every existing call site across the module keeps compiling unchanged.
//
// [kLiveClassMonths]/[kLiveClassWeekdays] are kept only for source
// back-compat with any external caller still reading them directly — do
// not use them for new formatting, they're English-only by construction.
@Deprecated('English-only — use liveClassFmtDate/DateFormat instead')
const List<String> kLiveClassMonths = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
@Deprecated('English-only — use liveClassFmtDateWeekday/DateFormat instead')
const List<String> kLiveClassWeekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

String? _localeOf(BuildContext? context) => context == null ? null : Localizations.maybeLocaleOf(context)?.toString();

String liveClassFmtDate(DateTime d, [BuildContext? context]) => DateFormat.yMMMd(_localeOf(context)).format(d.toLocal());

String liveClassFmtDateWeekday(DateTime d, [BuildContext? context]) =>
    DateFormat('EEE, d MMM y', _localeOf(context)).format(d.toLocal());

String liveClassFmtDateTime(DateTime d, [BuildContext? context]) {
  final locale = _localeOf(context);
  final local = d.toLocal();
  return '${DateFormat.yMMMd(locale).format(local)} · ${DateFormat.jm(locale).format(local)}';
}