"""P-C4: post/views.py became the post/views/ package. These tests only guard the re-export contract (written, not run)."""
import importlib
import pkgutil

from django.test import SimpleTestCase

import post.views as views_pkg

# Names other code imports from `post.views` (urls.py, core/home_bootstrap, doubts adapter, management commands, tests).
PUBLIC_CONTRACT = [
    "serve_media_with_range", "StoryListAPIView", "StoryLiveAPIView", "build_feed_ad_config",
    "PostAnswerListCreateAPIView", "PostAnswerMarkBestAPIView", "HomeFeedView", "PostCreateAPIView",
    "StoryCreateAPIView", "StandardResultsSetPagination", "_first_error_text", "_home_base_qs",
    "_video_and_velocity_boost", "requests", "logger", "User",
]


class ViewsPackageContractTests(SimpleTestCase):
    def test_old_import_paths_still_resolve(self):
        for name in PUBLIC_CONTRACT:
            self.assertTrue(hasattr(views_pkg, name), f"post.views.{name} disappeared")

    def test_every_submodule_imports_cleanly(self):
        for mod in pkgutil.iter_modules(views_pkg.__path__):
            importlib.import_module(f"post.views.{mod.name}")

    def test_reexport_is_the_same_object(self):
        from post.views.feed import HomeFeedView
        self.assertIs(views_pkg.HomeFeedView, HomeFeedView)

    def test_logger_name_unchanged(self):
        self.assertEqual(views_pkg.logger.name, "post.views")
