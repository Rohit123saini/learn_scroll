# tuitionclass/tests_share_commission_rules.py
"""
Share & earn rules:
  * only a CLASSROOM / TEST SERIES link can ever earn commission - an app or
    profile share never does;
  * a test series pays only when its creator turned referral_enabled on AND chose a % above 0 (no platform default);
  * a later class / test-series link is not blocked by an earlier app link;
  * feed share exposes the sharer's ref_code only when commission is allowed.

NOTE: written without being able to run Django in the authoring environment -
run `python manage.py test tuitionclass.tests_share_commission_rules` once.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from post.shared_items import resolve_shared_item
from testseries.access import series_is_referable, series_referral_percent
from testseries.models import TestSeries

from .models import ReferralAttribution

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password="pw-12345")


def _series(creator, **kw):
    base = dict(source=TestSeries.Source.INDIVIDUAL, creator=creator, title="T",
                status=TestSeries.Status.PUBLISHED, is_paid=True, price_coins=100)
    base.update(kw)
    return TestSeries.objects.create(**base)


class AttributionSourceTests(TestCase):
    def test_app_share_never_commissionable(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="app")
        self.assertIsNotNone(ReferralAttribution.active_for(b))
        self.assertIsNone(ReferralAttribution.active_for(b, commissionable_only=True))

    def test_certificate_share_never_commissionable(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="certificate")
        self.assertIsNone(ReferralAttribution.active_for(b, commissionable_only=True))

    def test_class_and_testseries_links_are_commissionable(self):
        a, b, c = _user("a"), _user("b"), _user("c")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="classroom", source_id="x")
        ReferralAttribution.claim(referee=c, referrer=a, source_type="testseries", source_id="y")
        self.assertEqual(ReferralAttribution.active_for(b, commissionable_only=True).referrer_id, a.id)
        self.assertEqual(ReferralAttribution.active_for(c, commissionable_only=True).referrer_id, a.id)

    def test_app_link_does_not_block_later_class_link(self):
        a, d, b = _user("a"), _user("d"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="app")
        row, created = ReferralAttribution.claim(referee=b, referrer=d, source_type="classroom", source_id="x")
        self.assertTrue(created)
        self.assertEqual(ReferralAttribution.active_for(b, commissionable_only=True).referrer_id, d.id)

    def test_first_commissionable_touch_still_wins(self):
        a, d, b = _user("a"), _user("d"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="classroom", source_id="x")
        row, created = ReferralAttribution.claim(referee=b, referrer=d, source_type="testseries", source_id="y")
        self.assertFalse(created)
        self.assertEqual(row.referrer_id, a.id)


@override_settings(TESTSERIES_REFERRAL_COMMISSION_PERCENT="10", TESTSERIES_REFERRAL_MAX_PERCENT="30")
class TestSeriesOptInTests(TestCase):
    def test_default_off_even_when_platform_percent_is_on(self):
        s = _series(_user("owner"))
        self.assertFalse(series_is_referable(s))

    def test_opt_in_with_zero_percent_pays_nothing_no_platform_default(self):
        # platform percent is 10 in this class, but it is NOT a fallback any more
        s = _series(_user("owner"), referral_enabled=True, referral_commission_percent=0)
        self.assertEqual(int(series_referral_percent(s)), 0)
        self.assertFalse(series_is_referable(s))

    def test_owner_opt_in_with_percent_enables_and_uses_it(self):
        s = _series(_user("owner"), referral_enabled=True, referral_commission_percent=10)
        self.assertTrue(series_is_referable(s))
        self.assertEqual(int(series_referral_percent(s)), 10)

    def test_owner_percent_is_used_and_capped(self):
        owner = _user("owner")
        self.assertEqual(int(series_referral_percent(_series(owner, referral_enabled=True, referral_commission_percent=20))), 20)
        self.assertEqual(int(series_referral_percent(_series(owner, referral_enabled=True, referral_commission_percent=45))), 30)

    def test_free_series_never_referable(self):
        s = _series(_user("owner"), is_paid=False, price_coins=0, referral_enabled=True, referral_commission_percent=10)
        self.assertFalse(series_is_referable(s))


class AttributionItemMatchTests(TestCase):
    """referral_resolve_referrer: the attribution must come from the link of THAT class / test series."""

    def _resolve(self, buyer, source_type, source_id, aliases=()):
        from .bridge import referral_resolve_referrer

        return referral_resolve_referrer(
            buyer=buyer, source_type=source_type, source_id=source_id, source_aliases=aliases
        )

    def test_same_item_matches(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="testseries", source_id="S1")
        self.assertEqual(self._resolve(b, "testseries", "S1"), a.id)

    def test_other_series_does_not_match(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="testseries", source_id="S1")
        self.assertIsNone(self._resolve(b, "testseries", "S2"))

    def test_class_link_does_not_pay_on_a_series_and_vice_versa(self):
        a, b, c = _user("a"), _user("b"), _user("c")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="classroom", source_id="7")
        ReferralAttribution.claim(referee=c, referrer=a, source_type="testseries", source_id="7")
        self.assertIsNone(self._resolve(b, "testseries", "7"))
        self.assertIsNone(self._resolve(c, "classroom", "7"))
        self.assertEqual(self._resolve(b, "classroom", "7"), a.id)

    def test_alias_matches_but_unrelated_alias_does_not(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="testseries", source_id="slug-1")
        self.assertEqual(self._resolve(b, "testseries", "uuid-1", aliases=["slug-1"]), a.id)
        self.assertIsNone(self._resolve(b, "testseries", "uuid-1", aliases=["slug-2"]))

    def test_app_link_never_matches(self):
        a, b = _user("a"), _user("b")
        ReferralAttribution.claim(referee=b, referrer=a, source_type="app")
        self.assertIsNone(self._resolve(b, "testseries", ""))


class FeedShareCardTests(TestCase):
    def test_testseries_card_has_ref_code_only_when_owner_allows(self):
        owner, sharer = _user("owner"), _user("sharer")
        off = _series(owner)
        zero = _series(owner, referral_enabled=True, referral_commission_percent=0)
        on = _series(owner, referral_enabled=True, referral_commission_percent=10)
        self.assertIsNone(resolve_shared_item("testseries", off.id, sharer.id)["ref_code"])
        self.assertIsNone(resolve_shared_item("testseries", zero.id, sharer.id)["ref_code"])  # on, but 0% -> no earning
        card = resolve_shared_item("testseries", on.id, sharer.id)
        self.assertTrue(card["can_earn"])
        self.assertTrue(card["ref_code"])
        self.assertEqual(card["commission_basis"], "per_user")

    def test_owner_sharing_own_item_earns_nothing(self):
        owner = _user("owner")
        card = resolve_shared_item(
            "testseries", _series(owner, referral_enabled=True, referral_commission_percent=10).id, owner.id
        )
        self.assertFalse(card["can_earn"])
        self.assertIsNone(card["ref_code"])

    def test_unavailable_item(self):
        draft = _series(_user("owner"), status=TestSeries.Status.DRAFT)
        self.assertTrue(resolve_shared_item("testseries", draft.id, 1)["is_unavailable"])


# ---------------------------------------------------------------------------
# Web landing pages for class / test-series share links (carry ?ref= + install referrer)
# ---------------------------------------------------------------------------
@override_settings(
    PLAY_STORE_URL="https://play.google.com/store/apps/details?id=com.learnscroll.app",
    APP_STORE_URL="https://apps.apple.com/app/id1",
    IOS_APP_ID="TEAM.com.learnscroll.app",
    PUBLIC_WEB_BASE_URL="https://learnscroll.app",
)
class ShareLandingTests(TestCase):
    def test_classroom_landing_carries_ref_and_play_referrer(self):
        r = self.client.get("/classroom/5", {"ref": "AB12CD34"})
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("learnscroll://classroom/5?ref=AB12CD34", body)
        self.assertIn("referrer=ref%3DAB12CD34%26t%3Dclassroom%26id%3D5", body)
        self.assertIn("learnscroll-ref:AB12CD34:classroom:5", body)

    def test_test_series_landing(self):
        r = self.client.get("/test-series/abc123xy/", {"ref": "ZZ99YY88"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("learnscroll://test-series/abc123xy?ref=ZZ99YY88", r.content.decode())

    def test_bad_ref_is_ignored_not_reflected(self):
        r = self.client.get("/classroom/5", {"ref": '"><script>alert(1)</script>'})
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn("alert(1)", body)
        self.assertNotIn("referrer=", body)

    def test_bad_ids_are_404(self):
        self.assertEqual(self.client.get("/classroom/abc").status_code, 404)
        self.assertEqual(self.client.get("/test-series/a/").status_code, 404)

    def test_aasa_includes_share_paths(self):
        r = self.client.get("/.well-known/apple-app-site-association")
        paths = r.json()["applinks"]["details"][0]["paths"]
        self.assertIn("/classroom/*", paths)
        self.assertIn("/test-series/*", paths)

    def test_test_series_share_url_is_the_web_landing(self):
        from testseries.serializers import TestSeriesSerializer

        s = _series(_user("owner"), referral_enabled=True)
        s.ensure_share_slug(save=True)
        url = TestSeriesSerializer(s, context={}).data["share_url"]
        self.assertEqual(url, f"https://learnscroll.app/test-series/{s.share_slug}/")


# ---------------------------------------------------------------------------
# T2.1 - landing page: OG tags, class / teacher / price / time, trial CTA
# ---------------------------------------------------------------------------
@override_settings(PUBLIC_WEB_BASE_URL="https://learnscroll.app")
class ClassLandingCardTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # landing cards are cached for 60s; ids can repeat across tests
        from django.contrib.auth import get_user_model
        from tuitionclass.models import ClassPass, Classroom

        U = get_user_model()
        self.teacher = U.objects.create_user(username="mrsharma", password="x", email="t@example.com")
        self.c = Classroom.objects.create(
            teacher=self.teacher, title="JEE Physics Batch", subject="Physics",
            description="Daily doubt solving <b>live</b>", is_trial_enabled=True, trial_duration_minutes=5,
        )
        self.c.set_trial_password("SECRET99")  # stored hashed, like production
        self.c.save()
        ClassPass.objects.create(classroom=self.c, pass_type="monthly", price=300, validity_days=30)

    def test_card_has_og_teacher_price_and_trial_cta(self):
        body = self.client.get(f"/classroom/{self.c.id}", {"ref": "AB12CD34"}).content.decode()
        self.assertIn('property="og:title"', body)
        self.assertIn("JEE Physics Batch", body)
        self.assertIn("mrsharma", body)
        self.assertIn("From 300 coins", body)
        self.assertIn("learnscroll://classroom/%d?ref=AB12CD34&amp;trial=1" % self.c.id, body)

    def test_no_secret_and_no_html_injection(self):
        body = self.client.get(f"/classroom/{self.c.id}").content.decode()
        self.assertNotIn("SECRET99", body)
        self.assertNotIn("<b>live</b>", body)

    def test_inactive_class_falls_back_to_generic(self):
        self.c.is_active = False
        self.c.save()
        body = self.client.get(f"/classroom/{self.c.id}").content.decode()
        self.assertNotIn("JEE Physics Batch", body)
        self.assertNotIn("og:title", body)


    def test_not_indexable_by_default_but_has_og_for_previews(self):
        body = self.client.get(f"/classroom/{self.c.id}").content.decode()
        self.assertIn('content="noindex"', body)
        self.assertIn('property="og:title"', body)
