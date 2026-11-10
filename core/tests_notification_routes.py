"""N2 - route map tests (run: python manage.py test core.tests_notification_routes). Not run by the author."""
import json
import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from core import notification_routes as nr
from core.models import Notification
from core.services import PUSH_DATA_KEYS

NT = Notification.NotifType
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


class RouteTableTests(SimpleTestCase):
    def test_every_notiftype_has_an_explicit_spec(self):
        self.assertEqual(set(NT.values), set(nr.SPECS), "NotifType and SPECS differ - add/remove the spec")

    def test_every_route_is_known_and_non_empty(self):
        for ntype in NT.values:
            res = nr.route_for(ntype, {})
            self.assertTrue(res.route, ntype)
            self.assertIn(res.route, nr.ROUTES, ntype)
        for spec in list(nr.SPECS.values()) + [nr.DEFAULT_SPEC]:
            self.assertIn(spec.fallback, nr.ROUTES)
            for rule in spec.rules:
                self.assertIn(rule.route, nr.ROUTES)

    def test_expected_routes(self):
        table = [
            (NT.CHAT_MESSAGE, {"conversation_id": "9"}, "chat"),
            (NT.POST_COMMENTED, {"post_id": "3", "comment_id": "4"}, "post"),
            (NT.POST_TAG, {"post_id": "3", "collab_invite": "true"}, "coAuthorInvites"),
            (NT.POST_TAG, {"post_id": "3"}, "post"),
            (NT.STORY_MENTION, {"story_id": "5"}, "story"),
            (NT.SUPPORT_REPLY, {"ticket_id": "2"}, "supportTicket"),
            (NT.COPYRIGHT_NOTICE, {"claim_id": "1"}, "copyrightComplaints"),
            (NT.WEEKLY_RECAP_READY, {}, "weeklyRecap"),
            (NT.WITHDRAWAL_PAID, {}, "wallet"),
            (NT.DOUBT_ANSWERED, {"group_id": "1", "conversation_id": "2"}, "doubt"),
            (NT.FEE_DUE_REMINDER, {"student_id": "7"}, "parentChild"),
            (NT.FEE_DUE_REMINDER, {}, "campus"),
            (NT.CAMPUS_SESSION_LIVE, {"live_session_id": "8", "mode": "offline"}, "campus"),
            (NT.CAMPUS_SESSION_LIVE, {"live_session_id": "8"}, "campusLive"),
            (NT.GENERIC, {"kind": "video_processing_failed", "post_id": "6"}, "reel"),
            (NT.REPORT_OUTCOME, {"post_id": "1"}, "notifications"),
        ]
        for ntype, data, route in table:
            self.assertEqual(nr.route_for(ntype, data).route, route, (ntype, data))

    def test_deleted_target_falls_back_to_unavailable(self):
        self.assertEqual(nr.route_for(NT.JOIN_REQUEST_RECEIVED, {}).route, "unavailable")
        self.assertEqual(nr.route_for(NT.JOIN_REQUEST_RECEIVED, {}, classroom_id=4).route, "classroom")
        self.assertEqual(nr.route_for(NT.POST_LIKED, {}).route, "unavailable")

    def test_unknown_type_never_dead(self):
        self.assertEqual(nr.route_for("brand_new_type", {"post_id": "1"}).route, "post")
        self.assertEqual(nr.route_for("brand_new_type", {}).route, "notifications")

    def test_odd_data_never_raises(self):
        for bad in (None, "x", 5, [], {"post_id": None}):
            nr.route_for(NT.POST_LIKED, bad)

    def test_keys_used_by_rules_reach_the_push(self):
        shipped = set(PUSH_DATA_KEYS) | {"classroom_id", "session_id"}
        needed = {k for s in nr.SPECS.values() for r in s.rules for k in (*r.requires, *r.truthy, *(k for k, _ in r.eq))}
        needed |= {a for _, args in nr.ROUTES.values() for a in args[:1]}
        self.assertEqual(sorted(needed - shipped), [])

    def test_push_payload_has_route(self):
        from core.services import build_push_payload

        n = Notification(id=1, recipient_id=1, notif_type=NT.POST_LIKED, title="t", data={"post_id": "3"})
        d = build_push_payload(n)["data"]
        self.assertEqual((d["route"], d["target_type"], d["target_id"]), ("post", "post", "3"))


class DartContractTests(SimpleTestCase):
    def test_generated_files_are_current(self):
        script = REPO / "tool" / "gen_notification_routes.py"
        if not script.exists():
            self.skipTest("Flutter side not in this checkout")
        r = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_cases_json_matches_python(self):
        cases = json.loads((HERE / "notification_route_cases.json").read_text())
        for c in cases:
            got = nr.route_for(c["type"], c["data"], in_app=c["in_app"])
            self.assertEqual(got.route, c["route"], c)

    def test_dart_enum_matches_routes(self):
        f = REPO / "lib" / "lib" / "notifications" / "notification_router.dart"
        if not f.exists():
            self.skipTest("notification_router.dart not written yet (N2 partial)")
        m = re.search(r"enum PushDestination \{(.*?)\}", f.read_text(), re.S)
        names = set(re.findall(r"[A-Za-z]+", re.sub(r"//.*", "", m.group(1))))
        self.assertEqual(names, set(nr.ROUTES))
