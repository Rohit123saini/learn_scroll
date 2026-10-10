import 'package:flutter/material.dart';

import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'doubts_screen.dart';

// ============================================================
// T4 §E/§G — MY CLASSES
//
// One card per subject-class (approved section + subject + teacher) of the
// student's (or linked child's) CURRENT section. The backend already limits
// what comes back — this screen never filters by role itself.
//
// §G rule shown here: an ONLINE class shows its time; an OFFLINE class shows
// only the "Offline class" label (the API sends no time for it).
// ============================================================

class MyClassesScreen extends StatefulWidget {
  final String? campusId;
  final String? studentId; // parent: pick one child; null = all

  const MyClassesScreen({super.key, this.campusId, this.studentId});

  @override
  State<MyClassesScreen> createState() => _MyClassesScreenState();
}

class _MyClassesScreenState extends State<MyClassesScreen> {
  bool _loading = true;
  String? _error;
  List<MyClassCard> _cards = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final cards = await CampusService.myClasses(campusId: widget.campusId, studentId: widget.studentId);
      if (!mounted) return;
      setState(() {
        _cards = cards;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  static String _two(int v) => v.toString().padLeft(2, '0');

  /// "Today, 9:00 AM" / "Tue 9:00 AM" — only ever called for ONLINE sessions.
  static String _when(DateTime t) {
    final now = DateTime.now();
    final isToday = t.year == now.year && t.month == now.month && t.day == now.day;
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    final h12 = t.hour % 12 == 0 ? 12 : t.hour % 12;
    final ampm = t.hour < 12 ? 'AM' : 'PM';
    return '${isToday ? 'Today' : days[t.weekday - 1]}, $h12:${_two(t.minute)} $ampm';
  }

  Widget _nextLine(MyClassCard c, ColorScheme cs) {
    final n = c.nextSession;
    if (n == null) {
      return Text('No class scheduled', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant));
    }
    if (n.timeHidden || n.startsAt == null) {
      return Row(children: [
        Icon(Icons.location_on_outlined, size: 14, color: cs.onSurfaceVariant),
        const SizedBox(width: 4),
        Text(n.label ?? 'Offline class', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      ]);
    }
    return Row(children: [
      Icon(Icons.videocam_outlined, size: 14, color: cs.primary),
      const SizedBox(width: 4),
      Text('Online · ${_when(n.startsAt!)}', style: TextStyle(fontSize: 12, color: cs.primary)),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: Text('My classes', style: LsType.head(context, size: 15))),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs)),
    );
  }

  Widget _body(ColorScheme cs) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 96),
        SizedBox(height: 8),
        LsSkeletonBox(height: 96),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(title: 'Could not load classes', subtitle: _error, retryLabel: 'Retry', onRetry: _load),
      ]);
    }
    if (_cards.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 60),
        EmptyStateWidget(icon: Icons.class_outlined, title: 'No classes yet'),
      ]);
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 24),
      itemCount: _cards.length,
      itemBuilder: (_, i) {
        final c = _cards[i];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: LsCard(
            padding: const EdgeInsets.all(12),
            child: InkWell(
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => DoubtsScreen(card: c)),
              ).then((_) => _load()),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text(c.subjectName, style: LsType.head(context, size: 15))),
                  LsStatusChip(
                    label: c.mode == 'online' ? 'Online' : 'Offline',
                    color: c.mode == 'online' ? cs.primary : cs.onSurfaceVariant,
                  ),
                ]),
                const SizedBox(height: 4),
                Text('${c.teacherName} · ${c.className}-${c.sectionName}',
                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                const SizedBox(height: 8),
                _nextLine(c, cs),
                const SizedBox(height: 8),
                Row(children: [
                  Icon(Icons.campaign_outlined, size: 14, color: cs.onSurfaceVariant),
                  const SizedBox(width: 4),
                  Text('${c.notices} notices', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                  const SizedBox(width: 14),
                  Icon(Icons.help_outline, size: 14, color: cs.onSurfaceVariant),
                  const SizedBox(width: 4),
                  Text('${c.doubtsOpen} open doubts',
                      style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                ]),
              ]),
            ),
          ),
        );
      },
    );
  }
}
