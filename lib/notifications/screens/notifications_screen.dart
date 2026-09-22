// lib/features/notifications/screens/notifications_screen.dart
//
// Home ke bell-icon tap se yahan push karo:
//   Navigator.push(context, MaterialPageRoute(builder: (_) => const NotificationsScreen()));
//
// Design-system: `ls_ui.dart` ke shared widgets use kiye hain (lsAppBar, lsBg, lsSnack)
// taaki Home/Assignments/Test-Series ke saath visually consistent rahe.
// Colors kahin bhi hardcoded nahi — sab Theme.of(context).colorScheme se.

import 'package:flutter/material.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/notification_model.dart';
import '../services/notification_service.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import 'notification_settings_screen.dart';
// import '../../../widgets/skeletons.dart'; // agar generic list-skeleton chahiye to add karo

const double _kScrollThreshold = 700; // home.dart wala hi 700px convention (Task 10.5)

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  final _scrollController = ScrollController();
  final List<NotificationModel> _items = [];

  bool _loading = true;
  bool _loadingMore = false;
  bool _hasError = false;
  bool _hasMore = true;
  int _offset = 0;
  static const int _limit = 30;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _loadFirstPage();
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_hasMore || _loadingMore) return;
    final pos = _scrollController.position;
    if (pos.maxScrollExtent - pos.pixels < _kScrollThreshold) {
      _loadMore();
    }
  }

  Future<void> _loadFirstPage() async {
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final res = await NotificationService.instance.getNotifications(
        limit: _limit,
        offset: 0,
      );
      setState(() {
        _items
          ..clear()
          ..addAll(res.results);
        _offset = res.results.length;
        _hasMore = res.results.length >= _limit;
        _loading = false;
      });
    } catch (_) {
      setState(() {
        _loading = false;
        _hasError = true;
      });
    }
  }

  Future<void> _loadMore() async {
    setState(() => _loadingMore = true);
    try {
      final res = await NotificationService.instance.getNotifications(
        limit: _limit,
        offset: _offset,
      );
      setState(() {
        _items.addAll(res.results);
        _offset += res.results.length;
        _hasMore = res.results.length >= _limit;
        _loadingMore = false;
      });
    } catch (_) {
      // Instagram-jaisa: agla page fail ho to silently ruk jao, poori list error na ho.
      setState(() => _loadingMore = false);
    }
  }

  Future<void> _onMarkAllRead() async {
    // Optimistic: pehle UI update, phir background me API.
    setState(() {
      for (var i = 0; i < _items.length; i++) {
        if (!_items[i].isRead) _items[i] = _items[i].copyWithRead();
      }
    });
    try {
      await NotificationService.instance.markAllRead();
    } catch (_) {
      if (mounted) {
        lsSnack(context, 'Sab read mark karne me kuch gadbad hui, retry karo.');
      }
    }
  }

  Future<void> _onTapNotification(int index) async {
    final n = _items[index];
    if (!n.isRead) {
      setState(() => _items[index] = n.copyWithRead());
      // Fire-and-forget — UI already updated, fail hone par silently ignore.
      NotificationService.instance.markRead(n.id).catchError((_) {});
    }

    // Source ke hisaab se deep-link route karo.
    switch (n.source) {
      case NotificationSource.liveclass:
        // TODO: classroom_id/session_id ke hisaab se classroom-detail
        // ya class-session screen pe navigate karo.
        break;
      case NotificationSource.message:
        // TODO: conversation/chat screen pe navigate karo (data['conversation_id'] wagera).
        break;
      case NotificationSource.unknown:
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(
        context,
        title: 'Notifications',
        actions: [
          // 🔥 FIX [Task 5] — settings gear here is the quick path straight
          // from the notification list; also reachable from main Settings.
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            tooltip: 'Notification settings',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const NotificationSettingsScreen()),
            ),
          ),
          TextButton(
            onPressed: _items.any((n) => !n.isRead) ? _onMarkAllRead : null,
            child: const Text('Mark all read'),
          ),
        ],
      ),
      body: _buildBody(scheme),
    );
  }

  Widget _buildBody(ColorScheme scheme) {
    if (_loading) {
      // TODO: skeletons.dart se ek generic list-row skeleton use karo yahan.
      return const Center(child: CircularProgressIndicator());
    }
    if (_hasError && _items.isEmpty) {
      return ErrorStateWidget(
        title: 'Notifications load nahi ho payi',
        // 🔥 FIX — `retryLabel` ErrorStateWidget me `required` hai (Task 11.1),
        // ye call pehle bina isi ke tha isliye "Required named parameter
        // 'retryLabel' must be provided" error aa raha tha.
        retryLabel: 'Retry',
        onRetry: _loadFirstPage,
      );
    }
    if (_items.isEmpty) {
      return const EmptyStateWidget(
        title: 'Koi notification nahi hai',
        // retry button jaan-boojh kar nahi — empty list retry se bhi empty hi rahegi.
      );
    }

    return RefreshIndicator(
      onRefresh: _loadFirstPage,
      child: ListView.separated(
        controller: _scrollController,
        itemCount: _items.length + (_hasMore ? 1 : 0),
        separatorBuilder: (_, __) => Divider(height: 1, color: scheme.outlineVariant),
        itemBuilder: (context, index) {
          if (index >= _items.length) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final n = _items[index];
          return ListTile(
            tileColor: n.isRead ? null : scheme.primary.withOpacity(0.06),
            title: Text(
              n.title,
              style: TextStyle(
                fontWeight: n.isRead ? FontWeight.normal : FontWeight.w600,
                color: scheme.onSurface,
              ),
            ),
            subtitle: Text(
              n.message,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
            trailing: Text(
              timeago.format(n.createdAt),
              style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
            ),
            onTap: () => _onTapNotification(index),
          );
        },
      ),
    );
  }
}