import 'package:flutter/material.dart';
import '../utils/optimistic.dart';
import '../widgets/skeletons.dart';

/// C3 demo — sirf dekhne/test karne ke liye, kisi route me wired nahi.
/// Temporarily `home: const C3DemoScreen()` laga ke run karo.
///
///  * "Reload" dabao -> 2 sec skeleton (profile header + grid + list rows).
///  * Like button turant toggle hota hai; "Fail next request" ON ho to API
///    fail hoti hai -> like wapas rollback + snackbar.
class C3DemoScreen extends StatefulWidget {
  const C3DemoScreen({super.key});
  @override
  State<C3DemoScreen> createState() => _C3DemoScreenState();
}

class _C3DemoScreenState extends State<C3DemoScreen> {
  bool _loading = true;
  bool _liked = false;
  int _likes = 12;
  bool _failNext = true;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    await Future.delayed(const Duration(seconds: 2));
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _toggleLike() {
    final oldLiked = _liked, oldLikes = _likes;
    return optimisticUpdate<void>(
      context: context,
      key: 'demo-like',
      isMounted: () => mounted,
      apply: () => setState(() {
        _liked = !oldLiked;
        _likes = oldLiked ? oldLikes - 1 : oldLikes + 1;
      }),
      rollback: () => setState(() {
        _liked = oldLiked;
        _likes = oldLikes;
      }),
      request: () async {
        await Future.delayed(const Duration(milliseconds: 800));
        if (_failNext) throw Exception('demo failure');
      },
      errorMessage: "Couldn't update like. Please try again.",
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('C3 demo'), actions: [
        IconButton(icon: const Icon(Icons.refresh), onPressed: _reload),
      ]),
      body: ListView(children: [
        if (_loading) ...[
          const LsProfileHeaderSkeleton(),
          const LsPostGridSkeleton(count: 6),
          const SizedBox(height: 12),
          const LsListSkeleton(count: 3, trailingChip: true),
        ] else ...[
          SwitchListTile(
            title: const Text('Fail next request'),
            value: _failNext,
            onChanged: (v) => setState(() => _failNext = v),
          ),
          ListTile(
            title: Text('$_likes likes'),
            trailing: IconButton(
              icon: Icon(_liked ? Icons.favorite : Icons.favorite_border, color: _liked ? Colors.red : null),
              onPressed: _toggleLike,
            ),
          ),
        ],
      ]),
    );
  }
}