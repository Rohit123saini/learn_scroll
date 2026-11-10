# message/tests_doubts.py
"""
Doubt Queue — T1.1 photo attachment, T1.2 DOUBT_ANSWERED notification/push,
T1.3 multi-answer + best answer + teacher-verified, T1.4 list-query
optimisation + throttle wiring.

Run:  python manage.py test message.tests_doubts

Nothing here talks to FCM, Redis or Gemini: push senders are patched and the
WebSocket fan-out is captured with a fake channel layer.
"""
import io
import shutil
import tempfile
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import Notification

from . import push_utils
from .models import DoubtAnswer, DoubtQuestion, DoubtUpvote
from .services import (
    MAX_DOUBT_ANSWERS_PER_USER,
    add_doubt_answer,
    answer_doubt_question,
    create_group,
)

User = get_user_model()


def _png(size=(40, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def _noisy_png(size=(64, 64)):
    """Incompressible PNG (random pixels) — reliably bigger than a tiny cap."""
    import os
    from PIL import Image
    buf = io.BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buf, "PNG")
    return buf.getvalue()


def _jpeg_with_exif(size=(3000, 1000)):
    """A big JPEG carrying EXIF (camera make) — must come back small + clean."""
    from PIL import Image
    img = Image.new("RGB", size, (10, 120, 200))
    exif = img.getexif()
    exif[0x010F] = "SecretCameraMake"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


class DoubtBase(TestCase):
    def setUp(self):
        cache.clear()  # group-role cache + throttle counters
        self._media = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._media, True)

        self.teacher = User.objects.create_user(username="teacher", password="x")
        self.alice = User.objects.create_user(username="alice", password="x")   # asker
        self.bob = User.objects.create_user(username="bob", password="x")       # classmate
        self.carol = User.objects.create_user(username="carol", password="x")   # NOT a member
        self.group = create_group(
            created_by=self.teacher, name="Physics 12",
            member_ids=[self.alice.id, self.bob.id],
        )
        self.base = f"/message/groups/{self.group.id}/doubts/"

        # Capture WebSocket fan-out instead of hitting a channel layer.
        self.sent = []
        layer = mock.Mock()
        layer.group_send = lambda group, payload: self.sent.append((group, payload))
        p1 = mock.patch("message.views.groups.get_channel_layer", return_value=layer)
        p2 = mock.patch("message.views.groups.async_to_sync", side_effect=lambda fn: fn)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    # helpers --------------------------------------------------------
    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def ask(self, user, **payload):
        return self.api(user).post(self.base, payload, format="json")

    def make_doubt(self, author=None, **kw):
        return DoubtQuestion.objects.create(
            group=self.group, conversation_id=self.group.conversation_id,
            author=author or self.alice, text=kw.pop("text", "What is inertia?"), **kw,
        )

    def answers_url(self, doubt, answer=None, suffix=""):
        url = f"{self.base}{doubt.id}/answers/"
        if answer is not None:
            url += f"{answer.id}/"
        return url + suffix

    def answer(self, user, doubt, text="Because physics."):
        return self.api(user).post(self.answers_url(doubt), {"text": text}, format="json")


