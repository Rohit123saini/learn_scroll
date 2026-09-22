// ============================================================
// LIVECLASS — CLASSROOM DETAIL SCREEN
//
// Backend surface used: GET /classrooms/{id}/, /has-access/, /my-pass/,
// /stats/, /passes/?classroom=, /sessions/?classroom=, /materials/,
// /reviews/, /wishlist-classrooms/, /share/, /refer-link/.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';
import 'join_request_sheet.dart';
import 'live_session_screen.dart';
import 'assignments_screen.dart';
import 'classroom_info_hub_screen.dart';
import 'certificates_report_cards_screen.dart';
import 'pass_management_screen.dart';
import 'recordings_library_screen.dart';

class ClassroomDetailScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  /// Whether the signed-in user is this classroom's teacher/co-teacher —
  /// pass this from wherever this project already knows the current
  /// user's id/role (not derived here, since no auth/user context was
  /// part of what was uploaded). Gates the teacher-only menu items below;
  /// the backend re-checks permissions on every call regardless.
  final bool isTeacher;
  const ClassroomDetailScreen({super.key, required this.api, required this.classroomId, this.isTeacher = false});

  @override
  State<ClassroomDetailScreen> createState() => _ClassroomDetailScreenState();
}

class _ClassroomDetailScreenState extends State<ClassroomDetailScreen> {
  Classroom? _classroom;
  List<ClassPass> _passes = const [];
  List<ClassSession> _sessions = const [];
  List<ClassMaterial> _materials = const [];
  MyPassStatus? _myPass;
  int? _wishlistEntryId; // null = not wishlisted; the row id once it is
  Object? _error;
  bool _loading = true;
  bool _entering = false;

  /// Teacher/co-teacher/moderator/org-staff: caller-supplied flag OR what the
  /// backend's `my-pass` says (`status == "owner"`) — the backend is the
  /// source of truth, the constructor flag is only an override.
  bool get _isTeacher => widget.isTeacher || (_myPass?.isManager ?? false);

