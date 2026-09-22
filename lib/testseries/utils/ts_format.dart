import 'dart:math';

/// Timer / duration display: `mm:ss`, ya 1 ghante se zyada ho to `h:mm:ss`.
String tsFormatRemaining(Duration d) {
  if (d.isNegative) d = Duration.zero;
  final h = d.inHours;
  final m = (d.inMinutes % 60).toString().padLeft(2, '0');
  final s = (d.inSeconds % 60).toString().padLeft(2, '0');
  return h > 0 ? '$h:$m:$s' : '$m:$s';
}

/// Deterministic shuffle: same `seed` → hamesha same order.
///
/// Kyun: `order` type ke question me options backend me shayad sahi
/// sequence me hi stored hon. Wahi order screen pe dikha diya to jawab
/// leak ho jaata hai. Random shuffle resume pe alag aata, isliye seed =
/// attemptId + questionId. `String.hashCode` runs ke beech stable nahi hota,
/// isliye apna FNV-1a hash.
List<T> tsSeededShuffle<T>(List<T> input, String seed) {
  final list = List<T>.of(input);
  final rnd = Random(_fnv1a(seed));
  for (var i = list.length - 1; i > 0; i--) {
    final j = rnd.nextInt(i + 1);
    final tmp = list[i];
    list[i] = list[j];
    list[j] = tmp;
  }
  return list;
}

int _fnv1a(String s) {
  var h = 0x811c9dc5;
  for (final c in s.codeUnits) {
    h ^= c;
    h = (h * 0x01000193) & 0x7fffffff;
  }
  return h;
}
