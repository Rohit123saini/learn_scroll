import 'package:flutter/material.dart';

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
      case NotificationCategory.liveClasses:
        return t.notifCategoryLiveClasses;
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
