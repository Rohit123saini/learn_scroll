"""
H20 - UploadQueue / Drafts / Idempotency tests.  Written by reading the code; NOT run yet.
Run: python manage.py test post.tests_upload_queue
"""
import hashlib
import io
import json
import shutil
import tempfile
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from . import upload_sessions
from .models import Draft, IdempotencyRecord, Post, Story, UploadSession
from .tasks import purge_stale_uploads

User = get_user_model()

_TMP = tempfile.mkdtemp(prefix="ls_h20_tests_")


def make_user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="testpass123")


def jpeg_bytes(size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def md5(data):
    return hashlib.md5(data).hexdigest()


def new_key():
    return "k-" + uuid.uuid4().hex[:20]


@override_settings(MEDIA_ROOT=_TMP, CHUNKED_UPLOAD_TMP_ROOT=_TMP)
class _Base(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.me = make_user("h20_me")
        self.other = make_user("h20_other")
        self.client.force_authenticate(self.me)

    # helpers -------------------------------------------------------------
    def init(self, **over):
        body = {"idempotency_key": new_key(), "kind": "post",
                "payload": {"post_type": "text", "content": "hello", "category": "general"}, "files": []}
        body.update(over)
        return self.client.post(reverse("upload-create"), body, format="json")

    def send_chunk(self, session_id, data, file_index=0, chunk_index=0, chunk_hash=None):
        return self.client.post(
            reverse("upload-chunk", args=[session_id]),
            {"file_index": file_index, "chunk_index": chunk_index,
             "chunk_hash": chunk_hash if chunk_hash is not None else md5(data),
             "chunk": SimpleUploadedFile("c.bin", data, content_type="application/octet-stream")},
            format="multipart")

    def complete(self, session_id, body=None, **extra):
        return self.client.post(reverse("upload-complete", args=[session_id]), body or {}, format="json", **extra)


# =====================================================================================================
class IdempotentDecoratorTests(_Base):
    def _create(self, key=None, content="same text"):
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(
            reverse("post-create"), {"content": content, "category": "general", "post_type": "text"},
            format="multipart", **extra)

    def test_same_key_creates_only_one_post_and_replays_the_answer(self):
        key = new_key()
        first = self._create(key)
        second = self._create(key)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second["Idempotent-Replay"], "true")
        self.assertEqual(first.data["data"]["id"], second.data["data"]["id"])
        self.assertEqual(Post.objects.filter(user=self.me, content="same text").count(), 1)

    def test_no_header_keeps_the_old_behaviour(self):
        self._create()
        self._create()
        self.assertEqual(Post.objects.filter(user=self.me, content="same text").count(), 2)

    def test_bad_key_is_refused_with_a_code(self):
        res = self._create("short")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "idempotency_key_invalid")

    def test_failed_first_request_leaves_no_record_so_the_key_can_be_retried(self):
        key = new_key()
        bad = self._create(key, content="")  # text post without text -> 400
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(IdempotencyRecord.objects.filter(user=self.me, key=key).exists())
        good = self._create(key, content="now with text")
        self.assertEqual(good.status_code, 201, good.data)

    def test_key_still_running_gives_409(self):
        key = new_key()
        IdempotencyRecord.objects.create(user=self.me, scope="post_create", key=key)  # in_progress, just now
        res = self._create(key)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "request_in_progress")

    def test_stale_in_progress_record_is_taken_over(self):
        key = new_key()
        IdempotencyRecord.objects.create(
            user=self.me, scope="post_create", key=key, created_at=timezone.now() - timedelta(minutes=10))
        res = self._create(key)
        self.assertEqual(res.status_code, 201, res.data)

    def test_same_key_of_another_user_does_not_collide(self):
        key = new_key()
        self._create(key)
        self.client.force_authenticate(self.other)
        res = self._create(key)
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("Idempotent-Replay", res)

    def test_story_create_is_idempotent_too(self):
        key = new_key()
        for _ in range(2):
            res = self.client.post(
                reverse("story-create"),
                {"media": SimpleUploadedFile("s.jpg", jpeg_bytes(), content_type="image/jpeg"), "media_type": "image"},
                format="multipart", HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(res.status_code, 201, getattr(res, "data", None))
        self.assertEqual(Story.objects.filter(user=self.me).count(), 1)


# =====================================================================================================
class UploadSessionInitTests(_Base):
    def test_init_creates_a_session_and_same_key_returns_the_same_one(self):
        key = new_key()
        first = self.init(idempotency_key=key)
        again = self.init(idempotency_key=key)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.data["session_id"], again.data["session_id"])
        self.assertTrue(first.data["created"])
        self.assertFalse(again.data["created"])
        self.assertEqual(UploadSession.objects.filter(user=self.me).count(), 1)

    def test_same_key_other_kind_is_a_conflict(self):
        key = new_key()
        self.init(idempotency_key=key)
        res = self.init(idempotency_key=key, kind="story", files=[{"name": "a.jpg", "size": 10}])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "idempotency_key_reused")

    def test_early_validation_fails_before_any_upload(self):
        cases = [
            ({"idempotency_key": "x"}, "idempotency_key_invalid"),
            ({"kind": "banana"}, "kind_invalid"),
            ({"chunk_size": 10}, "chunk_size_invalid"),
            ({"payload": {"post_type": "text", "content": "  "}}, "content_required"),
            ({"payload": {"post_type": "text", "content": "x", "category": "nope"}}, "category_invalid"),
            ({"payload": {"post_type": "image", "content": "x"}}, "media_required"),
            ({"kind": "story"}, "media_required"),
            ({"kind": "story", "files": [{"name": "a.jpg", "size": 0}]}, "file_empty"),
            ({"payload": "not-an-object"}, "payload_invalid"),
            ({"files": [{"name": "a.jpg", "size": 5, "role": "cover"}]}, "cover_without_media"),
        ]
        for over, code in cases:
            res = self.init(**over)
            self.assertEqual(res.status_code, 400, (over, res.data))
            self.assertEqual(res.data["code"], code, over)
        self.assertEqual(UploadSession.objects.count(), 0)

    def test_reserved_payload_keys_are_dropped(self):
        res = self.init(payload={"post_type": "text", "content": "hi", "user": 999, "media_files": "x", "id": "1"})
        self.assertEqual(res.status_code, 201, res.data)
        stored = UploadSession.objects.get(pk=res.data["session_id"]).payload
        for key in ("user", "media_files", "id"):
            self.assertNotIn(key, stored)

    def test_too_many_active_sessions_is_429(self):
        for _ in range(upload_sessions.MAX_ACTIVE_SESSIONS_PER_USER):
            self.assertEqual(self.init().status_code, 201)
        res = self.init()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "too_many_uploads")

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.init().status_code, 401)

    def test_other_users_session_is_404(self):
        sid = self.init().data["session_id"]
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(reverse("upload-detail", args=[sid])).status_code, 404)
        self.assertEqual(self.complete(sid).status_code, 404)

    def test_copyright_blocked_user_cannot_start(self):
        with mock.patch("copyrights.services.can_upload", return_value=(False, "blocked")):
            res = self.init()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["code"], "copyright_upload_blocked")


