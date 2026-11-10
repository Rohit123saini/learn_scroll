# tuitionclass/tests_referral_earnable.py
"""
Refer & earn is URL-only and creator opt-in. These tests cover:
  * GET /referrals/earnable/  - lists ONLY classes / test series whose creator set a commission % > 0,
    to every user except the owner, each with a share URL that carries the caller's ?ref=<code>;
  * Classroom.referral_active (enabled AND % > 0) and the refer-link that rides on it;
  * the test-series serializer refusing referral ON with 0%.

NOTE: written without being able to run Django in the authoring environment - run
`python manage.py test tuitionclass.tests_referral_earnable` once and adjust fixtures if a model needs more fields.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from testseries.models import TestSeries

from .models import Classroom, referral_code_for_user

User = get_user_model()

EARNABLE = "/referrals/earnable/"


def _user(name):
    return User.objects.create_user(username=name, password="pw-12345")


def _class(teacher, title="Class", enabled=True, percent=20, **kw):
    return Classroom.objects.create(
        teacher=teacher, title=title, subject="Physics",
        referral_enabled=enabled, referral_commission_percent=Decimal(str(percent)), **kw,
    )


def _series(creator, title="Series", enabled=True, percent=10, **kw):
    base = dict(
        creator=creator, title=title, source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED,
        is_paid=True, price_coins=100, referral_enabled=enabled, referral_commission_percent=percent,
    )
    base.update(kw)
    return TestSeries.objects.create(**base)


def _get(user, **params):
    client = APIClient()
    client.force_authenticate(user)
    return client.get(EARNABLE, params)


def _titles(resp):
    return {
        "classes": {c["title"] for c in resp.data["classes"]},
        "test_series": {s["title"] for s in resp.data["test_series"]},
    }


class ClassroomReferralActiveTests(TestCase):
    def test_active_only_when_enabled_and_percent_above_zero(self):
        t = _user("t")
        self.assertTrue(_class(t, "on", enabled=True, percent=5).referral_active)
        self.assertFalse(_class(t, "zero", enabled=True, percent=0).referral_active)
        self.assertFalse(_class(t, "off", enabled=False, percent=30).referral_active)
        self.assertFalse(_class(t, "off0", enabled=False, percent=0).referral_active)

    def test_refer_link_refused_when_enabled_but_zero_percent(self):
        teacher, other = _user("teacher"), _user("other")
        c = _class(teacher, enabled=True, percent=0)
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(client.get(f"/tuitionclass/classrooms/{c.id}/refer-link/").status_code, 400)

    @override_settings(TUITIONCLASS_WEB_BASE_URL="https://learnscroll.app")
    def test_refer_link_works_with_percent(self):
        teacher, other = _user("teacher"), _user("other")
        c = _class(teacher, enabled=True, percent=15)
        client = APIClient()
        client.force_authenticate(other)
        resp = client.get(f"/tuitionclass/classrooms/{c.id}/refer-link/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(f"ref={referral_code_for_user(other.id)}", resp.data["web_url"])


class EarnableListTests(TestCase):
    def setUp(self):
        self.owner = _user("owner")
        self.viewer = _user("viewer")

    def test_requires_login(self):
        self.assertIn(APIClient().get(EARNABLE).status_code, (401, 403))

    def test_lists_only_items_with_commission_kept(self):
        _class(self.owner, "class-on", enabled=True, percent=20)
        _class(self.owner, "class-zero", enabled=True, percent=0)
        _class(self.owner, "class-off", enabled=False, percent=20)
        _series(self.owner, "series-on", enabled=True, percent=10)
        _series(self.owner, "series-zero", enabled=True, percent=0)
        _series(self.owner, "series-off", enabled=False, percent=10)

        got = _titles(_get(self.viewer))
        self.assertEqual(got["classes"], {"class-on"})
        self.assertEqual(got["test_series"], {"series-on"})

    def test_every_other_user_sees_the_same_list(self):
        _class(self.owner, "c", percent=20)
        _series(self.owner, "s", percent=10)
        other = _user("other")
        self.assertEqual(_titles(_get(self.viewer)), _titles(_get(other)))
        self.assertEqual(_titles(_get(other)), {"classes": {"c"}, "test_series": {"s"}})

    def test_own_items_are_not_listed(self):
        _class(self.owner, "mine-class", percent=20)
        _series(self.owner, "mine-series", percent=10)
        _class(self.viewer, "viewer-class", percent=20)
        _series(self.viewer, "viewer-series", percent=10)

        got = _titles(_get(self.owner))
        self.assertEqual(got["classes"], {"viewer-class"})
        self.assertEqual(got["test_series"], {"viewer-series"})

    def test_unavailable_classes_are_hidden(self):
        _class(self.owner, "inactive", percent=20, is_active=False)
        _class(self.owner, "deleted", percent=20, is_deleted=True)
        _class(self.owner, "flagged", percent=20, is_flagged=True)
        _class(self.owner, "personal", percent=20, is_personal=True)
        _class(self.owner, "ok", percent=20)
        self.assertEqual(_titles(_get(self.viewer))["classes"], {"ok"})

    def test_unavailable_series_are_hidden(self):
        _series(self.owner, "draft", status=TestSeries.Status.DRAFT)
        _series(self.owner, "archived", status=TestSeries.Status.ARCHIVED)
        _series(self.owner, "free", is_paid=False, price_coins=0)
        _series(self.owner, "campus", source=TestSeries.Source.CAMPUS)
        _series(self.owner, "ok")
        self.assertEqual(_titles(_get(self.viewer))["test_series"], {"ok"})

    @override_settings(TUITIONCLASS_WEB_BASE_URL="https://learnscroll.app", PUBLIC_WEB_BASE_URL="https://learnscroll.app")
    def test_each_item_has_a_share_url_with_the_callers_ref(self):
        c = _class(self.owner, "c", percent=20)
        s = _series(self.owner, "s", percent=10)
        code = referral_code_for_user(self.viewer.id)

        resp = _get(self.viewer)
        klass = resp.data["classes"][0]
        self.assertEqual(klass["type"], "classroom")
        self.assertEqual(klass["share_url"], f"https://learnscroll.app/classroom/{c.id}?ref={code}")
        self.assertEqual(klass["commission_percent"], "20.00")

        s.refresh_from_db()
        self.assertTrue(s.share_slug)  # minted on demand for a legacy published series
        series = resp.data["test_series"][0]
        self.assertEqual(series["type"], "testseries")
        self.assertEqual(series["share_url"], f"https://learnscroll.app/test-series/{s.share_slug}/?ref={code}")
        self.assertEqual(series["commission_percent"], "10.00")

    @override_settings(PUBLIC_WEB_BASE_URL="https://learnscroll.app")
    def test_share_url_ref_is_per_caller(self):
        _series(self.owner, "s", percent=10)
        other = _user("other")
        url_a = _get(self.viewer).data["test_series"][0]["share_url"]
        url_b = _get(other).data["test_series"][0]["share_url"]
        self.assertIn(referral_code_for_user(self.viewer.id), url_a)
        self.assertIn(referral_code_for_user(other.id), url_b)
        self.assertNotEqual(url_a, url_b)

    def test_search_and_limit(self):
        _class(self.owner, "Physics batch", percent=20)
        _class(self.owner, "Chemistry batch", percent=20)
        _series(self.owner, "Physics mock", percent=10)
        got = _titles(_get(self.viewer, q="physics"))
        self.assertEqual(got, {"classes": {"Physics batch"}, "test_series": {"Physics mock"}})
        self.assertEqual(len(_get(self.viewer, limit=1).data["classes"]), 1)
        self.assertEqual(len(_get(self.viewer, limit="junk").data["classes"]), 2)  # bad limit -> default

    def test_empty_when_nothing_has_commission(self):
        _class(self.owner, "c", enabled=False)
        resp = _get(self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"classes": [], "test_series": []})


class SeriesPercentRequiredTests(TestCase):
    """Creator screen / API: turning referral ON needs a real % (> 0, within the platform max)."""

    def _validate(self, series, data):
        from testseries.serializers import TestSeriesSerializer

        ser = TestSeriesSerializer(instance=series, data=data, partial=True, context={})
        ser.is_valid()
        return ser.errors

    @override_settings(TESTSERIES_REFERRAL_MAX_PERCENT="50")
    def test_on_with_zero_percent_is_rejected(self):
        s = _series(_user("c"), enabled=False, percent=0)
        errors = self._validate(s, {"referral_enabled": True, "referral_commission_percent": 0})
        self.assertIn("referral_commission_percent", errors)

    @override_settings(TESTSERIES_REFERRAL_MAX_PERCENT="50")
    def test_on_without_percent_in_the_request_is_rejected_when_stored_percent_is_zero(self):
        s = _series(_user("c"), enabled=False, percent=0)
        errors = self._validate(s, {"referral_enabled": True})
        self.assertIn("referral_commission_percent", errors)

    @override_settings(TESTSERIES_REFERRAL_MAX_PERCENT="50")
    def test_on_with_valid_percent_is_accepted(self):
        s = _series(_user("c"), enabled=False, percent=0)
        errors = self._validate(s, {"referral_enabled": True, "referral_commission_percent": 12})
        self.assertNotIn("referral_commission_percent", errors)
        self.assertNotIn("referral_enabled", errors)

    @override_settings(TESTSERIES_REFERRAL_MAX_PERCENT="50")
    def test_above_platform_max_is_rejected(self):
        s = _series(_user("c"), enabled=False, percent=0)
        errors = self._validate(s, {"referral_enabled": True, "referral_commission_percent": 51})
        self.assertIn("referral_commission_percent", errors)

    def test_turning_it_off_needs_no_percent(self):
        s = _series(_user("c"), enabled=True, percent=10)
        errors = self._validate(s, {"referral_enabled": False})
        self.assertNotIn("referral_commission_percent", errors)
