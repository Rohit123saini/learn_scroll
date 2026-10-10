# user_profile/tests_streak_freeze.py
"""
Streak freeze (coins se token) + daily goal ("aaj ka N minute challenge").

Real DB pe chalte hain (CoinLedger/Streak/DailyUsage). `timezone.localdate`
patch karke din aage-peeche kiye jaate hain.
"""
from contextlib import contextmanager
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import CoinLedger, DailyUsage, Streak

User = get_user_model()

D0 = date(2026, 3, 10)


@contextmanager
def on_day(d):
    """`timezone.localdate()` ko din `d` pe fix karo."""
    with mock.patch("django.utils.timezone.localdate", side_effect=lambda *a, **k: d):
        yield


def _make_user(name="s1", coin=0):
    u = User.objects.create_user(username=name, password="x")
    if coin:
        User.objects.filter(pk=u.pk).update(coin=coin)
        u.refresh_from_db()
    return u


def _checkin(user, d):
    with on_day(d):
        return Streak.objects.record_activity(user)


class FreezeConsumptionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()

    def _build_streak(self, days, tokens):
        """`days` din ki streak D0 tak banao, phir tokens set karo."""
        for i in range(days):
            _checkin(self.user, D0 - timedelta(days=days - 1 - i))
        Streak.objects.filter(user=self.user).update(freeze_tokens=tokens)

    def test_consecutive_day_uses_no_token(self):
        self._build_streak(3, tokens=2)
        s, _ = _checkin(self.user, D0 + timedelta(days=1))
        self.assertEqual((s.current_streak, s.freeze_tokens, s.freezes_consumed_now), (4, 2, 0))

    def test_one_missed_day_consumes_one_token_and_keeps_streak(self):
        self._build_streak(3, tokens=1)
        s, _ = _checkin(self.user, D0 + timedelta(days=2))  # D0+1 missed
        s.refresh_from_db()
        self.assertEqual(s.current_streak, 4)
        self.assertEqual(s.freeze_tokens, 0)
        self.assertEqual(s.freezes_used_total, 1)
        self.assertEqual(s.total_active_days, 4)  # missed din count nahi hua

    def test_two_missed_days_with_two_tokens(self):
        self._build_streak(5, tokens=2)
        s, _ = _checkin(self.user, D0 + timedelta(days=3))
        self.assertEqual((s.current_streak, s.freeze_tokens, s.freezes_consumed_now), (6, 0, 2))

    def test_not_enough_tokens_breaks_streak_and_keeps_tokens(self):
        self._build_streak(5, tokens=1)
        s, _ = _checkin(self.user, D0 + timedelta(days=3))  # 2 missed, 1 token
        s.refresh_from_db()
        self.assertEqual(s.current_streak, 1)
        self.assertEqual(s.freeze_tokens, 1)  # adhoori protection pe token waste nahi
        self.assertEqual(s.freezes_used_total, 0)

    def test_no_tokens_behaves_like_before(self):
        self._build_streak(5, tokens=0)
        s, _ = _checkin(self.user, D0 + timedelta(days=2))
        self.assertEqual(s.current_streak, 1)

    def test_same_day_repeat_is_noop(self):
        self._build_streak(2, tokens=1)
        _checkin(self.user, D0 + timedelta(days=2))
        s, _ = _checkin(self.user, D0 + timedelta(days=2))
        s.refresh_from_db()
        self.assertEqual((s.current_streak, s.freeze_tokens), (3, 0))

    def test_longest_streak_tracks_frozen_continuation(self):
        self._build_streak(4, tokens=1)
        s, _ = _checkin(self.user, D0 + timedelta(days=2))
        self.assertEqual(s.longest_streak, 5)


class FreezePurchaseTests(TestCase):
    URL = "/profile/streak/freeze/"

    def setUp(self):
        cache.clear()
        self.user = _make_user(coin=120)
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_buy_debits_coins_and_adds_token(self):
        r = self.api.post(self.URL)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["data"]["freeze_tokens"], 1)
        self.assertEqual(r.data["coin_balance"], 70)  # 120 - 50
        entry = CoinLedger.objects.get(user=self.user)
        self.assertEqual(entry.transaction_type, CoinLedger.TransactionType.SPEND)
        self.assertEqual((entry.amount, entry.balance_after), (-50, 70))
        self.assertEqual(entry.metadata.get("kind"), "streak_freeze")

    def test_two_buys_make_two_distinct_ledger_rows(self):
        self.api.post(self.URL)
        self.api.post(self.URL)
        self.assertEqual(CoinLedger.objects.filter(user=self.user).count(), 2)
        self.assertEqual(Streak.objects.get(user=self.user).freeze_tokens, 2)

    def test_cap_blocks_third_token_without_charging(self):
        self.api.post(self.URL)
        self.api.post(self.URL)
        r = self.api.post(self.URL)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "freeze_limit")
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 20)  # sirf 2 khareed ke paise gaye

    def test_insufficient_coins_no_token_no_ledger(self):
        poor = _make_user("poor", coin=10)
        api = APIClient()
        api.force_authenticate(poor)
        r = api.post(self.URL)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "insufficient_coins")
        # Poora transaction rollback hota hai — token nahi, ledger row nahi.
        self.assertFalse(Streak.objects.filter(user=poor, freeze_tokens__gt=0).exists())
        self.assertFalse(CoinLedger.objects.filter(user=poor).exists())
        poor.refresh_from_db()
        self.assertEqual(poor.coin, 10)

    @override_settings(STREAK_FREEZE_COST_COINS=30, STREAK_FREEZE_MAX_TOKENS=1)
    def test_cost_and_cap_come_from_settings(self):
        r = self.api.post(self.URL)
        self.assertEqual(r.data["coin_balance"], 90)
        self.assertEqual(self.api.post(self.URL).status_code, 400)

    def test_requires_auth(self):
        self.assertIn(APIClient().post(self.URL).status_code, (401, 403))