# =====================================================================================================
class UploadChunkAndCompleteTests(_Base):
    def _story_session(self, data=None, chunk_size=1024):
        data = data or jpeg_bytes((300, 300))
        with mock.patch.object(upload_sessions, "MIN_CHUNK_SIZE", 256):
            res = self.init(kind="story", payload={"media_type": "image"}, chunk_size=chunk_size,
                            files=[{"name": "s.jpg", "size": len(data), "content_type": "image/jpeg"}])
        self.assertEqual(res.status_code, 201, res.data)
        return res.data["session_id"], data, chunk_size

    def _chunks(self, data, size):
        return [data[i:i + size] for i in range(0, len(data), size)]

    def test_full_flow_creates_the_story_once(self):
        sid, data, size = self._story_session()
        parts = self._chunks(data, size)
        self.assertGreater(len(parts), 1)
        for i, part in enumerate(parts):
            self.assertEqual(self.send_chunk(sid, part, chunk_index=i).status_code, 200)
        res = self.complete(sid)
        self.assertEqual(res.status_code, 201, res.data)
        again = self.complete(sid)  # duplicate complete = replay, no second story
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again["Idempotent-Replay"], "true")
        self.assertEqual(Story.objects.filter(user=self.me).count(), 1)
        self.assertEqual(UploadSession.objects.get(pk=sid).status, UploadSession.STATUS_COMPLETED)

    def test_status_lists_received_chunks_for_resume(self):
        sid, data, size = self._story_session()
        parts = self._chunks(data, size)
        self.send_chunk(sid, parts[0], chunk_index=0)
        self.send_chunk(sid, parts[2], chunk_index=2)
        res = self.client.get(reverse("upload-detail", args=[sid]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["files"][0]["received_chunks"], [0, 2])

    def test_damaged_chunk_is_rejected_and_can_be_sent_again(self):
        sid, data, size = self._story_session()
        part = self._chunks(data, size)[0]
        bad = self.send_chunk(sid, part, chunk_hash="0" * 32)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data["code"], "chunk_hash_mismatch")
        self.assertEqual(self.send_chunk(sid, part).status_code, 200)

    def test_wrong_size_and_bad_index_are_refused(self):
        sid, data, size = self._story_session()
        part = self._chunks(data, size)[0]
        self.assertEqual(self.send_chunk(sid, part[:-1]).data["code"], "chunk_size_mismatch")
        self.assertEqual(self.send_chunk(sid, part, chunk_index=99).data["code"], "chunk_index_invalid")
        self.assertEqual(self.send_chunk(sid, part, file_index=5).data["code"], "file_index_invalid")
        self.assertEqual(self.send_chunk(sid, part, chunk_hash="").data["code"], "chunk_hash_required")

    def test_complete_with_missing_chunks_is_409_and_lists_them(self):
        sid, data, size = self._story_session()
        self.send_chunk(sid, self._chunks(data, size)[0], chunk_index=0)
        res = self.complete(sid)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "chunks_missing")
        self.assertIn(1, res.data["missing"])
        self.assertEqual(Story.objects.count(), 0)

    def test_validation_error_keeps_chunks_and_edit_fixes_it(self):
        sid, data, size = self._story_session()
        for i, part in enumerate(self._chunks(data, size)):
            self.send_chunk(sid, part, chunk_index=i)
        # a story audience the serializer does not know -> the story serializer raises a ValidationError
        bad = self.complete(sid, {"payload": {"media_type": "image", "audience": "martians"}})
        self.assertEqual(bad.status_code, 400, bad.data)
        self.assertFalse(bad.data["success"])
        self.assertEqual(Story.objects.count(), 0)
        session = UploadSession.objects.get(pk=sid)
        self.assertEqual(session.status, UploadSession.STATUS_UPLOADING)  # chunks stay: nothing to re-upload
        good = self.complete(sid, {"payload": {"media_type": "image", "audience": "everyone"}})
        self.assertEqual(good.status_code, 201, good.data)

    def test_5xx_from_the_create_view_rolls_back_and_session_stays_open(self):
        sid, data, size = self._story_session()
        for i, part in enumerate(self._chunks(data, size)):
            self.send_chunk(sid, part, chunk_index=i)
        from rest_framework.response import Response

        with mock.patch.object(upload_sessions, "_run_create_view",
                               return_value=Response({"success": False, "message": "boom"}, status=500)):
            res = self.complete(sid)
        self.assertEqual(res.status_code, 500)
        self.assertEqual(UploadSession.objects.get(pk=sid).status, UploadSession.STATUS_UPLOADING)
        self.assertEqual(self.complete(sid).status_code, 201)  # the retry works

    def test_text_post_session_without_files_completes_and_deletes_the_draft(self):
        draft_id = uuid.uuid4()
        Draft.objects.create(user=self.me, client_id=draft_id, kind="post", data={"content": "hello"})
        res = self.init(payload={"post_type": "text", "content": "hello", "category": "general",
                                 "draft_id": str(draft_id)})
        sid = res.data["session_id"]
        done = self.complete(sid)
        self.assertEqual(done.status_code, 201, done.data)
        self.assertTrue(Draft.objects.get(user=self.me, client_id=draft_id).is_deleted)
        self.assertEqual(Post.objects.filter(user=self.me, content="hello").count(), 1)

    def test_cancel_frees_the_chunks_and_blocks_further_use(self):
        sid, data, size = self._story_session()
        self.send_chunk(sid, self._chunks(data, size)[0])
        self.assertEqual(self.client.delete(reverse("upload-detail", args=[sid])).status_code, 200)
        self.assertEqual(self.client.delete(reverse("upload-detail", args=[sid])).status_code, 200)  # idempotent
        self.assertEqual(UploadSession.objects.get(pk=sid).status, UploadSession.STATUS_CANCELLED)
        res = self.send_chunk(sid, self._chunks(data, size)[0])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "session_cancelled")

    def test_expired_session_is_410(self):
        sid, data, size = self._story_session()
        UploadSession.objects.filter(pk=sid).update(expires_at=timezone.now() - timedelta(minutes=1))
        res = self.send_chunk(sid, self._chunks(data, size)[0])
        self.assertEqual(res.status_code, 410)
        self.assertEqual(res.data["code"], "session_expired")


