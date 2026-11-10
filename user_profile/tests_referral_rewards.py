# user_profile/tests_referral_rewards.py
"""
T2.7 (Loop 7) — cost-free referee / referrer rewards: streak-freeze tokens,
recognition badges and the founding-member badge. Nothing here may touch the
coin ledger.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Notification
from tuitionclass.models import Referral, referral_code_for_user

from .models import Streak, UserBadge
from .services import award_badge, founding_window_open, grant_referral_rewards

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password="x")


def _referral(referrer, referred):
    return Referral.objects.create(referrer=referrer, referred=referred, bonus_amount=50)


def _badges(user):
    return set(UserBadge.objects.filter(user=user).values_list("badge__code", flat=True))


def _tokens(user):
    streak = Streak.objects.filter(user=user).first()
    return streak.freeze_tokens if streak else 0


class GrantFreezeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _user("gf")

    def test_grants_and_respects_cap_without_moving_coins(self):
        coin_before = User.objects.get(pk=self.user.pk).coin
        self.assertEqual(Streak.objects.grant_freeze(self.user, 1), 1)
        self.assertEqual(Streak.objects.grant_freeze(self.user, 5), 1)  # cap = 2 -> only 1 more fits
        self.assertEqual(Streak.objects.grant_freeze(self.user, 1), 0)  # already full
        self.assertEqual(_tokens(self.user), 2)
        self.assertEqual(User.objects.get(pk=self.user.pk).coin, coin_before)

    def test_zero_or_negative_is_noop(self):
        self.assertEqual(Streak.objects.grant_freeze(self.user, 0), 0)
        self.assertEqual(Streak.objects.grant_freeze(self.user, -3), 0)


class GrantReferralRewardsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.referrer = _user("ref_a")
        self.referee = _user("ref_b")

    def test_both_sides_get_freeze_badges_and_notification(self):
        referral = _referral(self.referrer, self.referee)
        result = grant_referral_rewards(referral.pk)

        self.assertEqual(_tokens(self.referrer), 1)
        self.assertEqual(_tokens(self.referee), 1)
        self.assertIn("referrer_first", _badges(self.referrer))
        self.assertNotIn("referrer_5", _badges(self.referrer))
        self.assertIn("founding_member", _badges(self.referee))
        self.assertEqual(result["referrals_total"], 1)

        rows = Notification.objects.filter(notif_type=Notification.NotifType.REFERRAL_REWARD)
        self.assertEqual(set(rows.values_list("recipient_id", flat=True)), {self.referrer.pk, self.referee.pk})
        referrer_row = rows.get(recipient=self.referrer)
        self.assertEqual(referrer_row.data["role"], "referrer")
        self.assertEqual(referrer_row.data["reward"]["streak_freeze"], 1)

    def test_is_exactly_once(self):
        referral = _referral(self.referrer, self.referee)
        grant_referral_rewards(referral.pk)
        again = grant_referral_rewards(referral.pk)
        self.assertIn("skipped", again)
        self.assertEqual(_tokens(self.referrer), 1)
        self.assertEqual(_tokens(self.referee), 1)
        self.assertEqual(Notification.objects.filter(notif_type=Notification.NotifType.REFERRAL_REWARD).count(), 2)

    def test_missing_referral_is_a_noop(self):
        self.assertIn("skipped", grant_referral_rewards(999999))

    def test_milestone_badges_for_referrer(self):
        for i in range(5):
            referral = _referral(self.referrer, _user(f"friend{i}"))
            grant_referral_rewards(referral.pk)
            if i == 1:
                self.assertNotIn("founding_member", _badges(self.referrer))
            if i == 2:
                self.assertIn("founding_member", _badges(self.referrer))  # 3rd referral
            if i == 3:
                self.assertNotIn("referrer_5", _badges(self.referrer))
        self.assertTrue({"referrer_first", "referrer_5", "founding_member"} <= _badges(self.referrer))
        # holding cap (2) was reached after two referrals -> never exceeded
        self.assertEqual(_tokens(self.referrer), 2)

    def test_full_freeze_holder_still_gets_badge_and_message_skips_freeze(self):
        Streak.objects.grant_freeze(self.referee, 2)
        referral = _referral(self.referrer, self.referee)
        grant_referral_rewards(referral.pk)
        self.assertEqual(_tokens(self.referee), 2)
        row = Notification.objects.get(recipient=self.referee, notif_type=Notification.NotifType.REFERRAL_REWARD)
        self.assertEqual(row.data["reward"]["streak_freeze"], 0)
        self.assertEqual(row.data["reward"]["badges"], ["founding_member"])

    def test_no_notification_when_nothing_was_granted(self):
        Streak.objects.grant_freeze(self.referee, 2)
        award_badge(self.referee, "founding_member", notify=False)
        referral = _referral(self.referrer, self.referee)
        grant_referral_rewards(referral.pk)
        self.assertFalse(
            Notification.objects.filter(recipient=self.referee, notif_type=Notification.NotifType.REFERRAL_REWARD).exists()
        )

    @override_settings(FOUNDING_MEMBER_UNTIL="2000-01-01")
    def test_founding_window_closed_by_date(self):
        self.assertFalse(founding_window_open())
        referral = _referral(self.referrer, self.referee)
        grant_referral_rewards(referral.pk)
        self.assertNotIn("founding_member", _badges(self.referee))
        self.assertEqual(_tokens(self.referee), 1)  # freeze is NOT part of the founding window

    @override_settings(FOUNDING_MEMBER_MAX_AWARDS=1)
    def test_founding_window_closed_by_cap(self):
        first = _referral(self.referrer, self.referee)
        grant_referral_rewards(first.pk)
        self.assertIn("founding_member", _badges(self.referee))
        self.assertFalse(founding_window_open())
        late = _user("late")
        grant_referral_rewards(_referral(self.referrer, late).pk)
        self.assertNotIn("founding_member", _badges(late))

    @override_settings(REFERRAL_REWARD_FREEZE_REFERRER=0, REFERRAL_REWARD_FREEZE_REFEREE=0)
    def test_freeze_rewards_can_be_switched_off(self):
        grant_referral_rewards(_referral(self.referrer, self.referee).pk)
        self.assertEqual(_tokens(self.referrer), 0)
        self.assertEqual(_tokens(self.referee), 0)


class RedeemEndpointClosedTests(TestCase):
    """The enter-a-code flow is closed (410): it must not create a Referral nor grant any reward any more."""

    def setUp(self):
        cache.clear()
        self.referrer = _user("redeem_ref")
        self.newcomer = _user("redeem_new")

    def test_redeem_is_gone_and_grants_no_rewards(self):
        client = APIClient()
        client.force_authenticate(self.newcomer)
        code = referral_code_for_user(self.referrer.id)
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.post(reverse("referral-redeem"), {"code": code}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)
        self.assertFalse(Referral.objects.filter(referred=self.newcomer).exists())
        self.assertEqual(_tokens(self.referrer), 0)
        self.assertEqual(_tokens(self.newcomer), 0)
        self.assertNotIn("referrer_first", _badges(self.referrer))
        self.assertNotIn("founding_member", _badges(self.newcomer))
