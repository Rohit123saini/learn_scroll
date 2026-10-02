// lib/onboarding/screens/onboarding_screen.dart
//
// TASK G18 (growth_and_feature_tasks.md — Empty states & first-time-user
// onboarding).
//
// A brand-new user with zero follows/campus/tests sees mostly blank
// screens on first open — this is the fix: a short 3-step flow pushed
// right after signup (see `login/signup_screen.dart` and
// `login/complete_profile_screen.dart`, both of which now push this
// screen instead of going straight to `HomeScreen`):
//   1. Pick interests   -> `post.UserInterestsAPIView` (TASK 3, already
//                           existed — this screen is its first-ever caller
//                           from a fresh signup, not a new endpoint).
//   2. Suggested people to follow (+ a few campuses to know about)
//                        -> `core.OnboardingSuggestionsView`.
//   3. One sample test to try
//                        -> same view's `sample_test_series`.
//
// Every step is skippable — `_finish(skipped: true)` fires from the
// AppBar's "Skip" action on any page — because gating the whole app
// behind a mandatory wizard is worse than an empty feed for a user who
// just wants to look around first.
import 'package:flutter/material.dart';

import '../../home.dart';
import '../../services/onboarding_service.dart';
import '../../testseries/screens/test_series_detail_screen.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';