# =====================================================================================================
class DraftTests(_Base):
    def _put(self, client_id, **over):
        body = {"kind": "post", "data": {"content": "draft text"}, "media": [], "base_version": 0}
        body.update(over)
        return self.client.put(reverse("draft-detail", args=[client_id]), body, format="json")

    def test_create_update_list_delete(self):
        cid = uuid.uuid4()
        created = self._put(cid)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["version"], 1)
        updated = self._put(cid, data={"content": "edited"}, base_version=1)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["version"], 2)
        listing = self.client.get(reverse("draft-list"))
        self.assertEqual([d["id"] for d in listing.data["results"]], [str(cid)])
        self.assertEqual(listing.data["results"][0]["data"]["content"], "edited")
        self.assertEqual(self.client.delete(reverse("draft-detail", args=[cid])).status_code, 200)
        self.assertEqual(self.client.delete(reverse("draft-detail", args=[cid])).status_code, 200)  # idempotent
        self.assertEqual(self.client.get(reverse("draft-list")).data["results"], [])
        self.assertEqual(self.client.get(reverse("draft-detail", args=[cid])).status_code, 404)

    def test_stale_base_version_is_a_conflict_with_the_server_copy(self):
        cid = uuid.uuid4()
        self._put(cid)
        self._put(cid, data={"content": "from phone A"}, base_version=1)
        res = self._put(cid, data={"content": "from phone B"}, base_version=1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "draft_conflict")
        self.assertEqual(res.data["server"]["data"]["content"], "from phone A")

    def test_retry_of_the_same_write_is_not_a_conflict(self):
        cid = uuid.uuid4()
        self._put(cid)
        first = self._put(cid, data={"content": "same"}, base_version=1)
        retry = self._put(cid, data={"content": "same"}, base_version=1)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["version"], first.data["version"])

    def test_deleted_draft_reappears_only_as_a_new_draft(self):
        cid = uuid.uuid4()
        self._put(cid)
        self.client.delete(reverse("draft-detail", args=[cid]))
        self.assertEqual(self._put(cid, base_version=1).status_code, 410)
        self.assertEqual(self._put(cid, base_version=0).status_code, 200)

    def test_since_returns_tombstones(self):
        cid = uuid.uuid4()
        self._put(cid)
        mark = (timezone.now() - timedelta(seconds=5)).isoformat()
        self.client.delete(reverse("draft-detail", args=[cid]))
        res = self.client.get(reverse("draft-list"), {"since": mark})
        self.assertEqual(len(res.data["results"]), 1)
        self.assertTrue(res.data["results"][0]["deleted"])
        self.assertEqual(self.client.get(reverse("draft-list"), {"since": "garbage"}).status_code, 400)

    def test_validation(self):
        cid = uuid.uuid4()
        self.assertEqual(self._put(cid, kind="x").data["code"], "kind_invalid")
        self.assertEqual(self._put(cid, data="nope").data["code"], "draft_data_invalid")
        self.assertEqual(self._put(cid, media="nope").data["code"], "draft_media_invalid")
        self.assertEqual(self._put(cid, data={"content": "x" * (70 * 1024)}).status_code, 413)
        stored = self._put(cid, data={"content": "ok", "user": 5, "media_files": "x"})
        self.assertNotIn("user", stored.data["data"])
        self.assertNotIn("media_files", stored.data["data"])

    def test_one_users_draft_is_invisible_to_another(self):
        cid = uuid.uuid4()
        self._put(cid)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(reverse("draft-detail", args=[cid])).status_code, 404)
        self.assertEqual(self.client.get(reverse("draft-list")).data["results"], [])

    def test_too_many_drafts_is_429(self):
        from . import drafts

        with mock.patch.object(drafts, "MAX_DRAFTS_PER_USER", 2):
            self.assertEqual(self._put(uuid.uuid4()).status_code, 201)
            self.assertEqual(self._put(uuid.uuid4()).status_code, 201)
            res = self._put(uuid.uuid4())
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "too_many_drafts")


