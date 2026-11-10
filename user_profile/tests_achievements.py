"""
P3 - Achievements + learning identity. NOT RUN by the author (read-only task: no test / migrate / run):

    python manage.py test user_profile.tests_achievements testseries.tests_certificate_page

Covers: pure helpers (heat level, percent, notes), the API (`GET /profile/profile/<username>/achievements/` and the
newly wired `.../badges/`), the visibility matrix (owner / visitor / private / blocked / minor) and the subject chips.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from leaderboard import services as lb
from leaderboard.models import LeaderboardEntry
from testseries.models import TestSeries
from testseries.tests_advanced import _Base, _answers, _series

from . import achievements as ach
from .models import BlockUser, DailyUsage, Follow, Streak
from .services import award_badge

User = get_user_model()


def _client(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c


def _url(username):
    return reverse("user-achievements", kwargs={"username": username})


# ---------------------------------------------------------------------------------------------------------------------
class PureHelperTests(SimpleTestCase):
    def test_heat_level_boundaries(self):
        cases = [(0, 0), (None, 0), ("abc", 0), (-5, 0), (1, 1), (599, 1), (600, 2), (1799, 2), (1800, 3),
                 (3599, 3), (3600, 4), (99999, 4)]
        for seconds, level in cases:
            self.assertEqual(ach.heat_level(seconds), level, msg=repr(seconds))

    def test_percent_is_clamped(self):
        self.assertEqual(ach.percent(4, 7), 57)
        self.assertEqual(ach.percent(7, 7), 100)
        self.assertEqual(ach.percent(50, 7), 100)
        self.assertEqual(ach.percent(0, 7), 0)
        self.assertEqual(ach.percent(3, 0), 0)
        self.assertEqual(ach.percent(None, None), 0)
        self.assertEqual(ach.percent(-2, 7), 0)

    def test_skill_level(self):
        self.assertEqual(ach.skill_level(80), "strong")
        self.assertEqual(ach.skill_level(79.9), "good")

    def test_earned_note(self):
        self.assertEqual(ach.earned_note("weekly_top10", {"rank": 4, "week": "2026-W39"}), "Ranked #4 in week 2026-W39.")
        self.assertIn("3 friend", ach.earned_note("referrer_5", {"referrals": 3}))
        self.assertIn("friend's invite", ach.earned_note("founding_member", {"reason": "referee"}))
        self.assertEqual(ach.earned_note("streak_7", {}), "")
        self.assertEqual(ach.earned_note("streak_7", None), "")  # context is never trusted to be a dict

    def test_week_start_is_monday(self):
        self.assertEqual(ach.week_start_for(datetime.date(2026, 10, 28)), datetime.date(2026, 10, 26))
        self.assertEqual(ach.week_start_for(datetime.date(2026, 10, 26)), datetime.date(2026, 10, 26))
        self.assertEqual(ach.week_start_for(datetime.date(2026, 11, 1)), datetime.date(2026, 10, 26))


# ---------------------------------------------------------------------------------------------------------------------
class AchievementsApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner_p3", password="x")
        self.visitor = User.objects.create_user(username="visitor_p3", password="x")

    # ---- access -------------------------------------------------------------------------------------------------
    def test_login_required(self):
        self.assertIn(_client().get(_url("owner_p3")).status_code, (401, 403))

    def test_unknown_user_is_404(self):
        self.assertEqual(_client(self.owner).get(_url("nobody_here")).status_code, 404)

    def test_username_is_case_insensitive(self):
        self.assertEqual(_client(self.visitor).get(_url("OWNER_P3")).status_code, 200)

    def test_block_either_direction_is_404(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.visitor)
        self.assertEqual(_client(self.visitor).get(_url("owner_p3")).status_code, 404)
        self.assertEqual(_client(self.owner).get(_url("visitor_p3")).status_code, 404)

    def test_private_account_is_locked_until_the_viewer_follows(self):
        award_badge(self.owner, "first_test", notify=False)
        self.owner.is_private = True
        self.owner.save(update_fields=["is_private"])

        data = _client(self.visitor).get(_url("owner_p3")).json()
        self.assertTrue(data["locked"])
        self.assertEqual(data["badges"], [])

        follow = Follow.objects.create(follower=self.visitor, following=self.owner, status=Follow.Status.PENDING)
        self.assertTrue(_client(self.visitor).get(_url("owner_p3")).json()["locked"])  # pending is not enough

        follow.status = Follow.Status.ACCEPTED
        follow.save(update_fields=["status"])
        data = _client(self.visitor).get(_url("owner_p3")).json()
        self.assertFalse(data["locked"])
        self.assertEqual([b["code"] for b in data["badges"]], ["first_test"])

        # the owner always sees their own page
        self.assertFalse(_client(self.owner).get(_url("owner_p3")).json()["locked"])

    # ---- badges + progress --------------------------------------------------------------------------------------
    def test_owner_sees_locked_badges_with_progress_bars(self):
        Streak.objects.create(user=self.owner, current_streak=2, longest_streak=4)
        data = _client(self.owner).get(_url("owner_p3")).json()
        by_code = {b["code"]: b for b in data["badges"]}

        self.assertFalse(by_code["streak_7"]["earned"])
        self.assertEqual(by_code["streak_7"]["progress"]["current"], 4)
        self.assertEqual(by_code["streak_7"]["progress"]["target"], 7)
        self.assertEqual(by_code["streak_7"]["progress"]["percent"], 57)
        self.assertEqual(by_code["streak_30"]["progress"]["percent"], 13)
        self.assertEqual(by_code["first_test"]["progress"]["kind"], "count")
        self.assertEqual(by_code["weekly_top10"]["progress"]["kind"], "rank")
        self.assertIsNone(by_code["weekly_top10"]["progress"]["current"])  # never ranked
        self.assertEqual(by_code["teacher_verified"]["progress"]["kind"], "flag")
        self.assertTrue(by_code["streak_7"]["how_to"])  # every built-in badge explains how to earn it

    def test_progress_never_exceeds_the_target(self):
        Streak.objects.create(user=self.owner, current_streak=40, longest_streak=40)
        by_code = {b["code"]: b for b in _client(self.owner).get(_url("owner_p3")).json()["badges"]}
        # streak_7 / streak_30 would normally already be earned by the signal; here nothing awarded them yet
        self.assertEqual(by_code["streak_7"]["progress"]["current"], 7)
        self.assertEqual(by_code["streak_7"]["progress"]["percent"], 100)

    def test_earned_badge_is_listed_once_with_its_note(self):
        award_badge(self.owner, "weekly_top10", context={"week": "2026-W39", "rank": 4}, notify=False)
        badges = _client(self.owner).get(_url("owner_p3")).json()["badges"]
        rows = [b for b in badges if b["code"] == "weekly_top10"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["earned"])
        self.assertEqual(rows[0]["progress"]["percent"], 100)
        self.assertTrue(rows[0]["earned_note"].startswith("Ranked #4"))

    def test_visitor_gets_earned_badges_only_and_no_private_numbers(self):
        award_badge(self.owner, "first_test", notify=False)
        Streak.objects.create(user=self.owner, current_streak=9, longest_streak=9)
        DailyUsage.objects.create(user=self.owner, date=timezone.localdate(), seconds=4000)

        data = _client(self.visitor).get(_url("owner_p3")).json()
        self.assertFalse(data["is_self"])
        self.assertTrue(all(b["earned"] for b in data["badges"]))
        self.assertEqual([b["code"] for b in data["badges"]], ["first_test"])
        for private_key in ("streak", "heatmap", "study_time", "latest_recap"):
            self.assertNotIn(private_key, data, msg=private_key)

    def test_badges_endpoint_is_wired_and_respects_private_accounts(self):
        award_badge(self.owner, "first_test", notify=False)
        url = reverse("user-badges", kwargs={"username": "owner_p3"})
        self.assertEqual(_client(self.visitor).get(url).json()["count"], 1)

        self.owner.is_private = True
        self.owner.save(update_fields=["is_private"])
        self.assertEqual(_client(self.visitor).get(url).json()["count"], 0)
        self.assertEqual(_client(self.owner).get(url).json()["count"], 1)

    # ---- streak, heat-map, study time (owner only) --------------------------------------------------------------
    def test_heatmap_and_study_time(self):
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)
        old = today - datetime.timedelta(days=200)  # outside the 26-week grid, still part of the all-time total
        DailyUsage.objects.create(user=self.owner, date=today, seconds=3700)
        DailyUsage.objects.create(user=self.owner, date=yesterday, seconds=120)
        DailyUsage.objects.create(user=self.owner, date=old, seconds=500)
        DailyUsage.objects.create(user=self.visitor, date=today, seconds=9999)  # someone else's row never leaks in

        data = _client(self.owner).get(_url("owner_p3")).json()
        heat = data["heatmap"]
        self.assertEqual([(d["date"], d["level"]) for d in heat["days"]], [(yesterday.isoformat(), 1), (today.isoformat(), 4)])
        self.assertEqual(heat["active_days"], 2)
        self.assertEqual(heat["weeks"], 26)
        self.assertEqual(datetime.date.fromisoformat(heat["start"]).weekday(), 0)  # grid starts on a Monday

        study = data["study_time"]
        self.assertEqual(study["total_seconds"], 3700 + 120 + 500)
        self.assertEqual(study["last_30_days_seconds"], 3820)
        self.assertEqual(study["best_day"], {"date": today.isoformat(), "seconds": 3700})
        self.assertEqual(study["avg_active_day_seconds"], 1910)
        expected_week = 3700 + (120 if yesterday >= ach.week_start_for(today) else 0)
        self.assertEqual(study["this_week_seconds"], expected_week)

    def test_streak_block_for_owner(self):
        Streak.objects.create(user=self.owner, current_streak=3, longest_streak=11, total_active_days=20)
        s = _client(self.owner).get(_url("owner_p3")).json()["streak"]
        self.assertEqual((s["current"], s["longest"], s["total_active_days"]), (3, 11, 20))

    def test_owner_with_no_activity_gets_empty_but_valid_blocks(self):
        data = _client(self.owner).get(_url("owner_p3")).json()
        self.assertEqual(data["heatmap"]["days"], [])
        self.assertEqual(data["study_time"]["total_seconds"], 0)
        self.assertIsNone(data["study_time"]["best_day"])
        self.assertEqual(data["streak"]["current"], 0)
        self.assertIsNone(data["latest_recap"])

    # ---- rank chip + minors -------------------------------------------------------------------------------------
    def _entry(self, period, key, rank):
        return LeaderboardEntry.objects.create(
            scope_type=LeaderboardEntry.ScopeType.ENGAGEMENT, scope_id=None, period_type=period,
            period_key=key, user=self.owner, score=10.0, rank=rank,
        )

    def test_rank_chip_only_shows_ranks_inside_the_cap(self):
        self._entry(LeaderboardEntry.PeriodType.WEEKLY, lb.current_week_key(), 5)
        self._entry(LeaderboardEntry.PeriodType.ALL_TIME, "all", 500)  # worse than the default cap of 100
        rank = _client(self.visitor).get(_url("owner_p3")).json()["rank"]
        self.assertEqual(rank["weekly"]["rank"], 5)
        self.assertIsNone(rank["all_time"])

    def test_minor_owner_hides_rank_and_subjects_from_visitors_but_not_from_self(self):
        fake_rank = {"weekly": {"rank": 1, "score": 5.0, "period_key": "x"}, "all_time": None}
        fake_skills = [{"subject": "Physics", "level": "strong"}]
        with mock.patch("user_profile.privacy.is_minor_user", return_value=True), \
                mock.patch.object(ach, "rank_chip", return_value=fake_rank), \
                mock.patch.object(ach, "skill_chips", return_value=fake_skills):
            as_visitor = _client(self.visitor).get(_url("owner_p3")).json()
            as_owner = _client(self.owner).get(_url("owner_p3")).json()
        self.assertEqual(as_visitor["rank"], {"weekly": None, "all_time": None})
        self.assertEqual(as_visitor["skills"], [])
        self.assertEqual(as_owner["rank"]["weekly"]["rank"], 1)
        self.assertEqual(as_owner["skills"], fake_skills)


# ---------------------------------------------------------------------------------------------------------------------
@mock.patch("testseries.models._notify")
class SkillChipTests(_Base):
    """`self.student` takes the tests, `self.other` is the visitor. Uses the real attempt -> checked flow."""

    def _take(self, subject, answers=("a", "a")):
        series = _series(self.teacher, questions=2, subject=subject)
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, list(answers)))
        return series

    def test_two_good_attempts_make_a_chip_and_subject_grouping_ignores_case(self, _notify):
        self._take("Physics")
        self._take("physics")
        chips = ach.skill_chips(self.student, with_numbers=True)
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0]["subject"], "Physics")
        self.assertEqual(chips[0]["level"], "strong")
        self.assertEqual(chips[0]["attempts"], 2)
        self.assertGreaterEqual(chips[0]["avg_percent"], 80)

    def test_visitor_chips_carry_no_numbers(self, _notify):
        self._take("Chemistry")
        self._take("Chemistry")
        chips = ach.skill_chips(self.student, with_numbers=False)
        self.assertEqual(set(chips[0].keys()), {"subject", "level"})

    def test_one_attempt_is_not_enough(self, _notify):
        self._take("Maths")
        self.assertEqual(ach.skill_chips(self.student, with_numbers=True), [])

    def test_weak_subject_gets_no_chip(self, _notify):
        self._take("History", answers=("b", "b"))
        self._take("History", answers=("b", "b"))
        self.assertEqual(ach.skill_chips(self.student, with_numbers=True), [])

    def test_campus_tests_never_become_chips(self, _notify):
        a = self._take("Biology")
        b = self._take("Biology")
        TestSeries.objects.filter(pk__in=[a.pk, b.pk]).update(source=TestSeries.Source.CAMPUS)
        self.assertEqual(ach.skill_chips(self.student, with_numbers=True), [])

    def test_chips_are_per_user(self, _notify):
        self._take("Physics")
        self._take("Physics")
        self.assertEqual(ach.skill_chips(self.other, with_numbers=True), [])
