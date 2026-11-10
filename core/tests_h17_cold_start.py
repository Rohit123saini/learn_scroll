"""H17 cold-start tests (written, NOT run): D1/D3/D7 retention rows, interests_saved / first_feed events,
quick-start subjects, and the never-empty-feed backfill.
Run: python manage.py test core.tests_h17_cold_start"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User
from post.models import Post, UserInterest

from . import funnel
from .models_funnel import FunnelEvent

E = FunnelEvent.Event
DEV = "device-abcdef1234567890"


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


def _signup_days_ago(user, days):
    """Move the user's signup row (and joined date) `days` back so today's app_open is day N."""
    when = timezone.now() - timedelta(days=days)
    FunnelEvent.objects.filter(event=E.SIGNUP, user=user).update(created_at=when)


class RetentionEventTests(TestCase):
    def test_d1_d3_d7_rows_are_written_once_on_the_exact_day(self):
        for days, event in ((1, E.RETENTION_D1), (3, E.RETENTION_D3), (7, E.RETENTION_D7)):
            u = _user(f"ret{days}")
            _signup_days_ago(u, days)
            self.assertTrue(funnel.record_app_open(DEV, user=u))
            self.assertEqual(FunnelEvent.objects.filter(event=event, user=u).count(), 1)
            funnel.record_app_open(DEV, user=u)  # same day again: nothing new
            self.assertEqual(FunnelEvent.objects.filter(event=event, user=u).count(), 1)

    def test_other_days_write_no_retention_row(self):
        u = _user("ret_day2")
        _signup_days_ago(u, 2)
        funnel.record_app_open(DEV, user=u)
        self.assertFalse(FunnelEvent.objects.filter(user=u, event__startswith="retention_").exists())

    def test_clients_cannot_report_retention_or_first_feed(self):
        c = APIClient()
        for ev in ("retention_d1", "first_feed", "interests_saved"):
            r = c.post("/core/funnel/events/", {"device_id": DEV, "events": [{"event": ev}]}, format="json")
            self.assertEqual(r.status_code, 400, ev)

    def test_report_has_d3_and_cold_start_block(self):
        r = funnel.funnel_report(days=30)
        self.assertEqual(set(r["retention"]), {"d1", "d3", "d7"})
        self.assertEqual(r["cold_start"]["target_seconds"], 60)


class QuickStartSubjectsTests(TestCase):
    URL = "/core/onboarding/quick-start/"

    def setUp(self):
        self.user = _user("h17_qs")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, **extra):
        body = {"study_class": "class_11", "target_exam": "jee", "interests": ["education"]}
        body.update(extra)
        return self.client.post(self.URL, body, format="json")

    def test_subjects_saved_to_focus_subjects_and_funnel_event_once(self):
        r = self._post(subjects=["physics", "maths", "physics"])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["subjects"], ["physics", "maths"])
        self.assertTrue(r.data["first_feed_ready"])
        self.assertEqual(FunnelEvent.objects.filter(event=E.INTERESTS_SAVED, user=self.user).count(), 1)
        self._post(subjects=["chemistry"])  # an edit later is not a second cold-start event
        self.assertEqual(FunnelEvent.objects.filter(event=E.INTERESTS_SAVED, user=self.user).count(), 1)

    def test_subjects_optional_and_untouched_when_omitted(self):
        self._post(subjects=["biology"])
        self._post()  # no `subjects` key -> keep
        self.assertEqual(self.client.get(self.URL).data["subjects"], ["biology"])

    def test_too_many_or_unknown_subjects_rejected(self):
        self.assertEqual(self._post(subjects=["maths", "physics", "chemistry", "biology", "english", "hindi"]).status_code, 400)
        self.assertEqual(self._post(subjects=["nope"]).status_code, 400)

    def test_options_list_subjects(self):
        d = self.client.get("/core/onboarding/options/").data
        self.assertIn("maths", {s["key"] for s in d["subjects"]})
        self.assertEqual(d["max_subjects"], 5)


class NeverEmptyFeedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = _user("h17_new")
        self.author = _user("h17_author")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _old_public_post(self, **kw):
        p = Post.objects.create(user=self.author, content="evergreen", visibility="public",
                                moderation_status="approved", **kw)
        Post.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=200))
        return p

    def test_new_user_with_only_old_posts_still_gets_a_feed(self):
        self._old_public_post()
        r = self.client.get("/post/feed/")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["results"]), 1)

    def test_interest_matching_posts_come_first(self):
        UserInterest.objects.create(user=self.me, category="tech")
        self._old_public_post(category="sports")
        want = self._old_public_post(category="tech")
        results = self.client.get("/post/feed/").json()["results"]
        self.assertEqual(str(results[0]["id"]), str(want.id))

    def test_first_feed_event_recorded_once_with_seconds(self):
        self._old_public_post()
        self.client.get("/post/feed/")
        self.client.get("/post/feed/")
        rows = FunnelEvent.objects.filter(event=E.FIRST_FEED, user=self.me)
        self.assertEqual(rows.count(), 1)
        self.assertIn("seconds_since_signup", rows.first().props)

    def test_chip_filter_is_not_backfilled(self):
        self._old_public_post(category="sports")
        r = self.client.get("/post/feed/", {"category": "tech"})
        self.assertEqual(r.json()["results"], [])