# =====================================================================================================
class PurgeTaskTests(_Base):
    def test_purge_removes_expired_chunks_old_sessions_records_and_drafts(self):
        expired = UploadSession.objects.create(
            user=self.me, idempotency_key=new_key(), kind="post", expires_at=timezone.now() - timedelta(hours=1))
        old_done = UploadSession.objects.create(
            user=self.me, idempotency_key=new_key(), kind="post", status="completed",
            expires_at=timezone.now() + timedelta(hours=1))
        UploadSession.objects.filter(pk=old_done.pk).update(updated_at=timezone.now() - timedelta(days=8))
        fresh = UploadSession.objects.create(
            user=self.me, idempotency_key=new_key(), kind="post", expires_at=timezone.now() + timedelta(hours=1))
        IdempotencyRecord.objects.create(user=self.me, scope="post_create", key=new_key(),
                                         created_at=timezone.now() - timedelta(hours=49))
        IdempotencyRecord.objects.create(user=self.me, scope="post_create", key=new_key())
        Draft.objects.create(user=self.me, client_id=uuid.uuid4(), kind="post", is_deleted=True,
                             deleted_at=timezone.now() - timedelta(days=31))
        Draft.objects.create(user=self.me, client_id=uuid.uuid4(), kind="post")

        counts = purge_stale_uploads()

        self.assertEqual(counts["sessions_expired"], 1)
        self.assertEqual(counts["sessions_deleted"], 1)
        self.assertEqual(counts["idempotency_deleted"], 1)
        self.assertEqual(counts["drafts_deleted"], 1)
        expired.refresh_from_db()
        self.assertEqual(expired.status, UploadSession.STATUS_CANCELLED)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, UploadSession.STATUS_UPLOADING)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        self.assertEqual(Draft.objects.count(), 1)

    def test_purge_is_safe_when_nothing_to_do(self):
        self.assertEqual(purge_stale_uploads(),
                         {"sessions_expired": 0, "sessions_deleted": 0, "idempotency_deleted": 0, "drafts_deleted": 0})
