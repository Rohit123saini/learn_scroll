"""
core/tests_home_bootstrap.py - H4 `GET /core/home-bootstrap/`.

Run:  python manage.py test core.tests_home_bootstrap

Written by reading the code only (not executed when it was written).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import home_bootstrap
from .models import Notification

User = get_user_model()

URL = "/core/home-bootstrap/"

TOP_LEVEL_KEYS = {
    "version",
    "server_now",
    "me",
    "role",
    "unread",
    "daily_challenge",
    "feed_ad_config",
    "stories",
    "stories_live",
    "classroom_switcher",
    "invite",
    "errors",
}


def _user(name):
    return User.objects.create_user(username=name, password="pw12345!")


class HomeBootstrapTests(TestCase):
    def setUp(self):
        self.user = _user("h4_user")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_requires_login(self):
        r = APIClient().get(URL)
        self.assertIn(r.status_code, (401, 403))

    def test_payload_has_every_section(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(TOP_LEVEL_KEYS.issubset(body.keys()), body.keys())
        self.assertEqual(set(body["classroom_switcher"].keys()), {"classrooms", "live_now", "dashboard"})
        self.assertEqual(body["version"], home_bootstrap.BOOTSTRAP_VERSION)

    def test_me_matches_me_context_endpoint(self):
        body = self.client.get(URL).json()
        ctx = self.client.get("/core/me/context/").json()
        self.assertEqual(body["me"], ctx)

    def test_feed_ad_config_matches_its_own_endpoint(self):
        body = self.client.get(URL).json()
        own = self.client.get("/post/feed/ad-config/").json()
        self.assertEqual(body["feed_ad_config"], own)

    def test_unread_split_matches_unread_count_endpoint(self):
        # No rows: both endpoints agree on zero; with rows the same `_filter_by_source` is used by both.
        body = self.client.get(URL).json()
        bell = self.client.get("/core/notifications/unread-count/?source=tuitionclass").json()["unread_count"]
        chats = self.client.get("/core/notifications/unread-count/?source=message").json()["unread_count"]
        self.assertEqual(body["unread"], {"bell": bell, "chats": chats})
        self.assertEqual(Notification.objects.for_user(self.user).unread().count(), bell + chats)

    def test_plain_user_role_signals_default_to_nothing(self):
        role = self.client.get(URL).json()["role"]
        self.assertFalse(role["is_campus_management"])
        self.assertFalse(role["is_campus_teacher"])
        self.assertFalse(role["is_campus_student"])
        self.assertFalse(role["has_parent_links"])
        self.assertFalse(role["teaches_classroom"])
        self.assertEqual(role["campus_ids"], {"management": None, "teacher": None, "student": None, "parent": None})

    def test_onboarding_role_is_the_hint(self):
        self.user.onboarding_role = "teacher"
        self.user.save(update_fields=["onboarding_role"])
        self.assertEqual(self.client.get(URL).json()["role"]["hint"], "teacher")

    def test_one_failing_section_does_not_break_the_rest(self):
        with mock.patch.object(home_bootstrap, "unread_counts", side_effect=RuntimeError("boom")):
            r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["unread"])
        self.assertIn("unread", body["errors"])
        # the other sections are still there
        self.assertIsNotNone(body["me"])
        self.assertIsNotNone(body["feed_ad_config"])

    def test_failing_view_section_is_reported_not_raised(self):
        with mock.patch.object(home_bootstrap, "_call_view", side_effect=RuntimeError("nope")):
            r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for name in ("stories", "stories_live", "classrooms", "live_now", "dashboard", "invite"):
            self.assertIn(name, body["errors"])
        self.assertIsNone(body["stories"])
        self.assertIsNone(body["classroom_switcher"]["classrooms"])
