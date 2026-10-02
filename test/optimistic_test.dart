import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
// NOTE: package name apne pubspec.yaml ke `name:` se badal lena.
import 'package:learnscroll/utils/optimistic.dart';
import 'package:learnscroll/widgets/skeletons.dart';

class _Harness extends StatefulWidget {
  final Future<void> Function() request;
  const _Harness({required this.request});
  @override
  State<_Harness> createState() => _HarnessState();
}

class _HarnessState extends State<_Harness> {
  bool liked = false;
  int likes = 10;
  bool? lastResult;

  Future<void> tap() async {
    final oldLiked = liked, oldLikes = likes;
    lastResult = await optimisticUpdate<void>(
      context: context,
      key: 'like',
      apply: () => setState(() {
        liked = !oldLiked;
        likes = oldLiked ? oldLikes - 1 : oldLikes + 1;
      }),
      rollback: () => setState(() {
        liked = oldLiked;
        likes = oldLikes;
      }),
      request: widget.request,
      errorMessage: 'Like failed',
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        body: Center(
          child: TextButton(onPressed: tap, child: Text('liked=$liked likes=$likes')),
        ),
      );
}

Future<_HarnessState> _pump(WidgetTester t, Future<void> Function() request) async {
  await t.pumpWidget(MaterialApp(home: _Harness(request: request)));
  return t.state<_HarnessState>(find.byType(_Harness));
}

void main() {
  testWidgets('UI turant badalti hai, request pending hone par bhi', (t) async {
    final c = Completer<void>();
    await _pump(t, () => c.future);
    await t.tap(find.byType(TextButton));
    await t.pump();
    expect(find.text('liked=true likes=11'), findsOneWidget); // request abhi pending
    c.complete();
    await t.pump();
    expect(find.text('liked=true likes=11'), findsOneWidget);
    expect(find.text('Like failed'), findsNothing);
  });

  testWidgets('fail hone par rollback + snackbar', (t) async {
    final s = await _pump(t, () async => throw Exception('boom'));
    await t.tap(find.byType(TextButton));
    await t.pump(); // apply
    await t.pump(); // request fail -> rollback + snackbar
    expect(find.text('liked=false likes=10'), findsOneWidget);
    expect(find.text('Like failed'), findsOneWidget);
    expect(s.lastResult, false);
  });

  testWidgets('same key ka duplicate tap ignore hota hai', (t) async {
    final c = Completer<void>();
    await _pump(t, () => c.future);
    await t.tap(find.byType(TextButton));
    await t.pump();
    await t.tap(find.byType(TextButton)); // in-flight, ignore
    await t.pump();
    expect(find.text('liked=true likes=11'), findsOneWidget);
    c.complete();
    await t.pump();
  });

  testWidgets('skeletons render without layout errors', (t) async {
    await t.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: Column(children: [
            LsProfileHeaderSkeleton(),
            LsPostGridSkeleton(count: 6),
            LsListSkeleton(count: 2),
          ]),
        ),
      ),
    ));
    await t.pump(const Duration(milliseconds: 300));
    expect(tester_noExceptions(t), isTrue);
  });
}

bool tester_noExceptions(WidgetTester t) => t.takeException() == null;
