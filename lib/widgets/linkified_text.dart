import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'app_link_handlers.dart';

// ============================================================
// P7-FE — LinkifiedText: text where  @username  #hashtag  and  http(s):// / www. URLs
// are tappable. Built for bios now, meant for feed captions / comments next.
//
//   LinkifiedText(user.bio, style: TextStyle(fontSize: 12.5))
//
// Defaults (override any via the on*Tap params):
//   @name  -> that user's profile           (app_link_handlers.dart)
//   #tag   -> hashtag posts screen          (app_link_handlers.dart)
//   URL    -> external browser (http/https only)
// ============================================================

enum LinkKind { text, url, mention, hashtag }

class LinkSegment {
  final LinkKind kind;

  /// Exactly what is shown (includes the leading @ / # for mentions / hashtags).
  final String text;

  /// What a tap acts on: username (no @), tag (no #), or absolute URL (https:// added to www.).
  final String value;
  const LinkSegment(this.kind, this.text, [this.value = '']);

  @override
  String toString() => '${kind.name}($text)';
}

// One pass, alternation order matters: a URL is matched first so an `@` or `#` INSIDE a
// URL (user@host paths, #fragments) is never split out as a mention / hashtag.
//  - mention: not preceded by a letter/digit/_ . @  => "me@mail.com" is not a mention
//  - hashtag: \p{M} keeps Devanagari matras (e.g. #परीक्षा) inside the tag; same set the
//    backend's Python `\w` accepts. Digits-only ("#1") stays plain text.
final RegExp _linkRe = RegExp(
  r'(https?://[^\s<>]+|www\.[^\s<>]+)'
  r'|(?<![\p{L}\p{N}_.@])@([A-Za-z0-9_.]{1,30})'
  r'|(?<![\p{L}\p{N}_&#])#([\p{L}\p{M}\p{N}_]+)',
  unicode: true,
);

const String _urlTrailing = '.,;:!?\'"';

/// Pure function (unit-testable): splits [input] into text / url / mention / hashtag runs.
List<LinkSegment> parseLinkified(String input) {
  final out = <LinkSegment>[];
  void addText(String t) {
    if (t.isEmpty) return;
    if (out.isNotEmpty && out.last.kind == LinkKind.text) {
      out[out.length - 1] = LinkSegment(LinkKind.text, out.last.text + t);
    } else {
      out.add(LinkSegment(LinkKind.text, t));
    }
  }

  var last = 0;
  for (final m in _linkRe.allMatches(input)) {
    addText(input.substring(last, m.start));
    last = m.end;

    if (m.group(1) != null) {
      var url = m.group(1)!;
      // "visit https://x.com." / "(https://x.com)" — punctuation that ends the
      // sentence is not part of the link.
      var trailing = '';
      while (url.isNotEmpty) {
        final ch = url[url.length - 1];
        // a ')' is trailing only if it has no '(' partner inside the URL (wikipedia links keep theirs)
        final unmatchedParen = ch == ')' && ')'.allMatches(url).length > '('.allMatches(url).length;
        if (_urlTrailing.contains(ch) || unmatchedParen) {
          trailing = ch + trailing;
          url = url.substring(0, url.length - 1);
        } else {
          break;
        }
      }
      final bare = url.replaceFirst(RegExp(r'^(https?://|www\.)'), '');
      if (bare.isEmpty) {
        addText(m.group(0)!); // just "http://" — not a link
        continue;
      }
      out.add(LinkSegment(LinkKind.url, url, url.startsWith('www.') ? 'https://$url' : url));
      addText(trailing);
    } else if (m.group(2) != null) {
      var name = m.group(2)!;
      var trailing = '';
      while (name.endsWith('.')) {
        name = name.substring(0, name.length - 1);
        trailing += '.';
      }
      if (name.isEmpty) {
        addText(m.group(0)!);
        continue;
      }
      out.add(LinkSegment(LinkKind.mention, '@$name', name));
      addText(trailing);
    } else {
      final tag = m.group(3)!;
      if (RegExp(r'^[0-9_]+$').hasMatch(tag)) {
        addText(m.group(0)!); // "#1", "#2024" — a number, not a hashtag
        continue;
      }
      out.add(LinkSegment(LinkKind.hashtag, '#$tag', tag));
    }
  }
  addText(input.substring(last));
  return out;
}