# ======================================================================
# T1.1 — photo attachment
# ======================================================================
class DoubtPhotoTests(DoubtBase):
    def test_create_with_photo_multipart(self):
        r = self.api(self.alice).post(
            self.base,
            {"text": "See the diagram", "image": SimpleUploadedFile("d.png", _png(), "image/png")},
            format="multipart",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["image_url"].startswith("http"))
        self.assertTrue(r.data["image_url"].endswith(".png"))
        d = DoubtQuestion.objects.get(pk=r.data["id"])
        self.assertTrue(d.image)
        self.assertIn("doubts/", d.image.name)

    def test_photo_only_doubt_is_valid(self):
        r = self.api(self.alice).post(
            self.base, {"image": SimpleUploadedFile("d.png", _png(), "image/png")}, format="multipart",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["text"], "")
        self.assertTrue(r.data["image_url"])

    def test_text_only_json_still_works_and_has_no_image(self):
        r = self.ask(self.alice, text="Plain doubt")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(r.data["image_url"])

    def test_empty_doubt_rejected(self):
        self.assertEqual(self.ask(self.alice, text="   ").status_code, 400)

    def test_non_image_rejected(self):
        r = self.api(self.alice).post(
            self.base, {"image": SimpleUploadedFile("x.png", b"%PDF-1.7 hello", "image/png")}, format="multipart",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("image", r.data)

    def test_forged_image_bytes_rejected(self):
        # right magic bytes, garbage body -> real decode fails
        fake = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        r = self.api(self.alice).post(
            self.base, {"image": SimpleUploadedFile("x.jpg", fake, "image/jpeg")}, format="multipart",
        )
        self.assertEqual(r.status_code, 400)

    @override_settings(DOUBT_IMAGE_MAX_BYTES=100)
    def test_oversize_rejected(self):
        r = self.api(self.alice).post(
            self.base, {"image": SimpleUploadedFile("d.png", _noisy_png(), "image/png")}, format="multipart",
        )
        self.assertEqual(r.status_code, 400)

    def test_exif_stripped_and_downscaled(self):
        from PIL import Image
        r = self.api(self.alice).post(
            self.base, {"image": SimpleUploadedFile("c.jpg", _jpeg_with_exif(), "image/jpeg")}, format="multipart",
        )
        self.assertEqual(r.status_code, 201, r.data)
        d = DoubtQuestion.objects.get(pk=r.data["id"])
        with Image.open(d.image.path) as stored:
            self.assertLessEqual(max(stored.size), 2048)
            self.assertNotIn(0x010F, dict(stored.getexif()))

    def test_non_member_cannot_post(self):
        r = self.api(self.carol).post(self.base, {"text": "hi"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_photo_upload_is_throttled_tighter(self):
        from rest_framework.throttling import SimpleRateThrottle
        rates = dict(SimpleRateThrottle.THROTTLE_RATES)
        rates["doubt_create_image"] = "2/hour"
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            codes = []
            for _ in range(3):
                r = self.api(self.alice).post(
                    self.base, {"image": SimpleUploadedFile("d.png", _png(), "image/png")}, format="multipart",
                )
                codes.append(r.status_code)
        self.assertEqual(codes, [201, 201, 429])

    def test_text_doubts_are_not_hit_by_the_photo_limit(self):
        from rest_framework.throttling import SimpleRateThrottle
        rates = dict(SimpleRateThrottle.THROTTLE_RATES)
        rates["doubt_create_image"] = "1/hour"
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            codes = [self.ask(self.alice, text=f"q{i}").status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 201])


class DoubtAnonymityBroadcastTests(DoubtBase):
    def test_broadcast_never_leaks_anonymous_author(self):
        r = self.ask(self.alice, text="Embarrassing doubt", is_anonymous=True)
        self.assertEqual(r.status_code, 201, r.data)
        # Alice herself sees her name...
        self.assertTrue(r.data["is_mine"])
        self.assertEqual(r.data["author"]["username"], "alice")
        # ...but the fan-out payload (sent to the WHOLE group) must not.
        _, payload = self.sent[-1]
        self.assertEqual(payload["event"], "doubt_created")
        self.assertIsNone(payload["doubt"]["author"])
        self.assertFalse(payload["doubt"]["is_mine"])
        self.assertFalse(payload["doubt"]["upvoted_by_me"])

    def test_revealed_anonymous_author_is_in_broadcast(self):
        d = self.make_doubt(is_anonymous=True)
        r = self.api(self.teacher).post(f"{self.base}{d.id}/reveal/")
        self.assertEqual(r.status_code, 200)
        _, payload = self.sent[-1]
        self.assertEqual(payload["event"], "doubt_revealed")
        self.assertEqual(payload["doubt"]["author"]["username"], "alice")


# ======================================================================
# T1.2 — DOUBT_ANSWERED notification + push
# ======================================================================
class DoubtAnsweredNotificationTests(DoubtBase):
    def _rows(self):
        return Notification.objects.filter(recipient=self.alice, notif_type=Notification.NotifType.DOUBT_ANSWERED)

    def test_teacher_answer_notifies_asker(self):
        d = self.make_doubt()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch("message.push_utils.send_doubt_answered_push") as push:
                r = self.answer(self.teacher, d, "Inertia resists change.")
        self.assertEqual(r.status_code, 201, r.data)
        row = self._rows().get()
        self.assertEqual(row.data["doubt_id"], str(d.id))
        self.assertEqual(row.data["group_id"], str(self.group.id))
        self.assertEqual(row.data["conversation_id"], str(self.group.conversation_id))
        self.assertTrue(row.data["by_teacher"])
        self.assertIn("Inertia", row.message)
        self.assertIn("Physics 12", row.title)
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0].pk, row.pk)

    def test_peer_answer_notifies_asker_too(self):
        d = self.make_doubt()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch("message.push_utils.send_doubt_answered_push"):
                self.answer(self.bob, d)
        row = self._rows().get()
        self.assertFalse(row.data["by_teacher"])

    def test_nobody_else_is_notified(self):
        d = self.make_doubt()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch("message.push_utils.send_doubt_answered_push"):
                self.answer(self.teacher, d)
        self.assertFalse(
            Notification.objects.filter(
                notif_type=Notification.NotifType.DOUBT_ANSWERED,
            ).exclude(recipient=self.alice).exists()
        )

    def test_no_row_no_push_when_notification_skipped(self):
        d = self.make_doubt()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch("message.services.transaction.on_commit") as oc, \
                 mock.patch("core.services.create_notification", return_value=None):
                add_doubt_answer(doubt=d, author=self.bob, text="x")
        oc.assert_not_called()

    def test_push_payload_carries_routing_keys(self):
        d = self.make_doubt()
        with self.captureOnCommitCallbacks(execute=False):
            self.answer(self.teacher, d)
        row = self._rows().get()
        with mock.patch.object(push_utils, "send_rich_push") as rich:
            push_utils.send_doubt_answered_push(row)
        data = rich.call_args.kwargs["data"]
        self.assertEqual(data["type"], "doubt_answered")
        self.assertEqual(data["notif_type"], "doubt_answered")
        self.assertEqual(data["group_id"], str(self.group.id))
        self.assertEqual(data["doubt_id"], str(d.id))

    def test_none_row_is_noop(self):
        with mock.patch.object(push_utils, "send_rich_push") as rich:
            push_utils.send_doubt_answered_push(None)
        rich.assert_not_called()

    def test_category_and_source(self):
        t = Notification.NotifType.DOUBT_ANSWERED
        self.assertEqual(Notification.category_for(t), "classroom")
        self.assertIn(t, Notification.MESSAGE_APP_TYPES)

    def test_testseries_context_doubt_keeps_its_own_notification(self):
        d = DoubtQuestion.objects.create(
            author=self.alice, text="why?", context_type="testseries_attempt", context_id=uuid.uuid4(),
        )
        out = answer_doubt_question(doubt=d, actor=None, answered_by=self.teacher, answer_text="because")
        self.assertTrue(out.is_answered)
        self.assertEqual(out.answered_by_id, self.teacher.id)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.alice, notif_type=Notification.NotifType.TESTSERIES_QUERY_ANSWERED,
            ).exists()
        )
        self.assertFalse(self._rows().exists())
        with self.assertRaises(ValueError):
            answer_doubt_question(doubt=out, actor=None, answered_by=self.teacher, answer_text="again")
        ans = DoubtAnswer.objects.get(doubt=d)
        self.assertTrue(ans.is_teacher_verified and ans.is_best)


