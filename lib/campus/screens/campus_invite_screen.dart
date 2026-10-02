import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../widgets/ls_ui.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// CAMPUS — Task 13/G13: "family" network-effect invite codes
//
// Two screens, matching the two sides of the flow:
//   1. CampusInviteManageScreen — admin/principal OR the section's own
//      class-teacher generates/shares/rotates/revokes ONE section's
//      join code. Pushed from SectionDetailScreen (see the "Invite
//      classmates" row added there).
//   2. CampusJoinWithCodeScreen — any student pastes a code they got
//      from a classmate and self-enrolls. Pushed from CampusScreen's
//      app bar (campus-agnostic — works even before the student
//      belongs to any campus yet).
//
// Deliberately two separate small screens rather than one shared one:
// the admin side needs a `sectionId` + `CampusAccess` context it was
// already inside; the student side needs neither — it's the one
// campus-agnostic entry point in this whole app (mirrors why
// `ParentLinkScreen` in campus_screen.dart is also always-visible,
// independent of `_selected` campus).
// ============================================================

class CampusInviteManageScreen extends StatefulWidget {
  final String campusId;
  final String sectionId;
  final String sectionLabel;

  const CampusInviteManageScreen({
    super.key,
    required this.campusId,
    required this.sectionId,
    required this.sectionLabel,
  });

  @override
  State<CampusInviteManageScreen> createState() => _CampusInviteManageScreenState();
}

class _CampusInviteManageScreenState extends State<CampusInviteManageScreen> {
  CampusInviteCode? _code;
  bool _loading = true;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// `generateInviteCode` reuses an already-usable code for this section
  /// by default (see campus_invite.py's generate view) — so simply
  /// calling it on open doubles as "fetch the current code, or mint the
  /// first one" without needing a separate GET endpoint.
  Future<void> _load({bool forceNew = false}) async {
    setState(() {
      if (!forceNew) _loading = true;
      _busy = forceNew;
      _error = null;
    });
    try {
      final code = await CampusService.generateInviteCode(
        campusId: widget.campusId,
        sectionId: widget.sectionId,
        forceNew: forceNew,
      );
      if (!mounted) return;
      setState(() {
        _code = code;
        _loading = false;
        _busy = false;
      });
    } on CampusApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
        _busy = false;
      });
    }
  }

  Future<void> _revoke() async {
    final code = _code;
    if (code == null) return;
    setState(() => _busy = true);
    try {
      await CampusService.revokeInviteCode(code.id);
      if (mounted) lsSnack(context, 'Code deactivated.');
      await _load();
    } on CampusApiException catch (e) {
      if (mounted) {
        lsSnack(context, e.message, error: true);
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _rotate() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Get a new code?'),
        content: const Text(
          'The old code will stop working. Anyone who already joined stays enrolled — only NEW joins need the new code.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Get new code')),
        ],
      ),
    );
    if (confirmed == true) await _load(forceNew: true);
  }

  void _copy() {
    final code = _code;
    if (code == null) return;
    Clipboard.setData(ClipboardData(text: code.shareText ?? code.code));
    lsSnack(context, 'Copied — paste it into your class group.');
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: lsAppBar(context, title: 'Invite classmates'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _ErrorBox(message: _error!, onRetry: () => _load())
              : ListView(
                  padding: const EdgeInsets.all(14),
                  children: [
                    LsCard(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(children: [
                            CircleAvatar(
                              radius: 18,
                              backgroundColor: cs.primaryContainer,
                              child: Icon(Icons.group_add_rounded, size: 18, color: cs.onPrimaryContainer),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text('Invite ${widget.sectionLabel}', style: LsType.head(context, size: 14)),
                            ),
                          ]),
                          const SizedBox(height: 12),
                          Text(
                            'Share this code with your classmates — anyone who enters it joins '
                            '${widget.sectionLabel} instantly, no waiting on an admin.',
                            style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant),
                          ),
                          const SizedBox(height: 16),
                          Center(
                            child: Text(
                              _code?.code ?? '——————',
                              style: const TextStyle(
                                fontSize: 30,
                                fontWeight: FontWeight.w800,
                                letterSpacing: 4,
                              ),
                            ),
                          ),
                          const SizedBox(height: 6),
                          if (_code != null)
                            Center(
                              child: Text(
                                _code!.isActive
                                    ? '${_code!.usesCount} classmate${_code!.usesCount == 1 ? '' : 's'} joined so far'
                                    : 'This code is deactivated',
                                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                              ),
                            ),
                          const SizedBox(height: 16),
                          Row(children: [
                            Expanded(
                              child: LsPrimaryButton(
                                label: 'Copy & share',
                                icon: Icons.share_rounded,
                                loading: _busy,
                                onPressed: _code == null ? null : _copy,
                              ),
                            ),
                          ]),
                          const SizedBox(height: 8),
                          Row(children: [
                            Expanded(
                              child: TextButton.icon(
                                onPressed: _busy ? null : _rotate,
                                icon: const Icon(Icons.refresh_rounded, size: 18),
                                label: const Text('Get new code'),
                              ),
                            ),
                            Expanded(
                              child: TextButton.icon(
                                onPressed: (_busy || _code == null || !_code!.isActive) ? null : _revoke,
                                icon: const Icon(Icons.block_rounded, size: 18),
                                label: const Text('Deactivate'),
                                style: TextButton.styleFrom(foregroundColor: cs.error),
                              ),
                            ),
                          ]),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: Text(
                        'Anyone can reuse this same code until you deactivate it or it expires — '
                        'it isn\'t single-use.',
                        style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                      ),
                    ),
                  ],
                ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorBox({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      ),
    );
  }
}

