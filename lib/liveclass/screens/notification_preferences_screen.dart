// lib/liveclass/screens/notification_preferences_screen.dart
//
// NEW screen (frontend integration architecture v3, §1.6, Pass 14).
// One row per NotifType with push/email toggles. No sms toggle — the
// backend doc documents sms as a no-op channel, so a control for it would
// do nothing (doc §1.6).
//
// Entry point: notifications_screen.dart (not in this batch) should get a
// settings/gear icon on its app bar, next to the existing unread-filter
// chip, pushing this screen (doc §1.6 "Entry point"):
//   IconButton(
//     icon: const Icon(Icons.settings_outlined),
//     onPressed: () => Navigator.push(context,
//       MaterialPageRoute(builder: (_) => const NotificationPreferencesScreen())),
//   ),
//
// ⚠️ `NotificationPreference`'s shape (liveclass_models.dart) is itself an
// architecture skeleton per that model's own doc comment — confirm against
// `NotificationPreferenceSerializer` before shipping. This screen is
// written against the shape as it exists in liveclass_models.dart today
// (one settings object keyed by notif-type string).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

/// Human-readable label per `NotifType` constant. Kept local to this screen
/// rather than added to liveclass_models.dart, since it's presentation-only
/// and notifications_screen.dart (not in this batch) may already have its
/// own copy in `_notifIcon` per the doc's §23 note — reconcile the two if/
/// when that file is touched.
const Map<String, String> _kNotifTypeLabels = {
  NotifType.joinRequestReceived: 'New join request',
  NotifType.joinRequestAccepted: 'Join request accepted',
  NotifType.joinRequestRejected: 'Join request rejected',
  NotifType.passRefunded: 'Pass refunded',
  NotifType.sessionReminder: 'Session reminder',
  NotifType.sessionLive: 'Session went live',
  NotifType.sessionCancelled: 'Session cancelled',
  NotifType.assignmentPosted: 'New assignment',
  NotifType.assignmentGraded: 'Assignment graded',
  NotifType.submissionReceived: 'Submission received',
  NotifType.queryAnswered: 'Query answered',
  NotifType.certificateIssued: 'Certificate issued',
  NotifType.waitlistPromoted: 'Promoted from waitlist',
  NotifType.classroomFlagged: 'Classroom flagged',
  NotifType.noticePosted: 'New notice',
  NotifType.staffAdded: 'Added as staff',
  NotifType.reviewPosted: 'New review',
  NotifType.reportReviewed: 'Report reviewed',
  NotifType.generic: 'Other updates',
};

class NotificationPreferencesScreen extends StatefulWidget {
  const NotificationPreferencesScreen({super.key});

  @override
  State<NotificationPreferencesScreen> createState() => _NotificationPreferencesScreenState();
}

class _NotificationPreferencesScreenState extends State<NotificationPreferencesScreen> {
  bool _loading = true;
  String? _error;
  NotificationPreference? _prefs;
  // Types currently mid-save, so we can show a small per-row spinner and
  // avoid double-submitting the same row.
  final Set<String> _saving = {};

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
      final prefs = await LiveClassApi.notificationPreferences.get();
      if (!mounted) return;
      setState(() {
        _prefs = prefs;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load your notification settings.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _toggle(String notifType, {bool? push, bool? email}) async {
    final current = _prefs;
    if (current == null || _saving.contains(notifType)) return;

    final existing = current.forType(notifType);
    final updated = existing.copyWith(push: push, email: email);
    final optimistic = current.withType(notifType, updated);

    setState(() {
      _prefs = optimistic;
      _saving.add(notifType);
    });

    try {
      // Send only the changed row — avoids clobbering a concurrent change
      // from another device (see NotificationPreferenceApi.update's own
      // doc comment).
      final saved = await LiveClassApi.notificationPreferences
          .update(NotificationPreference(perType: {notifType: updated}));
      if (!mounted) return;
      setState(() {
        final merged = optimistic.withType(notifType, saved.forType(notifType));
        _prefs = merged;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _prefs = current); // revert
      _snack(e is LiveClassApiException ? e.message : 'Could not save. Please try again.');
    } finally {
      if (mounted) setState(() => _saving.remove(notifType));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Notification Preferences'),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) return const LiveClassLoading();
    if (_error != null || _prefs == null) {
      return LiveClassErrorState(message: _error ?? 'Something went wrong.', onRetry: _load);
    }

    final prefs = _prefs!;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.symmetric(vertical: LiveClassSpacing.sm),
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(LiveClassSpacing.lg, LiveClassSpacing.sm, LiveClassSpacing.lg, LiveClassSpacing.md),
            child: Text(
              'Choose which notifications you get by push and by email. SMS isn\'t used '
              'by this app.',
              style: TextStyle(color: Colors.grey, fontSize: 12.5),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg),
            child: Row(
              children: [
                const Expanded(child: SizedBox()),
                SizedBox(width: 56, child: Center(child: Icon(Icons.notifications_none, size: 18, color: Colors.grey.shade600))),
                SizedBox(width: 56, child: Center(child: Icon(Icons.email_outlined, size: 18, color: Colors.grey.shade600))),
              ],
            ),
          ),
          const Divider(height: LiveClassSpacing.lg),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg),
            child: LiveClassCard(
              padding: EdgeInsets.zero,
              margin: EdgeInsets.zero,
              child: Column(
                children: [
                  for (int i = 0; i < kAllNotifTypesForPreferences.length; i++) ...[
                    if (i > 0) Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                    _PrefRow(
                      label: _kNotifTypeLabels[kAllNotifTypesForPreferences[i]] ?? kAllNotifTypesForPreferences[i],
                      pref: prefs.forType(kAllNotifTypesForPreferences[i]),
                      saving: _saving.contains(kAllNotifTypesForPreferences[i]),
                      onPushChanged: (v) => _toggle(kAllNotifTypesForPreferences[i], push: v),
                      onEmailChanged: (v) => _toggle(kAllNotifTypesForPreferences[i], email: v),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: LiveClassSpacing.xxl),
        ],
      ),
    );
  }
}

class _PrefRow extends StatelessWidget {
  final String label;
  final NotifChannelPref pref;
  final bool saving;
  final ValueChanged<bool> onPushChanged;
  final ValueChanged<bool> onEmailChanged;

  const _PrefRow({
    required this.label,
    required this.pref,
    required this.saving,
    required this.onPushChanged,
    required this.onEmailChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg, vertical: LiveClassSpacing.xs),
      child: Row(
        children: [
          Expanded(
            child: Text(label, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis),
          ),
          SizedBox(
            width: 56,
            child: Center(
              child: saving
                  ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
                  : Switch(
                      value: pref.push,
                      activeColor: LiveClassColors.navy,
                      onChanged: onPushChanged,
                    ),
            ),
          ),
          SizedBox(
            width: 56,
            child: Center(
              child: Switch(
                value: pref.email,
                activeColor: LiveClassColors.navy,
                onChanged: saving ? null : onEmailChanged,
              ),
            ),
          ),
        ],
      ),
    );
  }
}