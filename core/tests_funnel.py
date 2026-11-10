"""T3.8 tests: idempotent recording, client endpoint, server-side hooks, funnel math, staff gating."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User

from . import funnel
from .models_funnel import FunnelEvent

E = FunnelEvent.Event
DEV = "device-abcdef1234567890"


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


class RecordTests(TestCase):
    def test_install_is_idempotent(self):
        self.assertTrue(funnel.record_install(DEV, "android", "1.0"))
        self.assertFalse(funnel.record_install(DEV, "android", "1.0"))
        self.assertEqual(FunnelEvent.objects.filter(event=E.INSTALL).count(), 1)

    def test_app_open_once_per_day_per_user(self):
        u = _user("opener")
        self.assertTrue(funnel.record_app_open(DEV, user=u))
        self.assertFalse(funnel.record_app_open(DEV, user=u))
        self.assertTrue(funnel.record_app_open(DEV))  # anonymous open of the same device is its own key

    def test_signup_recorded_by_signal_once(self):
        u = _user("newbie")
        self.assertEqual(FunnelEvent.objects.filter(event=E.SIGNUP, user=u).count(), 1)
        u.first_name = "x"
        u.save()
        self.assertEqual(FunnelEvent.objects.filter(event=E.SIGNUP, user=u).count(), 1)

    def test_record_never_raises(self):
        self.assertFalse(funnel.record("not-an-event-" + "x" * 100, dedupe_key="k" * 500))


class EventsEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def post(self, body, **kw):
        return self.client.post("/core/funnel/events/", body, format="json", **kw)

    def test_anonymous_install_and_open(self):
        r = self.post({"device_id": DEV, "platform": "android", "events": [{"event": "install"}, {"event": "app_open"}]})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["stored"], 2)
        self.assertEqual(self.post({"device_id": DEV, "events": [{"event": "install"}]}).json()["stored"], 0)

    def test_rejects_bad_device_server_only_events_and_oversize(self):
        self.assertEqual(self.post({"device_id": "short", "events": []}).status_code, 400)
        self.assertEqual(self.post({"device_id": DEV, "events": [{"event": "signup"}]}).status_code, 400)
        self.assertEqual(self.post({"device_id": DEV, "events": [{"event": "app_open"}] * 21}).status_code, 400)

    def test_invalid_token_still_counts_as_anonymous(self):
        r = self.post({"device_id": DEV, "events": [{"event": "install"}]}, HTTP_AUTHORIZATION="Bearer garbage")
        self.assertEqual(r.status_code, 202)

    def test_authenticated_call_links_install_and_signup_to_user(self):
        self.post({"device_id": DEV, "platform": "ios", "events": [{"event": "install"}]})
        u = _user("linker")
        c = APIClient()
        c.force_authenticate(u)
        r = c.post("/core/funnel/events/", {"device_id": DEV, "events": []}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(FunnelEvent.objects.get(event=E.INSTALL).user, u)
        self.assertEqual(FunnelEvent.objects.get(event=E.SIGNUP, user=u).device_id, DEV)


class FunnelReportTests(TestCase):
    """Builds a small known cohort and checks every number."""

    def setUp(self):
        self.now = timezone.now()

    def _cohort_member(self, name, device, *, onboard=False, skipped=False, doubt=False, test=False,
                       signup_days_ago=10, open_days_after=()):
        u = _user(name)
        signup_at = self.now - timedelta(days=signup_days_ago)
        FunnelEvent.objects.filter(event=E.SIGNUP, user=u).update(created_at=signup_at)
        FunnelEvent.objects.create(event=E.INSTALL, user=u, device_id=device, platform="android",
                                   dedupe_key=f"install:{device}", created_at=signup_at - timedelta(minutes=5))
        if onboard:
            FunnelEvent.objects.create(event=E.ONBOARDING_COMPLETED, user=u, props={"skipped": skipped},
                                       dedupe_key=f"onboarding:{u.pk}", created_at=signup_at + timedelta(minutes=2))
        if doubt:
            FunnelEvent.objects.create(event=E.FIRST_DOUBT, user=u, dedupe_key=f"first_doubt:{u.pk}",
                                       created_at=signup_at + timedelta(hours=1))
        if test:
            FunnelEvent.objects.create(event=E.FIRST_TEST, user=u, dedupe_key=f"first_test:{u.pk}",
                                       created_at=signup_at + timedelta(hours=2))
        for n in open_days_after:
            FunnelEvent.objects.create(event=E.APP_OPEN, user=u, device_id=device,
                                       dedupe_key=f"open:{u.pk}:{n}", created_at=signup_at + timedelta(days=n))
        return u

    def test_numbers(self):
        self._cohort_member("a", "dev-aaaaaaaaaaaaaaaa", onboard=True, doubt=True, open_days_after=(1, 7))
        self._cohort_member("b", "dev-bbbbbbbbbbbbbbbb", onboard=True, skipped=True, test=True, open_days_after=(1,))
        self._cohort_member("c", "dev-cccccccccccccccc", onboard=True)            # onboarded, never activated
        self._cohort_member("d", "dev-dddddddddddddddd")                          # signed up, no onboarding
        self._cohort_member("e", "dev-eeeeeeeeeeeeeeee", signup_days_ago=3)       # too young for D7
        FunnelEvent.objects.create(event=E.INSTALL, device_id="dev-ffffffffffffffff", platform="android",
                                   dedupe_key="install:f", created_at=self.now - timedelta(days=2))  # never logged in

        r = funnel.funnel_report(days=30)
        counts = [s["count"] for s in r["steps"]]
        self.assertEqual(counts, [6, 5, 3, 2])
        self.assertEqual(r["steps"][1]["pct_of_previous"], round(100 * 5 / 6, 1))
        self.assertEqual(r["details"]["onboarding_skipped"], 1)
        self.assertEqual(r["details"]["installs_not_logged_in"], 1)
        # D1: a,b,c,d,e all eligible (signup >= 1 day ago); a & b opened on day 1
        self.assertEqual(r["retention"]["d1"], {"eligible": 5, "retained": 2, "pct": 40.0})
        # D7: e is too young -> a,b,c,d eligible; only a opened on day 7
        self.assertEqual(r["retention"]["d7"], {"eligible": 4, "retained": 1, "pct": 25.0})

    def test_returning_user_on_new_phone_is_not_a_signup(self):
        old = _user("veteran")
        FunnelEvent.objects.filter(event=E.SIGNUP, user=old).update(created_at=self.now - timedelta(days=200))
        FunnelEvent.objects.create(event=E.INSTALL, user=old, device_id="dev-gggggggggggggggg", dedupe_key="install:g",
                                   created_at=self.now - timedelta(days=1))
        r = funnel.funnel_report(days=30)
        self.assertEqual([s["count"] for s in r["steps"]][:2], [1, 0])

    def test_platform_filter(self):
        self._cohort_member("a", "dev-aaaaaaaaaaaaaaaa")
        self.assertEqual(funnel.funnel_report(days=30, platform="ios")["steps"][0]["count"], 0)


class StaffGatingTests(TestCase):
    def test_report_requires_staff(self):
        c = APIClient()
        self.assertIn(c.get("/core/analytics/funnel/").status_code, (401, 403))
        c.force_authenticate(_user("plain"))
        self.assertEqual(c.get("/core/analytics/funnel/").status_code, 403)
        staff = _user("boss")
        staff.is_staff = True
        staff.save()
        c.force_authenticate(staff)
        r = c.get("/core/analytics/funnel/?days=7&platform=android")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["window"]["days"], 7)

    def test_dashboard_redirects_anonymous_and_renders_for_staff(self):
        self.assertEqual(self.client.get("/core/analytics/funnel/dashboard/").status_code, 302)
        staff = _user("boss2")
        staff.is_staff = True
        staff.save()
        self.client.force_login(staff)
        r = self.client.get("/core/analytics/funnel/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Growth funnel")
