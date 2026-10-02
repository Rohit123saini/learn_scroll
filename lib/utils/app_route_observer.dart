import 'package:flutter/widgets.dart';

/// App-wide [RouteObserver]. Register it once on `MaterialApp.navigatorObservers`
/// (main.dart) and any screen can mix in [RouteAware] and subscribe to it to
/// learn when another route (a page, dialog or bottom sheet) is pushed on top of
/// it / popped off again. Used by the Reels screen to pause & resume playback.
final RouteObserver<ModalRoute<void>> appRouteObserver = RouteObserver<ModalRoute<void>>();
