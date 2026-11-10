"""P-A2 — resumable upload: the cases the app's queue relies on after a crash / restart.
Run: python manage.py test post.tests_upload_resume_extra   (written by reading the code; NOT run)
Builds on post/tests_upload_queue.py helpers. Status codes that the code does not pin down are asserted as a set.
"""
from unittest import mock

from django.urls import reverse
from rest_framework.test import APIClient

from . import upload_sessions
from .models import Story, UploadSession
from .tests_upload_queue import _Base, jpeg_bytes


class UploadResumeAfterRestartTests(_Base):
    def _session(self, size=1024):
        data = jpeg_bytes((300, 300))
        with mock.patch.object(upload_sessions, "MIN_CHUNK_SIZE", 256):
            res = self.init(kind="story", payload={"media_type": "image"}, chunk_size=size,
                            files=[{"name": "s.jpg", "size": len(data), "content_type": "image/jpeg"}])
        self.assertEqual(res.status_code, 201, res.data)
        parts = [data[i:i + size] for i in range(0, len(data), size)]
        self.assertGreater(len(parts), 2)
        return res.data["session_id"], res.data, parts

    def test_new_client_resumes_with_only_the_missing_chunks(self):
        sid, _, parts = self._session()
        self.send_chunk(sid, parts[0], chunk_index=0)
        # "app restarted": a brand-new client for the same user asks what the server already has
        fresh = APIClient()
        fresh.force_authenticate(self.me)
        got = fresh.get(reverse("upload-detail", args=[sid]))
        self.assertEqual(got.status_code, 200)
        have = set(got.data["files"][0]["received_chunks"])
        self.assertEqual(have, {0})
        for i, part in enumerate(parts):
            if i not in have:
                self.assertEqual(self.send_chunk(sid, part, chunk_index=i).status_code, 200)
        self.assertEqual(self.complete(sid).status_code, 201)
        self.assertEqual(Story.objects.filter(user=self.me).count(), 1)

    def test_resending_a_received_chunk_is_harmless(self):
        sid, _, parts = self._session()
        self.assertEqual(self.send_chunk(sid, parts[0], chunk_index=0).status_code, 200)
        again = self.send_chunk(sid, parts[0], chunk_index=0)
        self.assertIn(again.status_code, (200, 409))
        for i, part in enumerate(parts[1:], start=1):
            self.send_chunk(sid, part, chunk_index=i)
        self.assertEqual(self.complete(sid).status_code, 201)
        self.assertEqual(Story.objects.filter(user=self.me).count(), 1)

    def test_same_idempotency_key_after_restart_returns_the_same_session_with_chunks_kept(self):
        data = jpeg_bytes((300, 300))
        key = "resume-key-0001"
        files = [{"name": "s.jpg", "size": len(data), "content_type": "image/jpeg"}]
        with mock.patch.object(upload_sessions, "MIN_CHUNK_SIZE", 256):
            first = self.init(idempotency_key=key, kind="story", payload={"media_type": "image"}, chunk_size=1024, files=files)
            self.assertEqual(first.status_code, 201, first.data)
            sid = first.data["session_id"]
            self.send_chunk(sid, data[:1024], chunk_index=0)
            second = self.init(idempotency_key=key, kind="story", payload={"media_type": "image"}, chunk_size=1024, files=files)
        self.assertIn(second.status_code, (200, 201))
        self.assertEqual(second.data["session_id"], sid)
        self.assertEqual(UploadSession.objects.filter(user=self.me).count(), 1)
        detail = self.client.get(reverse("upload-detail", args=[sid]))
        self.assertEqual(detail.data["files"][0]["received_chunks"], [0])