/// (value, display label) — mirrors `post.models.Post.CATEGORY_CHOICES`
/// exactly (backend, post/models.py) since `saveInterests()` sends the
/// value straight through to `UserInterestsUpdateSerializer`.
const List<MapEntry<String, String>> _kCategoryChoices = [
  MapEntry('general', 'General'),
  MapEntry('tech', 'Technology'),
  MapEntry('jobs', 'Jobs'),
  MapEntry('news', 'News'),
  MapEntry('education', 'Education'),
  MapEntry('business', 'Business'),
  MapEntry('entertainment', 'Entertainment'),
  MapEntry('sports', 'Sports'),
  MapEntry('lifestyle', 'Lifestyle'),
  MapEntry('other', 'Other'),
];

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  static const int _pageCount = 3;

  final PageController _pageController = PageController();
  int _page = 0;

  final Set<String> _selectedCategories = {};
  bool _savingInterests = false;

  bool _loadingSuggestions = true;
  OnboardingSuggestions _suggestions = OnboardingSuggestions.empty;
  final Set<int> _followingIds = {};
  final Set<int> _followBusyIds = {};

  bool _finishing = false;

  @override
  void initState() {
    super.initState();
    _loadSuggestions();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  Future<void> _loadSuggestions() async {
    final result = await OnboardingService.fetchSuggestions();
    if (!mounted) return;
    setState(() {
      _suggestions = result;
      _loadingSuggestions = false;
    });
  }

  void _goToPage(int index) {
    _pageController.animateToPage(
      index,
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOut,
    );
  }

  Future<void> _onNextFromInterests() async {
    setState(() => _savingInterests = true);
    // Fire-and-forget-ish: don't let a slow/failed save block navigation
    // — `saveInterests()` already fails silently on the network side
    // (see its own docstring), so a blocked "Next" button here would
    // just be a second point of failure for the same non-critical call.
    await OnboardingService.saveInterests(_selectedCategories.toList());
    if (!mounted) return;
    setState(() => _savingInterests = false);
    _goToPage(1);
  }

  Future<void> _toggleFollow(SuggestedUser user) async {
    if (_followBusyIds.contains(user.id)) return;
    final alreadyFollowing = _followingIds.contains(user.id);
    setState(() => _followBusyIds.add(user.id));

    final ok = await OnboardingService.followUser(user.id);

    if (!mounted) return;
    setState(() {
      _followBusyIds.remove(user.id);
      if (ok) {
        if (alreadyFollowing) {
          _followingIds.remove(user.id);
        } else {
          _followingIds.add(user.id);
        }
      }
    });
  }

  Future<void> _finish({required bool skipped}) async {
    if (_finishing) return;
    setState(() => _finishing = true);
    await OnboardingService.markComplete(skipped: skipped);
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => const HomeScreen()),
    );
  }

  void _openSampleTest(SampleTestSeries series) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => TestSeriesDetailScreen(seriesId: series.id),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: false,
        title: Text('Step ${_page + 1} of $_pageCount'),
        actions: [
          TextButton(
            onPressed: _finishing ? null : () => _finish(skipped: true),
            child: const Text('Skip'),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            _ProgressDots(current: _page, count: _pageCount),
            Expanded(
              child: PageView(
                controller: _pageController,
                physics: const NeverScrollableScrollPhysics(),
                onPageChanged: (i) => setState(() => _page = i),
                children: [
                  _InterestsStep(
                    selected: _selectedCategories,
                    onToggle: (value) {
                      setState(() {
                        if (_selectedCategories.contains(value)) {
                          _selectedCategories.remove(value);
                        } else {
                          _selectedCategories.add(value);
                        }
                      });
                    },
                    onNext: _onNextFromInterests,
                    saving: _savingInterests,
                  ),
                  _SuggestedFollowsStep(
                    loading: _loadingSuggestions,
                    suggestions: _suggestions,
                    followingIds: _followingIds,
                    busyIds: _followBusyIds,
                    onToggleFollow: _toggleFollow,
                    onNext: () => _goToPage(2),
                  ),
                  _SampleTestStep(
                    loading: _loadingSuggestions,
                    series: _suggestions.sampleTests,
                    onTrySeries: _openSampleTest,
                    onFinish: _finishing ? null : () => _finish(skipped: false),
                    finishing: _finishing,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProgressDots extends StatelessWidget {
  final int current;
  final int count;
  const _ProgressDots({required this.current, required this.count});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: List.generate(count, (i) {
          final active = i == current;
          return AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            margin: const EdgeInsets.symmetric(horizontal: 4),
            width: active ? 22 : 8,
            height: 8,
            decoration: BoxDecoration(
              color: active ? cs.primary : cs.outlineVariant,
              borderRadius: BorderRadius.circular(4),
            ),
          );
        }),
      ),
    );
  }
}

// ============================================================
// Step 1 — pick interests
// ============================================================
class _InterestsStep extends StatelessWidget {
  final Set<String> selected;
  final ValueChanged<String> onToggle;
  final VoidCallback onNext;
  final bool saving;

  const _InterestsStep({
    required this.selected,
    required this.onToggle,
    required this.onNext,
    required this.saving,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
            children: [
              Text(
                "What are you into?",
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: cs.onSurface),
              ),
              const SizedBox(height: 8),
              Text(
                "Pick a few topics — your feed and test-series suggestions start here. You can change these anytime.",
                style: TextStyle(fontSize: 13.5, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 20),
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: _kCategoryChoices.map((entry) {
                  final isSelected = selected.contains(entry.key);
                  return GestureDetector(
                    onTap: () => onToggle(entry.key),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 150),
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                      decoration: BoxDecoration(
                        color: isSelected ? cs.primary : cs.surfaceVariant,
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: isSelected ? cs.primary : cs.outlineVariant),
                      ),
                      child: Text(
                        entry.value,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: isSelected ? cs.onPrimary : cs.onSurface,
                        ),
                      ),
                    ),
                  );
                }).toList(),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: LsPrimaryButton(
            label: selected.isEmpty ? 'Skip for now' : 'Next',
            loading: saving,
            onPressed: onNext,
          ),
        ),
      ],
    );
  }
}

// ============================================================
// Step 2 — suggested people to follow (+ a few campuses to know about)
// ============================================================
class _SuggestedFollowsStep extends StatelessWidget {
  final bool loading;
  final OnboardingSuggestions suggestions;
  final Set<int> followingIds;
  final Set<int> busyIds;
  final ValueChanged<SuggestedUser> onToggleFollow;
  final VoidCallback onNext;

