import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

// ============================================================
// ERROR / EMPTY STATES — Task 11.1
//
// Pehle feed fail hone par sirf `_isLoading = false` hota tha — user ko
// khaali screen dikhti thi, na message na retry. Ye do widgets har section
// me reuse hote hain.
//
// 🔧 Strings yahan hardcode NAHI ki gayi — caller `AppLocalizations` se
// translated text pass karta hai. Isse ye file l10n pe depend nahi karti
// (i.e. kisi bhi module me copy karke use ho sakti hai) aur Task 5.8 ka
// "koi hardcoded English string nahi" rule bhi nahi tootta.
// ============================================================

class ErrorStateWidget extends StatelessWidget {
  final String title;
  final String? subtitle;
  final String retryLabel;
  final VoidCallback? onRetry;
  final IconData icon;

  /// `true` = section ke andar fit hone wali chhoti version (live-now row,
  /// classroom row), `false` = poori screen wali badi version (feed).
  final bool compact;

  const ErrorStateWidget({
    super.key,
    required this.title,
    required this.retryLabel,
    this.subtitle,
    this.onRetry,
    this.icon = Icons.cloud_off_rounded,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 18, vertical: compact ? 16 : 40),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: compact ? 44 : 60,
          height: compact ? 44 : 60,
          decoration: BoxDecoration(color: cs.surfaceVariant, shape: BoxShape.circle),
          child: Icon(icon, size: compact ? 20 : 28, color: cs.onSurfaceVariant),
        ),
        SizedBox(height: compact ? 10 : 14),
        Text(
          title,
          textAlign: TextAlign.center,
          // Sora (heading font) google_fonts se — pubspec me koi 'Sora'
          // family register nahi hai, isliye plain fontFamily kaam nahi karta.
          style: GoogleFonts.sora(
            fontSize: compact ? 12.5 : 14.5,
            fontWeight: FontWeight.w700,
            color: cs.onSurface,
          ),
        ),
        if (subtitle != null) ...[
          const SizedBox(height: 5),
          Text(
            subtitle!,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurfaceVariant),
          ),
        ],
        if (onRetry != null) ...[
          SizedBox(height: compact ? 12 : 16),
          Semantics(
            button: true,
            label: retryLabel,
            child: InkWell(
              onTap: onRetry,
              borderRadius: BorderRadius.circular(20),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
                decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(20)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.refresh_rounded, size: 15, color: cs.onPrimary),
                  const SizedBox(width: 6),
                  Text(retryLabel,
                      style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onPrimary)),
                ]),
              ),
            ),
          ),
        ],
      ]),
    );
  }
}

/// Data load to ho gaya par khaali hai — error nahi hai, isliye alag widget
/// (retry button yahan galat hota: retry karne se bhi wahi khaali list
/// aayegi; user ko action chahiye, refresh nahi).
class EmptyStateWidget extends StatelessWidget {
  final String title;
  final String? subtitle;
  final IconData icon;
  final String? actionLabel;
  final VoidCallback? onAction;

  const EmptyStateWidget({
    super.key,
    required this.title,
    this.subtitle,
    this.icon = Icons.inbox_rounded,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(28, 40, 28, 40),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 60,
          height: 60,
          decoration: BoxDecoration(color: cs.surfaceVariant, shape: BoxShape.circle),
          child: Icon(icon, size: 28, color: cs.onSurfaceVariant),
        ),
        const SizedBox(height: 14),
        Text(title,
            textAlign: TextAlign.center,
            style: GoogleFonts.sora(fontSize: 14.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
        if (subtitle != null) ...[
          const SizedBox(height: 5),
          Text(subtitle!,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurfaceVariant)),
        ],
        if (actionLabel != null && onAction != null) ...[
          const SizedBox(height: 16),
          InkWell(
            onTap: onAction,
            borderRadius: BorderRadius.circular(20),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
              decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(20)),
              child: Text(actionLabel!,
                  style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onPrimary)),
            ),
          ),
        ],
      ]),
    );
  }
}
