"""P-C4: tuitionclass/models.py became the tuitionclass/models/ package. Guards the re-export + registry contract (written, not run)."""
import importlib
import pkgutil

from django.apps import apps
from django.test import SimpleTestCase

import tuitionclass.models as models_pkg

PUBLIC_CONTRACT = [
    "Classroom", "ClassSession", "ClassPass", "PassPurchase", "ClassJoinRequest", "ClassroomStaff", "ChunkedUpload",
    "MaxFileSizeValidator", "compute_attendance_percent_bulk", "generate_referral_code", "referral_code_for_user",
    "bump_classroom_list_cache_version", "get_notice_list_cache_version",
]


class TuitionclassModelsPackageContractTests(SimpleTestCase):
    def test_old_import_paths_still_resolve(self):
        for name in PUBLIC_CONTRACT:
            self.assertTrue(hasattr(models_pkg, name), f"tuitionclass.models.{name} disappeared")

    def test_every_submodule_imports_cleanly(self):
        for mod in pkgutil.iter_modules(models_pkg.__path__):
            importlib.import_module(f"tuitionclass.models.{mod.name}")

    def test_models_still_belong_to_tuitionclass_app(self):
        for name in ("Classroom", "PassPurchase", "ChunkedUpload", "Notice", "ReferralCode"):
            model = apps.get_model("tuitionclass", name)
            self.assertEqual(model._meta.app_label, "tuitionclass")
            self.assertIs(getattr(models_pkg, name), model)

    def test_forward_references_are_bound(self):
        # methods use these at call time; they must be the real objects, not the `None` placeholders
        from tuitionclass.models import classroom, passes, schedules, cache_versions
        self.assertIs(classroom.PassPurchase, models_pkg.PassPurchase)
        self.assertIs(classroom.ClassSchedule, models_pkg.ClassSchedule)
        self.assertIs(classroom.referral_code_for_user, models_pkg.referral_code_for_user)
        self.assertIs(passes.Coupon, models_pkg.Coupon)
        self.assertIs(schedules.SessionParticipant, models_pkg.SessionParticipant)
        self.assertIs(cache_versions.Notice, models_pkg.Notice)

    def test_logger_name_unchanged(self):
        import logging
        from tuitionclass.models import passes
        self.assertEqual(logging.getLogger("tuitionclass.models").name, "tuitionclass.models")
        self.assertTrue(hasattr(passes, "logging"))
