import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import '../theme_service.dart';

// ============================================================
// LEARNSCROLL SHARED UI KIT
//
// Home, Assignments aur Test Series — teeno screens ek hi design language
// follow karti hain (`learnscroll_home_final.html`). Ye file wahi common
// pieces rakhti hai, taaki teen jagah teen alag-alag "card" definitions
// na banein aur baad me design tweak karna ek hi jagah ka kaam rahe.
//
// Yahan ek bhi hardcoded string nahi hai — har text caller se aata hai
// (l10n se). Aur ek bhi hardcoded color nahi — sab ColorScheme /
// AppThemeTokens se.
// ============================================================

/// HTML ke section paddings (`padding: 0 18px`).
const double kLsPad = 18;
const double kLsRadius = 18;

/// Heading font (HTML: `--font-head: Sora`). Body text theme ka default
/// (Inter) hai, isliye uske liye koi helper nahi chahiye.
class LsType {
  LsType._();

  static TextStyle head(BuildContext context, {double size = 14.5, Color? color, FontWeight weight = FontWeight.w700}) {
    return GoogleFonts.sora(
      fontSize: size,
      fontWeight: weight,
      color: color ?? Theme.of(context).colorScheme.onSurface,
    );
  }
}

/// `.section-title` + optional trailing link (HTML `.section-head`).
class LsSectionHead extends StatelessWidget {
  final String title;
  final String? actionLabel;
  final VoidCallback? onAction;
  final EdgeInsetsGeometry padding;

  const LsSectionHead({
    super.key,
    required this.title,
    this.actionLabel,
    this.onAction,
    this.padding = const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: padding,
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Expanded(child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context))),
        if (actionLabel != null && onAction != null)
          GestureDetector(
            onTap: onAction,
            child: Text(actionLabel!,
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.primary)),
          ),
      ]),
    );
  }
}

/// `.post-card` / `.live-card` wala surface — rounded, bordered, tinted.
class LsCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? margin;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final bool tinted;
  final Color? borderColor;

  const LsCard({
    super.key,
    required this.child,
    this.margin,
    this.padding = const EdgeInsets.all(14),
    this.onTap,
    this.tinted = false,
    this.borderColor,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final card = Container(
      margin: margin,
      decoration: BoxDecoration(
        color: tinted ? cs.surfaceVariant : cs.surface,
        border: Border.all(color: borderColor ?? cs.outlineVariant),
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      clipBehavior: Clip.antiAlias,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          child: Padding(padding: padding, child: child),
        ),
      ),
    );
    return card;
  }
}

/// Status pill — assignments ka submission status, test attempt status,
/// "LIVE" tag, "FREE"/coins badge; sab isi se bante hain.
class LsStatusChip extends StatelessWidget {
  final String label;
  final Color color;
  final IconData? icon;
  final bool solid;

  const LsStatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.solid = false,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final bg = solid ? color : color.withOpacity(isDark ? .22 : .12);
    final fg = solid ? _onColor(color) : color;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: 11, color: fg), const SizedBox(width: 4)],
        Text(label,
            style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.w700, letterSpacing: .2, color: fg)),
      ]),
    );
  }

  static Color _onColor(Color bg) =>
      ThemeData.estimateBrightnessForColor(bg) == Brightness.dark ? Colors.white : const Color(0xFF1A1625);
}

/// Primary CTA — "Start test", "Submit", "Join".
class LsPrimaryButton extends StatelessWidget {
  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final bool loading;
  final bool expanded;
  final Color? color;

  const LsPrimaryButton({
    super.key,
    required this.label,
    this.onPressed,
    this.icon,
    this.loading = false,
    this.expanded = true,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bg = color ?? cs.primary;
    final disabled = onPressed == null || loading;
    final child = Row(
      mainAxisSize: expanded ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (loading)
          SizedBox(
              width: 15,
              height: 15,
              child: CircularProgressIndicator(strokeWidth: 2, color: LsStatusChip._onColor(bg)))
        else if (icon != null)
          Icon(icon, size: 16, color: LsStatusChip._onColor(bg)),
        if (loading || icon != null) const SizedBox(width: 8),
        Flexible(
          child: Text(label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: LsStatusChip._onColor(bg))),
        ),
      ],
    );

    return Semantics(
      button: true,
      enabled: !disabled,
      label: label,
      child: Opacity(
        opacity: disabled ? .55 : 1,
        child: Material(
          color: bg,
          borderRadius: BorderRadius.circular(24),
          child: InkWell(
            onTap: disabled ? null : onPressed,
            borderRadius: BorderRadius.circular(24),
            child: Padding(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 13), child: child),
          ),
        ),
      ),
    );
  }
}

