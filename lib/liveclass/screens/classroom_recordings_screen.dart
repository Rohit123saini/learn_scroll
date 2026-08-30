// lib/liveclass/screens/classroom_recordings_screen.dart
//
// Recordings Library — reached from Classroom Detail's manage sheet, same
// entry pattern as Certificates. New screen: the backend
// (ClassroomViewSet.recordings in views.py) already fully implemented this
// — a browsable, paginated list of a classroom's past recorded sessions —
// but no screen anywhere in the module ever called it, even though
// per-session `recording_url` has existed since the LiveKit egress wiring.
//
// API: GET classrooms/{id}/recordings/ (paginated — unlike
// classrooms/{id}/bans/, this one goes through the normal DRF pagination,
// same shape as every other list call in the module).
//
// Access tier on the backend is the same as Materials/Notices — teacher/
// staff/anyone who has ever held a pass (active or expired), NOT owner-
// only — but for now this is reached the same way Certificates is (via
// the manage sheet), matching that existing screen's convention rather
// than introducing a new entry point on its own.
//
// NOTE: recording_url only fills in asynchronously once LiveKit's egress
// webhook confirms the uploaded file is ready — a session that was
// recorded but hasn't finished uploading simply won't appear here yet.
//
// Opening a recording uses `url_launcher` to hand off to the device's
// video player / browser. If this package isn't already a pubspec.yaml
// dependency in this app, add: url_launcher: ^6.2.0 (or current).
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class ClassroomRecordingsScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const ClassroomRecordingsScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<ClassroomRecordingsScreen> createState() => _ClassroomRecordingsScreenState();
}

class _ClassroomRecordingsScreenState extends State<ClassroomRecordingsScreen> {
  List<SessionRecording> _recordings = [];
  bool _loading = true;
  bool _loadingMore = false;
  String? _error;
  int _page = 1;
  bool _hasMore = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _page = 1;
    });
    try {
      final res = await LiveClassApi.classrooms.recordings(widget.classroomId, page: 1);
      if (!mounted) return;
      setState(() {
        _recordings = res.results;
        _hasMore = res.next != null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load recordings.';
      });
    }
  }

  Future<void> _loadMore() async {
    if (_loadingMore || !_hasMore) return;
    setState(() => _loadingMore = true);
    try {
      final res = await LiveClassApi.classrooms.recordings(widget.classroomId, page: _page + 1);
      if (!mounted) return;
      setState(() {
        _recordings = [..._recordings, ...res.results];
        _page += 1;
        _hasMore = res.next != null;
        _loadingMore = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingMore = false);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openRecording(SessionRecording r) async {
    final uri = Uri.tryParse(r.recordingUrl);
    if (uri == null || !await canLaunchUrl(uri)) {
      _snack('Could not open this recording.');
      return;
    }
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? 'Recordings — ${widget.classroomTitle}' : 'Recordings'),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _recordings.isEmpty
                    ? const LiveClassEmptyState(
                        icon: Icons.videocam_outlined,
                        title: 'No recordings yet.',
                        subtitle: 'Recorded sessions will show up here once uploaded.',
                      )
                    : NotificationListener<ScrollNotification>(
                        onNotification: (n) {
                          if (n.metrics.pixels >= n.metrics.maxScrollExtent - 200) _loadMore();
                          return false;
                        },
                        child: ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                          itemCount: _recordings.length + (_hasMore ? 1 : 0),
                          itemBuilder: (_, i) {
                            if (i >= _recordings.length) {
                              return const Padding(
                                padding: EdgeInsets.symmetric(vertical: 16),
                                child: Center(child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy)),
                              );
                            }
                            return _recordingTile(_recordings[i]);
                          },
                        ),
                      ),
      ),
    );
  }

  Widget _recordingTile(SessionRecording r) {
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      onTap: () => _openRecording(r),
      child: Row(
        children: [
          const LiveClassIconBadge(icon: Icons.play_circle_fill_rounded),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  liveClassFmtDateTime(r.scheduledStart, context),
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5),
                ),
                if (r.classroomTitle.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(r.classroomTitle, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                ],
                if (r.actualEnd != null) ...[
                  const SizedBox(height: 2),
                  Text('Ended ${liveClassFmtDateTime(r.actualEnd!, context)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                ],
              ],
            ),
          ),
          const Icon(Icons.chevron_right_rounded, color: Colors.grey),
        ],
      ),
    );
  }
}