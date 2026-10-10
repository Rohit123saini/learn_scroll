// [T4 §A-§D] Light models for the participants / control-panel APIs.
// Backend: campus/participants.py, campus/panel.py, campus/roster.py.

class ParticipantRow {
  final String category; // admin | principal_hod | moderator | class_teacher | subject_teacher | non_teaching | student | parent
  final String id;
  final String username;
  final String name;
  final String? departmentName;
  final String? className;
  final String? sectionName;
  final String rollNumber;
  final String? childUsername; // parents only

  const ParticipantRow({
    required this.category,
    required this.id,
    required this.username,
    required this.name,
    this.departmentName,
    this.className,
    this.sectionName,
    this.rollNumber = '',
    this.childUsername,
  });

  factory ParticipantRow.fromJson(Map<String, dynamic> j) {
    final u = Map<String, dynamic>.from((j['user'] ?? const {}) as Map);
    final full = '${u['first_name'] ?? ''} ${u['last_name'] ?? ''}'.trim();
    String? nameOf(dynamic m) => m is Map ? m['name']?.toString() : null;
    return ParticipantRow(
      category: (j['category'] ?? '').toString(),
      id: j['id'].toString(),
      username: (u['username'] ?? '').toString(),
      name: full.isEmpty ? (u['username'] ?? '').toString() : full,
      departmentName: nameOf(j['department']),
      className: nameOf(j['school_class']),
      sectionName: nameOf(j['section']),
      rollNumber: (j['roll_number'] ?? '').toString(),
      childUsername: j['child'] is Map ? (j['child']['username'] ?? '').toString() : null,
    );
  }
}

class ParticipantsPage {
  final int count;
  final bool hasNext;
  final List<ParticipantRow> rows;
  const ParticipantsPage({required this.count, required this.hasNext, required this.rows});

  factory ParticipantsPage.fromJson(Map<String, dynamic> j) => ParticipantsPage(
        count: (j['count'] ?? 0) as int,
        hasNext: j['next'] != null,
        rows: ((j['results'] ?? const []) as List)
            .map((e) => ParticipantRow.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList(),
      );
}

/// Category keys in display order (matches backend `participants.CATEGORIES`).
const List<String> kParticipantCategories = [
  'admin',
  'principal_hod',
  'moderator',
  'class_teacher',
  'subject_teacher',
  'non_teaching',
  'student',
  'parent',
];
