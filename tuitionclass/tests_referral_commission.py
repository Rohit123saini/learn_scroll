# tuitionclass/tests_referral_commission.py
"""
TASK 12 — Refer & Earn: unguessable codes, link attribution, commission
ledger, fraud rules, test-series commission carve-out.

NOTE: written without being able to run Django in the authoring environment —
run `python manage.py test tuitionclass.tests_referral_commission` once and
adjust `_user()` if login.User needs more than username/password.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from user_profile import fraud
from user_profile.models import CoinLedger

from .models import (
    ReferralAttribution,
    ReferralCode,
    ReferralCommission,
    referral_code_for_user,
    referral_code_to_user_id,
)

User = get_user_model()


def _user(name, coins=0):
    u = User.objects.create_user(username=name, password="pw-12345")
    if coins:
        CoinLedger.objects.record_transaction(
            user=u, transaction_type=CoinLedger.TransactionType.PURCHASE, amount=coins, reference=f"seed-{name}"
        )
        u.refresh_from_db()
    return u


class CodeTests(TestCase):
    def test_code_is_random_stable_and_roundtrips(self):
        a, b = _user("a"), _user("b")
        ca, cb = referral_code_for_user(a.id), referral_code_for_user(b.id)
        self.assertNotEqual(ca, cb)
        self.assertEqual(ca, referral_code_for_user(a.id))  # stable
        self.assertTrue(ca.startswith("L"))
        self.assertEqual(referral_code_to_user_id(ca), a.id)
        self.assertEqual(referral_code_to_user_id(ca.lower()), a.id)
        self.assertEqual(ReferralCode.objects.count(), 2)

    def test_unknown_and_malformed_codes(self):
        for bad in ("", "LZZZZZZZZ", "X123", "R", "Rzz"):
            self.assertIsNone(referral_code_to_user_id(bad))

    def test_legacy_code_flag(self):
        a = _user("a")
        legacy = "R" + format(a.id * 7919, "X")
        self.assertEqual(referral_code_to_user_id(legacy), a.id)
        with override_settings(REFERRAL_ACCEPT_LEGACY_CODES=False):
            self.assertIsNone(referral_code_to_user_id(legacy))


class AttributionTests(TestCase):
    def test_first_touch_wins(self):
        a, b, c = _user("a"), _user("b"), _user("c")
        row, created = ReferralAttribution.claim(referee=c, referrer=a)
        self.assertTrue(created)
        row2, created2 = ReferralAttribution.claim(referee=c, referrer=b)
        self.assertFalse(created2)
        self.assertEqual(row2.referrer_id, a.id)

    def test_expired_attribution_can_be_reclaimed(self):
        a, b, c = _user("a"), _user("b"), _user("c")
        row, _ = ReferralAttribution.claim(referee=c, referrer=a)
        ReferralAttribution.objects.filter(pk=row.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertIsNone(ReferralAttribution.active_for(c))
        row2, created = ReferralAttribution.claim(referee=c, referrer=b)
        self.assertTrue(created)
        self.assertEqual(row2.referrer_id, b.id)
        self.assertEqual(ReferralAttribution.active_for(c).referrer_id, b.id)


class FraudTests(TestCase):
    def test_self_and_circular(self):
        a, b = _user("a"), _user("b")
        self.assertEqual(fraud.check_referral_abuse(a, a), (False, "self_referral"))
        self.assertEqual(fraud.check_referral_abuse(a, b), (True, ""))
        ReferralAttribution.claim(referee=a, referrer=b)  # b referred a
        self.assertEqual(fraud.check_referral_abuse(a, b), (False, "circular_referral"))

    @override_settings(REFERRAL_MAX_COMMISSION_COINS_PER_DAY=100)
    def test_daily_coin_cap(self):
        a, b = _user("a"), _user("b")
        ReferralCommission.objects.create(
            referrer=a, referee=b, kind="testseries", reference="r1", gross_coins=900, percent=10,
            commission_coins=90, status="paid",
        )
        self.assertEqual(fraud.check_referral_abuse(a, b, coins=10), (True, ""))
        self.assertEqual(fraud.check_referral_abuse(a, b, coins=11), (False, "daily_coin_cap"))


@override_settings(TESTSERIES_REFERRAL_COMMISSION_PERCENT="10", TESTSERIES_REFERRAL_MAX_PERCENT="50")
class TestSeriesCommissionTests(TestCase):
    def _series(self, creator, price=100, source="individual", **kw):
        from testseries.models import TestSeries

        base = dict(
            creator=creator, title="T", source=source, is_paid=price > 0, price_coins=price,
            status=TestSeries.Status.PUBLISHED, referral_enabled=True, referral_commission_percent=10,
        )
        base.update(kw)
        return TestSeries.objects.create(**base)

    def test_snapshot_only_with_active_attribution(self):
        from testseries.access import referral_snapshot

        creator, ref, buyer = _user("creator"), _user("ref"), _user("buyer")
        s = self._series(creator)
        self.assertEqual(referral_snapshot(buyer=buyer, series=s)[0], None)
        # attribution must come from THIS series' own link (source_type + source_id), not a bare / app link
        ReferralAttribution.claim(referee=buyer, referrer=ref, source_type="testseries", source_id=str(s.id))
        rid, pct, coins = referral_snapshot(buyer=buyer, series=s)
        self.assertEqual((rid, coins), (ref.id, 10))

    def test_no_platform_default_when_creator_percent_is_zero(self):
        # T-REF: the old fallback (0% -> platform 10%) is gone. Opt-in at 0% pays nothing, even with a valid link.
        from testseries.access import referral_snapshot

        creator, ref, buyer = _user("creator"), _user("ref"), _user("buyer")
        s = self._series(creator, referral_commission_percent=0)
        ReferralAttribution.claim(referee=buyer, referrer=ref, source_type="testseries", source_id=str(s.id))
        self.assertEqual(referral_snapshot(buyer=buyer, series=s), (None, 0, 0))

    def test_creator_percent_is_what_is_paid_not_platform_percent(self):
        from testseries.access import referral_snapshot

        creator, ref, buyer = _user("creator"), _user("ref"), _user("buyer")
        s = self._series(creator, referral_commission_percent=25)
        ReferralAttribution.claim(referee=buyer, referrer=ref, source_type="testseries", source_id=str(s.id))
        rid, pct, coins = referral_snapshot(buyer=buyer, series=s)
        self.assertEqual((rid, int(pct), coins), (ref.id, 25, 25))  # NOT the platform 10%

    def test_snapshot_ignores_attribution_from_another_series_or_a_class(self):
        from testseries.access import referral_snapshot

        creator, ref, b1, b2 = _user("creator"), _user("ref"), _user("b1"), _user("b2")
        s, other = self._series(creator), self._series(creator)
        ReferralAttribution.claim(referee=b1, referrer=ref, source_type="testseries", source_id=str(other.id))
        ReferralAttribution.claim(referee=b2, referrer=ref, source_type="classroom", source_id=str(s.id))
        self.assertIsNone(referral_snapshot(buyer=b1, series=s)[0])
        self.assertIsNone(referral_snapshot(buyer=b2, series=s)[0])

    def test_snapshot_accepts_share_slug_as_source_id(self):
        # The shared link is /test-series/<share_slug>/ and the app records the last path segment (the slug).
        from testseries.access import referral_snapshot

        creator, ref, buyer = _user("creator"), _user("ref"), _user("buyer")
        s = self._series(creator)
        slug = s.ensure_share_slug(save=True)
        ReferralAttribution.claim(referee=buyer, referrer=ref, source_type="testseries", source_id=slug)
        self.assertEqual(referral_snapshot(buyer=buyer, series=s)[0], ref.id)

    def test_percent_is_capped(self):
        from testseries.access import referral_percent

        with override_settings(TESTSERIES_REFERRAL_COMMISSION_PERCENT="90"):
            self.assertEqual(int(referral_percent()), 50)

    def test_pay_is_idempotent_and_ledgered(self):
        from tuitionclass.bridge import referral_pay_testseries_commission as pay

        ref, buyer = _user("ref"), _user("buyer")
        kw = dict(referrer_id=ref.id, referee_id=buyer.id, purchase_id="p1", series_id="s1",
                  gross_coins=100, percent=10, coins=10)
        self.assertEqual(pay(**kw), 10)
        self.assertEqual(pay(**kw), 10)  # second call: no double credit
        ref.refresh_from_db()
        self.assertEqual(ref.coin, 10)
        self.assertEqual(ReferralCommission.objects.filter(referrer=ref).count(), 1)
        self.assertTrue(
            CoinLedger.objects.filter(user=ref, transaction_type=CoinLedger.TransactionType.REFERRAL_COMMISSION).exists()
        )

    def test_blocked_commission_pays_nothing(self):
        from tuitionclass.bridge import referral_pay_testseries_commission as pay

        a = _user("a")
        out = pay(referrer_id=a.id, referee_id=a.id, purchase_id="p2", series_id="s1",
                  gross_coins=100, percent=10, coins=10)
        self.assertEqual(out, 0)
        row = ReferralCommission.objects.get(reference="testseries_referral:p2")
        self.assertEqual((row.status, row.block_reason), ("blocked", "self_referral"))
