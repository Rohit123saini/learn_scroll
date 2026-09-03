// lib/liveclass/screens/notification_preferences_screen.dart
//
// FIX (backend cross-check — LIVECLASS_PRODUCTION_AUDIT.md): rebuilt against
// `NotificationPreferenceSerializer` (serializers.py). The previous version
// of this screen rendered a push/email switch per NotifType, on the
// assumption `NotificationPreference` was a per-type channel matrix — that
// shape never existed on the backend (see liveclass_models.dart's
// NotificationPreference for the real one) and every toggle here was a
// no-op: PATCHing it sent fields the serializer doesn't have, and GETting
// it back never found the matrix it was looking for, so every row silently
// stayed at its default forever.
//
// The real model (models.py `NotificationPreference.allowed_channels_for`)
// is two layers:
//   1. Four blanket channel toggles (push/email/sms/whatsapp) — global,
//      not per notification type.
//   2. A per-type mute list — no channel at all for a muted type, not even
//      the in-app bell row's alert (the row is still written, just quiet).
// Plus an independent digest-email frequency (off/daily/weekly).
//
// Entry point unchanged: notifications_screen.dart's settings gear icon.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

/// Human-readable label per `NotifType` constant, used for the mute-list
/// rows below. Kept local to this screen rather than added to
/// liveclass_models.dart, since it's presentation-only —
/// notifications_screen.dart's `_notifIcon` has its own icon-only mapping
/// for the same constants; reconcile the two if either file adds a type
/// the other doesn't know about yet.
const Map<String, String> _kNotifTypeLabels = {
  NotifType.joinRequestReceived: 'New join request',
  NotifType.joinRequestAccepted: 'Join request accepted',
  NotifType.joinRequestRejected: 'Join request rejected',
  NotifType.passRefunded: 'Pass refunded',
  NotifType.passAutoRenewed: 'Pass auto-renewed',
  NotifType.autoRenewFailed: 'Auto-renew failed',
  NotifType.passGiftExpired: 'Gift expired',
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
  NotifType.classroomShared: 'Classroom shared',
  NotifType.passGiftReceived: 'Pass gift received',
  NotifType.passGiftClaimed: 'Pass gift claimed',
  // FIX (backend cross-check): withdrawal types existed in NotifType but
  // had no label anywhere — same "constant added, presentation map
  // missed" gap as the earlier fixes noted above.
  NotifType.withdrawalApproved: 'Withdrawal approved',
  NotifType.withdrawalRejected: 'Withdrawal rejected',
  NotifType.withdrawalPaid: 'Withdrawal paid out',
  NotifType.generic: 'Other updates',
};