# ======================================================================
# T1.3 — multi-answer, best answer, teacher-verified
# ======================================================================
class DoubtMultiAnswerTests(DoubtBase):
    def setUp(self):
        super().setUp()
        self.doubt = self.make_doubt()

    def detail(self, user=None):
        r = self.api(user or self.alice).get(f"{self.base}{self.doubt.id}/")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_multiple_answers_thread_and_ordering(self):
        rb = self.answer(self.bob, self.doubt, "bob's take")
        rt = self.answer(self.teacher, self.doubt, "teacher's take")
        self.assertEqual(rb.status_code, 201, rb.data)
        self.assertEqual(rt.status_code, 201, rt.data)
        d = self.detail()
        self.assertEqual(d["answers_count"], 2)
        self.assertTrue(d["is_answered"])
        self.assertTrue(d["has_verified_answer"])
        self.assertFalse(d["has_best_answer"])
        # teacher (auto-verified) sorts above the older peer answer
        self.assertEqual([a["text"] for a in d["answers"]], ["teacher's take", "bob's take"])
        self.assertTrue(d["answers"][0]["is_teacher_answer"])
        self.assertTrue(d["answers"][0]["is_teacher_verified"])
        self.assertFalse(d["answers"][1]["is_teacher_verified"])
        # legacy mirror -> primary answer
        self.assertEqual(d["answer_text"], "teacher's take")
        self.assertEqual(d["answered_by"]["username"], "teacher")

    def test_post_answer_returns_updated_thread(self):
        r = self.answer(self.bob, self.doubt)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(r.data["answers"]), 1)
        self.assertEqual(r.data["answers"][0]["author"]["username"], "bob")
        self.assertTrue(r.data["answers"][0]["is_mine"])

    def test_asker_cannot_answer_own_doubt(self):
        r = self.answer(self.alice, self.doubt)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(DoubtAnswer.objects.count(), 0)

    def test_non_member_cannot_answer_or_read(self):
        self.assertEqual(self.answer(self.carol, self.doubt).status_code, 403)
        self.assertEqual(self.api(self.carol).get(f"{self.base}{self.doubt.id}/").status_code, 403)

    def test_empty_answer_rejected(self):
        self.assertEqual(self.answer(self.bob, self.doubt, "   ").status_code, 400)

    def test_per_user_answer_cap_but_teacher_exempt(self):
        for i in range(MAX_DOUBT_ANSWERS_PER_USER):
            self.assertEqual(self.answer(self.bob, self.doubt, f"a{i}").status_code, 201)
        self.assertEqual(self.answer(self.bob, self.doubt, "one too many").status_code, 400)
        for i in range(MAX_DOUBT_ANSWERS_PER_USER + 1):
            self.assertEqual(self.answer(self.teacher, self.doubt, f"t{i}").status_code, 201)

    def test_best_answer_asker_marks_and_replaces(self):
        a1 = DoubtAnswer.objects.get(pk=self.answer(self.bob, self.doubt, "one").data["answers"][0]["id"])
        self.answer(self.teacher, self.doubt, "two")
        a2 = DoubtAnswer.objects.get(doubt=self.doubt, text="two")

        r = self.api(self.alice).post(self.answers_url(self.doubt, a1, "best/"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["has_best_answer"])
        self.assertEqual(r.data["best_answer"]["id"], str(a1.id))
        self.assertEqual(r.data["answers"][0]["id"], str(a1.id))  # best sorts first

        r = self.api(self.alice).post(self.answers_url(self.doubt, a2, "best/"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(DoubtAnswer.objects.filter(doubt=self.doubt, is_best=True).count(), 1)
        self.assertEqual(r.data["best_answer"]["id"], str(a2.id))
        a1.refresh_from_db()
        self.assertFalse(a1.is_best)

    def test_unmark_best(self):
        self.answer(self.bob, self.doubt)
        a = DoubtAnswer.objects.get(doubt=self.doubt)
        self.api(self.alice).post(self.answers_url(self.doubt, a, "best/"))
        r = self.api(self.alice).delete(self.answers_url(self.doubt, a, "best/"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["has_best_answer"])
        self.assertIsNone(r.data["best_answer"])

    def test_teacher_can_mark_best_but_classmate_cannot(self):
        self.answer(self.bob, self.doubt)
        a = DoubtAnswer.objects.get(doubt=self.doubt)
        self.assertEqual(self.api(self.bob).post(self.answers_url(self.doubt, a, "best/")).status_code, 403)
        self.assertEqual(self.api(self.teacher).post(self.answers_url(self.doubt, a, "best/")).status_code, 200)

    def test_db_enforces_single_best(self):
        a = DoubtAnswer.objects.create(doubt=self.doubt, author=self.bob, text="a", is_best=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DoubtAnswer.objects.create(doubt=self.doubt, author=self.teacher, text="b", is_best=True)
        self.assertTrue(DoubtAnswer.objects.get(pk=a.pk).is_best)

    def test_teacher_verifies_and_unverifies_peer_answer(self):
        self.answer(self.bob, self.doubt)
        a = DoubtAnswer.objects.get(doubt=self.doubt)
        self.assertFalse(self.detail()["has_verified_answer"])

        r = self.api(self.teacher).post(self.answers_url(self.doubt, a, "verify/"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["has_verified_answer"])
        self.assertTrue(r.data["answers"][0]["is_teacher_verified"])
        self.assertEqual(r.data["answers"][0]["verified_by"]["username"], "teacher")

        r = self.api(self.teacher).delete(self.answers_url(self.doubt, a, "verify/"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["has_verified_answer"])

    def test_only_teacher_can_verify(self):
        self.answer(self.bob, self.doubt)
        a = DoubtAnswer.objects.get(doubt=self.doubt)
        for user in (self.alice, self.bob):
            self.assertEqual(self.api(user).post(self.answers_url(self.doubt, a, "verify/")).status_code, 403)

    def test_teachers_own_answer_cannot_be_unverified(self):
        self.answer(self.teacher, self.doubt)
        a = DoubtAnswer.objects.get(doubt=self.doubt)
        r = self.api(self.teacher).delete(self.answers_url(self.doubt, a, "verify/"))
        self.assertEqual(r.status_code, 400)

    def test_delete_answer_permissions_and_summary(self):
        self.answer(self.bob, self.doubt, "bob")
        self.answer(self.teacher, self.doubt, "teacher")
        ab = DoubtAnswer.objects.get(doubt=self.doubt, text="bob")
        at = DoubtAnswer.objects.get(doubt=self.doubt, text="teacher")

        # bob can't delete the teacher's answer, alice can't delete anything
        self.assertEqual(self.api(self.bob).delete(self.answers_url(self.doubt, at)).status_code, 403)
        self.assertEqual(self.api(self.alice).delete(self.answers_url(self.doubt, ab)).status_code, 403)

        # bob deletes his own
        r = self.api(self.bob).delete(self.answers_url(self.doubt, ab))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["answers_count"], 1)

        # teacher deletes the last one -> doubt is unanswered again
        r = self.api(self.teacher).delete(self.answers_url(self.doubt, at))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["answers_count"], 0)
        self.assertFalse(r.data["is_answered"])
        self.assertFalse(r.data["has_verified_answer"])
        self.assertEqual(r.data["answer_text"], "")

    def test_answer_from_other_doubt_is_404(self):
        other = self.make_doubt(text="another")
        self.answer(self.bob, other)
        a = DoubtAnswer.objects.get(doubt=other)
        r = self.api(self.alice).post(self.answers_url(self.doubt, a, "best/"))
        self.assertEqual(r.status_code, 404)

    def test_legacy_answer_endpoint_still_works(self):
        r = self.api(self.teacher).post(f"{self.base}{self.doubt.id}/answer/", {"answer_text": "legacy"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["is_answered"])
        self.assertEqual(r.data["answer_text"], "legacy")
        self.assertEqual(r.data["answered_by"]["username"], "teacher")

    def test_answers_list_endpoint(self):
        self.answer(self.bob, self.doubt, "one")
        r = self.api(self.alice).get(self.answers_url(self.doubt))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([a["text"] for a in r.data], ["one"])

    def test_answer_events_are_broadcast_with_thread(self):
        self.answer(self.bob, self.doubt)
        _, payload = self.sent[-1]
        self.assertEqual(payload["event"], "doubt_answered")
        self.assertEqual(len(payload["doubt"]["answers"]), 1)
        self.assertFalse(payload["doubt"]["answers"][0]["is_mine"])  # viewer-neutral

    def test_deleted_author_answer_survives(self):
        self.answer(self.bob, self.doubt, "keep me")
        DoubtAnswer.objects.filter(doubt=self.doubt).update(author=None)  # what SET_NULL does on user delete
        d = self.detail()
        self.assertEqual(d["answers_count"], 1)
        self.assertIsNone(d["answers"][0]["author"])


class DoubtStatusFilterTests(DoubtBase):
    def test_needs_review_and_verified_filters(self):
        peer_only = self.make_doubt(text="peer only")
        verified = self.make_doubt(text="verified")
        fresh = self.make_doubt(text="fresh")
        self.answer(self.bob, peer_only)
        self.answer(self.teacher, verified)

        def texts(qs):
            r = self.api(self.teacher).get(self.base + qs)
            self.assertEqual(r.status_code, 200)
            return {d["text"] for d in r.data["results"]}

        self.assertEqual(texts("?status=needs_review"), {"peer only"})
        self.assertEqual(texts("?status=verified"), {"verified"})
        self.assertEqual(texts("?status=unanswered"), {"fresh"})
        self.assertEqual(texts("?status=answered"), {"peer only", "verified"})

    def test_mine_filter(self):
        self.make_doubt(author=self.alice, text="a")
        self.make_doubt(author=self.bob, text="b")
        r = self.api(self.alice).get(self.base + "?mine=1")
        self.assertEqual({d["text"] for d in r.data["results"]}, {"a"})


# ======================================================================
# T1.4 — list query optimisation + throttle wiring
# ======================================================================
class DoubtListQueryTests(DoubtBase):
    def _list_query_count(self):
        client = self.api(self.bob)
        with CaptureQueriesContext(connection) as ctx:
            r = client.get(self.base)
        self.assertEqual(r.status_code, 200, r.data)
        return len(ctx.captured_queries), r.data

    def test_query_count_does_not_grow_with_page_size(self):
        for i in range(3):
            self.make_doubt(text=f"d{i}")
        small, _ = self._list_query_count()

        for i in range(12):
            d = self.make_doubt(text=f"more{i}")
            self.answer(self.teacher, d)
            if i % 2:
                a = DoubtAnswer.objects.get(doubt=d)
                self.api(self.alice).post(self.answers_url(d, a, "best/"))
            DoubtUpvote.objects.create(doubt=d, user=self.bob)
        big, data = self._list_query_count()

        self.assertEqual(len(data["results"]), 15)
        self.assertEqual(small, big, f"list is N+1: {small} queries for 3 doubts vs {big} for 15")
        self.assertLessEqual(big, 8)

    def test_list_payload_shape(self):
        d = self.make_doubt(text="shape")
        self.answer(self.teacher, d, "full answer")
        a = DoubtAnswer.objects.get(doubt=d)
        self.api(self.alice).post(self.answers_url(d, a, "best/"))
        DoubtUpvote.objects.create(doubt=d, user=self.bob)
        DoubtQuestion.objects.filter(pk=d.pk).update(upvotes_count=1)

        r = self.api(self.bob).get(self.base)
        row = r.data["results"][0]
        self.assertTrue(row["upvoted_by_me"])
        self.assertFalse(row["is_mine"])
        self.assertEqual(row["answers_count"], 1)
        self.assertTrue(row["has_best_answer"] and row["has_verified_answer"])
        self.assertEqual(row["best_answer"]["text"], "full answer")
        self.assertNotIn("answers", row)  # full thread only on the detail endpoint

        r = self.api(self.alice).get(self.base)
        row = r.data["results"][0]
        self.assertFalse(row["upvoted_by_me"])
        self.assertTrue(row["is_mine"])

    def test_anonymous_author_hidden_in_list_for_others(self):
        self.make_doubt(is_anonymous=True)
        row = self.api(self.bob).get(self.base).data["results"][0]
        self.assertIsNone(row["author"])
        row = self.api(self.alice).get(self.base).data["results"][0]
        self.assertEqual(row["author"]["username"], "alice")

    def test_ordering_is_upvotes_then_newest(self):
        a = self.make_doubt(text="old")
        b = self.make_doubt(text="new")
        c = self.make_doubt(text="popular")
        DoubtQuestion.objects.filter(pk=c.pk).update(upvotes_count=5)
        texts = [d["text"] for d in self.api(self.bob).get(self.base).data["results"]]
        self.assertEqual(texts, ["popular", "new", "old"])


class DoubtThrottleWiringTests(DoubtBase):
    def test_every_doubt_scope_has_a_rate(self):
        from django.conf import settings
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in ("doubt_create", "doubt_create_image", "doubt_answer",
                      "doubt_upvote", "doubt_action", "doubt_read"):
            self.assertIn(scope, rates)

    def test_action_to_throttle_mapping(self):
        from .views import DoubtQuestionViewSet
        from . import throttles as t

        def classes_for(action_name, files=False):
            view = DoubtQuestionViewSet()
            view.action = action_name
            req = mock.Mock()
            req.FILES = {"image": object()} if files else {}
            view.request = req
            return [type(x) for x in view.get_throttles()]

        self.assertEqual(classes_for("create"), [t.DoubtCreateThrottle])
        self.assertEqual(classes_for("create", files=True), [t.DoubtCreateThrottle, t.DoubtImageUploadThrottle])
        self.assertEqual(classes_for("create_answer"), [t.DoubtAnswerThrottle])
        self.assertEqual(classes_for("answer"), [t.DoubtAnswerThrottle])
        self.assertEqual(classes_for("upvote"), [t.DoubtUpvoteThrottle])
        for a in ("best", "verify", "reveal", "delete_answer"):
            self.assertEqual(classes_for(a), [t.DoubtActionThrottle])
        for a in ("list", "retrieve", "list_answers", None):
            self.assertEqual(classes_for(a), [t.DoubtReadThrottle])

    def test_answer_endpoint_is_rate_limited(self):
        from rest_framework.throttling import SimpleRateThrottle
        rates = dict(SimpleRateThrottle.THROTTLE_RATES)
        rates["doubt_answer"] = "2/hour"
        d = self.make_doubt()
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            codes = [self.answer(self.bob, d, f"a{i}").status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 429])
