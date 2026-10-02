// lib/post/screens/interests_screen.dart
//
// TASK 3 (production_readiness_tasks.md) — Interests entry point.
// Instagram-style multi-select chip picker over the same fixed category
// list the post composer already uses (Post.CATEGORY_CHOICES via
// GET /post/interests/, which folds category_taxonomy's list together
// with the caller's current selection in one call). Selecting/deselecting
// a chip is local-only until "Save" — Save calls PUT /post/interests/ with
// the full selected set, which HomeFeedView/ExploreFeedAPIView then use to
// bias feed/explore ranking towards those categories.
//
// Design-system: `ls_ui.dart` shared widgets (lsAppBar, lsBg, lsSnack) so
// this looks consistent with the rest of the app; no hardcoded colors.

import 'package:flutter/material.dart';

import '../../widgets/ls_ui.dart';
import '../services/api_service.dart';

class InterestsScreen extends StatefulWidget {
  const InterestsScreen({super.key});

  @override
  State<InterestsScreen> createState() => _InterestsScreenState();
}

class _InterestsScreenState extends State<InterestsScreen> {
  final _api = ApiService();

  bool _loading = true;
  bool _saving = false;
  bool _hasError = false;

  List<Map<String, dynamic>> _categories = const [];
  final Set<String> _selected = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final categories = await _api.getMyInterests();
      _selected
        ..clear()
        ..addAll(categories.where((c) => c['selected'] == true).map((c) => c['key'] as String));
      if (!mounted) return;
      setState(() {
        _categories = categories;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _hasError = true;
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await _api.updateMyInterests(_selected.toList());
      if (!mounted) return;
      lsSnack(context, 'Interests updated');
      Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      lsSnack(context, 'Could not save interests', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _toggle(String key) {
    setState(() {
      if (_selected.contains(key)) {
        _selected.remove(key);
      } else {
        _selected.add(key);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(
        context,
        title: 'Interests',
        actions: [
          TextButton(
            onPressed: (_loading || _saving) ? null : _save,
            child: _saving
                ? SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary),
                  )
                : Text('Save', style: TextStyle(color: cs.primary, fontWeight: FontWeight.w700)),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _hasError
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('Could not load interests', style: TextStyle(color: cs.onSurfaceVariant)),
                      const SizedBox(height: 12),
                      TextButton(onPressed: _load, child: const Text('Retry')),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.fromLTRB(kLsPad, 18, kLsPad, 32),
                  children: [
                    Text(
                      'Pick topics you care about and your feed and explore '
                      'will show more of them.',
                      style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant, height: 1.4),
                    ),
                    const SizedBox(height: 18),
                    Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      children: _categories.map((c) {
                        final key = c['key'] as String;
                        final label = c['label'] as String;
                        final active = _selected.contains(key);
                        return GestureDetector(
                          onTap: () => _toggle(key),
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 150),
                            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                            decoration: BoxDecoration(
                              color: active ? cs.primary : cs.surface,
                              borderRadius: BorderRadius.circular(22),
                              border: Border.all(color: active ? cs.primary : cs.outlineVariant),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                if (active) ...[
                                  Icon(Icons.check_rounded, size: 16, color: cs.onPrimary),
                                  const SizedBox(width: 6),
                                ],
                                Text(
                                  label,
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w700,
                                    color: active ? cs.onPrimary : cs.onSurface,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                  ],
                ),
    );
  }
}