class DailyGoalTests(TestCase):
    URL = "/profile/daily-goal/"

    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.today = D0

    def _usage(self, seconds):
        DailyUsage.objects.update_or_create(
            user=self.user, date=self.today, defaults={"seconds": seconds}
        )

    def _get(self):
        with on_day(self.today):
            return self.api.get(self.URL).data["data"]

    def test_default_goal_is_ten_minutes_and_not_complete(self):
        d = self._get()
        self.assertEqual((d["goal_minutes"], d["completed"], d["today_seconds"]), (10, False, 0))
        self.assertIn(10, d["options"])

    def test_completes_once_when_usage_reaches_goal(self):
        self._usage(599)
        self.assertFalse(self._get()["completed"])
        self._usage(600)
        with on_day(self.today):
            first = Streak.objects.evaluate_daily_goal(self.user)
            second = Streak.objects.evaluate_daily_goal(self.user)
        self.assertTrue(first["just_completed"])
        self.assertFalse(second["just_completed"])
        self.assertTrue(second["completed"])
        self.assertEqual(Streak.objects.get(user=self.user).goals_completed_total, 1)

    def test_next_day_resets_completion(self):
        self._usage(600)
        self._get()
        self.today = D0 + timedelta(days=1)
        self.assertFalse(self._get()["completed"])

    def test_patch_validates_options(self):
        with on_day(self.today):
            self.assertEqual(self.api.patch(self.URL, {"goal_minutes": 7}, format="json").status_code, 400)
            self.assertEqual(self.api.patch(self.URL, {"goal_minutes": "abc"}, format="json").status_code, 400)
            r = self.api.patch(self.URL, {"goal_minutes": 20}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["goal_minutes"], 20)

    def test_lowering_goal_below_current_usage_completes_immediately(self):
        self._usage(400)  # 6m40s
        with on_day(self.today):
            r = self.api.patch(self.URL, {"goal_minutes": 5}, format="json")
        self.assertTrue(r.data["data"]["completed"])
        self.assertTrue(r.data["data"]["just_completed"])

    def test_every_seventh_goal_awards_free_freeze(self):
        Streak.objects.get_or_create(user=self.user)
        Streak.objects.filter(user=self.user).update(goals_completed_total=6)
        self._usage(600)
        with on_day(self.today):
            d = Streak.objects.evaluate_daily_goal(self.user)
        self.assertTrue(d["freeze_awarded"])
        self.assertEqual(d["freeze_tokens"], 1)

    def test_free_freeze_respects_cap(self):
        Streak.objects.get_or_create(user=self.user)
        Streak.objects.filter(user=self.user).update(goals_completed_total=6, freeze_tokens=2)
        self._usage(600)
        with on_day(self.today):
            d = Streak.objects.evaluate_daily_goal(self.user)
        self.assertTrue(d["just_completed"])
        self.assertFalse(d["freeze_awarded"])
        self.assertEqual(d["freeze_tokens"], 2)

    def test_non_seventh_goal_gives_no_freeze(self):
        self._usage(600)
        with on_day(self.today):
            d = Streak.objects.evaluate_daily_goal(self.user)
        self.assertFalse(d["freeze_awarded"])

    def test_goal_gives_no_coins(self):
        self._usage(600)
        self._get()
        self.assertFalse(CoinLedger.objects.filter(user=self.user).exists())


class StreakEndpointShapeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user("shape")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_get_has_new_fields(self):
        d = self.api.get("/profile/streak/").data["data"]
        for k in ("freeze_tokens", "freezes_used_total", "freeze_cost_coins",
                  "freeze_max_tokens", "daily_goal_minutes", "goal_completed_today",
                  "goals_completed_total"):
            self.assertIn(k, d)
        self.assertEqual((d["freeze_cost_coins"], d["freeze_max_tokens"]), (50, 2))

    def test_post_reports_freeze_used(self):
        _checkin(self.user, D0)
        Streak.objects.filter(user=self.user).update(freeze_tokens=1)
        with on_day(D0 + timedelta(days=2)):
            r = self.api.post("/profile/streak/")
        self.assertEqual(r.data["freeze_used"], 1)
        self.assertEqual(r.data["data"]["current_streak"], 2)
        self.assertEqual(r.data["data"]["freeze_tokens"], 0)

    def test_heartbeat_response_includes_goal_and_completes_it(self):
        DailyUsage.objects.create(user=self.user, date=D0, seconds=590)
        with on_day(D0):
            r = self.api.post("/profile/activity/heartbeat/", {"seconds": 30}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["goal"]["just_completed"])
        self.assertTrue(r.data["goal"]["completed"])
