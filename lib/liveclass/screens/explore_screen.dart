// lib/liveclass/screens/explore_screen.dart
//
// Screen 1 — Explore / Discover (see LIVECLASS_SCREEN_ARCHITECTURE.md §1).
// API: GET classrooms/?search=&language=&mine=&page=
// Any authenticated user can see this (public listing).
//
// Tap on a card → Classroom Detail Screen.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'classroom_detail_screen.dart';
import 'classroom_form_screen.dart';
import 'my_progress_screen.dart';

// FIX (design-system drift — production readiness audit): this was the last
// screen still fully hand-rolling its own hex-literal palette with zero tie
// back to liveclass_theme.dart (every other screen in the module either
// imports the shared tokens directly or, at minimum, aliases its local `_k*`
// constants to them — see the matching notes in wishlist_screen.dart,
// waitlist_screen.dart and classroom_purchases_screen.dart). If
// LiveClassColors.navy/bg/gradient ever changed, Explore — the module's
// entry tab — would have silently stopped matching every other screen, with
// no compile-time signal. Aliased instead: zero visual change today, single
// source of truth going forward.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

const List<String> _kLanguages = [
  'English',
  'Hindi',
  'Hinglish',
  'Tamil',
  'Telugu',
  'Bengali',
  'Marathi',
  'Gujarati',
  'Kannada',
  'Malayalam',
  'Punjabi',
];

class ExploreScreen extends StatefulWidget {
  const ExploreScreen({super.key});

  @override
  State<ExploreScreen> createState() => _ExploreScreenState();
}

class _ExploreScreenState extends State<ExploreScreen> {
  final TextEditingController _searchCtrl = TextEditingController();
  final ScrollController _scrollCtrl = ScrollController();
  Timer? _debounce;

  String _search = '';
  String? _language; // null == "All"
  bool _mine = false;

  final List<Classroom> _items = [];
  int _page = 1;
  bool _hasMore = true;
  bool _loading = false; // first page / filter change
  bool _loadingMore = false; // subsequent pages
  String? _error;

  // FEATURE (Phase 2, item 9 — personalized discovery): backend
  // (ClassroomViewSet.recommended, GET classrooms/recommended/) was ready
  // with no frontend caller anywhere in the module. Shown only on the
  // default browse view (no search/language filter, "All Classes" tab) —
  // once the person is actively filtering, a generic recommendation row
  // just gets in the way of what they're actually looking for.
  List<Classroom> _recommended = [];
  bool _recommendedLoading = true;

  @override
  void initState() {
    super.initState();
    _scrollCtrl.addListener(_onScroll);
    _fetch(reset: true);
    _fetchRecommended();
  }

