"""
ST-A - Story gaps: highlight Share / Save, owner Delete, Class story mentions.

NOT RUN when written (read-only session). Run: python manage.py test post.tests_story_st_a

Covers
  * POST /post/highlights/<id>/stories/<sid>/share/ and /save/ (an ENDED story reached through a highlight):
    201 / 200 for an allowed viewer, 403 `story_reshare_off` / `story_save_off`, the owner always allowed, 404 for a
    story of another highlight, a Close Friends item, a story the owner removed early, a blocked owner; not a
    participant 403; unknown conversation 404; login required.
  * DELETE /post/stories/<id>/ (owner Delete): 204 + soft delete, someone else 404 (no leak), already deleted 404,
    the story is out of the live detail afterwards, login required.
  * `_check_mention_can_see` for a Class story: member ok, non-member -> `mention_not_in_class`.
"""
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from message.models import Conversation, Message, MessageType
from user_profile.models import BlockUser

from .models import Story
from .story_stickers import StickerError, _check_mention_can_see
from .story_visibility import STORY_RESHARE_OFF, STORY_SAVE_OFF
from .tests_highlights import make_highlight, make_story, make_user, follow
from .tests_story_stickers import _TMP_MEDIA


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class _HlBase(APITestCase):
    def setUp(self):
        self.owner = make_user("sta_owner")
        self.viewer = make_user("sta_viewer")
        self.other = make_user("sta_other")
        follow(self.viewer, self.owner)
        # an ENDED story (swept after its 24 h) - the live endpoints answer 404 for it, the highlight ones must not
        self.story = make_story(self.owner, "ended.jpg", state="swept")
        self.hl = make_highlight(self.owner, [self.story])

    def _flags(self, story=None, **flags):
        story = story or self.story
        Story.objects.filter(pk=story.pk).update(**flags)

    def _chat(self, a, b):
        convo, _ = Conversation.get_or_create_private(a, b)
        return convo

    def _share_url(self, hl=None, story=None):
        return reverse("highlight-story-share", args=[(hl or self.hl).id, (story or self.story).id])

    def _save_url(self, hl=None, story=None):
        return reverse("highlight-story-save", args=[(hl or self.hl).id, (story or self.story).id])


