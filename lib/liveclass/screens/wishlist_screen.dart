// lib/liveclass/screens/wishlist_screen.dart
//
// Screen 17 — Wishlist (see LIVECLASS_SCREEN_ARCHITECTURE.md §17).
//
// "Save for later" — bookmarked classrooms, no pass/commitment attached.
// API: GET wishlist-classrooms/ (own, no classroom filter param — the
// backend already scopes the list to the caller), DELETE
// wishlist-classrooms/{id}/ to remove. Adding happens from Classroom
// Detail's heart toggle, not here.

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'classroom_detail_screen.dart';

// FIX (design-system drift): aliased to the shared tokens instead of
// locally-duplicated literals — see the matching note in
// waitlist_screen.dart. Zero-risk: same values, single source of truth.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

// ===========================================================================
// SCREEN
// ===========================================================================
class WishlistScreen extends StatefulWidget {
  const WishlistScreen({super.key});

  @override
  State<WishlistScreen> createState() => _WishlistScreenState();
}

class _WishlistScreenState extends State<WishlistScreen> {
  List<ClassroomWishlistItem> _items = [];
  bool _loading = true;
  String? _error;
  final Set<int> _removing = {};

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
      final res = await LiveClassApi.wishlist.list();
      final items = res.results.toList()..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load wishlist.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _remove(ClassroomWishlistItem item) async {
    setState(() => _removing.add(item.id));
    final previous = List<ClassroomWishlistItem>.from(_items);
    setState(() => _items.removeWhere((x) => x.id == item.id));
    try {
      await LiveClassApi.wishlist.remove(item.id);
    } catch (e) {
      if (!mounted) return;
      setState(() => _items = previous);
      _snack(e is LiveClassApiException ? e.message : 'Could not remove.');
    } finally {
      if (mounted) setState(() => _removing.remove(item.id));
    }
  }

  void _openClassroom(int classroomId) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: classroomId)))
        .then((_) => _load()); // pass/enrollment state might've changed
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: liveClassAppBar('Wishlist'),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: _kNavy,
                  onRefresh: _load,
                  child: _items.isEmpty
                      ? ListView(
                          children: const [
                            Padding(
                              padding: EdgeInsets.only(top: 100),
                              child: Center(
                                child: Column(children: [
                                  Icon(Icons.favorite_border_rounded, size: 44, color: Colors.black26),
                                  SizedBox(height: 10),
                                  Text('Your wishlist is empty.', style: TextStyle(color: Colors.black45)),
                                  SizedBox(height: 4),
                                  Text('Save classrooms from Explore.', style: TextStyle(color: Colors.black38, fontSize: 12)),
                                ]),
                              ),
                            ),
                          ],
                        )
                      : GridView.builder(
                          padding: const EdgeInsets.fromLTRB(14, 14, 14, 24),
                          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                            crossAxisCount: 2,
                            crossAxisSpacing: 12,
                            mainAxisSpacing: 12,
                            childAspectRatio: 0.68,
                          ),
                          itemCount: _items.length,
                          itemBuilder: (_, i) => _WishlistCard(
                            item: _items[i],
                            removing: _removing.contains(_items[i].id),
                            onTap: () => _openClassroom(_items[i].classroom.id),
                            onRemove: () => _remove(_items[i]),
                          ),
                        ),
                ),
    );
  }
}

// ===========================================================================
// Card
// ===========================================================================
class _WishlistCard extends StatelessWidget {
  final ClassroomWishlistItem item;
  final bool removing;
  final VoidCallback onTap;
  final VoidCallback onRemove;
  const _WishlistCard({required this.item, required this.removing, required this.onTap, required this.onRemove});

  @override
  Widget build(BuildContext context) {
    final classroom = item.classroom;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 10, offset: const Offset(0, 3))],
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AspectRatio(
              aspectRatio: 16 / 10,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  classroom.coverImage != null && classroom.coverImage!.isNotEmpty
                      ? CachedNetworkImage(
                          imageUrl: classroom.coverImage!,
                          fit: BoxFit.cover,
                          placeholder: (_, __) => Container(color: _kBg),
                          errorWidget: (_, __, ___) => _coverFallback(),
                        )
                      : _coverFallback(),
                  Positioned(
                    top: 6,
                    right: 6,
                    child: GestureDetector(
                      onTap: removing ? null : onRemove,
                      child: Container(
                        width: 28,
                        height: 28,
                        decoration: BoxDecoration(color: Colors.black.withOpacity(0.55), shape: BoxShape.circle),
                        alignment: Alignment.center,
                        child: removing
                            ? const SizedBox(
                                width: 12,
                                height: 12,
                                child: CircularProgressIndicator(strokeWidth: 1.5, color: Colors.white),
                              )
                            : const Icon(Icons.favorite_rounded, size: 15, color: Colors.redAccent),
                      ),
                    ),
                  ),
                  if (!classroom.isActive)
                    Positioned(
                      bottom: 6,
                      left: 6,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                        decoration: BoxDecoration(color: Colors.black.withOpacity(0.6), borderRadius: BorderRadius.circular(20)),
                        child: const Text('Closed', style: TextStyle(color: Colors.white, fontSize: 9.5, fontWeight: FontWeight.bold)),
                      ),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    classroom.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5, color: _kNavy, height: 1.2),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    classroom.teacher?.fullName ?? 'Unknown',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                  ),
                  const SizedBox(height: 5),
                  Row(
                    children: [
                      const Icon(Icons.star_rounded, size: 14, color: Color(0xFFFFAD33)),
                      const SizedBox(width: 2),
                      Text(
                        classroom.ratingCount > 0 ? classroom.ratingAvg.toStringAsFixed(1) : 'New',
                        style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.bold, color: _kNavy),
                      ),
                      const Spacer(),
                      Text(classroom.language, style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _coverFallback() {
    return Container(
      decoration: const BoxDecoration(gradient: _kGradient),
      alignment: Alignment.center,
      child: const Icon(Icons.cast_for_education_rounded, color: Colors.white, size: 30),
    );
  }
}