/// Secondary / outline button.
class LsOutlineButton extends StatelessWidget {
  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  const LsOutlineButton({super.key, required this.label, this.onPressed, this.icon});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      button: true,
      label: label,
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(24),
        child: InkWell(
          onTap: onPressed,
          borderRadius: BorderRadius.circular(24),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: cs.primary),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              if (icon != null) ...[Icon(icon, size: 15, color: cs.primary), const SizedBox(width: 7)],
              Text(label, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.primary)),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Filter chips row (Test Series: All / Free / Paid).
class LsFilterChips extends StatelessWidget {
  final List<String> labels;
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final EdgeInsetsGeometry padding;

  const LsFilterChips({
    super.key,
    required this.labels,
    required this.selectedIndex,
    required this.onSelected,
    this.padding = const EdgeInsets.symmetric(horizontal: kLsPad),
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      height: 40,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: padding,
        itemCount: labels.length,
        itemBuilder: (context, i) {
          final active = i == selectedIndex;
          return Center(
            child: Padding(
              padding: const EdgeInsets.only(right: 8),
              child: GestureDetector(
                onTap: () => onSelected(i),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
                  decoration: BoxDecoration(
                    color: active ? cs.primary : cs.surface,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: active ? cs.primary : cs.outlineVariant),
                  ),
                  child: Text(labels[i],
                      style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          color: active ? cs.onPrimary : cs.onSurface)),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// Label + value ki ek line (detail screens ke meta rows).
class LsMetaRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final Color? valueColor;
  const LsMetaRow({super.key, required this.icon, required this.label, required this.value, this.valueColor});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Icon(icon, size: 15, color: cs.onSurfaceVariant),
        const SizedBox(width: 9),
        Expanded(child: Text(label, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant))),
        const SizedBox(width: 10),
        Text(value,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: valueColor ?? cs.onSurface)),
      ]),
    );
  }
}

/// Bade numbers dikhane wala tile (result screen ka score).
class LsScoreTile extends StatelessWidget {
  final String value;
  final String label;
  final Color color;
  const LsScoreTile({super.key, required this.value, required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 10),
      decoration: BoxDecoration(
        color: color.withOpacity(isDark ? .18 : .10),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(children: [
        Text(value, style: GoogleFonts.sora(fontSize: 20, fontWeight: FontWeight.w700, color: color)),
        const SizedBox(height: 3),
        Text(label,
            textAlign: TextAlign.center,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
      ]),
    );
  }
}

/// Progress bar — attempt me "kitne answer ho gaye".
class LsProgressBar extends StatelessWidget {
  final double value; // 0..1
  final Color? color;
  final double height;
  const LsProgressBar({super.key, required this.value, this.color, this.height = 6});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ClipRRect(
      borderRadius: BorderRadius.circular(height),
      child: LinearProgressIndicator(
        value: value.clamp(0.0, 1.0),
        minHeight: height,
        backgroundColor: cs.surfaceVariant,
        color: color ?? cs.primary,
      ),
    );
  }
}

/// App bar jo home ke `.brand-row` jaisa hi flat dikhta hai (page bg,
/// koi elevation nahi) — dono naye modules isi ko use karte hain.
AppBar lsAppBar(BuildContext context, {required String title, List<Widget>? actions, Widget? leading}) {
  final cs = Theme.of(context).colorScheme;
  return AppBar(
    backgroundColor: lsBg(context),
    surfaceTintColor: Colors.transparent,
    elevation: 0,
    scrolledUnderElevation: 0,
    leading: leading,
    titleSpacing: leading == null ? kLsPad : 0,
    title: Text(title, style: LsType.head(context, size: 16)),
    iconTheme: IconThemeData(color: cs.onSurface),
    actions: actions,
  );
}

/// Snackbar helper — har screen me same shape.
void lsSnack(BuildContext context, String message, {bool error = false}) {
  final cs = Theme.of(context).colorScheme;
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(message, style: TextStyle(color: error ? cs.onError : null)),
      backgroundColor: error ? cs.error : null,
      behavior: SnackBarBehavior.floating,
    ));
}

/// Theme tokens ka chhota shortcut (import ek hi jagah se rahe).
AppThemeTokens lsTokens(BuildContext context) => AppThemeTokens.of(context);

/// Page background (HTML ka `--bg`).
///
/// `ColorScheme.background` jaan-boojh kar use NAHI hota — wo Flutter 3.18
/// me deprecate hua aur naye SDKs me hai hi nahi. Isliye bg hamesha
/// `AppThemeTokens` se aata hai; `Scaffold` bina `backgroundColor` diye
/// bhi theme ke `scaffoldBackgroundColor` se wahi color leta hai.
Color lsBg(BuildContext context) => AppThemeTokens.of(context).background;
