# message/tests_sticky_notes.py
"""
Study-room collaborative sticky notes — logic (LWW), persisted ops, REST GET
aur WebSocket flow (ack + broadcast) ke tests.

    python manage.py test message.tests_sticky_notes
"""
import uuid
from unittest import mock

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

from . import sticky_notes as sn
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMember,
    StudyRoomNote,
    StudyRoomState,
)
from .routing import websocket_urlpatterns

User = get_user_model()
import time

# Real clock se 1 ghanta pehle: REST/WS paths server ka asli `now` use karte hain, aur
# future ts clamp hote hain — isliye test timestamps ko past me rakhna zaroori hai.
NOW = int(time.time() * 1000) - 3_600_000


def _new_id():
    return str(uuid.uuid4())


def _make_room(group=False):
    a = User.objects.create_user(username=f"a{_new_id()[:6]}", password="x")
    b = User.objects.create_user(username=f"b{_new_id()[:6]}", password="x")
    conv = Conversation.objects.create(type=ConversationType.GROUP if group else ConversationType.PRIVATE)
    ConversationParticipant.objects.create(conversation=conv, user=a)
    ConversationParticipant.objects.create(conversation=conv, user=b)
    return conv, a, b


def _add(conv, user, note_id=None, ts=NOW, **fields):
    note = {"id": note_id or _new_id(), "pageId": "page_1", "x": 10, "y": 20, "text": "hi"}
    note.update(fields)
    return sn.apply_note_op(conv.id, user, "note_add", {"opId": "op-add", "ts": ts, "note": note}, now_ms=NOW + 10_000)


# ----------------------------------------------------------------------
# Pure logic
# ----------------------------------------------------------------------
class PureLogicTests(SimpleTestCase):
    def test_plan_lww_applies_newer_or_equal_rejects_older(self):
        field_ts = {"pos": 100, "text": 200}
        applied, rejected = sn.plan_lww(
            field_ts,
            {"pos": ({"x": 1, "y": 2}, 100), "text": ({"text": "a"}, 199), "color": ({"color": 1}, 5)},
        )
        self.assertEqual(set(applied), {"pos", "color"})  # tie (100>=100) + unseen group apply
        self.assertEqual(rejected, ["text"])

    def test_clean_ts_clamps_future_and_defaults_missing(self):
        self.assertEqual(sn.clean_ts(NOW + 10 ** 9, NOW), NOW + sn.FUTURE_SKEW_MS)
        self.assertEqual(sn.clean_ts(None, NOW), NOW)
        self.assertEqual(sn.clean_ts("x", NOW), NOW)
        self.assertEqual(sn.clean_ts(NOW - 50, NOW), NOW - 50)

    def test_validators(self):
        self.assertEqual(sn.clean_width(1), sn.MIN_WIDTH)
        self.assertEqual(sn.clean_width(10 ** 6), sn.MAX_WIDTH)
        for bad in (True, "5", None, float("nan"), float("inf")):
            with self.assertRaises(sn.NoteValidationError):
                sn.clean_x(bad)
        with self.assertRaises(sn.NoteValidationError):
            sn.clean_color(True)
        with self.assertRaises(sn.NoteValidationError):
            sn.clean_color(-1)
        with self.assertRaises(sn.NoteValidationError):
            sn.clean_color(0x1FFFFFFFF)
        self.assertEqual(sn.clean_color(0xFFFFFFFF), 0xFFFFFFFF)
        self.assertEqual(sn.clean_text("a\x00b"), "ab")  # NUL Postgres me crash karta
        with self.assertRaises(sn.NoteValidationError):
            sn.clean_text("x" * (sn.MAX_TEXT_LENGTH + 1))

    def test_build_groups(self):
        g = sn.build_groups_from_payload("note_move", {"x": 1, "y": 2, "width": 200, "height": 100, "front": True}, 5)
        self.assertEqual(set(g), {"pos", "size", "z"})
        with self.assertRaises(sn.NoteValidationError):
            sn.build_groups_from_payload("note_move", {"x": 1}, 5)
        with self.assertRaises(sn.NoteValidationError):
            sn.build_groups_from_payload("note_edit", {}, 5)

    def test_drag_payload(self):
        nid = _new_id()
        self.assertEqual(sn.clean_drag_payload({"noteId": nid, "x": 1, "y": 2})["noteId"], nid)
        self.assertIsNone(sn.clean_drag_payload({"noteId": "nope", "x": 1, "y": 2}))
        self.assertIsNone(sn.clean_drag_payload({"noteId": nid, "x": "1", "y": 2}))