  const _SuggestedFollowsStep({
    required this.loading,
    required this.suggestions,
    required this.followingIds,
    required this.busyIds,
    required this.onToggleFollow,
    required this.onNext,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
            children: [
              Text(
                "Follow a few people",
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: cs.onSurface),
              ),
              const SizedBox(height: 8),
              Text(
                "Your feed won't be empty once you follow a few people who post about what you like.",
                style: TextStyle(fontSize: 13.5, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 18),
              if (loading)
                const LsListSkeleton(count: 5)
              else if (suggestions.users.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 24),
                  child: Text(
                    "No suggestions right now — you can find people to follow anytime from Search.",
                    style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
                  ),
                )
              else
                ...suggestions.users.map((user) {
                  final isFollowing = followingIds.contains(user.id);
                  final isBusy = busyIds.contains(user.id);
                  return LsCard(
                    margin: const EdgeInsets.only(bottom: 10),
                    child: Row(
                      children: [
                        CircleAvatar(
                          radius: 20,
                          backgroundColor: cs.surfaceVariant,
                          backgroundImage:
                              (user.profilePhoto != null && user.profilePhoto!.isNotEmpty)
                                  ? NetworkImage(user.profilePhoto!)
                                  : null,
                          child: (user.profilePhoto == null || user.profilePhoto!.isEmpty)
                              ? Text(user.displayName.isNotEmpty ? user.displayName[0].toUpperCase() : '?')
                              : null,
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(user.displayName,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
                              Text('@${user.username}',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                            ],
                          ),
                        ),
                        const SizedBox(width: 8),
                        isFollowing
                            ? LsOutlineButton(
                                label: isBusy ? '...' : 'Following',
                                onPressed: isBusy ? null : () => onToggleFollow(user),
                              )
                            : LsPrimaryButton(
                                label: 'Follow',
                                expanded: false,
                                loading: isBusy,
                                onPressed: () => onToggleFollow(user),
                              ),
                      ],
                    ),
                  );
                }),
              if (!loading && suggestions.campuses.isNotEmpty) ...[
                const SizedBox(height: 16),
                LsSectionHead(title: 'Campuses on LearnScroll', padding: EdgeInsets.zero),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: suggestions.campuses.map((c) {
                    return Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        color: cs.surfaceVariant,
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: Text(
                        '${c.name} · ${c.type}',
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 4),
                Text(
                  "Ask your school/coaching for an invite code to join their campus.",
                  style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                ),
              ],
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: LsPrimaryButton(label: 'Next', onPressed: onNext),
        ),
      ],
    );
  }
}

// ============================================================
// Step 3 — try one sample test
// ============================================================
class _SampleTestStep extends StatelessWidget {
  final bool loading;
  final List<SampleTestSeries> series;
  final ValueChanged<SampleTestSeries> onTrySeries;
  final VoidCallback? onFinish;
  final bool finishing;

  const _SampleTestStep({
    required this.loading,
    required this.series,
    required this.onTrySeries,
    required this.onFinish,
    required this.finishing,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
            children: [
              Text(
                "Try a free test",
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: cs.onSurface),
              ),
              const SizedBox(height: 8),
              Text(
                "See how test series work on LearnScroll — these are free, no commitment.",
                style: TextStyle(fontSize: 13.5, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 18),
              if (loading)
                const LsGridCardSkeleton(height: 110)
              else if (series.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 24),
                  child: Text(
                    "No sample tests available right now — you'll find plenty in the Test Series tab.",
                    style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
                  ),
                )
              else
                ...series.map((s) => LsCard(
                      margin: const EdgeInsets.only(bottom: 10),
                      onTap: () => onTrySeries(s),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(s.title,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
                          const SizedBox(height: 6),
                          Row(
                            children: [
                              LsStatusChip(label: 'FREE', color: Colors.green[700]!),
                              const SizedBox(width: 8),
                              Text('${s.durationMinutes} min · ${s.questionCount} questions',
                                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                            ],
                          ),
                          if (s.avgRating != null) ...[
                            const SizedBox(height: 6),
                            Row(
                              children: [
                                Icon(Icons.star, size: 14, color: Colors.amber[700]),
                                const SizedBox(width: 4),
                                Text('${s.avgRating!.toStringAsFixed(1)} (${s.reviewCount})',
                                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                              ],
                            ),
                          ],
                          const SizedBox(height: 10),
                          LsOutlineButton(label: 'Start now', onPressed: () => onTrySeries(s)),
                        ],
                      ),
                    )),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: LsPrimaryButton(label: 'Finish', loading: finishing, onPressed: onFinish),
        ),
      ],
    );
  }
}
