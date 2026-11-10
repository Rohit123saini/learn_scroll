"""P-F9_F10 tests for tuitionclass/creator_earnings.py (written, NOT RUN)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import creator_earnings as ce
from . import tests as base_tests  # module import: its test classes are NOT re-collected here
from .models import PassDailyCharge, ReferralCommission


class PureHelperTests(SimpleTestCase):
    def test_clamp_days(self):
        self.assertEqual(ce.clamp_days(None), 30)
        self.assertEqual(ce.clamp_days("x"), 30)
        self.assertEqual(ce.clamp_days(0), 1)
        self.assertEqual(ce.clamp_days(500), 90)

    def test_change_pct(self):
        self.assertIsNone(ce.change_pct(10, 0))
        self.assertEqual(ce.change_pct(30, 20), 50.0)

    def test_window_dates(self):
        today = timezone.localdate()
        start, prev = ce.window_dates(today, 7)
        self.assertEqual(start, today - timedelta(days=6))
        self.assertEqual(prev, start - timedelta(days=7))

    def test_fill_every_day(self):
        today = timezone.localdate()
        start = today - timedelta(days=2)
        out = ce.fill(start, 3, {today: {"class_coins": 5, "referral_coins": 2}})
        self.assertEqual([r["class_coins"] for r in out], [0, 0, 5])
        self.assertEqual(out[2]["referral_coins"], 2)


class CreatorEarningsApiTests(base_tests.TuitionClassTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.purchase = base_tests._charge_and_create_purchase(self.student, self.class_pass, coupon_code="")
        today = timezone.localdate()
        PassDailyCharge.objects.create(purchase=self.purchase, date=today, amount=10)
        PassDailyCharge.objects.create(purchase=self.purchase, date=today - timedelta(days=1), amount=10)
        PassDailyCharge.objects.create(purchase=self.purchase, date=today - timedelta(days=40), amount=7)
        self.url = reverse("creator-earnings")

    def _ref(self, n, coins, status_=ReferralCommission.Status.PAID):
        return ReferralCommission.objects.create(
            referrer=self.teacher, referee=self.student, kind=ReferralCommission.Kind.CLASSROOM,
            reference=f"t:{n}", gross_coins=100, commission_coins=coins, status=status_,
        )

    def _get(self, user, qs=""):
        c = APIClient()
        c.force_authenticate(user)
        return c.get(self.url + qs)

    def test_requires_login(self):
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))

    def test_window_totals_and_previous_window(self):
        r = self._get(self.teacher, "?days=30")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["totals"]["class_coins"], 20)
        self.assertEqual(r.data["totals"]["prev_class_coins"], 7)   # 40 days ago sits in the previous 30-day window
        self.assertEqual(r.data["all_time"]["class_coins"], 27)
        self.assertTrue(r.data["is_teacher"])
        self.assertEqual(len(r.data["daily"]), 30)

    def test_referral_paid_counts_blocked_does_not(self):
        self._ref(1, 6)
        self._ref(2, 50, ReferralCommission.Status.BLOCKED)
        r = self._get(self.teacher)
        self.assertEqual(r.data["totals"]["referral_coins"], 6)
        self.assertEqual(r.data["totals"]["total"], 26)
        self.assertEqual(r.data["referral_by_kind"]["classroom"]["coins"], 6)

    def test_other_users_see_only_their_own(self):
        self._ref(1, 6)
        r = self._get(self.other_teacher)
        self.assertEqual(r.data["totals"]["total"], 0)
        self.assertFalse(r.data["is_teacher"])
        self.assertFalse(r.data["has_referral"])

    def test_by_classroom_breakdown(self):
        r = self._get(self.teacher)
        self.assertEqual(r.data["by_classroom"][0]["title"], "DSA Batch")
        self.assertEqual(r.data["by_classroom"][0]["coins"], 20)

    def test_cached_second_call(self):
        first = self._get(self.teacher).data["generated_at"]
        self._ref(9, 5)
        again = self._get(self.teacher).data
        self.assertEqual(again["generated_at"], first)
        self.assertEqual(again["totals"]["referral_coins"], 0)