  Future<void> _enterClass() async {
    if (_entering) return;
    setState(() => _entering = true);
    try {
      // One call: joins the live session (or the next joinable one). For a
      // manager with nothing live, the backend starts an ad-hoc class.
      final res = await widget.api.startOrJoinClassroom(widget.classroomId);
      final session = res['session'];
      final sessionId = session is Map ? _asInt(session['id']) : null;
      if (!mounted) return;
      if (sessionId == null) {
        lsSnack(context, AppLocalizations.of(context)!.noUpcomingSessions, error: true);
        return;
      }
      await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => LiveSessionScreen(
          api: widget.api,
          sessionId: sessionId,
          classroomId: widget.classroomId,
          isHost: _isTeacher,
          joinInfo: res,
        ),
      ));
      if (mounted) _load();
    } on ApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _entering = false);
    }
  }

  static int? _asInt(dynamic v) => v is int ? v : int.tryParse('$v');

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
      final results = await Future.wait([
        widget.api.classroom(widget.classroomId),
        widget.api.passes(widget.classroomId),
        widget.api.sessions(classroomId: widget.classroomId),
        widget.api.materials(classroomId: widget.classroomId),
        widget.api.myPass(widget.classroomId),
        widget.api.wishlist(),
      ]);
      final wishlistRows = (results[5] as List).cast<Map<String, dynamic>>();
      final match = wishlistRows.where((r) => (r['classroom'] as Map<String, dynamic>)['id'] == widget.classroomId);
      setState(() {
        _classroom = Classroom.fromJson(results[0] as Map<String, dynamic>);
        _passes = (results[1] as List).map((e) => ClassPass.fromJson(e as Map<String, dynamic>)).toList();
        _sessions = (results[2] as List).map((e) => ClassSession.fromJson(e as Map<String, dynamic>)).toList();
        _materials = (results[3] as List).map((e) => ClassMaterial.fromJson(e as Map<String, dynamic>)).toList();
        _myPass = MyPassStatus.fromJson(results[4] as Map<String, dynamic>);
        _wishlistEntryId = match.isEmpty ? null : match.first['id'] as int;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _toggleWishlist() async {
    final wasWishlisted = _wishlistEntryId != null;
    try {
      if (wasWishlisted) {
        final id = _wishlistEntryId!;
        setState(() => _wishlistEntryId = null); // optimistic
        await widget.api.removeWishlist(id);
      } else {
        setState(() => _wishlistEntryId = -1); // optimistic placeholder while awaiting the real row id
        final res = await widget.api.addWishlist(widget.classroomId);
        // addWishlist() only returns void today — refresh once to pick up
        // the real row id so a follow-up removal has something to delete.
        await _load();
        return;
      }
    } catch (e) {
      // Roll back on failure so the heart reflects reality, not the tap.
      setState(() => _wishlistEntryId = wasWishlisted ? _wishlistEntryId : null);
      await _load();
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    if (_loading) {
      return Scaffold(
        backgroundColor: lsBg(context),
        appBar: lsAppBar(context, title: t.classroomDetailTitle),
        body: ListView(children: const [
          LsPostCardSkeleton(withMedia: true),
          LsClassroomRowSkeleton(),
        ]),
      );
    }
    if (_error != null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        appBar: lsAppBar(context, title: t.classroomDetailTitle),
        body: ErrorStateWidget(
          title: t.couldNotOpenClassroom,
          retryLabel: t.retry,
          onRetry: _load,
        ),
      );
    }

    final c = _classroom!;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: c.title, actions: [
        IconButton(
          icon: Icon(_wishlistEntryId != null ? Icons.favorite_rounded : Icons.favorite_border_rounded, color: cs.primary),
          onPressed: _toggleWishlist,
        ),
        IconButton(
          icon: const Icon(Icons.ios_share_rounded),
          onPressed: () async {
            final res = await widget.api.shareClassroom(widget.classroomId);
            final link = res['link']?.toString();
            if (link != null && context.mounted) {
              lsSnack(context, link);
            }
          },
        ),
        PopupMenuButton<String>(
          icon: const Icon(Icons.more_vert_rounded),
          onSelected: (v) {
            final push = (Widget page) => Navigator.of(context).push(MaterialPageRoute(builder: (_) => page));
            switch (v) {
              case 'assignments':
                push(AssignmentsScreen(api: widget.api, classroomId: widget.classroomId, isTeacher: _isTeacher));
                break;
              case 'info':
                push(ClassroomInfoHubScreen(api: widget.api, classroomId: widget.classroomId, isTeacher: _isTeacher));
                break;
              case 'certificates':
                push(CertificatesReportCardsScreen(api: widget.api, classroomId: widget.classroomId, isTeacher: _isTeacher));
                break;
              case 'recordings':
                push(RecordingsLibraryScreen(api: widget.api, classroomId: widget.classroomId));
                break;
              case 'managePasses':
                push(PassManagementScreen(api: widget.api, classroomId: widget.classroomId));
                break;
            }
          },
          itemBuilder: (context) => [
            PopupMenuItem(value: 'assignments', child: Text(t.assignmentsTitle)),
            PopupMenuItem(value: 'info', child: Text(t.classroomInfoTitle)),
            PopupMenuItem(value: 'certificates', child: Text(t.certificatesReportCardsTitle)),
            PopupMenuItem(value: 'recordings', child: Text(t.recordingsTitle)),
            if (_isTeacher) PopupMenuItem(value: 'managePasses', child: Text(t.managePassesTitle)),
          ],
        ),
      ]),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.only(bottom: 100),
          children: [
            if (c.coverImageUrl != null)
              Image.network(c.coverImageUrl!, height: 180, width: double.infinity, fit: BoxFit.cover),
            Padding(
              padding: const EdgeInsets.all(kLsPad),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(c.title, style: LsType.head(context, size: 19)),
                const SizedBox(height: 4),
                Text(c.teacherName, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                const SizedBox(height: 8),
                Row(children: [
                  LsStatusChip(label: c.subject, color: cs.primary),
                  const SizedBox(width: 6),
                  LsStatusChip(label: c.language.toUpperCase(), color: cs.secondary),
                  const SizedBox(width: 6),
                  if (c.ratingCount > 0)
                    LsStatusChip(
                      label: '${c.ratingAvg.toStringAsFixed(1)} (${c.ratingCount})',
                      color: Colors.amber.shade700,
                      icon: Icons.star_rounded,
                    ),
                ]),
                const SizedBox(height: 14),
                Text(c.description, style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface)),
              ]),
            ),
            LsSectionHead(title: t.upcomingSessionsTitle),
            if (_sessions.isEmpty)
              EmptyStateWidget(title: t.noUpcomingSessions, icon: Icons.event_busy_rounded)
            else
              ..._sessions.take(5).map((s) => LsCard(
                    margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
                    onTap: (_myPass?.canEnterClass ?? false) && (s.status == SessionStatus.live || s.status == SessionStatus.scheduled)
                        ? () => Navigator.of(context).push(MaterialPageRoute(
                              builder: (_) => LiveSessionScreen(api: widget.api, sessionId: s.id, classroomId: widget.classroomId, isHost: _isTeacher),
                            ))
                        : null,
                    child: Row(children: [
                      LsStatusChip(
                        label: s.status.name.toUpperCase(),
                        color: s.status == SessionStatus.live ? Colors.red : cs.primary,
                        solid: s.status == SessionStatus.live,
                      ),
                      const SizedBox(width: 10),
                      Expanded(child: Text(_formatRange(s.scheduledStart, s.scheduledEnd), style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                      if (s.status == SessionStatus.live) Icon(Icons.chevron_right_rounded, color: cs.primary),
                    ]),
                  )),
            LsSectionHead(title: t.materialsTitle),
            if (_materials.isEmpty)
              EmptyStateWidget(title: t.noMaterialsYet, icon: Icons.folder_open_rounded)
            else
              ..._materials.map((m) => LsCard(
                    margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
                    child: Row(children: [
                      Icon(_materialIcon(m.materialType), color: cs.onSurfaceVariant),
                      const SizedBox(width: 10),
                      Expanded(child: Text(m.title, style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                    ]),
                  )),
          ],
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(kLsPad),
          child: _buildCta(context, t),
        ),
      ),
    );
  }

  Widget _buildCta(BuildContext context, AppLocalizations t) {
    if (_myPass?.canEnterClass ?? false) {
      return LsPrimaryButton(
        label: _isTeacher ? t.startClass : t.enterClassCta,
        icon: Icons.play_circle_fill_rounded,
        onPressed: _entering ? null : _enterClass,
      );
    }
    if (_myPass?.state == 'pending') {
      return LsPrimaryButton(label: t.requestPendingCta, icon: Icons.hourglass_top_rounded, onPressed: null);
    }
    return LsPrimaryButton(
      label: t.requestToJoinCta,
      icon: Icons.login_rounded,
      onPressed: _passes.isEmpty
          ? null
          : () => showModalBottomSheet(
                context: context,
                isScrollControlled: true,
                builder: (_) => JoinRequestSheet(api: widget.api, passes: _passes),
              ),
    );
  }

  IconData _materialIcon(String type) => switch (type) {
        'pdf' => Icons.picture_as_pdf_rounded,
        'ppt' => Icons.slideshow_rounded,
        'video' => Icons.play_circle_outline_rounded,
        'image' => Icons.image_outlined,
        'link' => Icons.link_rounded,
        _ => Icons.insert_drive_file_outlined,
      };

  String _formatRange(DateTime a, DateTime b) =>
      '${a.day}/${a.month} ${a.hour.toString().padLeft(2, '0')}:${a.minute.toString().padLeft(2, '0')}'
      ' - ${b.hour.toString().padLeft(2, '0')}:${b.minute.toString().padLeft(2, '0')}';
}
