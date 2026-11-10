# testseries/tests_t3_create_modes.py
"""
T3 (create-flow upgrade): the two things the app's "Create test series" form now sends.

  * Free / Paid       - an individual creator may publish a FREE test (default policy "optional");
                        a paid one still needs price >= the minimum.
  * Delivery window   - Anytime (self-paced) or Scheduled (starts_at required, ends_at optional + after start).

Run:  python manage.py test testseries.tests_t3_create_modes

WRITTEN BY READING THE CODE ONLY - nothing was run (no Django in the authoring sandbox). Treat the first run as the review.
Reuses the helpers of tests_advanced (same seam for the User model).
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import TestSeries
from .tests_advanced import _client, _make_user, _mcq


class CreateModesApiTests(TestCase):
    def setUp(self):
        self.teacher = _make_user("teacher")

    def create(self, payload):
        return _client(self.teacher).post(reverse("testseries-list"), payload, format="json")

    def publish(self, series_id):
        return _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": series_id}))

    def _publishable(self, series_id):
        series = TestSeries.objects.get(pk=series_id)
        _mcq(series, 1)
        return series

    # ---- Free / Paid -------------------------------------------------------------------------------------------
    def test_default_policy_lets_an_individual_create_and_publish_a_free_test(self):
        res = self.create({"title": "Free self-paced", "is_paid": False, "price_coins": 0})
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertFalse(body["is_paid"])
        self.assertEqual(body["price_coins"], 0)
        self.assertEqual(body["delivery_mode"], "self_paced")
        self._publishable(body["id"])
        pub = self.publish(body["id"])
        self.assertEqual(pub.status_code, 200, pub.content)

    def test_paid_still_needs_the_minimum_price(self):
        res = self.create({"title": "Paid", "is_paid": True, "price_coins": 0})
        self.assertEqual(res.status_code, 400)
        self.assertIn("price_coins", res.json())

    def test_a_free_test_cannot_carry_a_price(self):
        res = self.create({"title": "Free with price", "is_paid": False, "price_coins": 5})
        self.assertEqual(res.status_code, 400)
        self.assertIn("price_coins", res.json())

    @override_settings(TESTSERIES_PRICING_POLICY={"individual": {"mode": "required"}})
    def test_required_policy_error_is_a_field_error_the_app_can_show_next_to_the_switch(self):
        res = self.create({"title": "Free try", "is_paid": False, "price_coins": 0})
        self.assertEqual(res.status_code, 400)
        self.assertIn("is_paid", res.json())

    # ---- Delivery window ---------------------------------------------------------------------------------------
    def test_scheduled_window_is_created_and_published(self):
        start = timezone.now() + timedelta(hours=2)
        end = start + timedelta(hours=3)
        res = self.create({
            "title": "Window", "is_paid": False, "price_coins": 0, "delivery_mode": "scheduled",
            "starts_at": start.isoformat(), "ends_at": end.isoformat(),
        })
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["delivery_mode"], "scheduled")
        self.assertIsNotNone(body["starts_at"])
        self.assertIsNotNone(body["ends_at"])
        self._publishable(body["id"])
        self.assertEqual(self.publish(body["id"]).status_code, 200)

    def test_scheduled_without_start_is_a_starts_at_field_error(self):
        res = self.create({"title": "No start", "is_paid": False, "price_coins": 0, "delivery_mode": "scheduled"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("starts_at", res.json())

    def test_end_must_be_after_start_is_an_ends_at_field_error(self):
        start = timezone.now() + timedelta(hours=5)
        res = self.create({
            "title": "Backwards", "is_paid": False, "price_coins": 0, "delivery_mode": "scheduled",
            "starts_at": start.isoformat(), "ends_at": (start - timedelta(hours=1)).isoformat(),
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("ends_at", res.json())

    def test_scheduled_with_start_only_is_allowed(self):
        start = timezone.now() + timedelta(days=1)
        res = self.create({
            "title": "Open ended", "is_paid": False, "price_coins": 0, "delivery_mode": "scheduled",
            "starts_at": start.isoformat(),
        })
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()["ends_at"])

    def test_self_paced_body_with_no_window_fields_is_unchanged(self):
        res = self.create({"title": "Paid classic", "is_paid": True, "price_coins": 20, "duration_minutes": 30})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["delivery_mode"], "self_paced")

    # ---- Pass mark + result release ------------------------------------------------------------------------------
    def test_pass_mark_is_saved(self):
        res = self.create({"title": "With pass mark", "is_paid": False, "price_coins": 0, "pass_percentage": 40})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["pass_percentage"], 40)

    def test_pass_mark_out_of_range_is_a_field_error(self):
        for bad in (0, 101):
            res = self.create({"title": "Bad pass mark", "is_paid": False, "price_coins": 0, "pass_percentage": bad})
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn("pass_percentage", res.json())

    def test_manual_result_release_is_allowed_for_a_self_paced_test(self):
        res = self.create({"title": "Manual release", "is_paid": False, "price_coins": 0, "result_release": "manual"})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["result_release"], "manual")

    def test_after_end_release_without_an_end_time_is_a_result_release_field_error(self):
        res = self.create({"title": "After end, no end", "is_paid": False, "price_coins": 0, "result_release": "after_end"})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("result_release", res.json())

    def test_after_end_release_with_a_window_end_is_allowed(self):
        start = timezone.now() + timedelta(hours=1)
        res = self.create({
            "title": "After end ok", "is_paid": False, "price_coins": 0, "delivery_mode": "scheduled",
            "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat(),
            "result_release": "after_end",
        })
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["result_release"], "after_end")
