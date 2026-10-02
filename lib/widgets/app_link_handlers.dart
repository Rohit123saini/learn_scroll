import 'package:flutter/material.dart';

import '../post/screens/hashtag_posts_screen.dart';
import '../profile/screens/target_profile.dart';

// ============================================================
// P7-FE — default navigation for tappable @mentions / #hashtags (LinkifiedText).
// Kept in its own file so the app's routing for these two lives in ONE place: if you
// later add deep-link routes / a different hashtag screen, change only this file.
// ============================================================

void openMentionProfile(BuildContext context, String username) {
  Navigator.push(context, MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)));
}

void openHashtagFeed(BuildContext context, String tag) {
  Navigator.push(context, MaterialPageRoute(builder: (_) => HashtagPostsScreen(tag: tag)));
}
