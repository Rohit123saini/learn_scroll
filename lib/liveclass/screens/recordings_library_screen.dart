// ============================================================
// LIVECLASS — RECORDINGS LIBRARY
//
// Backend surface used: GET /sessions/?classroom= (client-side filters
// to sessions with a non-empty recording_url). There's no dedicated
// "recordings/" endpoint on the backend — recording_url just lives on
// ClassSession and fills in asynchronously once LiveKit's egress
// webhook confirms the file is ready (see models.py), so a session
// with status=completed and recording_url empty just means the file
// isn't ready yet, not that it failed.
// ============================================================

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class RecordingsLibraryScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  const RecordingsLibraryScreen({super.key, required this.api, required this.classroomId});

  @override
  State<RecordingsLibraryScreen> createState() => _RecordingsLibraryScreenState();
}

class _RecordingsLibraryScreenState extends State<RecordingsLibraryScreen> {
  List<ClassSession> _recorded = const [];
  int _pendingCount = 0;
  bool _loading = true;
  Object? _error;

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
      final raw = await widget.api.sessions(classroomId: widget.classroomId);
      final all = raw.map((e) => ClassSession.fromJson(e as Map<String, dynamic>)).toList();
      setState(() {
        _recorded = all.where((s) => s.recordingUrl.isNotEmpty).toList();
        _pendingCount = all.where((s) => s.status == SessionStatus.completed && s.recordingUrl.isEmpty).length;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.recordingsTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadRecordings, retryLabel: t.retry, onRetry: _load)
                : ListView(children: [
                    if (_pendingCount > 0)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(kLsPad, 12, kLsPad, 0),
                        child: Text(t.recordingsPendingNote(_pendingCount), style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                      ),
                    if (_recorded.isEmpty)
                      EmptyStateWidget(title: t.noRecordingsYet, icon: Icons.video_library_outlined)
                    else
                      ..._recorded.map((s) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            onTap: () => launchUrl(Uri.parse(s.recordingUrl), mode: LaunchMode.externalApplication),
                            child: Row(children: [
                              Icon(Icons.play_circle_fill_rounded, color: cs.primary, size: 28),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                  Text(_formatDate(s.scheduledStart), style: LsType.head(context, size: 13)),
                                  Text(s.classroomTitle, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                                ]),
                              ),
                            ]),
                          )),
                  ]),
      ),
    );
  }

  String _formatDate(DateTime d) => '${d.day}/${d.month}/${d.year}';
}