// ============================================================
// Student side — "Join with code"
// ============================================================

class CampusJoinWithCodeScreen extends StatefulWidget {
  const CampusJoinWithCodeScreen({super.key});

  @override
  State<CampusJoinWithCodeScreen> createState() => _CampusJoinWithCodeScreenState();
}

class _CampusJoinWithCodeScreenState extends State<CampusJoinWithCodeScreen> {
  final _controller = TextEditingController();
  bool _joining = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _join() async {
    final code = _controller.text.trim();
    if (code.isEmpty) return;
    setState(() {
      _joining = true;
      _error = null;
    });
    try {
      final result = await CampusService.redeemInviteCode(code);
      if (!mounted) return;
      final campusName = result['campus_name']?.toString() ?? 'the campus';
      final alreadyEnrolled = result['already_enrolled'] == true;
      await showDialog(
        context: context,
        builder: (_) => AlertDialog(
          title: Text(alreadyEnrolled ? 'Already joined' : 'You\'re in!'),
          content: Text(
            alreadyEnrolled
                ? 'You were already part of $campusName.'
                : 'You just joined $campusName. Pull down on the Campus tab to see it.',
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('Done')),
          ],
        ),
      );
      if (mounted) Navigator.pop(context, true);
    } on CampusApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _joining = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: lsAppBar(context, title: 'Join with code'),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Got an invite code from a classmate? Enter it below to join their batch instantly.',
              style: TextStyle(fontSize: 13, height: 1.4, color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: 18),
            TextField(
              controller: _controller,
              textCapitalization: TextCapitalization.characters,
              autofocus: true,
              maxLength: 12,
              style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: 3),
              decoration: InputDecoration(
                labelText: 'Invite code',
                border: const OutlineInputBorder(),
                errorText: _error,
              ),
              onSubmitted: (_) => _join(),
            ),
            const SizedBox(height: 8),
            LsPrimaryButton(
              label: 'Join',
              icon: Icons.group_add_rounded,
              loading: _joining,
              onPressed: _join,
            ),
          ],
        ),
      ),
    );
  }
}
