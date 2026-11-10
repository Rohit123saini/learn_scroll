"""P-A: the phone-measured first-feed latency (client_ms on the server's own `first_feed` row).
Written, NOT run. Run: python manage.py test core.tests_first_feed_latency"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User

from . import funnel
from .models_funnel import FunnelEvent

E = FunnelEvent.Event
URL = "/core/funnel/first-feed-latency/"


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


def _first_feed(user, age=timedelta(0), **props):
    row = FunnelEvent.objects.create(event=E.FIRST_FEED, user=user, dedupe_key=f"first_feed:{user.pk}",
                                     props={"count": 5, "personalised": True, **props})
    if age:
        FunnelEvent.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)
    return row


class RecordClientFirstFeedTests(TestCase):
    def setUp(self):
        self.u = _user("lat1")

    def _props(self):
        return FunnelEvent.objects.get(event=E.FIRST_FEED, user=self.u).props

    def test_stores_ms_and_device_class_on_the_server_row(self):
        _first_feed(self.u)
        self.assertEqual(funnel.record_client_first_feed(self.u, ms=1840, device_class="low_end"), "stored")
        p = self._props()
        self.assertEqual(p["client_ms"], 1840)
        self.assertEqual(p["client_device_class"], "low_end")
        self.assertEqual(p["count"], 5)  # the server's own props are untouched

    def test_second_report_is_ignored(self):
        _first_feed(self.u)
        funnel.record_client_first_feed(self.u, ms=1000)
        self.assertEqual(funnel.record_client_first_feed(self.u, ms=9000), "already")
        self.assertEqual(self._props()["client_ms"], 1000)

    def test_no_first_feed_row_yet(self):
        self.assertEqual(funnel.record_client_first_feed(self.u, ms=1000), "no_first_feed")
        # (the signup signal already wrote a SIGNUP row; the point is that no FIRST_FEED row is invented)
        self.assertFalse(FunnelEvent.objects.filter(user=self.u, event=E.FIRST_FEED).exists())  # never creates a row

    def test_old_first_feed_is_not_a_cold_start(self):
        _first_feed(self.u, age=funnel.CLIENT_FIRST_FEED_WINDOW + timedelta(minutes=1))
        self.assertEqual(funnel.record_client_first_feed(self.u, ms=1000), "too_late")
        self.assertNotIn("client_ms", self._props())

    def test_bad_values(self):
        _first_feed(self.u)
        for bad in (0, -5, funnel.CLIENT_FIRST_FEED_MAX_MS + 1, "abc", None, True):
            self.assertEqual(funnel.record_client_first_feed(self.u, ms=bad), "bad_value", repr(bad))
        self.assertNotIn("client_ms", self._props())

    def test_unknown_device_class_is_dropped_not_stored(self):
        _first_feed(self.u)
        funnel.record_client_first_feed(self.u, ms=800, device_class="<script>")
        self.assertNotIn("client_device_class", self._props())

    def test_anonymous(self):
        self.assertEqual(funnel.record_client_first_feed(None, ms=1000), "anonymous")


class FirstFeedLatencyEndpointTests(TestCase):
    def setUp(self):
        self.u = _user("lat_api")
        self.c = APIClient()
        self.c.force_authenticate(self.u)

    def test_login_required(self):
        r = APIClient().post(URL, {"ms": 1000}, format="json")
        self.assertIn(r.status_code, (401, 403))

    def test_stored(self):
        _first_feed(self.u)
        r = self.c.post(URL, {"ms": 1500, "device_class": "standard"}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["status"], "stored")

    def test_refused_value_is_a_status_not_an_error(self):
        r = self.c.post(URL, {"ms": 1500}, format="json")  # no first_feed row yet
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["status"], "no_first_feed")

    def test_missing_or_non_numeric_ms_is_400(self):
        for body in ({}, {"ms": "fast"}, {"ms": [1]}, {"ms": True}, {"ms": None}):
            self.assertEqual(self.c.post(URL, body, format="json").status_code, 400, body)

    def test_float_string_is_accepted(self):
        _first_feed(self.u)
        self.assertEqual(self.c.post(URL, {"ms": "1234.7"}, format="json").data["status"], "stored")
        self.assertEqual(FunnelEvent.objects.get(event=E.FIRST_FEED, user=self.u).props["client_ms"], 1234)

    def test_a_client_still_cannot_create_a_first_feed_event(self):
        r = self.c.post("/core/funnel/events/", {"device_id": "device-abcdef1234567890", "events": [{"event": "first_feed"}]},
                        format="json")
        self.assertEqual(r.status_code, 400)


class ReportTests(TestCase):
    def _signed_up_with_first_feed(self, name, ms=None, cls=None):
        u = _user(name)
        dev = f"device-{name:0<12}abcdef"[:32]
        # the report only counts users whose install row is linked to them and who signed up after it
        FunnelEvent.objects.create(event=E.INSTALL, user=u, device_id=dev, platform="android", dedupe_key=f"install:{dev}")
        # the post_save signal on User already recorded SIGNUP (dedupe_key signup:<pk>); don't insert a second one
        funnel.record_once_per_user(E.SIGNUP, u)
        _first_feed(u)
        if ms is not None:
            funnel.record_client_first_feed(u, ms=ms, device_class=cls or "")
        return u

    def test_report_block_percentiles_and_device_split(self):
        for i, (ms, cls) in enumerate([(1000, "standard"), (2000, "standard"), (3000, "low_end"), (9000, "low_end")]):
            self._signed_up_with_first_feed(f"rep{i}", ms, cls)
        self._signed_up_with_first_feed("rep_none")  # never reported: not counted
        block = funnel.funnel_report(days=30)["cold_start"]["client_first_feed"]
        self.assertEqual(block["count"], 4)
        self.assertEqual(block["median_ms"], 3000)  # nearest rank of [1000, 2000, 3000, 9000] at q=.5 (index round(1.5)=2)
        self.assertEqual(block["p95_ms"], 9000)
        # two values per class: index round(0.5)=0 -> the lower one
        self.assertEqual(block["median_ms_by_device_class"], {"low_end": 3000, "standard": 1000})

    def test_empty_report(self):
        block = funnel.funnel_report(days=30)["cold_start"]["client_first_feed"]
        self.assertEqual(block["count"], 0)
        self.assertIsNone(block["median_ms"])
        self.assertEqual(block["median_ms_by_device_class"], {})

    def test_percentile_helper(self):
        self.assertIsNone(funnel._percentile([], 0.5))
        self.assertEqual(funnel._percentile([7], 0.95), 7)
        self.assertEqual(funnel._percentile([1, 2, 3, 4, 5], 0.5), 3)
