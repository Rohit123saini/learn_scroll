// lib/support/screens/feature_board_screen.dart
// "I want this" board: browse, search, vote (toggle), suggest.
// TODO(l10n): screen copy is English-only for now.

import 'package:flutter/material.dart';

import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../support_service.dart';

String _fStatus(String s) => switch (s) {
      'planned' => 'Planned',
      'in_progress' => 'In progress',
      'shipped' => 'Shipped',
      'declined' => 'Not planned',
      _ => '',
    };

class FeatureBoardScreen extends StatefulWidget {
  const FeatureBoardScreen({super.key});
  @override
  State<FeatureBoardScreen> createState() => _FeatureBoardScreenState();
}

class _FeatureBoardScreenState extends State<FeatureBoardScreen> {
  List<FeatureRequest>? _items;
  String? _error;
  String _sort = 'top';
  String _q = '';
  final Set<String> _voting = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await SupportService.featureRequests(sort: _sort, query: _q);
      if (mounted) {
        setState(() {
          _items = r;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _vote(int i) async {
    final f = _items![i];
    if (_voting.contains(f.id)) return;
    _voting.add(f.id);
    // optimistic
    setState(() => _items![i] = f.copyWith(hasVoted: !f.hasVoted, votes: f.votes + (f.hasVoted ? -1 : 1)));
    try {
      final r = await SupportService.toggleVote(f.id);
      if (mounted) setState(() => _items![i] = _items![i].copyWith(hasVoted: r.voted, votes: r.votes));
    } catch (e) {
      if (mounted) {
        setState(() => _items![i] = f);
        lsSnack(context, e.toString(), error: true);
      }
    } finally {
      _voting.remove(f.id);
    }
  }

  Future<void> _suggest() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => const _SuggestSheet(),
    );
    if (created == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget list;
    if (_error != null && _items == null) {
      list = ErrorStateWidget(title: _error!, retryLabel: 'Retry', onRetry: _load);
    } else if (_items == null) {
      list = const Center(child: CircularProgressIndicator());
    } else if (_items!.isEmpty) {
      list = EmptyStateWidget(
        title: _q.isEmpty ? 'No requests yet' : 'Nothing found',
        subtitle: 'Be the first to suggest a feature.',
        icon: Icons.lightbulb_outline_rounded,
        actionLabel: 'Suggest a feature',
        onAction: _suggest,
      );
    } else {
      list = RefreshIndicator(
        onRefresh: _load,
        child: ListView.separated(
          padding: const EdgeInsets.fromLTRB(kLsPad, 4, kLsPad, 90),
          itemCount: _items!.length,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (_, i) {
            final f = _items![i];
            final badge = _fStatus(f.status);
            return LsCard(
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Semantics(
                  button: true,
                  label: f.hasVoted ? 'Remove your vote. ${f.votes} votes' : 'Vote. ${f.votes} votes',
                  child: InkWell(
                    borderRadius: BorderRadius.circular(12),
                    onTap: f.votingClosed ? null : () => _vote(i),
                    child: Container(
                      width: 52,
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      decoration: BoxDecoration(
                        color: f.hasVoted ? cs.primaryContainer : cs.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Column(children: [
                        Icon(Icons.arrow_drop_up_rounded, size: 26, color: f.hasVoted ? cs.primary : cs.onSurfaceVariant),
                        Text('${f.votes}', style: const TextStyle(fontWeight: FontWeight.w800)),
                      ]),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(f.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                    if (f.description.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(f.description, maxLines: 3, overflow: TextOverflow.ellipsis, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
                    ],
                    if (badge.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Text(badge, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary)),
                    ],
                  ]),
                ),
              ]),
            );
          },
        ),
      );
    }
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Feature requests'),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 6),
          child: Row(children: [
            Expanded(
              child: TextField(
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(hintText: 'Search', prefixIcon: Icon(Icons.search_rounded), isDense: true, border: OutlineInputBorder()),
                onSubmitted: (v) {
                  _q = v.trim();
                  _load();
                },
              ),
            ),
            const SizedBox(width: 8),
            SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [ButtonSegment(value: 'top', label: Text('Top')), ButtonSegment(value: 'new', label: Text('New'))],
              selected: {_sort},
              onSelectionChanged: (s) {
                setState(() => _sort = s.first);
                _load();
              },
            ),
          ]),
        ),
        Expanded(child: list),
      ]),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _suggest,
        icon: const Icon(Icons.add_rounded),
        label: const Text('Suggest'),
      ),
    );
  }
}

class _SuggestSheet extends StatefulWidget {
  const _SuggestSheet();
  @override
  State<_SuggestSheet> createState() => _SuggestSheetState();
}

class _SuggestSheetState extends State<_SuggestSheet> {
  final _title = TextEditingController();
  final _desc = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _title.dispose();
    _desc.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final t = _title.text.trim();
    if (t.length < 4) {
      lsSnack(context, 'Please write a short title.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await SupportService.createFeatureRequest(title: t, description: _desc.text.trim());
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        lsSnack(context, e.toString(), error: true);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, MediaQuery.of(context).viewInsets.bottom + 16),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Suggest a feature', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
        const SizedBox(height: 12),
        TextField(
          controller: _title,
          maxLength: 120,
          decoration: const InputDecoration(labelText: 'I want...', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _desc,
          maxLength: 1000,
          minLines: 2,
          maxLines: 5,
          decoration: const InputDecoration(labelText: 'Details (optional)', alignLabelWithHint: true, border: OutlineInputBorder()),
        ),
        const SizedBox(height: 10),
        LsPrimaryButton(label: 'Submit', loading: _busy, onPressed: _busy ? null : _submit),
      ]),
    );
  }
}
