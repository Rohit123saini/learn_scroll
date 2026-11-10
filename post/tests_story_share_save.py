"""
X1 - Story allow_reshare / allow_save are now ENFORCED (they were only stored before).

NOT RUN when written (read-only session). Run: python manage.py test post.tests_story_share_save

Covers: the pure helpers (owner always, others follow the flag, anonymous never), POST /post/stories/<id>/share/
(403 `story_reshare_off`, 201 + a `story_reply` message with meta.story_share, 404 for a story the viewer may not see,
404 expired, 403 not a participant, owner always allowed), POST /post/stories/<id>/save/ (403 `story_save_off`,
200 + url, 404 hidden / expired) and the per-viewer `can_reshare` / `can_save` flags in the story detail payload.
"""
from datetime import timedelta
from types import SimpleNamespace

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from message.models import Conversation, Message, MessageType

from .models import Story
from .story_visibility import (
    STORY_RESHARE_OFF, STORY_SAVE_OFF, can_reshare_story, can_save_story,
)
from .tests_story_stickers import _TMP_MEDIA, follow, make_story, make_user


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class _Base(APITestCase):
    def setUp(self):
        self.owner = make_user("xs_owner")
        self.viewer = make_user("xs_viewer")
        self.other = make_user("xs_other")
        follow(self.viewer, self.owner)
        self.story = make_story(self.owner)  # everyone / allow_reshare + allow_save default True

    def _off(self, **flags):
        for k, v in flags.items():
            setattr(self.story, k, v)
        self.story.save(update_fields=list(flags))

    def _chat(self, a, b):
        convo, _ = Conversation.get_or_create_private(a, b)
        return convo


class VisibilityHelperTests(_Base):
    def test_owner_always_others_follow_flag(self):
        self._off(allow_reshare=False, allow_save=False)
        self.assertTrue(can_reshare_story(self.story, self.owner))
        self.assertTrue(can_save_story(self.story, self.owner))
        self.assertFalse(can_reshare_story(self.story, self.viewer))
        self.assertFalse(can_save_story(self.story, self.viewer))
        self._off(allow_reshare=True, allow_save=True)
        self.assertTrue(can_reshare_story(self.story, self.viewer))
        self.assertTrue(can_save_story(self.story, self.viewer))

    def test_anonymous_never(self):
        anon = SimpleNamespace(is_authenticated=False, id=None)
        self.assertFalse(can_reshare_story(self.story, anon))
        self.assertFalse(can_save_story(self.story, anon))
        self.assertFalse(can_reshare_story(self.story, None))


class ShareEndpointTests(_Base):
    def _share(self, convo_id, story=None):
        return self.client.post(
            reverse("story-share", args=[(story or self.story).id]), {"conversation_id": str(convo_id)}, format="json",
        )

    def test_reshare_off_is_403_with_code(self):
        self._off(allow_reshare=False)
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self._share(convo.id)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), STORY_RESHARE_OFF)
        self.assertFalse(Message.objects.filter(conversation=convo).exists())

    def test_allowed_share_creates_story_reply_message_with_share_meta(self):
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        res = self._share(convo.id)
        self.assertEqual(res.status_code, 201, res.data)
        msg = Message.objects.get(conversation=convo)
        self.assertEqual(msg.type, MessageType.STORY_REPLY)
        self.assertEqual(msg.story_id, self.story.id)
        self.assertTrue(msg.meta.get("story_share"))
        self.assertEqual(msg.meta.get("shared_story_id"), str(self.story.id))

    def test_owner_can_share_even_when_off(self):
        self._off(allow_reshare=False)
        convo = self._chat(self.owner, self.other)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self._share(convo.id).status_code, 201)

    def test_not_a_participant_is_403(self):
        convo = self._chat(self.owner, self.other)  # viewer is not in it
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._share(convo.id).status_code, 403)

    def test_unknown_conversation_is_404(self):
        import uuid
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._share(uuid.uuid4()).status_code, 404)

    def test_story_the_viewer_may_not_see_is_404_not_403(self):
        s = make_story(self.owner, Story.AUDIENCE_CLOSE_FRIENDS)
        s.allow_reshare = False
        s.save(update_fields=["allow_reshare"])
        convo = self._chat(self.other, self.viewer)
        self.client.force_authenticate(self.other)  # not a close friend
        self.assertEqual(self._share(convo.id, story=s).status_code, 404)

    def test_expired_story_is_404(self):
        self._off(expires_at=timezone.now() - timedelta(minutes=1))
        convo = self._chat(self.viewer, self.other)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._share(convo.id).status_code, 404)

    def test_missing_conversation_id_is_400(self):
        self.client.force_authenticate(self.viewer)
        res = self.client.post(reverse("story-share", args=[self.story.id]), {}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_login_required(self):
        res = self.client.post(reverse("story-share", args=[self.story.id]), {}, format="json")
        self.assertIn(res.status_code, (401, 403))


class SaveEndpointTests(_Base):
    def _save(self, story=None):
        return self.client.post(reverse("story-save", args=[(story or self.story).id]), {}, format="json")

    def test_save_off_is_403_with_code(self):
        self._off(allow_save=False)
        self.client.force_authenticate(self.viewer)
        res = self._save()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), STORY_SAVE_OFF)

    def test_allowed_save_returns_media_info(self):
        self.client.force_authenticate(self.viewer)
        res = self._save()
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data["data"]
        self.assertTrue(data["url"].endswith("test.jpg"))
        self.assertEqual(data["media_type"], "image")
        self.assertEqual(data["filename"], "test.jpg")

    def test_owner_can_save_even_when_off(self):
        self._off(allow_save=False)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self._save().status_code, 200)

    def test_reshare_off_does_not_block_save_and_vice_versa(self):
        self._off(allow_reshare=False)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._save().status_code, 200)

    def test_hidden_story_is_404(self):
        s = make_story(self.owner, Story.AUDIENCE_CLOSE_FRIENDS)
        self.client.force_authenticate(self.other)
        self.assertEqual(self._save(story=s).status_code, 404)

    def test_expired_story_is_404(self):
        self._off(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._save().status_code, 404)


class DetailFlagsTests(_Base):
    def test_flags_per_viewer(self):
        self._off(allow_reshare=False, allow_save=False)
        self.client.force_authenticate(self.viewer)
        data = self.client.get(reverse("story-detail", args=[self.story.id])).data
        self.assertFalse(data["can_reshare"])
        self.assertFalse(data["can_save"])
        self.client.force_authenticate(self.owner)
        data = self.client.get(reverse("story-detail", args=[self.story.id])).data
        self.assertTrue(data["can_reshare"])
        self.assertTrue(data["can_save"])

    def test_flags_default_true(self):
        self.client.force_authenticate(self.viewer)
        data = self.client.get(reverse("story-detail", args=[self.story.id])).data
        self.assertTrue(data["can_reshare"])
        self.assertTrue(data["can_save"])
