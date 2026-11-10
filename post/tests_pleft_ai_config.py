"""P-LEFT (P-J2) - GET /post/ai-assist/config/ follows the `ai_features` kill switch. WRITTEN, NOT RUN:
    python manage.py test post.tests_pleft_ai_config
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from copyrights.tests import mk_user


class AiConfigFollowsKillSwitchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(mk_user("aicfg"))

    @override_settings(FEATURE_FLAGS_OFF="ai_features")
    def test_switched_off_means_enabled_false_everywhere(self):
        from common import feature_flags as ff

        ff.invalidate_cache()
        r = self.client.get(reverse("post-ai-assist-config"))
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        self.assertFalse(r.data["enabled"])
        self.assertTrue(r.data["feature_flag_off"])
        for f in r.data["features"].values():
            if isinstance(f, dict):
                self.assertFalse(f["enabled"])

    @override_settings(FEATURE_FLAGS_OFF="")
    def test_switch_on_leaves_the_normal_answer(self):
        from common import feature_flags as ff

        ff.invalidate_cache()
        r = self.client.get(reverse("post-ai-assist-config"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["feature_flag_off"])