/// Opens an http/https URL in the external browser; shows a SnackBar if it can't.
Future<void> openExternalUrl(BuildContext context, String url) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  final uri = Uri.tryParse(url.trim());
  if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https') || uri.host.isEmpty) {
    messenger?.showSnackBar(const SnackBar(content: Text("Can't open this link.")));
    return;
  }
  try {
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok) messenger?.showSnackBar(const SnackBar(content: Text("Can't open this link.")));
  } catch (_) {
    messenger?.showSnackBar(const SnackBar(content: Text("Can't open this link.")));
  }
}

class LinkifiedText extends StatefulWidget {
  final String text;
  final TextStyle? style;

  /// Style of the tappable runs. Default: [style] + primary colour + semi-bold.
  final TextStyle? linkStyle;
  final int? maxLines;
  final TextOverflow overflow;
  final TextAlign textAlign;

  final ValueChanged<String>? onMentionTap; // username, no '@'
  final ValueChanged<String>? onHashtagTap; // tag, no '#'
  final ValueChanged<String>? onUrlTap; // absolute URL

  const LinkifiedText(
    this.text, {
    super.key,
    this.style,
    this.linkStyle,
    this.maxLines,
    this.overflow = TextOverflow.clip,
    this.textAlign = TextAlign.start,
    this.onMentionTap,
    this.onHashtagTap,
    this.onUrlTap,
  });

  @override
  State<LinkifiedText> createState() => _LinkifiedTextState();
}

class _LinkifiedTextState extends State<LinkifiedText> {
  late List<LinkSegment> _segments;
  // One recognizer per tappable segment. They MUST be disposed (leak otherwise), so they
  // are owned here and rebuilt only when the text changes — not on every build().
  List<TapGestureRecognizer?> _recognizers = const [];

  @override
  void initState() {
    super.initState();
    _parse();
  }

  @override
  void didUpdateWidget(covariant LinkifiedText old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) _parse();
  }

  @override
  void dispose() {
    _disposeRecognizers();
    super.dispose();
  }

  void _disposeRecognizers() {
    for (final r in _recognizers) {
      r?.dispose();
    }
    _recognizers = const [];
  }

  void _parse() {
    _disposeRecognizers();
    _segments = parseLinkified(widget.text);
    _recognizers = [
      for (final s in _segments)
        s.kind == LinkKind.text ? null : (TapGestureRecognizer()..onTap = () => _handleTap(s)),
    ];
  }

  // Reads widget.* at TAP time, so changing a callback never needs a re-parse.
  void _handleTap(LinkSegment s) {
    if (!mounted) return;
    switch (s.kind) {
      case LinkKind.mention:
        (widget.onMentionTap ?? (u) => openMentionProfile(context, u))(s.value);
      case LinkKind.hashtag:
        (widget.onHashtagTap ?? (t) => openHashtagFeed(context, t))(s.value);
      case LinkKind.url:
        (widget.onUrlTap ?? (u) => openExternalUrl(context, u))(s.value);
      case LinkKind.text:
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final base = DefaultTextStyle.of(context).style.merge(widget.style);
    final link = widget.linkStyle ?? base.copyWith(color: cs.primary, fontWeight: FontWeight.w600);

    return Text.rich(
      TextSpan(
        style: base,
        children: [
          for (var i = 0; i < _segments.length; i++)
            TextSpan(
              text: _segments[i].text,
              style: _segments[i].kind == LinkKind.text ? null : link,
              recognizer: _recognizers[i],
            ),
        ],
      ),
      maxLines: widget.maxLines,
      overflow: widget.overflow,
      textAlign: widget.textAlign,
    );
  }
}
