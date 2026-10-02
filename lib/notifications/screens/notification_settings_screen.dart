import 'package:flutter/material.dart';
// [N9] Needed for the device's IANA timezone name (quiet hours are
// evaluated server-side in it). Add `flutter_timezone` to pubspec.yaml.
// This assumes <=3.x (`getLocalTimezone()` returns a String); on 4.x use
// `(await FlutterTimezone.getLocalTimezone()).identifier`.
import 'package:flutter_timezone/flutter_timezone.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../models/notification_model.dart';
import '../services/notification_service.dart';

// ============================================================
// NOTIFICATION SETTINGS  [Task 5 gap-fix]
//
// `core/notification-preferences/me/` (GET/PATCH) backend pe pehle se
// production-ready tha — model, serializer, view sab bane hue (push/
// email/sms/whatsapp toggles, mute-by-type, digest frequency). Frontend
// me isko sirf ek URL comment ki tarah likha gaya tha
// (`notification_service.dart` header) — kabhi implement/call nahi hua,
// aur poori app me kahin koi settings UI nahi thi is chij ke liye. Ye
// wahi missing screen hai.
//
// Backend `muted_types` me 30+ raw notif-type strings expect karta hai
// (`core/models.py` — `Notification.NotifType`) — itni granularity ek
// settings screen ke liye bahut zyada hoti, isliye
// `NotificationCategory` (notification_model.dart) unhe 6 samajhne-laayak
// group me todta hai. Ek category off karne ka matlab: uske saare member
// types `muted_types` me chale jaate hain — bell-row abhi bhi banta hai,
// bas push/email/sms/whatsapp alert nahi jaata (backend
// `allowed_channels_for()` ka hi documented behaviour).
// ============================================================

class NotificationSettingsScreen extends StatefulWidget {
  const NotificationSettingsScreen({super.key});

  @override
  State<NotificationSettingsScreen> createState() => _NotificationSettingsScreenState();
}

class _NotificationSettingsScreenState extends State<NotificationSettingsScreen> {
  bool _loading = true;
  String? _error;
  NotificationPreferences? _prefs;

