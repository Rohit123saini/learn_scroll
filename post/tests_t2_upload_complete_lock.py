"""
T2.5 - upload `complete` + "database is locked": no duplicate post, same session replays the same answer.
(written by reading the code; NOT run yet)  Run: python manage.py test post.tests_t2_upload_complete_lock
"""
from unittest import mock

from django.db import OperationalError

from . import upload_sessions
from .models import Post, UploadSession
from .tests_upload_queue import _Base


class UploadCompleteLockTests(_Base):
    def _session(self):
        res = self.init(payload={"post_type": "text", "content": "t2 lock text", "category": "general"})
        self.assertEqual(res.status_code, 201, res.data)
        return res.data["session_id"]

    def _posts(self):
        return Post.objects.filter(user=self.me, content="t2 lock text").count()

    @mock.patch("common.db_retry.time.sleep")
    def test_lock_after_the_post_was_written_rolls_back_and_retry_creates_exactly_one(self, _sleep):
        sid = self._session()
        real = upload_sessions._run_create_view
        calls = {"n": 0}

        def run_then_lock(kind, shim):
            response = real(kind, shim)  # the post row IS written here (inside complete's atomic block)
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("database is locked")
            return response

        with mock.patch.object(upload_sessions, "_run_create_view", side_effect=run_then_lock):
            res = self.complete(sid)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._posts(), 1)
        self.assertEqual(UploadSession.objects.get(pk=sid).status, UploadSession.STATUS_COMPLETED)

    @mock.patch("common.db_retry.time.sleep")
    def test_complete_again_replays_the_same_post(self, _sleep):
        sid = self._session()
        first = self.complete(sid)
        again = self.complete(sid)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(again.status_code, first.status_code)
        self.assertEqual(again["Idempotent-Replay"], "true")
        self.assertEqual(again.data, first.data)
        self.assertEqual(self._posts(), 1)

    @mock.patch("common.db_retry.time.sleep")
    def test_same_idempotency_key_returns_the_same_session(self, _sleep):
        key = "t2-key-0123456789ab"
        a = self.init(idempotency_key=key, payload={"post_type": "text", "content": "t2 lock text", "category": "general"})
        b = self.init(idempotency_key=key, payload={"post_type": "text", "content": "t2 lock text", "category": "general"})
        self.assertEqual(a.data["session_id"], b.data["session_id"])
        self.complete(a.data["session_id"])
        self.complete(b.data["session_id"])
        self.assertEqual(self._posts(), 1)