# ----------------------------------------------------------------------
# Persisted ops (DB)
# ----------------------------------------------------------------------
class NoteOpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.conv, self.a, self.b = _make_room()

    def _op(self, user, action, **payload):
        payload.setdefault("opId", "op")
        return sn.apply_note_op(self.conv.id, user, action, payload, now_ms=NOW + 10_000)

    def test_add_persists_and_broadcasts(self):
        res = _add(self.conv, self.a)
        self.assertEqual(res.ack["status"], "applied")
        self.assertEqual(res.broadcasts[0][0], "note_upsert")
        note = StudyRoomNote.objects.get(id=res.ack["noteId"])
        self.assertEqual((note.x, note.y, note.text, note.created_by_id), (10.0, 20.0, "hi", self.a.id))
        self.assertEqual(note.z_index, 1)
        self.assertEqual(res.ack["note"]["zIndex"], 1)

    def test_add_is_idempotent_and_second_note_goes_on_top(self):
        nid = _new_id()
        _add(self.conv, self.a, nid)
        again = _add(self.conv, self.a, nid)
        self.assertEqual(again.ack["status"], "applied")
        self.assertEqual(again.broadcasts, [])  # retry pe duplicate broadcast nahi
        self.assertEqual(StudyRoomNote.objects.filter(room=self.conv).count(), 1)
        second = _add(self.conv, self.b)
        self.assertEqual(second.ack["note"]["zIndex"], 2)

    def test_lww_stale_write_is_rejected_with_authoritative_note(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        newer = self._op(self.b, "note_edit", noteId=nid, text="B (newer)", ts=NOW + 500)
        self.assertEqual(newer.ack["status"], "applied")
        stale = self._op(self.a, "note_edit", noteId=nid, text="A (older)", ts=NOW + 100)
        self.assertEqual(stale.ack["status"], "stale")
        self.assertEqual(stale.ack["rejected"], ["text"])
        self.assertEqual(stale.ack["note"]["text"], "B (newer)")
        self.assertEqual(stale.broadcasts, [])
        self.assertEqual(StudyRoomNote.objects.get(id=nid).text, "B (newer)")

    def test_out_of_order_delivery_of_same_clients_moves(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        self._op(self.a, "note_move", noteId=nid, x=300, y=300, ts=NOW + 300)   # pehle pahunchi
        late = self._op(self.a, "note_move", noteId=nid, x=200, y=200, ts=NOW + 200)  # purani, late
        self.assertEqual(late.ack["status"], "stale")
        note = StudyRoomNote.objects.get(id=nid)
        self.assertEqual((note.x, note.y), (300.0, 300.0))

    def test_different_field_groups_do_not_clobber_each_other(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        self._op(self.a, "note_move", noteId=nid, x=50, y=60, ts=NOW + 900)   # A moves
        edit = self._op(self.b, "note_edit", noteId=nid, text="edited", ts=NOW + 800)  # B edits (older ts, other group)
        self.assertEqual(edit.ack["status"], "applied")
        note = StudyRoomNote.objects.get(id=nid)
        self.assertEqual((note.x, note.y, note.text), (50.0, 60.0, "edited"))

    def test_partial_when_one_group_stale(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        self._op(self.b, "note_edit", noteId=nid, text="T", ts=NOW + 500)
        res = self._op(self.a, "note_edit", noteId=nid, text="old", color=0xFF112233, ts=NOW + 100)
        self.assertEqual(res.ack["status"], "partial")
        self.assertEqual(res.ack["rejected"], ["text"])
        note = StudyRoomNote.objects.get(id=nid)
        self.assertEqual((note.text, note.color), ("T", 0xFF112233))

    def test_future_timestamp_is_clamped_so_note_is_not_frozen(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        self._op(self.a, "note_edit", noteId=nid, text="from the future", ts=NOW + 10 ** 12)
        stored = StudyRoomNote.objects.get(id=nid).field_ts["text"]
        self.assertLessEqual(stored, NOW + 10_000 + sn.FUTURE_SKEW_MS)
        ok = self._op(self.b, "note_edit", noteId=nid, text="normal", ts=NOW + 10_000 + sn.FUTURE_SKEW_MS + 1)
        self.assertEqual(ok.ack["status"], "applied")

    def test_front_bumps_z_only_when_not_already_top(self):
        first = _add(self.conv, self.a).ack["noteId"]
        second = _add(self.conv, self.a).ack["noteId"]
        res = self._op(self.b, "note_front", noteId=first, ts=NOW + 50)
        self.assertEqual(res.ack["note"]["zIndex"], 3)
        self.assertGreater(StudyRoomNote.objects.get(id=first).z_index, StudyRoomNote.objects.get(id=second).z_index)
        again = self._op(self.b, "note_front", noteId=first, ts=NOW + 60)
        self.assertEqual(again.ack["note"]["zIndex"], 3)  # already top — z badhta nahi

    def test_move_with_front_flag_raises_note(self):
        first = _add(self.conv, self.a).ack["noteId"]
        _add(self.conv, self.a)
        res = self._op(self.a, "note_move", noteId=first, x=1, y=1, front=True, ts=NOW + 70)
        self.assertEqual(res.ack["note"]["zIndex"], 3)

    def test_delete_creates_tombstone_and_blocks_resurrection(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        res = self._op(self.a, "note_delete", noteId=nid)
        self.assertEqual(res.ack["status"], "deleted")
        self.assertEqual(res.broadcasts[0][0], "note_removed")
        self.assertFalse(StudyRoomNote.objects.filter(id=nid).exists())
        self.assertTrue(StudyRoomNote.all_objects.filter(id=nid, is_deleted=True).exists())
        # late edit + retried add dono ko 'deleted' milta hai
        self.assertEqual(self._op(self.b, "note_edit", noteId=nid, text="x", ts=NOW + 5).ack["status"], "deleted")
        self.assertEqual(_add(self.conv, self.a, nid).ack["status"], "deleted")
        # delete idempotent
        self.assertEqual(self._op(self.a, "note_delete", noteId=nid).ack["status"], "deleted")

    def test_non_member_is_rejected(self):
        outsider = User.objects.create_user(username="outsider", password="x")
        res = _add(self.conv, outsider)
        self.assertEqual((res.ack["status"], res.ack["reason"]), ("rejected", "not_member"))
        self.assertEqual(StudyRoomNote.objects.count(), 0)

    def test_left_member_is_rejected(self):
        from django.utils import timezone
        ConversationParticipant.objects.filter(conversation=self.conv, user=self.b).update(left_at=timezone.now())
        self.assertEqual(_add(self.conv, self.b).ack["reason"], "not_member")

    def test_note_id_from_another_room_is_forbidden(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        other, c, _ = _make_room()
        res = _add(other, c, nid)
        self.assertEqual((res.ack["status"], res.ack["reason"]), ("rejected", "forbidden"))
        self.assertEqual(self._op(c, "note_edit", noteId=nid, text="x").ack["status"], "rejected")

    def test_limit_and_validation(self):
        with mock.patch.object(sn, "MAX_NOTES_PER_ROOM", 1):
            _add(self.conv, self.a)
            self.assertEqual(_add(self.conv, self.a).ack["reason"], "limit_reached")
        self.assertEqual(_add(self.conv, self.a, x="nope").ack["reason"], "x_invalid")
        self.assertEqual(_add(self.conv, self.a, note_id="not-a-uuid").ack["reason"], "id_invalid")
        self.assertEqual(_add(self.conv, self.a, text="z" * 5000).ack["reason"], "text_too_long")

    def test_size_is_clamped(self):
        note = _add(self.conv, self.a, width=1, height=99999).ack["note"]
        self.assertEqual((note["width"], note["height"]), (sn.MIN_WIDTH, sn.MAX_HEIGHT))

    def test_clear_notes_for_page_only_touches_that_page(self):
        n1 = _add(self.conv, self.a, pageId="p1").ack["noteId"]
        n2 = _add(self.conv, self.a, pageId="p2").ack["noteId"]
        self.assertEqual(sn.clear_notes_for_page(self.conv.id, "p1", self.a), 1)
        self.assertFalse(StudyRoomNote.objects.filter(id=n1).exists())
        self.assertTrue(StudyRoomNote.objects.filter(id=n2).exists())
        outsider = User.objects.create_user(username="out2", password="x")
        self.assertEqual(sn.clear_notes_for_page(self.conv.id, "p2", outsider), 0)

    def test_purge_room_notes_removes_tombstones_too(self):
        nid = _add(self.conv, self.a).ack["noteId"]
        self._op(self.a, "note_delete", noteId=nid)
        _add(self.conv, self.a)
        self.assertEqual(sn.purge_room_notes(self.conv.id), 2)
        self.assertEqual(StudyRoomNote.all_objects.filter(room=self.conv).count(), 0)


class DeletePolicyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.conv, self.creator, self.other = _make_room(group=True)
        self.admin = User.objects.create_user(username="adm", password="x")
        ConversationParticipant.objects.create(conversation=self.conv, user=self.admin)
        group = Group.objects.create(conversation=self.conv, name="G", created_by=self.creator)
        GroupMember.objects.create(group=group, user=self.creator, role=GroupMember.Role.MEMBER)
        GroupMember.objects.create(group=group, user=self.other, role=GroupMember.Role.MEMBER)
        GroupMember.objects.create(group=group, user=self.admin, role=GroupMember.Role.ADMIN)
        self.nid = _add(self.conv, self.creator).ack["noteId"]

    def _delete(self, user):
        return sn.apply_note_op(self.conv.id, user, "note_delete", {"opId": "d", "noteId": self.nid})

    def test_plain_member_cannot_delete_others_note_but_can_edit_it(self):
        res = self._delete(self.other)
        self.assertEqual((res.ack["status"], res.ack["reason"]), ("rejected", "forbidden"))
        self.assertTrue(StudyRoomNote.objects.filter(id=self.nid).exists())
        edit = sn.apply_note_op(self.conv.id, self.other, "note_edit", {"noteId": self.nid, "text": "collab", "ts": NOW})
        self.assertEqual(edit.ack["status"], "applied")

    def test_creator_and_admin_can_delete(self):
        self.assertEqual(self._delete(self.admin).ack["status"], "deleted")
        nid2 = _add(self.conv, self.creator).ack["noteId"]
        res = sn.apply_note_op(self.conv.id, self.creator, "note_delete", {"noteId": nid2})
        self.assertEqual(res.ack["status"], "deleted")

    def test_group_study_room_permission_is_enforced(self):
        Group.objects.filter(conversation=self.conv).update(study_room_permission=Group.PermissionLevel.ADMINS_ONLY)
        cache.clear()
        self.assertEqual(_add(self.conv, self.other).ack["reason"], "forbidden")
        self.assertEqual(_add(self.conv, self.admin).ack["status"], "applied")


# ----------------------------------------------------------------------
# REST
# ----------------------------------------------------------------------
class NotesRestTests(TestCase):
    def setUp(self):
        cache.clear()
        self.conv, self.a, self.b = _make_room()
        self.url = f"/message/study-room/{self.conv.id}/notes/"

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_reload_returns_persisted_notes_in_z_order(self):
        n1 = _add(self.conv, self.a, text="one").ack["noteId"]
        n2 = _add(self.conv, self.a, text="two").ack["noteId"]
        sn.apply_note_op(self.conv.id, self.a, "note_front", {"noteId": n1, "ts": NOW + 1})
        res = self._client(self.b).get(self.url)  # rejoin karne wala doosra participant
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual([n["id"] for n in body["notes"]], [n2, n1])
        self.assertEqual(body["count"], 2)
        self.assertIn("server_time_ms", body)
        self.assertEqual(body["notes"][1]["text"], "one")

    def test_empty_room_and_page_filter_and_deleted_hidden(self):
        self.assertEqual(self._client(self.a).get(self.url).json()["notes"], [])
        keep = _add(self.conv, self.a, pageId="p2").ack["noteId"]
        gone = _add(self.conv, self.a, pageId="p1").ack["noteId"]
        sn.apply_note_op(self.conv.id, self.a, "note_delete", {"noteId": gone})
        self.assertEqual([n["id"] for n in self._client(self.a).get(self.url).json()["notes"]], [keep])
        self.assertEqual(self._client(self.a).get(self.url + "?page_id=p1").json()["notes"], [])

    def test_non_member_gets_404_and_anonymous_is_rejected(self):
        outsider = User.objects.create_user(username="o3", password="x")
        self.assertEqual(self._client(outsider).get(self.url).status_code, 404)
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))

    def test_legacy_snapshot_notes_are_imported_once(self):
        legacy_uuid = _new_id()
        StudyRoomState.objects.create(conversation=self.conv, state={"pages": [{
            "id": "page_1", "strokes": [], "stickyNotes": [
                {"id": legacy_uuid, "userId": str(self.a.id), "text": "old note", "dx": 33.5, "dy": 44.5, "color": 0xFFFFF59D},
                {"id": "legacy-non-uuid", "userId": "999999", "text": "orphan creator", "dx": 1, "dy": 2, "color": 4293848814},
            ]}]})
        body = self._client(self.a).get(self.url).json()
        self.assertEqual(body["count"], 2)
        first = next(n for n in body["notes"] if n["text"] == "old note")
        self.assertEqual((first["id"], first["x"], first["y"]), (legacy_uuid, 33.5, 44.5))
        # snapshot se hat gaye => dobara import nahi
        self.assertNotIn("stickyNotes", StudyRoomState.objects.get(conversation=self.conv).state["pages"][0])
        self.assertEqual(self._client(self.a).get(self.url).json()["count"], 2)

    def test_ending_session_purges_notes(self):
        _add(self.conv, self.a)
        res = self._client(self.a).delete(f"/message/study-room/{self.conv.id}/state/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._client(self.b).get(self.url).json()["notes"], [])


# ----------------------------------------------------------------------
# WebSocket end-to-end
# ----------------------------------------------------------------------
class NoteWebsocketTests(TransactionTestCase):
    def setUp(self):
        cache.clear()

    def _comm(self, conv, user):
        comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/chat/{conv.id}/")
        comm.scope["user"] = user
        return comm

    async def _drain(self, comm):
        while not await comm.receive_nothing(timeout=0.2):
            await comm.receive_json_from()

    async def _pair(self):
        conv, a, b = await sync_to_async(_make_room)()
        ca, cb = self._comm(conv, a), self._comm(conv, b)
        ok_a, _ = await ca.connect()
        ok_b, _ = await cb.connect()
        self.assertTrue(ok_a and ok_b)
        await self._drain(ca)
        await self._drain(cb)
        return conv, a, b, ca, cb

    @staticmethod
    def _ev(action, data):
        return {"type": "study_room_event", "action": action, "data": data}

    async def test_add_edit_move_delete_flow(self):
        conv, a, b, ca, cb = await self._pair()
        nid = _new_id()

        # --- add: A ko ack, B ko upsert, A ko echo nahi
        await ca.send_json_to(self._ev("note_add", {"opId": "1", "ts": NOW, "note": {
            "id": nid, "pageId": "page_1", "x": 5, "y": 6, "text": "hello"}}))
        ack = await ca.receive_json_from(timeout=2)
        self.assertEqual((ack["action"], ack["data"]["status"], ack["data"]["opId"]), ("note_ack", "applied", "1"))
        got = await cb.receive_json_from(timeout=2)
        self.assertEqual((got["action"], got["data"]["source"]), ("note_upsert", "note_add"))
        self.assertEqual(got["data"]["note"]["text"], "hello")
        self.assertTrue(await ca.receive_nothing(timeout=0.3))

        # --- B edit (text) -> A ko upsert
        await cb.send_json_to(self._ev("note_edit", {"opId": "2", "ts": NOW + 100, "noteId": nid, "text": "from B"}))
        self.assertEqual((await cb.receive_json_from(timeout=2))["data"]["status"], "applied")
        up = await ca.receive_json_from(timeout=2)
        self.assertEqual(up["data"]["note"]["text"], "from B")

        # --- A ka purana (stale) edit reject; B ko kuch broadcast nahi hota
        await ca.send_json_to(self._ev("note_edit", {"opId": "3", "ts": NOW + 50, "noteId": nid, "text": "stale"}))
        stale = await ca.receive_json_from(timeout=2)
        self.assertEqual((stale["data"]["status"], stale["data"]["note"]["text"]), ("stale", "from B"))
        self.assertTrue(await cb.receive_nothing(timeout=0.3))

        # --- live drag preview: relay only, DB me nahi
        await ca.send_json_to(self._ev("note_drag", {"noteId": nid, "x": 70, "y": 80}))
        drag = await cb.receive_json_from(timeout=2)
        self.assertEqual((drag["action"], drag["data"]["x"], drag["data"]["userId"]), ("note_drag", 70.0, str(a.id)))
        self.assertTrue(await ca.receive_nothing(timeout=0.3))
        self.assertEqual((await sync_to_async(StudyRoomNote.objects.get)(id=nid)).x, 5.0)

        # --- move commit
        await ca.send_json_to(self._ev("note_move", {"opId": "4", "ts": NOW + 200, "noteId": nid, "x": 70, "y": 80, "front": True}))
        await ca.receive_json_from(timeout=2)
        moved = await cb.receive_json_from(timeout=2)
        self.assertEqual((moved["data"]["note"]["x"], moved["data"]["note"]["y"]), (70.0, 80.0))

        # --- delete
        await cb.send_json_to(self._ev("note_delete", {"opId": "5", "noteId": nid}))
        self.assertEqual((await cb.receive_json_from(timeout=2))["data"]["status"], "deleted")
        removed = await ca.receive_json_from(timeout=2)
        self.assertEqual((removed["action"], removed["data"]["noteId"]), ("note_removed", nid))

        await ca.disconnect()
        await cb.disconnect()

    async def test_invalid_op_gets_rejected_ack_not_socket_error(self):
        conv, a, b, ca, cb = await self._pair()
        await ca.send_json_to(self._ev("note_move", {"opId": "9", "noteId": "garbage", "x": 1, "y": 1}))
        ack = await ca.receive_json_from(timeout=2)
        self.assertEqual((ack["action"], ack["data"]["status"], ack["data"]["reason"]), ("note_ack", "rejected", "noteId_invalid"))
        self.assertTrue(await cb.receive_nothing(timeout=0.3))
        await ca.disconnect()
        await cb.disconnect()

    async def test_clear_board_relays_and_deletes_page_notes(self):
        conv, a, b, ca, cb = await self._pair()
        nid = _new_id()
        await ca.send_json_to(self._ev("note_add", {"opId": "1", "ts": NOW, "note": {"id": nid, "pageId": "page_1"}}))
        await ca.receive_json_from(timeout=2)
        await cb.receive_json_from(timeout=2)

        await ca.send_json_to(self._ev("clear_board", {"pageId": "page_1"}))
        relayed = await cb.receive_json_from(timeout=2)  # purana relay behaviour intact
        self.assertEqual(relayed["action"], "clear_board")
        await ca.receive_nothing(timeout=0.5)
        self.assertEqual(await sync_to_async(StudyRoomNote.objects.filter(room=conv).count)(), 0)
        await ca.disconnect()
        await cb.disconnect()

    async def test_rate_limit_returns_rejected_ack(self):
        conv, a, b, ca, cb = await self._pair()
        with mock.patch.dict("message.throttles.WSStudyRoomNoteRateLimiter.LIMITS", {"ops": 1}):
            await ca.send_json_to(self._ev("note_add", {"opId": "1", "ts": NOW, "note": {"id": _new_id()}}))
            first = await ca.receive_json_from(timeout=2)
            await ca.send_json_to(self._ev("note_add", {"opId": "2", "ts": NOW, "note": {"id": _new_id()}}))
            second = await ca.receive_json_from(timeout=2)
        self.assertEqual(first["data"]["status"], "applied")
        self.assertEqual((second["data"]["status"], second["data"]["reason"]), ("rejected", "rate_limited"))
        await ca.disconnect()
        await cb.disconnect()


class ConcurrencyTests(TransactionTestCase):
    """Advisory lock: parallel adds me z_index unique + limit strict rehna chahiye."""

    def test_parallel_adds_get_unique_z_and_respect_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection

        cache.clear()
        conv, a, b = _make_room()

        def worker(i):
            try:
                user = a if i % 2 else b
                return _add(conv, user, text=f"n{i}").ack
            finally:
                connection.close()

        with mock.patch.object(sn, "MAX_NOTES_PER_ROOM", 6):
            with ThreadPoolExecutor(max_workers=10) as pool:
                acks = list(pool.map(worker, range(10)))

        applied = [x for x in acks if x["status"] == "applied"]
        self.assertEqual(len(applied), 6)
        self.assertEqual(sum(1 for x in acks if x.get("reason") == "limit_reached"), 4)
        zs = sorted(StudyRoomNote.objects.filter(room=conv).values_list("z_index", flat=True))
        self.assertEqual(zs, [1, 2, 3, 4, 5, 6])
