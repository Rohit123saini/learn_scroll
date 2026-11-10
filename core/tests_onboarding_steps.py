"""P-F1 tests: onboarding step recording, endpoint validation, drop-off report, quick-win conversion, staff gating."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User

from . import funnel, onboarding_steps
from .models_funnel import FunnelEvent

E = FunnelEvent.Event
URL = "/core/onboarding/step/"


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


class RecordStepTests(TestCase):
    def test_first_time_only_per_user_step_action(self):
        u = _user("stepper")
        self.assertTrue(onboarding_steps.record_step(u, "role", "viewed", role="student"))
        self.assertFalse(onboarding_steps.record_step(u, "role", "viewed", role="student"))
        self.assertTrue(onboarding_steps.record_step(u, "role", "completed", role="student"))
        self.assertEqual(FunnelEvent.objects.filter(event=E.ONBOARDING_STEP, user=u).count(), 2)

    def test_unknown_step_action_or_anonymous_is_ignored_and_never_raises(self):
        u = _user("badinput")
        self.assertFalse(onboarding_steps.record_step(u, "nope", "viewed"))
        self.assertFalse(onboarding_steps.record_step(u, "role", "exploded"))
        self.assertFalse(onboarding_steps.record_step(None, "role", "viewed"))
        self.assertFalse(onboarding_steps.record_step(u, None, None))
        self.assertEqual(FunnelEvent.objects.filter(event=E.ONBOARDING_STEP).count(), 0)

    def test_props_keep_only_small_scalars_and_known_role(self):
        u = _user("props")
        onboarding_steps.record_step(u, "goal", "completed", role="hacker", props={"goal_minutes": 10, "note": "free text"})
        row = FunnelEvent.objects.get(event=E.ONBOARDING_STEP, user=u)
        self.assertEqual(row.props, {"step": "goal", "action": "completed", "goal_minutes": 10})


class StepEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _user("endpoint")

    def post(self, body):
        return self.client.post(URL, body, format="json")

    def test_requires_login(self):
        self.assertIn(self.post({"step": "role", "action": "viewed"}).status_code, (401, 403))

    def test_stores_then_reports_already_counted(self):
        self.client.force_authenticate(self.user)
        r = self.post({"step": "quick_win", "action": "viewed", "role": "student"})
        self.assertEqual(r.status_code, 202)
        self.assertTrue(r.json()["stored"])
        self.assertFalse(self.post({"step": "quick_win", "action": "viewed", "role": "student"}).json()["stored"])

    def test_rejects_unknown_step_and_action(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.post({"step": "x", "action": "viewed"}).status_code, 400)
        self.assertEqual(self.post({"step": "role", "action": "x"}).status_code, 400)
        self.assertEqual(self.post({"action": "viewed"}).status_code, 400)
        self.assertEqual(FunnelEvent.objects.filter(event=E.ONBOARDING_STEP).count(), 0)

    def test_goal_minutes_only_when_sane(self):
        self.client.force_authenticate(self.user)
        self.post({"step": "goal", "action": "completed", "goal_minutes": 15})
        self.assertEqual(FunnelEvent.objects.get(event=E.ONBOARDING_STEP).props.get("goal_minutes"), 15)
        other = _user("endpoint2")
        self.client.force_authenticate(other)
        self.post({"step": "goal", "action": "completed", "goal_minutes": 99999})
        self.assertNotIn("goal_minutes", FunnelEvent.objects.get(event=E.ONBOARDING_STEP, user=other).props)

    def test_ai_doubt_flag_stored_and_counted(self):
        self.client.force_authenticate(self.user)
        self.post({"step": "quick_win", "action": "viewed"})
        self.post({"step": "quick_win", "action": "completed", "ai_doubt": True})
        self.assertTrue(FunnelEvent.objects.get(event=E.ONBOARDING_STEP,
                                                props__action="completed").props.get("ai_doubt"))
        self.assertEqual(onboarding_steps.steps_report(days=7)["quick_win"]["ai_doubt_opened"], 1)
        # only a real boolean true is kept
        other = _user("endpoint3")
        self.client.force_authenticate(other)
        self.post({"step": "quick_win", "action": "completed", "ai_doubt": "yes"})
        self.assertNotIn("ai_doubt", FunnelEvent.objects.get(event=E.ONBOARDING_STEP, user=other).props)

    def test_public_events_endpoint_still_refuses_onboarding_step(self):
        r = APIClient().post("/core/funnel/events/", {"device_id": "device-abcdef1234567890",
                                                      "events": [{"event": "onboarding_step"}]}, format="json")
        self.assertEqual(r.status_code, 400)


class StepsReportTests(TestCase):
    def _flow(self, name, actions):
        u = _user(name)
        for step, action in actions:
            onboarding_steps.record_step(u, step, action, role="student")
        return u

    def test_drop_off_counts(self):
        # a: viewed role+photo, completed role, skipped on photo
        self._flow("a", [("role", "viewed"), ("role", "completed"), ("photo", "viewed"), ("photo", "skipped")])
        # b: viewed role, closed the app (left without action)
        self._flow("b", [("role", "viewed")])
        # c: viewed + completed role
        self._flow("c", [("role", "viewed"), ("role", "completed")])
        rep = onboarding_steps.steps_report(days=7)
        role = next(s for s in rep["steps"] if s["step"] == "role")
        photo = next(s for s in rep["steps"] if s["step"] == "photo")
        self.assertEqual((role["viewed"], role["completed"], role["skipped"], role["left"]), (3, 2, 0, 1))
        self.assertEqual(role["completed_pct_of_viewed"], 66.7)
        self.assertEqual((photo["viewed"], photo["skipped"], photo["left"]), (1, 1, 0))
        self.assertEqual([s["step"] for s in rep["steps"]], list(onboarding_steps.STEPS))

    def test_empty_report_has_no_division_errors(self):
        rep = onboarding_steps.steps_report(days=30)
        self.assertTrue(all(s["viewed"] == 0 and s["completed_pct_of_viewed"] is None for s in rep["steps"]))
        self.assertEqual(rep["quick_win"], {"viewed": 0, "converted": 0, "converted_pct": None, "ai_doubt_opened": 0})

    def test_quick_win_conversion_uses_server_written_first_test_or_doubt(self):
        took = self._flow("took", [("quick_win", "viewed")])
        self._flow("didnt", [("quick_win", "viewed")])
        funnel.record_once_per_user(E.FIRST_TEST, took)
        rep = onboarding_steps.steps_report(days=7)
        self.assertEqual(rep["quick_win"]["viewed"], 2)
        self.assertEqual(rep["quick_win"]["converted"], 1)
        self.assertEqual(rep["quick_win"]["converted_pct"], 50.0)

    def test_first_test_long_before_quick_win_is_not_counted(self):
        u = self._flow("old", [("quick_win", "viewed")])
        funnel.record(E.FIRST_TEST, user=u, dedupe_key=f"first_test:{u.pk}",
                      created_at=timezone.now() - timedelta(days=3))
        self.assertEqual(onboarding_steps.steps_report(days=30)["quick_win"]["converted"], 0)


class ReportEndpointTests(TestCase):
    def test_staff_json_report_carries_onboarding_steps_and_dashboard_renders(self):
        staff = User.objects.create_user(username="boss", password="Str0ng-pass-123!", email="boss@example.com")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save()
        c = APIClient()
        c.force_authenticate(staff)
        r = c.get("/core/analytics/funnel/?days=7")
        self.assertEqual(r.status_code, 200)
        self.assertIn("onboarding_steps", r.json())
        self.assertEqual(len(r.json()["onboarding_steps"]["steps"]), len(onboarding_steps.STEPS))
        web = self.client
        web.force_login(staff)
        page = web.get("/core/analytics/funnel/dashboard/?days=7")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"First-session onboarding", page.content)

    def test_normal_user_cannot_read_report(self):
        c = APIClient()
        c.force_authenticate(_user("plain"))
        self.assertEqual(c.get("/core/analytics/funnel/").status_code, 403)
