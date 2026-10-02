import 'dart:convert';

// ============================================================
// P7-FE — one entry of `User.links` (backend P6-BE):
//   up to 3 × {"title": str (<=40), "url": absolute http(s) str (<=200)}
// ============================================================

const int kMaxProfileLinks = 3;
const int kProfileLinkTitleMax = 40;
const int kProfileLinkUrlMax = 200;

class ProfileLink {
  final String title;
  final String url;
  const ProfileLink({required this.title, required this.url});

  factory ProfileLink.fromJson(Map<String, dynamic> j) =>
      ProfileLink(title: (j['title'] ?? '').toString(), url: (j['url'] ?? '').toString());

  Map<String, String> toJson() => {'title': title, 'url': url};

  /// Tolerant parse of the API value: a List, or (defensively) a JSON string, or null.
  static List<ProfileLink> listFrom(dynamic raw) {
    dynamic v = raw;
    if (v is String && v.trim().isNotEmpty) {
      try {
        v = jsonDecode(v);
      } catch (_) {
        return const [];
      }
    }
    if (v is! List) return const [];
    return [
      for (final e in v)
        if (e is Map && (e['url'] ?? '').toString().isNotEmpty)
          ProfileLink.fromJson(Map<String, dynamic>.from(e)),
    ];
  }

  @override
  bool operator ==(Object other) => other is ProfileLink && other.title == title && other.url == url;

  @override
  int get hashCode => Object.hash(title, url);
}

/// Client-side mirror of the backend rule (login.models.clean_profile_links), so the
/// editor can show the error next to the field instead of after a failed save.
/// Returns null when valid. A bare "example.com" is accepted here because
/// [normalizeLinkUrl] turns it into https://example.com before saving.
String? validateLinkUrl(String? raw) {
  final v = normalizeLinkUrl(raw ?? '');
  if (v.isEmpty) return null; // emptiness is handled by the row-level check
  if (v.length > kProfileLinkUrlMax) return 'URL is too long (max $kProfileLinkUrlMax).';
  if (RegExp(r'\s').hasMatch(v)) return 'URL cannot contain spaces.';
  final uri = Uri.tryParse(v);
  if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https') || uri.host.isEmpty || !uri.host.contains('.')) {
    return 'Enter a valid http:// or https:// URL.';
  }
  return null;
}

/// "example.com/x" -> "https://example.com/x". Anything that already has a scheme is
/// left alone (so `javascript:` etc. still fails validation instead of being "fixed").
String normalizeLinkUrl(String raw) {
  final v = raw.trim();
  if (v.isEmpty) return v;
  // Has a scheme ("https:", "javascript:") => leave alone. "example.com:8080" is host:port, not a scheme.
  if (RegExp(r'^[a-zA-Z][a-zA-Z0-9+.-]*:(//|[^0-9])').hasMatch(v)) return v;
  return 'https://$v';
}