  /// Ek waqt me ek hi field ka save chal raha ho — taaki do toggles ek
  /// saath tap karne par unki PATCH calls ek-dusre ko race na karein aur
  /// purani response nayi state ko overwrite na kar de.
  bool _saving = false;

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
      final prefs = await NotificationService.instance.getPreferences();
      if (!mounted) return;
      setState(() {
        _prefs = prefs;
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

  Future<void> _patch(Map<String, dynamic> body, NotificationPreferences optimistic) async {
    final previous = _prefs;
    setState(() {
      _prefs = optimistic;
      _saving = true;
    });
    try {
      final updated = await NotificationService.instance.updatePreferences(body);
      if (!mounted) return;
      setState(() {
        _prefs = updated;
        _saving = false;
      });
    } catch (e) {
      if (!mounted) return;
      // Optimistic update revert — server ne reject kiya (network error,
      // session expiry, etc.) to UI purani, sach state pe wapas aa jaaye.
      setState(() {
        _prefs = previous;
        _saving = false;
      });
      lsSnack(context, e.toString(), error: true);
    }
  }

  void _toggleChannel(String field, bool value) {
    final p = _prefs;
    if (p == null || _saving) return;
    final optimistic = switch (field) {
      'push_enabled' => p.copyWith(pushEnabled: value),
      'email_enabled' => p.copyWith(emailEnabled: value),
      'sms_enabled' => p.copyWith(smsEnabled: value),
      'whatsapp_enabled' => p.copyWith(whatsappEnabled: value),
      _ => p,
    };
    _patch({field: value}, optimistic);
  }

  void _setDigest(String frequency) {
    final p = _prefs;
    if (p == null || _saving || p.digestFrequency == frequency) return;
    _patch({'digest_frequency': frequency}, p.copyWith(digestFrequency: frequency));
  }

  bool _categoryEnabled(NotificationCategory cat) {
    final muted = _prefs?.mutedTypes ?? const [];
    // Category "on" = koi bhi member type muted na ho. Agar kuch muted
    // hain kuch nahi (purani/manual state), to bhi "off" dikhate hain —
    // taaki toggle on karne par sab consistent ho jayein.
    return !cat.notifTypes.any(muted.contains);
  }

  void _toggleCategory(NotificationCategory cat, bool enabled) {
    final p = _prefs;
    if (p == null || _saving) return;
    final current = List<String>.from(p.mutedTypes);
    if (enabled) {
      current.removeWhere(cat.notifTypes.contains);
    } else {
      for (final t in cat.notifTypes) {
        if (!current.contains(t)) current.add(t);
      }
    }
    _patch({'muted_types': current}, p.copyWith(mutedTypes: current));
  }

  // ---- [N9] quiet hours / pause-all --------------------------------

  Future<String?> _deviceTimezone() async {
    try {
      // flutter_timezone >= 5 returns a TimezoneInfo (older versions returned a String).
      final tz = await FlutterTimezone.getLocalTimezone();
      return tz.identifier;
    } catch (_) {
      return null; // backend keeps whatever timezone it already has
    }
  }

  String _fmtHm(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  TimeOfDay _parseHm(String hm) {
    final parts = hm.split(':');
    return TimeOfDay(hour: int.parse(parts[0]), minute: int.parse(parts[1]));
  }

  Future<void> _setQuietEnabled(bool on) async {
    final p = _prefs;
    if (p == null || _saving) return;
    if (!on) {
      _patch({'quiet_start': null, 'quiet_end': null}, p.copyWith(clearQuietHours: true));
      return;
    }
    final tz = await _deviceTimezone();
    final latest = _prefs;
    if (!mounted || latest == null || _saving) return;
    _patch(
      {'quiet_start': '22:00', 'quiet_end': '07:00', if (tz != null) 'timezone': tz},
      latest.copyWith(quietStart: '22:00', quietEnd: '07:00', timezone: tz),
    );
  }

  Future<void> _pickQuietTime(bool isStart, AppLocalizations t) async {
    final p = _prefs;
    if (p == null || _saving || !p.quietHoursEnabled) return;
    final current = _parseHm((isStart ? p.quietStart : p.quietEnd)!);
    final picked = await showTimePicker(context: context, initialTime: current);
    if (picked == null || !mounted) return;
    final hm = _fmtHm(picked);
    final other = isStart ? p.quietEnd : p.quietStart;
    if (hm == other) {
      lsSnack(context, t.notifQuietSameTime, error: true); // backend rejects start == end
      return;
    }
    final tz = await _deviceTimezone();
    final latest = _prefs;
    if (!mounted || latest == null || _saving) return;
    _patch(
      {isStart ? 'quiet_start' : 'quiet_end': hm, if (tz != null) 'timezone': tz},
      isStart
          ? latest.copyWith(quietStart: hm, timezone: tz)
          : latest.copyWith(quietEnd: hm, timezone: tz),
    );
  }

  /// `minutes == 0` resumes. The server computes the deadline
  /// (`dnd_for_minutes`), so a wrong phone clock can't over-pause; the
  /// optimistic value below is replaced by the server's `dnd_until`.
  void _pauseFor(int minutes) {
    final p = _prefs;
    if (p == null || _saving) return;
    if (minutes == 0) {
      _patch({'dnd_for_minutes': 0}, p.copyWith(clearDndUntil: true));
    } else {
      _patch({'dnd_for_minutes': minutes},
          p.copyWith(dndUntil: DateTime.now().add(Duration(minutes: minutes))));
    }
  }

  String _fmtPausedUntil(BuildContext context, DateTime until) {
    final time = TimeOfDay.fromDateTime(until).format(context);
    final now = DateTime.now();
    final sameDay = until.year == now.year && until.month == now.month && until.day == now.day;
    return sameDay ? time : '${until.day}/${until.month} $time';
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.notificationSettingsTitle),
      body: _body(context, t),
    );
  }

  Widget _body(BuildContext context, AppLocalizations t) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _prefs == null) {
      return ErrorStateWidget(
        title: t.notificationSettingsLoadFailed,
        retryLabel: t.retry,
        onRetry: _load,
      );
    }
    final p = _prefs!;
    final cs = Theme.of(context).colorScheme;

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 12),
      children: [
        LsSectionHead(title: t.notifChannelsSectionTitle),
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: EdgeInsets.zero,
          child: Column(children: [
            _switchTile(context, t.notifChannelPush, p.pushEnabled,
                (v) => _toggleChannel('push_enabled', v)),
            Divider(height: 1, color: cs.outlineVariant),
            _switchTile(context, t.notifChannelEmail, p.emailEnabled,
                (v) => _toggleChannel('email_enabled', v)),
            Divider(height: 1, color: cs.outlineVariant),
            _switchTile(context, t.notifChannelSms, p.smsEnabled,
                (v) => _toggleChannel('sms_enabled', v)),
            Divider(height: 1, color: cs.outlineVariant),
            _switchTile(context, t.notifChannelWhatsapp, p.whatsappEnabled,
                (v) => _toggleChannel('whatsapp_enabled', v)),
          ]),
        ),
        const SizedBox(height: 22),
        LsSectionHead(title: t.notifPauseSectionTitle),
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: const EdgeInsets.all(16),
          child: p.isDndActive
              ? Row(children: [
                  Expanded(
                    child: Text(t.notifPausedUntil(_fmtPausedUntil(context, p.dndUntil!)),
                        style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
                  ),
                  TextButton(
                    onPressed: _saving ? null : () => _pauseFor(0),
                    child: Text(t.notifPauseResume),
                  ),
                ])
              : Wrap(spacing: 8, runSpacing: 8, children: [
                  ActionChip(label: Text(t.notifPause1h), onPressed: _saving ? null : () => _pauseFor(60)),
                  ActionChip(label: Text(t.notifPause8h), onPressed: _saving ? null : () => _pauseFor(8 * 60)),
                  ActionChip(label: Text(t.notifPause24h), onPressed: _saving ? null : () => _pauseFor(24 * 60)),
                ]),
        ),
        const SizedBox(height: 22),
        LsSectionHead(title: t.notifQuietSectionTitle),
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
          child: Text(t.notifQuietHint,
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, height: 1.4)),
        ),
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: EdgeInsets.zero,
          child: Column(children: [
            _switchTile(context, t.notifQuietToggle, p.quietHoursEnabled, _setQuietEnabled),
            if (p.quietHoursEnabled) ...[
              Divider(height: 1, color: cs.outlineVariant),
              _timeTile(context, t.notifQuietFrom, _parseHm(p.quietStart!), () => _pickQuietTime(true, t)),
              Divider(height: 1, color: cs.outlineVariant),
              _timeTile(context, t.notifQuietTo, _parseHm(p.quietEnd!), () => _pickQuietTime(false, t)),
            ],
          ]),
        ),
        const SizedBox(height: 22),
        LsSectionHead(title: t.notifDigestSectionTitle),
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: EdgeInsets.zero,
          child: Column(children: [
            _radioTile(context, t.notifDigestOff, p.digestFrequency == 'off', () => _setDigest('off')),
            Divider(height: 1, color: cs.outlineVariant),
            _radioTile(context, t.notifDigestDaily, p.digestFrequency == 'daily', () => _setDigest('daily')),
            Divider(height: 1, color: cs.outlineVariant),
            _radioTile(context, t.notifDigestWeekly, p.digestFrequency == 'weekly', () => _setDigest('weekly')),
          ]),
        ),
        const SizedBox(height: 22),
        LsSectionHead(title: t.notifCategoriesSectionTitle),
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
          child: Text(t.notifCategoriesHint,
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, height: 1.4)),
        ),
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: EdgeInsets.zero,
          child: Column(children: [
            for (int i = 0; i < NotificationCategory.values.length; i++) ...[
              if (i > 0) Divider(height: 1, color: cs.outlineVariant),
              _switchTile(
                context,
                _categoryLabel(t, NotificationCategory.values[i]),
                _categoryEnabled(NotificationCategory.values[i]),
                (v) => _toggleCategory(NotificationCategory.values[i], v),
              ),
            ],
          ]),
        ),
      ],
    );
  }

  String _categoryLabel(AppLocalizations t, NotificationCategory cat) {
    switch (cat) {
      case NotificationCategory.tuitionClasses:
        return t.notifCategoryTuitionClasses;
      case NotificationCategory.assignmentsTests:
        return t.notifCategoryAssignmentsTests;
      case NotificationCategory.messagesCalls:
        return t.notifCategoryMessagesCalls;
      case NotificationCategory.social:
        return t.notifCategorySocial;
      case NotificationCategory.payments:
        return t.notifCategoryPayments;
      case NotificationCategory.campus:
        return t.notifCategoryCampus;
    }
  }

  Widget _switchTile(BuildContext context, String label, bool value, ValueChanged<bool> onChanged) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(children: [
        Expanded(child: Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface))),
        Switch(value: value, onChanged: _saving ? null : onChanged),
      ]),
    );
  }

  Widget _timeTile(BuildContext context, String label, TimeOfDay value, VoidCallback onTap) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: _saving ? null : onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(children: [
          Expanded(child: Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface))),
          Text(value.format(context),
              style: TextStyle(fontSize: 13.5, color: cs.primary, fontWeight: FontWeight.w600)),
        ]),
      ),
    );
  }

  Widget _radioTile(BuildContext context, String label, bool selected, VoidCallback onTap) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: _saving ? null : onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        child: Row(children: [
          Expanded(child: Text(label, style: TextStyle(fontSize: 13.5, color: cs.onSurface))),
          if (selected) Icon(Icons.check_circle_rounded, size: 20, color: cs.primary),
        ]),
      ),
    );
  }
}