// ============================================================
// P8-FE — models for GET profile/<username>/mutuals/ and .../similar/  (P8-BE)
//   mutuals: {"status": true, "preview": [<=3 users], "total": 7}
//   similar: {"status": true, "suggested_users": [users]}
//   user:    {id, username, first_name, last_name, profile_photo}
// ============================================================

class MiniUser {
  final int id;
  final String username;
  final String firstName;
  final String lastName;
  final String profilePhoto; // relative ("/media/..") or absolute; '' = none

  const MiniUser({
    required this.id,
    required this.username,
    this.firstName = '',
    this.lastName = '',
    this.profilePhoto = '',
  });

  factory MiniUser.fromJson(Map<String, dynamic> j) => MiniUser(
        id: (j['id'] as num?)?.toInt() ?? 0,
        username: (j['username'] ?? '').toString(),
        firstName: (j['first_name'] ?? '').toString(),
        lastName: (j['last_name'] ?? '').toString(),
        profilePhoto: (j['profile_photo'] ?? '').toString(),
      );

  String get fullName => '$firstName $lastName'.trim();

  static List<MiniUser> listFrom(dynamic raw) => raw is List
      ? [
          for (final e in raw)
            if (e is Map && (e['username'] ?? '').toString().isNotEmpty)
              MiniUser.fromJson(Map<String, dynamic>.from(e)),
        ]
      : const [];
}

class MutualFollowers {
  final List<MiniUser> preview;
  final int total;
  const MutualFollowers({this.preview = const [], this.total = 0});

  static const empty = MutualFollowers();

  factory MutualFollowers.fromJson(Map<String, dynamic> j) => MutualFollowers(
        preview: MiniUser.listFrom(j['preview']),
        total: (j['total'] as num?)?.toInt() ?? 0,
      );

  bool get isEmpty => total <= 0 || preview.isEmpty;
}
