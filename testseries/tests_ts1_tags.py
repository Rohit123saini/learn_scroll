# testseries/tests_ts1_tags.py - TS1: subject / difficulty / tags on create + the free Live mode body.
# WRITTEN BY READING THE CODE ONLY - not run. `python manage.py test testseries.tests_ts1_tags`
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .tests_advanced import _client, _make_user


class Ts1MetaTests(TestCase):
    def setUp(self):
        self.teacher = _make_user("teacher_ts1")

    def create(self, payload):
        return _client(self.teacher).post(reverse("testseries-list"), payload, format="json")

    def test_subject_difficulty_tags_are_saved_and_returned(self):
        res = self.create({"title": "Algebra", "is_paid": False, "price_coins": 0,
                           "subject": "Maths", "difficulty": "hard", "tags": ["JEE", " algebra ", "jee"]})
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["subject"], "Maths")
        self.assertEqual(body["difficulty"], "hard")
        self.assertEqual(body["tags"], ["jee", "algebra"])  # lower-cased, trimmed, de-duplicated

    def test_too_many_or_bad_tags_rejected(self):
        res = self.create({"title": "T", "is_paid": False, "price_coins": 0, "tags": [f"t{i}" for i in range(9)]})
        self.assertEqual(res.status_code, 400)
        res = self.create({"title": "T", "is_paid": False, "price_coins": 0, "tags": "jee"})
        self.assertEqual(res.status_code, 400)

    def test_no_tags_defaults_to_empty_list(self):
        res = self.create({"title": "Plain", "is_paid": False, "price_coins": 0})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["tags"], [])

    def test_live_mode_needs_start_time(self):
        res = self.create({"title": "Live", "is_paid": False, "price_coins": 0, "delivery_mode": "live"})
        self.assertEqual(res.status_code, 400)
        start = (timezone.now() + timedelta(days=1)).isoformat()
        res = self.create({"title": "Live", "is_paid": False, "price_coins": 0, "delivery_mode": "live", "starts_at": start})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["delivery_mode"], "live")
