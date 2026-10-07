import 'package:flutter/material.dart';

import '../profile/profile_link.dart';
import 'linkified_text.dart';

// ============================================================
// P7-FE — the "bio area" of a profile header, shared by profile.dart and
// target_profile.dart so the two can't drift apart:
//
//   Teacher · she/her                 <- category_label · pronouns
//   Bio text with @mentions, #tags    <- LinkifiedText (tap = profile / hashtag / browser)
//   [🔗 Website] [🔗 YouTube]         <- link chips (tap = browser)
//
// Renders nothing (no stray spacing) when all four inputs are empty.
// ============================================================

class ProfileBioBlock extends StatelessWidget {
  final String bio;
  final String pronouns;
  final String categoryLabel;
  final List<ProfileLink> links;

  const ProfileBioBlock({
    super.key,
    required this.bio,
    this.pronouns = '',
    this.categoryLabel = '',
    this.links = const [],
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final category = categoryLabel.trim();
    final pron = pronouns.trim();
    final hasMeta = category.isNotEmpty || pron.isNotEmpty;
    final hasBio = bio.trim().isNotEmpty;
    if (!hasMeta && !hasBio && links.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (hasMeta)
            Padding(
              padding: EdgeInsets.only(bottom: hasBio || links.isNotEmpty ? 4 : 0),
              child: Text.rich(
                TextSpan(children: [
                  if (category.isNotEmpty)
                    TextSpan(text: category, style: const TextStyle(fontWeight: FontWeight.w700)),
                  if (category.isNotEmpty && pron.isNotEmpty) const TextSpan(text: '  ·  '),
                  if (pron.isNotEmpty) TextSpan(text: pron),
                ]),
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
              ),
            ),
          if (hasBio) LinkifiedText(bio, style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
          if (links.isNotEmpty)
            Padding(
              padding: EdgeInsets.only(top: hasBio ? 8 : 2),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [for (final l in links) _LinkChip(link: l)],
              ),
            ),
        ],
      ),
    );
  }
}

class _LinkChip extends StatelessWidget {
  final ProfileLink link;
  const _LinkChip({required this.link});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Tooltip(
      message: link.url,
      child: Semantics(
        button: true,
        label: '${link.title}, link',
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () => openExternalUrl(context, link.url),
          child: Container(
            constraints: const BoxConstraints(maxWidth: 220),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: cs.primary.withOpacity(0.08),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: cs.primary.withOpacity(0.25)),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.link_rounded, size: 14, color: cs.primary),
              const SizedBox(width: 4),
              Flexible(
                child: Text(
                  link.title.isEmpty ? link.url : link.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: cs.primary),
                ),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// 4.1/4.2 — shared header controls (own profile + someone else's).
//
// ProfileActionButton: Instagram-style tonal button. Unlike `LsOutlineButton`
// (an unconstrained `Row` + `Text`), its label is `Flexible` + ellipsis, so a
// long Hindi label ("प्रोफ़ाइल संपादित करें") in a half-width slot can no longer
// throw the yellow/black RenderFlex overflow stripes.
//
// ProfileMiniChip: small pill for coins / streak / invite / weekly recap —
// replaces the four full-width cards that used to push the grid off-screen.
// ============================================================

class ProfileActionButton extends StatelessWidget {
  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool filled; // primary-colour (e.g. Follow / Confirm) vs tonal grey
  final bool loading;

  const ProfileActionButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.filled = false,
    this.loading = false,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bg = filled ? cs.primary : cs.surfaceVariant;
    final fg = filled ? cs.onPrimary : cs.onSurface;
    final enabled = onPressed != null && !loading;
    return Semantics(
      button: true,
      enabled: enabled,
      label: label,
      child: Opacity(
        opacity: onPressed == null ? .55 : 1,
        child: Material(
          color: bg,
          borderRadius: BorderRadius.circular(10),
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: enabled ? onPressed : null,
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 36),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                child: Center(
                  child: loading
                      ? SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: fg))
                      : Row(mainAxisSize: MainAxisSize.min, children: [
                          if (icon != null) ...[Icon(icon, size: 15, color: fg), const SizedBox(width: 6)],
                          Flexible(
                            child: Text(
                              label,
                              maxLines: 1,
                              softWrap: false,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: fg),
                            ),
                          ),
                        ]),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class ProfileMiniChip extends StatelessWidget {
  final Widget leading; // small icon or emoji
  final String label;
  final String? semanticLabel;
  final VoidCallback onTap;

  const ProfileMiniChip({
    super.key,
    required this.leading,
    required this.label,
    required this.onTap,
    this.semanticLabel,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = semanticLabel ?? label;
    return Semantics(
      button: true,
      label: text,
      child: Tooltip(
        message: text,
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          child: Container(
            constraints: const BoxConstraints(minHeight: 30),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: cs.surface,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: cs.outlineVariant),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              leading,
              const SizedBox(width: 5),
              Flexible(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurface),
                ),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
