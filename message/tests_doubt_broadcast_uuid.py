# message/tests_doubt_broadcast_uuid.py
"""
T0.2 — doubt broadcast payload JSON-safe hona chahiye (UUID -> str).

Run:  python manage.py test message.tests_doubt_broadcast_uuid

Redis ki zaroorat nahi: channels_redis jis msgpack se serialize karta hai use
seedha yahan call karke "can not serialize 'UUID' object" regression pakadte hain.
"""
import json
import uuid

from common.ws_payload import json_safe

from .tests_doubts import DoubtBase


def _msgpack_roundtrip(obj):
    import msgpack  # channels_redis ka hi dependency
    return msgpack.unpackb(msgpack.packb(obj, use_bin_type=True), raw=False)


class JsonSafeHelperTests(DoubtBase):
    def test_uuid_becomes_string_and_msgpack_accepts_it(self):
        u = uuid.uuid4()
        out = json_safe({"id": u, "nested": [{"fk": u}]})
        self.assertEqual(out, {"id": str(u), "nested": [{"fk": str(u)}]})
        _msgpack_roundtrip(out)  # raw UUID hota to yahan TypeError aata


class DoubtCreateBroadcastTests(DoubtBase):
    def test_doubt_created_broadcast_is_json_safe_with_string_id(self):
        r = self.ask(self.alice, text="What is inertia?")
        self.assertEqual(r.status_code, 201, r.data)

        events = [(g, p) for g, p in self.sent if p.get("type") == "doubt_broadcast"]
        self.assertEqual(len(events), 1, self.sent)
        group_name, payload = events[0]

        self.assertEqual(group_name, f"chat_{self.group.conversation_id}")
        self.assertEqual(payload["event"], "doubt_created")
        self.assertEqual(payload["group_id"], str(self.group.id))
        self.assertIsInstance(payload["doubt"]["id"], str)
        self.assertEqual(payload["doubt"]["id"], str(r.data["id"]))

        json.dumps(payload)            # consumer ka `json.dumps(event['doubt'])`
        _msgpack_roundtrip(payload)    # channels_redis ka msgpack
