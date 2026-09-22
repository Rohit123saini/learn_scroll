// ============================================================
// LIVECLASS — WISHLIST SCREEN
//
// Backend surface used: GET/POST /wishlist-classrooms/, DELETE
// /wishlist-classrooms/{id}/.
//
// GAP THIS CLOSES: `classroom_detail_screen.dart`'s heart toggle could
// only ever POST (add) because it never had the wishlist *entry's* id
// to DELETE against — only the classroom's id. This screen is the
// source of that id: it fetches the real wishlist rows (each one
// carries both `id` and `classroom`), so remove-from-here always
// works, and it now hands that id back to the detail screen so the
// heart toggle there can remove too instead of only adding.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';
import 'classroom_detail_screen.dart';

class WishlistEntry {
  final int id; // wishlist row id — needed for DELETE
  final Classroom classroom;
  WishlistEntry({required this.id, required this.classroom});
}

class WishlistScreen extends StatefulWidget {
  final LiveClassApi api;
  const WishlistScreen({super.key, required this.api});

  @override
  State<WishlistScreen> createState() => _WishlistScreenState();
}

class _WishlistScreenState extends State<WishlistScreen> {
  List<WishlistEntry> _entries = const [];
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
      final raw = await widget.api.wishlist();
      setState(() {
        _entries = raw
            .map((e) => e as Map<String, dynamic>)
            .map((e) => WishlistEntry(id: e['id'] as int, classroom: Classroom.fromJson(e['classroom'] as Map<String, dynamic>)))
            .toList();
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _remove(WishlistEntry entry) async {
    final previous = _entries;
    setState(() => _entries = _entries.where((e) => e.id != entry.id).toList());
    try {
      await widget.api.removeWishlist(entry.id);
    } catch (e) {
      setState(() => _entries = previous); // roll back on failure
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.wishlistTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadWishlist, retryLabel: t.retry, onRetry: _load)
                : _entries.isEmpty
                    ? EmptyStateWidget(title: t.wishlistEmptyTitle, subtitle: t.wishlistEmptySubtitle, icon: Icons.favorite_border_rounded)
                    : ListView(children: _entries.map((entry) => LsCard(
                          margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                          onTap: () => Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => ClassroomDetailScreen(api: widget.api, classroomId: entry.classroom.id),
                          )),
                          child: Row(children: [
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text(entry.classroom.title, style: LsType.head(context, size: 13.5)),
                                const SizedBox(height: 3),
                                Text(entry.classroom.teacherName, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                              ]),
                            ),
                            IconButton(
                              icon: Icon(Icons.favorite_rounded, color: cs.primary),
                              onPressed: () => _remove(entry),
                            ),
                          ]),
                        )).toList()),
      ),
    );
  }
}
