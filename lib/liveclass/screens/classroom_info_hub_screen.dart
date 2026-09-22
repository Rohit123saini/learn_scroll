// ============================================================
// LIVECLASS — CLASSROOM INFO HUB (Notices / Holidays / Doubts)
//
// Backend surface used: GET/POST /notices/?classroom=, POST
// /notices/{id}/pin/, GET/POST /holidays/?classroom=, GET/POST
// /queries/?classroom=, POST /queries/{id}/answer/.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class ClassroomInfoHubScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  final bool isTeacher;
  const ClassroomInfoHubScreen({super.key, required this.api, required this.classroomId, this.isTeacher = false});

  @override
  State<ClassroomInfoHubScreen> createState() => _ClassroomInfoHubScreenState();
}

class _ClassroomInfoHubScreenState extends State<ClassroomInfoHubScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);
  List<ClassNotice> _notices = const [];
  List<dynamic> _holidays = const [];
  List<ClassQuery> _queries = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final results = await Future.wait([
      widget.api.notices(widget.classroomId),
      widget.api.holidays(widget.classroomId),
      widget.api.queries(classroomId: widget.classroomId),
    ]);
    setState(() {
      _notices = (results[0] as List).map((e) => ClassNotice.fromJson(e as Map<String, dynamic>)).toList();
      _holidays = results[1] as List;
      _queries = (results[2] as List).map((e) => ClassQuery.fromJson(e as Map<String, dynamic>)).toList();
      _loading = false;
    });
  }

  Future<void> _askQuestion() async {
    final t = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(t.askDoubtTitle),
        content: TextField(controller: ctrl, maxLines: 3),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancelCta)),
          TextButton(onPressed: () => Navigator.pop(context, true), child: Text(t.askCta)),
        ],
      ),
    );
    if (ok == true && ctrl.text.trim().isNotEmpty) {
      await widget.api.askQuery(widget.classroomId, ctrl.text.trim());
      _load();
    }
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.classroomInfoTitle),
      floatingActionButton: _tabs.index == 2
          ? FloatingActionButton(onPressed: _askQuestion, child: const Icon(Icons.add_rounded))
          : null,
      body: Column(children: [
        TabBar(
          controller: _tabs,
          onTap: (_) => setState(() {}),
          labelColor: cs.primary,
          unselectedLabelColor: cs.onSurfaceVariant,
          tabs: [Tab(text: t.noticesTab), Tab(text: t.holidaysTab), Tab(text: t.doubtsTab)],
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : TabBarView(controller: _tabs, children: [
                  _notices.isEmpty
                      ? EmptyStateWidget(title: t.noNoticesYet, icon: Icons.campaign_outlined)
                      : ListView(children: _notices.map((n) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Row(children: [
                                Expanded(child: Text(n.title, style: LsType.head(context, size: 13.5))),
                                if (n.isPinned) Icon(Icons.push_pin_rounded, size: 15, color: cs.primary),
                              ]),
                              const SizedBox(height: 4),
                              Text(n.message, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                            ]),
                          )).toList()),
                  _holidays.isEmpty
                      ? EmptyStateWidget(title: t.noHolidaysListed, icon: Icons.beach_access_outlined)
                      : ListView(children: _holidays.map((h) {
                          final m = h as Map<String, dynamic>;
                          return LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            child: LsMetaRow(icon: Icons.event_busy_rounded, label: m['title']?.toString() ?? '', value: m['date']?.toString() ?? ''),
                          );
                        }).toList()),
                  _queries.isEmpty
                      ? EmptyStateWidget(title: t.noDoubtsYet, icon: Icons.help_outline_rounded)
                      : ListView(children: _queries.map((q) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Text(q.question, style: TextStyle(fontSize: 13, color: cs.onSurface)),
                              if (q.answer != null) ...[
                                const SizedBox(height: 6),
                                Container(
                                  padding: const EdgeInsets.all(8),
                                  decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(10)),
                                  child: Text(q.answer!, style: TextStyle(fontSize: 12, color: cs.onSurface)),
                                ),
                              ] else if (widget.isTeacher)
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: TextButton(
                                    onPressed: () async {
                                      final ctrl = TextEditingController();
                                      final ok = await showDialog<bool>(
                                        context: context,
                                        builder: (context) => AlertDialog(
                                          title: Text(t.answerDoubtTitle),
                                          content: TextField(controller: ctrl, maxLines: 3),
                                          actions: [
                                            TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancelCta)),
                                            TextButton(onPressed: () => Navigator.pop(context, true), child: Text(t.sendCta)),
                                          ],
                                        ),
                                      );
                                      if (ok == true && ctrl.text.trim().isNotEmpty) {
                                        await widget.api.answerQuery(q.id, ctrl.text.trim());
                                        _load();
                                      }
                                    },
                                    child: Text(t.answerCta),
                                  ),
                                ),
                            ]),
                          )).toList()),
                ]),
        ),
      ]),
    );
  }
}
