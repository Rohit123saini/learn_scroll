import 'package:flutter/foundation.dart' show debugPrint;
import 'package:flutter/material.dart';

/// Keys jinke liye abhi request in-flight hai (see [optimisticUpdate.key]).
final Set<Object> _optimisticInFlight = <Object>{};

/// Optimistic-UI helper: UI turant badlo, API fail ho to rollback + snackbar.
///
/// Flow:
///   1. [apply] — turant chalta hai (aam taur pe `setState` me local state flip).
///   2. [request] — asli API call.
///   3. success -> [onSuccess] (server ki truth se counts/state reconcile karne ke liye).
///   4. fail    -> [rollback] + error snackbar; `false` return hota hai.
///
/// Returns `true` agar request succeed hui, `false` agar fail hui ya [key] ke
/// duplicate call ki wajah se skip ho gayi.
///
/// Notes:
///  * [apply] / [rollback] / [onSuccess] await ke baad bhi chal sakte hain, to
///    unke andar `setState` se pehle `if (!mounted) return;` lagao, ya
///    [isMounted] pass karo — tab helper unmounted hone pe rollback/onSuccess
///    skip kar deta hai (snackbar phir bhi dikhta hai, kyunki ScaffoldMessenger
///    screen se upar rehta hai).
///  * [key] (optional): same key ka request pehle se chal raha ho to naya call
///    ignore ho jata hai — rapid double-tap pe rollback galat value pe nahi
///    jaata. Example key: `'like:${post.id}'`.
///  * Rollback ke liye apply se pehle ki value apne closure me capture karo:
///    ```dart
///    final old = post.myReaction;
///    optimisticUpdate<Map>(
///      context: context,
///      key: 'react:${post.id}',
///      apply: () => setState(() => post.myReaction = 'like'),
///      rollback: () => setState(() => post.myReaction = old),
///      request: () => HomeFeedService.toggleReaction(post.id, 'like'),
///      errorMessage: l10n.saveFailed,
///    );
///    ```
Future<bool> optimisticUpdate<T>({
  required BuildContext context,
  required VoidCallback apply,
  required VoidCallback rollback,
  required Future<T> Function() request,
  void Function(T result)? onSuccess,
  bool Function()? isMounted,
  String errorMessage = "Couldn't save that. Please try again.",
  Object? key,
}) async {
  if (key != null && !_optimisticInFlight.add(key)) return false;

  // Messenger await se pehle pakad lo — baad me `context` use nahi karna.
  final messenger = ScaffoldMessenger.maybeOf(context);

  try {
    apply();

    T result;
    try {
      result = await request();
    } catch (e, st) {
      debugPrint('optimisticUpdate failed: $e\n$st');
      if (isMounted?.call() ?? true) rollback();
      messenger
        ?..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          content: Text(errorMessage),
          behavior: SnackBarBehavior.floating,
        ));
      return false;
    }

    // onSuccess ka apna error rollback trigger na kare — isliye try ke bahar.
    if (isMounted?.call() ?? true) onSuccess?.call(result);
    return true;
  } finally {
    if (key != null) _optimisticInFlight.remove(key);
  }
}
