"""P-F16 tests (written, NOT run). python manage.py test common.tests_p_f16_minor_policy"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from common import minor_policy
from user_profile.models import Follow

User = get_user_model()


def _user(name, years):
    dob = timezone.localdate() - timedelta(days=int(365.25 * years) + 5) if years is not None else None
    return User.objects.create_user(username=name, password="x12345678!", date_of_birth=dob)


class MinorPolicyTests(TestCase):
    def setUp(self):
        self.minor = _user("kid", 15)
        self.adult = _user("grownup", 30)
        self.unknown = _user("nodob", None)

    def test_is_minor_rules(self):
        self.assertTrue(minor_policy.is_minor(self.minor))
        self.assertTrue(minor_policy.is_minor(self.minor.pk))
        self.assertFalse(minor_policy.is_minor(self.adult))
        self.assertFalse(minor_policy.is_minor(self.unknown))
        self.assertFalse(minor_policy.is_minor(None))
        with override_settings(MINOR_POLICY_UNKNOWN_DOB_IS_MINOR=True):
            self.assertTrue(minor_policy.is_minor(self.unknown))

    def test_minor_ids_among(self):
        self.assertEqual(minor_policy.minor_ids_among([self.minor.pk, self.adult.pk, None]), {self.minor.pk})

    def test_search_hides_minor_from_stranger_not_from_link(self):
        qs = User.objects.all()
        self.assertNotIn(self.minor.pk, minor_policy.exclude_minors_for_stranger(qs, self.adult).values_list("pk", flat=True))
        self.assertNotIn(self.minor.pk, minor_policy.exclude_minors_for_stranger(qs, self.unknown).values_list("pk", flat=True))
        Follow.objects.create(follower=self.minor, following=self.adult, status=Follow.Status.ACCEPTED)
        self.assertIn(self.minor.pk, minor_policy.exclude_minors_for_stranger(qs, self.adult).values_list("pk", flat=True))

    def test_minor_viewer_sees_minor(self):
        peer = _user("peer", 16)
        self.assertIn(self.minor.pk, minor_policy.exclude_minors_for_stranger(User.objects.all(), peer).values_list("pk", flat=True))

    def test_switch_off(self):
        with override_settings(MINOR_POLICY_SEARCH="0"):
            self.assertIn(self.minor.pk, minor_policy.exclude_minors_for_stranger(User.objects.all(), self.adult).values_list("pk", flat=True))

    def test_masked_ids_for_viewer(self):
        ids = minor_policy.masked_ids_for_viewer(self.adult, [self.minor.pk, self.adult.pk], feature=minor_policy.LEADERBOARD)
        self.assertEqual(ids, {self.minor.pk})
        self.assertEqual(minor_policy.masked_ids_for_viewer(self.minor, [self.minor.pk], feature=minor_policy.LEADERBOARD), set())

    def test_referral_minor_cannot_earn(self):
        from user_profile.fraud import check_referral_abuse

        ok, reason = check_referral_abuse(self.minor, self.adult)
        self.assertFalse(ok)
        self.assertEqual(reason, minor_policy.MINOR_REFERRER_REASON)
        self.assertTrue(check_referral_abuse(self.adult, self.minor)[0])

    def test_suggestions_exclude_minor_for_unknown_dob_viewer(self):
        from user_profile.discovery import suggested_users_queryset

        self.assertNotIn(self.minor.pk, suggested_users_queryset(self.unknown).values_list("pk", flat=True))

    def test_web_links_minor_profile_not_exposed(self):
        from common import web_links

        self.minor.is_private = False
        self.minor.save()
        self.assertIsNone(web_links._profile_card(None, self.minor.username))

    def test_policy_for(self):
        self.assertTrue(minor_policy.policy_for(self.minor)["is_minor"])
        self.assertFalse(minor_policy.policy_for(self.adult)["rules"]["leaderboard_identity_masked"])


class MinorPolicyParentAndBoardTests(TestCase):
    """Parent read-only view + leaderboard masking (written, NOT run)."""

    def setUp(self):
        self.minor = _user("kid2", 14)
        self.adult = _user("viewer", 35)

    def test_leaderboard_serializer_masks_minor(self):
        from leaderboard.models import LeaderboardEntry
        from leaderboard.serializers import LeaderboardEntrySerializer

        entry = LeaderboardEntry.objects.create(
            scope_type="engagement", period_type="all_time", period_key="all", user=self.minor, score=10, rank=1
        )
        data = LeaderboardEntrySerializer(entry, context={"masked_minor_ids": {self.minor.pk}}).data
        self.assertIsNone(data["user"]["profile_picture"])
        self.assertEqual(data["user"]["id"], self.minor.pk)
        plain = LeaderboardEntrySerializer(entry, context={}).data
        self.assertEqual(plain["user"]["username"], self.minor.username)

    def test_child_privacy_requires_link(self):
        from user_profile import role_settings as rs

        with self.assertRaises(rs.ChildLimitError):
            rs.child_privacy(self.adult, self.minor.pk)

    def test_cohost_blocked_for_minor_flag(self):
        self.assertTrue(minor_policy.blocked(self.minor, minor_policy.LIVE_GUEST))
        with override_settings(MINOR_POLICY_LIVE_GUEST="off"):
            self.assertFalse(minor_policy.blocked(self.minor, minor_policy.LIVE_GUEST))
