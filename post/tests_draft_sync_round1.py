"""
H20 round 1 - legacy chunked gate + composer-form drafts on the server.  Written by reading the code; NOT run yet.
Run: python manage.py test post.tests_draft_sync_round1
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

User = get_user_model()


class _Base(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="r1_me", email="r1_me@example.com", password="testpass123")
        self.client.force_authenticate(self.me)


class LegacyChunkedGateTests(_Base):
    def _init(self):
        return self.client.post(reverse("post-chunked-init"), {}, format="json")

    def test_enabled_by_default_runs_the_old_validation(self):
        res = self._init()
        self.assertEqual(res.status_code, 400)  # "file_name, total_chunks, total_size required", NOT 410

    @override_settings(POST_LEGACY_CHUNKED_ENABLED=False)
    def test_disabled_init_complete_status_answer_410(self):
        for res in (
            self._init(),
            self.client.post(reverse("post-chunked-complete"), {"upload_id": "x"}, format="json"),
            self.client.get(reverse("post-chunked-status", args=["x"])),
        ):
            self.assertEqual(res.status_code, 410)
            self.assertEqual(res.data["code"], "legacy_chunked_removed")

    @override_settings(POST_LEGACY_CHUNKED_ENABLED=False)
    def test_comment_chunk_route_is_not_affected(self):
        # the shared comment/chunked/chunk/ route must still answer its own validation error, never 410
        res = self.client.post(reverse("chunked-chunk"), {}, format="multipart")
        self.assertNotEqual(res.status_code, 410)


class ComposerFormDraftTests(_Base):
    """A composer form draft is an ordinary draft row with data = {_slot, form}; the id is fixed per account + slot."""

    def _put(self, cid, form, base=0, kind="post", slot="newPost", media=None):
        body = {"kind": kind, "data": {"_slot": slot, "form": form}, "media": media or [], "base_version": base}
        return self.client.put(reverse("draft-detail", args=[cid]), body, format="json")

    def test_nested_form_keeps_a_files_key(self):
        # the server drops a TOP-LEVEL `files`; inside `form` it must survive (resource drafts keep file names there)
        cid = uuid.uuid4()
        form = {"title": "Notes", "files": [{"name": "a.pdf", "alt": ""}]}
        res = self._put(cid, form, kind="resource", slot="resource", media=[{"name": "a.pdf", "role": "file"}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["data"]["form"]["files"][0]["name"], "a.pdf")
        self.assertEqual(res.data["data"]["_slot"], "resource")

    def test_same_id_updates_with_base_version_and_conflicts_otherwise(self):
        cid = uuid.uuid4()
        self._put(cid, {"content": "v1"})
        self.assertEqual(self._put(cid, {"content": "phone A"}, base=1).status_code, 200)
        res = self._put(cid, {"content": "phone B"}, base=1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["server"]["data"]["form"]["content"], "phone A")

    def test_discard_then_new_draft_with_the_same_id_is_allowed(self):
        cid = uuid.uuid4()
        self._put(cid, {"content": "old"})
        self.client.delete(reverse("draft-detail", args=[cid]))
        res = self._put(cid, {"content": "new"}, base=0)  # base 0 = "new draft with this id"
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertEqual(res.data["data"]["form"]["content"], "new")

    def test_deleted_elsewhere_with_stale_base_is_410(self):
        cid = uuid.uuid4()
        self._put(cid, {"content": "x"})
        self.client.delete(reverse("draft-detail", args=[cid]))
        self.assertEqual(self._put(cid, {"content": "y"}, base=1).status_code, 410)
