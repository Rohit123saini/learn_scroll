"""P-C4: campus/views.py became the campus/views/ package. Guards the re-export contract only (written, not run)."""
import importlib
import pkgutil

from django.test import SimpleTestCase

import campus.views as views_pkg

PUBLIC_CONTRACT = [
    "CampusMemberScopedMixin", "CampusViewSet", "AttendanceViewSet", "NoticeViewSet", "FeeInvoiceViewSet",
    "FeePaymentViewSet", "CampusDoubtViewSet", "MyClassesView", "ParentLinkVerifyView", "get_my_campus_ids",
    "_truthy", "RosterAPIError", "bridge", "logger",
]


class CampusViewsPackageContractTests(SimpleTestCase):
    def test_old_import_paths_still_resolve(self):
        for name in PUBLIC_CONTRACT:
            self.assertTrue(hasattr(views_pkg, name), f"campus.views.{name} disappeared")

    def test_every_submodule_imports_cleanly(self):
        for mod in pkgutil.iter_modules(views_pkg.__path__):
            importlib.import_module(f"campus.views.{mod.name}")

    def test_reexport_is_the_same_object(self):
        from campus.views.fees import FeeInvoiceViewSet
        self.assertIs(views_pkg.FeeInvoiceViewSet, FeeInvoiceViewSet)

    def test_logger_name_unchanged(self):
        # campus/tests.py uses assertLogs("campus.views")
        self.assertEqual(views_pkg.logger.name, "campus.views")