class HighlightShareTests(_HlBase):
    def test_allowed_share_creates_story_reply_message_with_share_meta(self):
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 201, getattr(res, "data", None))
        msg = Message.objects.get(conversation=convo)
        self.assertEqual(msg.type, MessageType.STORY_REPLY)
        self.assertTrue(msg.meta.get("story_share"))
        self.assertEqual(msg.meta.get("shared_story_id"), str(self.story.id))

    def test_reshare_off_is_403_with_code_and_sends_nothing(self):
        self._flags(allow_reshare=False)
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), STORY_RESHARE_OFF)
        self.assertFalse(Message.objects.filter(conversation=convo).exists())

    def test_owner_may_share_even_when_off(self):
        self._flags(allow_reshare=False)
        convo = self._chat(self.owner, self.other)
        self.client.force_authenticate(self.owner)
        res = self.client.post(self._share_url(), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 201, getattr(res, "data", None))

    def test_not_a_participant_is_403(self):
        convo = self._chat(self.owner, self.other)  # the viewer is not in it
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_unknown_conversation_is_404(self):
        import uuid
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(), {"conversation_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_missing_conversation_id_is_400(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(self._share_url(), {}, format="json").status_code, 400)

    def test_story_of_another_highlight_is_404(self):
        other_story = make_story(self.owner, "other.jpg", state="swept")
        make_highlight(self.owner, [other_story], title="Other")
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(story=other_story), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_close_friends_item_is_404_for_a_non_friend(self):
        cf = make_story(self.owner, "cf.jpg", audience=Story.AUDIENCE_CLOSE_FRIENDS, state="swept")
        hl = make_highlight(self.owner, [cf], title="CF")
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(hl=hl, story=cf), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_story_the_owner_removed_early_is_gone_for_good(self):
        removed = make_story(self.owner, "removed.jpg", state="removed")
        hl = make_highlight(self.owner, [removed], title="Gone")
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(hl=hl, story=removed), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_blocked_owner_is_404(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.viewer)
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._share_url(), {"conversation_id": str(convo.id)}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_login_required(self):
        res = self.client.post(self._share_url(), {}, format="json")
        self.assertIn(res.status_code, (401, 403))


class HighlightSaveTests(_HlBase):
    def test_allowed_save_returns_media_info(self):
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._save_url(), {}, format="json")
        self.assertEqual(res.status_code, 200, getattr(res, "data", None))
        data = res.data["data"]
        self.assertTrue(data["url"].endswith("ended.jpg"))
        self.assertEqual(data["media_type"], "image")
        self.assertEqual(data["filename"], "ended.jpg")

    def test_save_off_is_403_with_code(self):
        self._flags(allow_save=False)
        self.client.force_authenticate(self.viewer)
        res = self.client.post(self._save_url(), {}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), STORY_SAVE_OFF)

    def test_owner_may_save_even_when_off(self):
        self._flags(allow_save=False)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self._save_url(), {}, format="json").status_code, 200)

    def test_share_off_does_not_block_save(self):
        self._flags(allow_reshare=False)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(self._save_url(), {}, format="json").status_code, 200)

    def test_story_of_another_highlight_is_404(self):
        other_story = make_story(self.owner, "other2.jpg", state="swept")
        make_highlight(self.owner, [other_story], title="Other")
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(self._save_url(story=other_story), {}, format="json").status_code, 404)

    def test_login_required(self):
        self.assertIn(self.client.post(self._save_url(), {}, format="json").status_code, (401, 403))


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class OwnerDeleteTests(APITestCase):
    def setUp(self):
        self.owner = make_user("sta_del_owner")
        self.viewer = make_user("sta_del_viewer")
        follow(self.viewer, self.owner)
        self.story = make_story(self.owner, "live.jpg")

    def _url(self, story=None):
        return reverse("story-detail", args=[(story or self.story).id])

    def test_owner_delete_is_204_and_soft(self):
        self.client.force_authenticate(self.owner)
        res = self.client.delete(self._url())
        self.assertEqual(res.status_code, 204)
        self.story.refresh_from_db()
        self.assertTrue(self.story.is_deleted)
        self.assertIsNotNone(self.story.deleted_at)
        self.assertTrue(Story.objects.filter(pk=self.story.pk).exists())  # soft delete, the row stays

    def test_someone_else_gets_404_and_nothing_changes(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.delete(self._url()).status_code, 404)
        self.story.refresh_from_db()
        self.assertFalse(self.story.is_deleted)

    def test_already_deleted_is_404(self):
        self.story.soft_delete()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(self._url()).status_code, 404)

    def test_deleted_story_is_gone_from_detail(self):
        self.client.force_authenticate(self.owner)
        self.client.delete(self._url())
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_unknown_story_is_404(self):
        import uuid
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(reverse("story-detail", args=[uuid.uuid4()])).status_code, 404)

    def test_login_required(self):
        self.assertIn(self.client.delete(self._url()).status_code, (401, 403))


class ClassStoryMentionTests(APITestCase):
    """`mention_not_in_class` - the rule is a pure function of `classroom_ids_of(target)`, so it is mocked (no campus rows)."""

    def setUp(self):
        self.owner = make_user("sta_cls_owner")
        self.member = make_user("sta_cls_member")
        self.outsider = make_user("sta_cls_outsider")

    def _check(self, target, member_of):
        with mock.patch("post.audience.classroom_ids_of", return_value=set(member_of)):
            return _check_mention_can_see(target, self.owner, Story.AUDIENCE_CLASS, 5, (), 0)

    def test_member_of_that_class_is_allowed(self):
        self.assertIsNone(self._check(self.member, {5, 9}))

    def test_member_of_another_class_only_is_refused(self):
        with self.assertRaises(StickerError) as cm:
            self._check(self.outsider, {9})
        self.assertEqual(cm.exception.code, "mention_not_in_class")
        self.assertEqual(cm.exception.index, 0)

    def test_member_of_no_class_is_refused(self):
        with self.assertRaises(StickerError) as cm:
            self._check(self.outsider, set())
        self.assertEqual(cm.exception.code, "mention_not_in_class")