const Map<String, String> _kDigestLabels = {
  DigestFrequency.off: 'Off',
  DigestFrequency.daily: 'Daily',
  DigestFrequency.weekly: 'Weekly',
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
  // Field(s) currently mid-save, so we can show a small spinner per control
  // and avoid double-submitting the same one. Uses the same string keys as
  // NotifType for muted rows, plus these fixed keys for the blanket toggles.
  static const _kPush = '__push';
  static const _kEmail = '__email';
  static const _kSms = '__sms';
  static const _kWhatsapp = '__whatsapp';
  static const _kDigest = '__digest';
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

  /// Shared save path for every control on this screen — optimistic update,
  /// PATCH the whole (small) settings object, revert on failure. Unlike the
  /// old per-type screen there's no "send only the changed row" concern:
  /// the object is one row per user, four booleans + a list + an enum, so
  /// resending it whole on every change is cheap and matches what
  /// NotificationPreferenceSerializer expects (partial=True on the backend
  /// still makes this safe against a concurrent change to a field this
  /// screen doesn't touch).
  Future<void> _apply(String savingKey, NotificationPreference updated) async {
    final current = _prefs;
    if (current == null || _saving.contains(savingKey)) return;

    setState(() {
      _prefs = updated;
      _saving.add(savingKey);
    });

    try {
      final saved = await LiveClassApi.notificationPreferences.update(updated);
      if (!mounted) return;
      setState(() => _prefs = saved);
    } catch (e) {
      if (!mounted) return;
      setState(() => _prefs = current); // revert
      _snack(e is LiveClassApiException ? e.message : 'Could not save. Please try again.');
    } finally {
      if (mounted) setState(() => _saving.remove(savingKey));
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
          _sectionLabel('Channels'),
          const Padding(
            padding: EdgeInsets.fromLTRB(LiveClassSpacing.lg, 4, LiveClassSpacing.lg, LiveClassSpacing.sm),
            child: Text(
              'These apply across every notification type below, unless you mute '
              'a type entirely.',
              style: TextStyle(color: Colors.grey, fontSize: 12.5),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg),
            child: LiveClassCard(
              padding: EdgeInsets.zero,
              margin: EdgeInsets.zero,
              child: Column(
                children: [
                  _channelTile('Push', Icons.notifications_none_rounded, prefs.pushEnabled, _kPush,
                      (v) => prefs.copyWith(pushEnabled: v)),
                  const Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                  _channelTile('Email', Icons.email_outlined, prefs.emailEnabled, _kEmail,
                      (v) => prefs.copyWith(emailEnabled: v)),
                  const Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                  _channelTile('SMS', Icons.sms_outlined, prefs.smsEnabled, _kSms,
                      (v) => prefs.copyWith(smsEnabled: v)),
                  const Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                  _channelTile('WhatsApp', Icons.chat_outlined, prefs.whatsappEnabled, _kWhatsapp,
                      (v) => prefs.copyWith(whatsappEnabled: v)),
                ],
              ),
            ),
          ),
          const SizedBox(height: LiveClassSpacing.lg),
          _sectionLabel('Email Digest'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg, vertical: LiveClassSpacing.sm),
            child: LiveClassCard(
              child: Row(
                children: [
                  const Expanded(
                    child: Text('Roundup email frequency',
                        style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500)),
                  ),
                  if (_saving.contains(_kDigest))
                    const SizedBox(
                        height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
                  else
                    DropdownButton<String>(
                      value: prefs.digestFrequency,
                      underline: const SizedBox(),
                      items: _kDigestLabels.entries
                          .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
                          .toList(),
                      onChanged: (v) {
                        if (v == null) return;
                        _apply(_kDigest, prefs.copyWith(digestFrequency: v));
                      },
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: LiveClassSpacing.lg),
          _sectionLabel('Mute Specific Notifications'),
          const Padding(
            padding: EdgeInsets.fromLTRB(LiveClassSpacing.lg, 4, LiveClassSpacing.lg, LiveClassSpacing.sm),
            child: Text(
              'A muted type is silenced on every channel — you\'ll still see it in '
              'your notification history.',
              style: TextStyle(color: Colors.grey, fontSize: 12.5),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg),
            child: LiveClassCard(
              padding: EdgeInsets.zero,
              margin: EdgeInsets.zero,
              child: Column(
                children: [
                  for (int i = 0; i < kAllNotifTypesForPreferences.length; i++) ...[
                    if (i > 0) Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                    _muteRow(kAllNotifTypesForPreferences[i], prefs),
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

  Widget _sectionLabel(String text) => Padding(
        padding: const EdgeInsets.fromLTRB(LiveClassSpacing.lg, LiveClassSpacing.md, LiveClassSpacing.lg, 0),
        child: Text(text, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
      );

  Widget _channelTile(String label, IconData icon, bool value, String savingKey,
      NotificationPreference Function(bool) buildUpdated) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg, vertical: LiveClassSpacing.xs),
      child: Row(
        children: [
          Icon(icon, size: 18, color: Colors.grey.shade600),
          const SizedBox(width: 12),
          Expanded(child: Text(label, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500))),
          _saving.contains(savingKey)
              ? const SizedBox(
                  height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
              : Switch(
                  value: value,
                  activeColor: LiveClassColors.navy,
                  onChanged: (v) => _apply(savingKey, buildUpdated(v)),
                ),
        ],
      ),
    );
  }

  Widget _muteRow(String notifType, NotificationPreference prefs) {
    final muted = prefs.isMuted(notifType);
    final saving = _saving.contains(notifType);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg, vertical: LiveClassSpacing.xs),
      child: Row(
        children: [
          Expanded(
            child: Text(_kNotifTypeLabels[notifType] ?? notifType,
                style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis),
          ),
          saving
              ? const SizedBox(
                  height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
              // Muting is the "off" state here, so the switch reads as
              // "notify me" (on = not muted) rather than "mute me" — less
              // confusing on a screen full of other on-means-on toggles.
              : Switch(
                  value: !muted,
                  activeColor: LiveClassColors.navy,
                  onChanged: (v) => _apply(notifType, prefs.withMuted(notifType, !v)),
                ),
        ],
      ),
    );
  }
}