# campus/tests_t05_cleanup.py
"""
T0.5 — (1) spelling-fixed URL aliases, (2) analytics-snapshots/latest/ empty state.

Run:  python manage.py test campus.tests_t05_cleanup
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Campus, CampusAnalyticsSnapshot, StaffProfile
from .tests import make_session

User = get_user_model()


class SpellingAliasUrlTests(APITestCase):
    def test_new_and_legacy_paths_both_resolve_to_the_same_viewset(self):
        for new, old in (
            ("/campus/class-teacher-assignments/", "/campus/class-teacher-assigmentss/"),
            ("/campus/subject-teacher-assignments/", "/campus/subject-teacher-assigmentss/"),
        ):
            m_new, m_old = resolve(new), resolve(old)
            self.assertIs(m_new.func.cls, m_old.func.cls, (new, old))

    def test_route_names_are_unique_and_legacy_reverse_still_works(self):
        self.assertEqual(reverse("class-teacher-assignments-list"), "/campus/class-teacher-assignments/")
        self.assertEqual(reverse("class-teacher-assigments-list"), "/campus/class-teacher-assigmentss/")
        self.assertEqual(reverse("subject-teacher-assignments-list"), "/campus/subject-teacher-assignments/")
        self.assertEqual(reverse("subject-teacher-assigments-list"), "/campus/subject-teacher-assigmentss/")

    def test_both_paths_serve_requests(self):
        user = User.objects.create_user(username="u", password="x")
        self.client.force_authenticate(user)
        for path in ("/campus/class-teacher-assignments/", "/campus/class-teacher-assigmentss/",
                     "/campus/subject-teacher-assignments/", "/campus/subject-teacher-assigmentss/"):
            self.assertEqual(self.client.get(path).status_code, status.HTTP_200_OK, path)


class AnalyticsSnapshotLatestTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="admin", password="x")
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.session = make_session(self.campus)
        self.client.force_authenticate(self.admin)
        self.url = "/campus/analytics-snapshots/latest/"

    def test_no_snapshot_returns_200_empty_not_404_and_queues_refresh(self):
        with mock.patch("campus.tasks.refresh_analytics_snapshot.delay") as delay:
            r = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["empty"])
        self.assertEqual(r.data["data"], {})
        self.assertTrue(r.data["refresh_queued"])
        delay.assert_called_once_with(str(self.campus.id), str(self.session.id))

    def test_refresh_is_queued_at_most_once_per_window(self):
        with mock.patch("campus.tasks.refresh_analytics_snapshot.delay") as delay:
            self.client.get(self.url, {"campus": str(self.campus.id)})
            r2 = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.data["refresh_queued"])

    def test_broker_down_does_not_break_the_endpoint(self):
        with mock.patch("campus.tasks.refresh_analytics_snapshot.delay", side_effect=OSError("broker down")):
            r = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["empty"])
        self.assertFalse(r.data["refresh_queued"])

    def test_existing_snapshot_is_returned_as_before(self):
        CampusAnalyticsSnapshot.objects.create(campus=self.campus, session=self.session, data={"x": 1})
        r = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("empty", r.data)
        self.assertEqual(r.data["data"], {"x": 1})

    def test_campus_param_still_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)