  Future<void> _fetchRecommended() async {
    try {
      final res = await LiveClassApi.classrooms.recommended();
      if (!mounted) return;
      setState(() {
        _recommended = res.results;
        _recommendedLoading = false;
      });
    } catch (_) {
      // Recommendations are a nice-to-have — never block Explore on them.
      if (!mounted) return;
      setState(() => _recommendedLoading = false);
    }
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_hasMore || _loadingMore || _loading) return;
    if (_scrollCtrl.position.pixels >= _scrollCtrl.position.maxScrollExtent - 300) {
      _fetch();
    }
  }

  void _onSearchChanged(String v) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), () {
      _search = v.trim();
      _fetch(reset: true);
    });
  }

  Future<void> _fetch({bool reset = false}) async {
    if (reset) {
      setState(() {
        _loading = true;
        _error = null;
        _page = 1;
        _hasMore = true;
        _items.clear();
      });
    } else {
      if (!_hasMore) return;
      setState(() => _loadingMore = true);
    }

    try {
      final result = await LiveClassApi.classrooms.explore(
        search: _search.isEmpty ? null : _search,
        language: _language,
        mine: _mine,
        page: _page,
      );
      if (!mounted) return;
      setState(() {
        _items.addAll(result.results);
        _hasMore = result.next != null;
        _page += 1;
        _loading = false;
        _loadingMore = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadingMore = false;
        _error = e is LiveClassApiException ? e.message : 'Something went wrong. Please retry.';
      });
    }
  }

  void _setLanguage(String? lang) {
    setState(() => _language = lang);
    _fetch(reset: true);
  }

  void _setMine(bool mine) {
    setState(() => _mine = mine);
    _fetch(reset: true);
  }

  void _openDetail(Classroom c) {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: c.id, initial: c)),
    );
  }

  Future<void> _openCreateClassroom() async {
    final saved = await Navigator.push<Classroom>(
      context,
      MaterialPageRoute(builder: (_) => const ClassroomFormScreen()),
    );
    if (saved == null || !mounted) return;
    // New classroom banne ke baad seedha "My Classrooms" pe switch karke
    // list refresh karo, taaki naya classroom turant dikhe.
    setState(() => _mine = true);
    await _fetch(reset: true);
    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: saved.id, initial: saved)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: _kNavy,
        onPressed: _openCreateClassroom,
        icon: const Icon(Icons.add_rounded),
        label: const Text('Create Classroom'),
      ),
      body: RefreshIndicator(
        color: _kNavy,
        onRefresh: () => _fetch(reset: true),
        child: CustomScrollView(
          controller: _scrollCtrl,
          slivers: [
            _buildAppBar(),
            _buildFilters(),
            _buildRecommended(),
            _buildBody(),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // App bar with embedded search field
  // ---------------------------------------------------------------------
  Widget _buildAppBar() {
    return SliverAppBar(
      backgroundColor: _kNavy,
      pinned: true,
      floating: true,
      elevation: 0,
      toolbarHeight: 64,
      titleSpacing: 16,
      title: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(12)),
            child: const Icon(Icons.cast_for_education_rounded, color: Colors.white, size: 20),
          ),
          const SizedBox(width: 10),
          const Text(
            'Live Classes',
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 20),
          ),
        ],
      ),
      // FEATURE (Phase 2, item 8 — student progress): my-progress/ was
      // ready server-side with no entry point anywhere in the module.
      // Explore is the module's own entry tab, so this doubles as the
      // reachable entry point until this app's main menu/profile section
      // (not part of this upload) wires its own.
      actions: [
        IconButton(
          icon: const Icon(Icons.insights_outlined, color: Colors.white),
          tooltip: 'My Progress',
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const MyProgressScreen())),
        ),
      ],
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(58),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: Container(
            height: 44,
            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(22)),
            child: TextField(
              controller: _searchCtrl,
              onChanged: _onSearchChanged,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: 'Search classes, subjects, teachers…',
                hintStyle: const TextStyle(fontSize: 14, color: Colors.grey),
                prefixIcon: const Icon(Icons.search, color: Colors.grey, size: 22),
                suffixIcon: _searchCtrl.text.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.close, color: Colors.grey, size: 20),
                        onPressed: () {
                          _searchCtrl.clear();
                          _onSearchChanged('');
                        },
                      ),
                border: InputBorder.none,
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(vertical: 12),
              ),
            ),
          ),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Filter row: My Classrooms toggle + language chips
  // ---------------------------------------------------------------------
  Widget _buildFilters() {
    return SliverToBoxAdapter(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Row(
              children: [
                Expanded(
                  child: _MineToggle(
                    mine: _mine,
                    onChanged: _setMine,
                  ),
                ),
              ],
            ),
          ),
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              children: [
                _LanguageChip(label: 'All', selected: _language == null, onTap: () => _setLanguage(null)),
                const SizedBox(width: 8),
                for (final lang in _kLanguages) ...[
                  _LanguageChip(label: lang, selected: _language == lang, onTap: () => _setLanguage(lang)),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          const SizedBox(height: 6),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Recommended for you (Phase 2, item 9) — personalized horizontal row,
  // shown only on the default unfiltered browse view.
  // ---------------------------------------------------------------------
  Widget _buildRecommended() {
    final show = !_mine && _search.isEmpty && _language == null && !_recommendedLoading && _recommended.isNotEmpty;
    if (!show) return const SliverToBoxAdapter(child: SizedBox.shrink());
    return SliverToBoxAdapter(
      child: Padding(
        padding: const EdgeInsets.only(top: 6, bottom: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                'Recommended for you',
                style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15.5, color: _kNavy),
              ),
            ),
            SizedBox(
              height: 210,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 16),
                itemCount: _recommended.length,
                separatorBuilder: (_, __) => const SizedBox(width: 12),
                itemBuilder: (_, i) {
                  final c = _recommended[i];
                  return SizedBox(width: 150, child: _ClassroomCard(classroom: c, onTap: () => _openDetail(c)));
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Body: loading / error / empty / grid
  // ---------------------------------------------------------------------
  Widget _buildBody() {
    if (_loading) {
      return const SliverFillRemaining(
        hasScrollBody: false,
        child: Center(child: CircularProgressIndicator(color: _kNavy)),
      );
    }

    if (_error != null && _items.isEmpty) {
      return SliverFillRemaining(
        hasScrollBody: false,
        child: _MessageState(
          icon: Icons.wifi_off_rounded,
          title: 'Could not load',
          subtitle: _error!,
          actionLabel: 'Retry',
          onAction: () => _fetch(reset: true),
        ),
      );
    }

    if (_items.isEmpty) {
      return SliverFillRemaining(
        hasScrollBody: false,
        child: _MessageState(
          icon: _mine ? Icons.school_outlined : Icons.search_off_rounded,
          title: _mine ? 'You haven\'t created a classroom yet' : 'No classrooms found',
          subtitle: _mine
              ? 'Create your first classroom and start live sessions with students.'
              : 'Try a different search or language filter.',
          actionLabel: _mine ? 'Create Classroom' : null,
          onAction: _mine ? _openCreateClassroom : null,
        ),
      );
    }

    return SliverPadding(
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 20),
      sliver: SliverGrid(
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 2,
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.68,
        ),
        delegate: SliverChildBuilderDelegate(
          (context, index) {
            if (index == _items.length) {
              return const Center(
                child: Padding(
                  padding: EdgeInsets.all(12),
                  child: SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(strokeWidth: 2.4, color: _kNavy),
                  ),
                ),
              );
            }
            return _ClassroomCard(classroom: _items[index], onTap: () => _openDetail(_items[index]));
          },
          childCount: _items.length + (_loadingMore ? 1 : 0),
        ),
      ),
    );
  }
}

// ===========================================================================
// My Classrooms toggle
// ===========================================================================
class _MineToggle extends StatelessWidget {
  final bool mine;
  final ValueChanged<bool> onChanged;
  const _MineToggle({required this.mine, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(24)),
      child: Row(
        children: [
          Expanded(child: _segment('All Classes', !mine, () => onChanged(false))),
          Expanded(child: _segment('My Classrooms', mine, () => onChanged(true))),
        ],
      ),
    );
  }

  Widget _segment(String label, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(vertical: 9),
        decoration: BoxDecoration(
          color: active ? _kNavy : Colors.transparent,
          borderRadius: BorderRadius.circular(20),
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: TextStyle(
            color: active ? Colors.white : Colors.grey.shade700,
            fontWeight: FontWeight.w600,
            fontSize: 13,
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// Language filter chip
// ===========================================================================
class _LanguageChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;
  const _LanguageChip({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 16),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: selected ? _kGradient : null,
          color: selected ? null : Colors.white,
          borderRadius: BorderRadius.circular(20),
          border: selected ? null : Border.all(color: Colors.grey.shade300),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? Colors.white : Colors.grey.shade800,
            fontWeight: FontWeight.w600,
            fontSize: 13,
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// Classroom card
// ===========================================================================
class _ClassroomCard extends StatelessWidget {
  final Classroom classroom;
  final VoidCallback onTap;
  const _ClassroomCard({required this.classroom, required this.onTap});

  @override
  Widget build(BuildContext context) {
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
                    top: 8,
                    left: 8,
                    child: _pill(classroom.language, Colors.black.withOpacity(0.55)),
                  ),
                  if (classroom.isFlagged)
                    Positioned(
                      top: 8,
                      right: 8,
                      child: _pill('Flagged', Colors.red.shade700),
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
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: _kNavy, height: 1.2),
                  ),
                  if (classroom.subject.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      classroom.subject,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                    ),
                  ],
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      CircleAvatar(
                        radius: 9,
                        backgroundColor: _kBg,
                        backgroundImage: (classroom.teacher?.profilePicture != null &&
                                classroom.teacher!.profilePicture!.isNotEmpty)
                            ? CachedNetworkImageProvider(classroom.teacher!.profilePicture!)
                            : null,
                        child: (classroom.teacher?.profilePicture == null || classroom.teacher!.profilePicture!.isEmpty)
                            ? const Icon(Icons.person, size: 11, color: Colors.grey)
                            : null,
                      ),
                      const SizedBox(width: 5),
                      Expanded(
                        child: Text(
                          classroom.teacher?.fullName ?? 'Unknown',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 11.5, color: Colors.grey.shade700, fontWeight: FontWeight.w500),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      const Icon(Icons.star_rounded, size: 15, color: Color(0xFFFFAD33)),
                      const SizedBox(width: 2),
                      Text(
                        classroom.ratingCount > 0 ? classroom.ratingAvg.toStringAsFixed(1) : 'New',
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: _kNavy),
                      ),
                      if (classroom.ratingCount > 0) ...[
                        const SizedBox(width: 3),
                        Text('(${classroom.ratingCount})', style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
                      ],
                      const Spacer(),
                      if (classroom.classroomType == ClassroomType.organisation)
                        Icon(Icons.apartment_rounded, size: 14, color: Colors.grey.shade500),
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
      child: const Icon(Icons.cast_for_education_rounded, color: Colors.white, size: 34),
    );
  }

  Widget _pill(String text, Color bg) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(10)),
      child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w600)),
    );
  }
}

// ===========================================================================
// Empty / error state
// ===========================================================================
class _MessageState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;
  const _MessageState({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 56, color: Colors.grey.shade400),
            const SizedBox(height: 14),
            Text(title, textAlign: TextAlign.center, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: _kNavy)),
            const SizedBox(height: 6),
            Text(subtitle, textAlign: TextAlign.center, style: TextStyle(fontSize: 13, color: Colors.grey.shade600, height: 1.4)),
            if (actionLabel != null) ...[
              const SizedBox(height: 16),
              ElevatedButton(
                onPressed: onAction,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _kNavy,
                  foregroundColor: Colors.white,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 11),
                ),
                child: Text(actionLabel!),
              ),
            ],
          ],
        ),
      ),
    );
  }
}