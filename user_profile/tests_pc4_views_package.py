"""P-C4: user_profile/views.py became the user_profile/views/ package. Guards the re-export contract only (written, not run)."""
import importlib
import pkgutil

from django.test import SimpleTestCase

import user_profile.views as views_pkg

PUBLIC_CONTRACT = [
    "is_blocked_between", "is_restricted_between", "ProfileView", "FollowAPIView", "UpdateProfileView",
    "BuyCoinConfirmView", "GatewayWebhookSignatureError", "_verify_gateway_webhook_signature", "_wallet_busy_response",
    "StreakView", "ActivityHeartbeatView", "fraud", "DailyUsage", "Streak", "logger",
]


class UserProfileViewsPackageContractTests(SimpleTestCase):
    def test_old_import_paths_still_resolve(self):
        for name in PUBLIC_CONTRACT:
            self.assertTrue(hasattr(views_pkg, name), f"user_profile.views.{name} disappeared")

    def test_every_submodule_imports_cleanly(self):
        for mod in pkgutil.iter_modules(views_pkg.__path__):
            importlib.import_module(f"user_profile.views.{mod.name}")

    def test_reexport_is_the_same_object(self):
        from user_profile.views.wallet import BuyCoinConfirmView
        self.assertIs(views_pkg.BuyCoinConfirmView, BuyCoinConfirmView)

    def test_logger_name_unchanged(self):
        self.assertEqual(views_pkg.logger.name, "user_profile.views")
