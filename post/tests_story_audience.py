"""
F3-AUD (story audience / replies / reshare + save flags / post card / expiry + archive) tests.

NOT RUN when written (read-only session). Run: python manage.py test post.tests_story_audience

Covers: audience followers / close_friends / everyone, "hide from" winning over every audience, reply_setting
(everyone / followers / off) on react + reply + the `can_reply` flag, allow_reshare / allow_save round-trip, the
`post_card` sticker (create rules + per-viewer preview + deleted post), the 24h default expiry, the expire_old_stories
sweep and the archive endpoint (expired stories stay pickable for highlights).
"""
import json
import shutil
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import CloseFriend, Post, Story, StoryHiddenFrom, StorySticker
from .story_visibility import can_reply_to_story, can_view_story
from .tests_story_stickers import _TMP_MEDIA, follow, image_upload, make_story, make_user, rows_of


def make_post(user, **extra):
    data = {"user": user, "content": "Newton laws revision", "post_type": "text", "visibility": "public"}
    data.update(extra)
    return Post.objects.create(**data)


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class _Base(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def _create(self, stickers=None, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        if stickers is not None:
            payload["stickers"] = json.dumps(stickers)
        return self.client.post(reverse("story-create"), payload, format="multipart")


class StoryAudienceVisibilityTests(_Base):
    def setUp(self):
        self.owner = make_user("aud_owner")
        self.follower = make_user("aud_follower")
        self.friend = make_user("aud_friend")
        self.stranger = make_user("aud_stranger")
        follow(self.follower, self.owner)
        follow(self.friend, self.owner)
        CloseFriend.objects.create(owner=self.owner, friend=self.friend)

    def test_followers_audience(self):
        s = make_story(self.owner, Story.AUDIENCE_FOLLOWERS)
        self.assertTrue(can_view_story(s, self.owner))
        self.assertTrue(can_view_story(s, self.follower))
        self.assertFalse(can_view_story(s, self.stranger))

    def test_close_friends_audience(self):
        s = make_story(self.owner, Story.AUDIENCE_CLOSE_FRIENDS)
        self.assertTrue(can_view_story(s, self.friend))
        self.assertFalse(can_view_story(s, self.follower))
        self.assertFalse(can_view_story(s, self.stranger))

    def test_everyone_audience(self):
        s = make_story(self.owner, Story.AUDIENCE_EVERYONE)
        self.assertTrue(can_view_story(s, self.stranger))

    def test_class_story_needs_a_classroom(self):
        self.client.force_authenticate(self.owner)
        res = self._create(audience=Story.AUDIENCE_CLASS)
        self.assertEqual(res.status_code, 400)
        self.assertIn("audience_classroom_id", res.data)

    def test_hidden_from_wins_over_audience(self):
        self.client.force_authenticate(self.owner)
        res = self._create(audience=Story.AUDIENCE_FOLLOWERS, hide_from=json.dumps([self.follower.id]))
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertTrue(StoryHiddenFrom.objects.filter(story=story, user=self.follower).exists())
        self.assertFalse(can_view_story(story, self.follower))
        self.assertTrue(can_view_story(story, self.owner))
        # 404 (not 403) on every read path - existence is not leaked.
        self.client.force_authenticate(self.follower)
        self.assertEqual(self.client.get(reverse("story-detail", args=[story.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("story-view", args=[story.id])).status_code, 404)

    def test_hide_from_ignores_owner_and_rejects_bad_payload(self):
        self.client.force_authenticate(self.owner)
        res = self._create(hide_from="not-a-list")
        self.assertEqual(res.status_code, 400)
        res = self._create(hide_from=json.dumps([self.owner.id, self.stranger.id]))
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(list(story.hidden_from.values_list("user_id", flat=True)), [self.stranger.id])

    def test_non_class_story_drops_classroom_id(self):
        self.client.force_authenticate(self.owner)
        res = self._create(audience=Story.AUDIENCE_FOLLOWERS, audience_classroom_id="7")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(Story.objects.get(id=res.data["id"]).audience_classroom_id)

    def test_tray_hides_restricted_stories(self):
        make_story(self.owner, Story.AUDIENCE_CLOSE_FRIENDS)
        self.client.force_authenticate(self.follower)
        res = self.client.get(reverse("story-list"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(rows_of(res)), 0)
        self.client.force_authenticate(self.friend)
        self.assertEqual(len(rows_of(self.client.get(reverse("story-list")))), 1)


class StoryReplySettingTests(_Base):
    def setUp(self):
        self.owner = make_user("rep_owner")
        self.follower = make_user("rep_follower")
        self.stranger = make_user("rep_stranger")
        follow(self.follower, self.owner)

    def _story(self, setting):
        s = make_story(self.owner)
        s.reply_setting = setting
        s.save(update_fields=["reply_setting"])
        return s

    def test_rule_function(self):
        everyone = self._story(Story.REPLY_EVERYONE)
        followers = self._story(Story.REPLY_FOLLOWERS)
        off = self._story(Story.REPLY_OFF)
        self.assertTrue(can_reply_to_story(everyone, self.stranger))
        self.assertTrue(can_reply_to_story(followers, self.follower))
        self.assertFalse(can_reply_to_story(followers, self.stranger))
        self.assertFalse(can_reply_to_story(off, self.follower))
        self.assertTrue(can_reply_to_story(off, self.owner))

    def test_off_blocks_reply_and_react(self):
        s = self._story(Story.REPLY_OFF)
        self.client.force_authenticate(self.follower)
        res = self.client.post(reverse("story-reply", args=[s.id]), {"text": "nice"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), "story_replies_off")
        res = self.client.post(reverse("story-react", args=[s.id]), {"emoji": "🔥"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data.get("code"), "story_replies_off")

    def test_followers_only_blocks_non_follower(self):
        s = self._story(Story.REPLY_FOLLOWERS)
        self.client.force_authenticate(self.stranger)
        res = self.client.post(reverse("story-react", args=[s.id]), {"emoji": "🔥"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.follower)
        res = self.client.post(reverse("story-react", args=[s.id]), {"emoji": "🔥"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_can_reply_flag_in_detail(self):
        s = self._story(Story.REPLY_OFF)
        self.client.force_authenticate(self.follower)
        self.assertFalse(self.client.get(reverse("story-detail", args=[s.id])).data["can_reply"])
        self.client.force_authenticate(self.owner)
        self.assertTrue(self.client.get(reverse("story-detail", args=[s.id])).data["can_reply"])

    def test_invalid_reply_setting_is_400(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self._create(reply_setting="nobody").status_code, 400)

    def test_reshare_and_save_flags_round_trip(self):
        self.client.force_authenticate(self.owner)
        res = self._create(reply_setting=Story.REPLY_FOLLOWERS, allow_reshare="false", allow_save="false")
        self.assertEqual(res.status_code, 201, res.data)
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(story.reply_setting, Story.REPLY_FOLLOWERS)
        self.assertFalse(story.allow_reshare)
        self.assertFalse(story.allow_save)
        self.client.force_authenticate(self.follower)
        data = self.client.get(reverse("story-detail", args=[story.id])).data
        self.assertFalse(data["allow_reshare"])
        self.assertFalse(data["allow_save"])

    def test_defaults_allow_reshare_and_save(self):
        self.client.force_authenticate(self.owner)
        res = self._create()
        story = Story.objects.get(id=res.data["id"])
        self.assertEqual(story.reply_setting, Story.REPLY_EVERYONE)
        self.assertTrue(story.allow_reshare)
        self.assertTrue(story.allow_save)


class StoryPostCardTests(_Base):
    def setUp(self):
        self.owner = make_user("pc_owner")
        self.other = make_user("pc_other")
        self.viewer = make_user("pc_viewer")
        follow(self.viewer, self.owner)

    def _card(self, post, kind="post"):
        return {"kind": "post_card", "data": {"post_id": str(post.id), "post_kind": kind}, "x": 0.5, "y": 0.5}

    def test_share_own_post_creates_card(self):
        post = make_post(self.owner)
        self.client.force_authenticate(self.owner)
        res = self._create(stickers=[self._card(post)])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(StorySticker.objects.filter(story_id=res.data["id"], kind="post_card").exists())

    def test_cannot_share_unknown_or_deleted_post(self):
        post = make_post(self.owner, is_deleted=True)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self._create(stickers=[self._card(post)]).status_code, 400)
        bad = {"kind": "post_card", "data": {"post_id": "not-a-uuid"}, "x": 0.5, "y": 0.5}
        self.assertEqual(self._create(stickers=[bad]).status_code, 400)

    def test_cannot_reshare_non_public_post_of_someone_else(self):
        post = make_post(self.owner, visibility="followers")
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self._create(stickers=[self._card(post)]).status_code, 400)

    def test_can_share_public_post_of_someone_else(self):
        post = make_post(self.owner)
        self.client.force_authenticate(self.other)
        self.assertEqual(self._create(stickers=[self._card(post)]).status_code, 201)

    def test_preview_is_built_per_viewer_and_goes_unavailable_after_delete(self):
        post = make_post(self.owner)
        self.client.force_authenticate(self.owner)
        story_id = self._create(stickers=[self._card(post)]).data["id"]

        def card_data():
            stickers = self.client.get(reverse("story-detail", args=[story_id])).data["stickers"]
            return next(s for s in stickers if s["kind"] == "post_card")["data"]

        self.client.force_authenticate(self.viewer)
        self.assertTrue(card_data()["available"])

        post.is_deleted = True
        post.save(update_fields=["is_deleted"])
        data = card_data()
        self.assertFalse(data["available"])
        self.assertFalse(data["thumb"])
        self.assertEqual(data["title"], "")


class StoryExpiryArchiveTests(_Base):
    def setUp(self):
        self.owner = make_user("exp_owner")
        self.viewer = make_user("exp_viewer")
        follow(self.viewer, self.owner)

    def test_default_expiry_is_24_hours(self):
        s = make_story(self.owner)
        delta = s.expires_at - timezone.now()
        self.assertGreater(delta, timedelta(hours=23, minutes=55))
        self.assertLessEqual(delta, timedelta(hours=24))

    def test_expired_story_is_gone_for_viewers_but_stays_in_owner_archive(self):
        s = make_story(self.owner)
        Story.objects.filter(id=s.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        from .tasks import expire_old_stories

        self.assertEqual(expire_old_stories(), 1)
        s.refresh_from_db()
        self.assertTrue(s.is_deleted)

        self.client.force_authenticate(self.viewer)
        self.assertEqual(len(rows_of(self.client.get(reverse("story-list")))), 0)
        self.assertEqual(self.client.get(reverse("story-detail", args=[s.id])).status_code, 404)

        self.client.force_authenticate(self.owner)
        ids = [str(r["id"]) for r in rows_of(self.client.get(reverse("story-archive")))]
        self.assertIn(str(s.id), ids)

    def test_owner_deleted_story_is_not_in_archive(self):
        s = make_story(self.owner)
        s.soft_delete()  # removed BEFORE it expired -> gone for good
        self.client.force_authenticate(self.owner)
        ids = [str(r["id"]) for r in rows_of(self.client.get(reverse("story-archive")))]
        self.assertNotIn(str(s.id), ids)

    def test_archive_is_owner_only(self):
        s = make_story(self.owner)
        self.client.force_authenticate(self.viewer)
        ids = [str(r["id"]) for r in rows_of(self.client.get(reverse("story-archive")))]
        self.assertNotIn(str(s.id), ids)


class StoryClassMentionCandidatesTests(_Base):
    """X5 (4): the mention picker of a Class story lists only the members of that class.
    NOT RUN when written. Run: python manage.py test post.tests_story_audience.StoryClassMentionCandidatesTests"""

    def setUp(self):
        from unittest import mock
        self._mock = mock
        self.teacher = make_user("cm_teacher")
        self.member = make_user("cm_member")
        self.outsider = make_user("cm_outsider")
        # the outsider is a follower (would be listed for `everyone`) but is NOT in the class
        follow(self.outsider, self.teacher)
        follow(self.member, self.teacher)
        self.client.force_authenticate(self.teacher)

    def _ids(self, **params):
        res = self.client.get(reverse("story-mention-candidates"), params)
        self.assertEqual(res.status_code, 200, getattr(res, "data", None))
        return {r["id"] for r in rows_of(res)}

    def test_class_audience_lists_only_class_members(self):
        with self._mock.patch("post.audience.classroom_member_ids", return_value={self.member.id}) as fn:
            ids = self._ids(audience="class", classroom_id="5")
        fn.assert_called_once_with("5")
        self.assertEqual(ids, {self.member.id})

    def test_class_audience_search_is_also_narrowed(self):
        with self._mock.patch("post.audience.classroom_member_ids", return_value={self.member.id}):
            ids = self._ids(audience="class", classroom_id="5", q="cm_")
        self.assertEqual(ids, {self.member.id})  # `cm_outsider` matches q but is not in the class

    def test_class_audience_without_classroom_is_empty_not_everybody(self):
        self.assertEqual(self._ids(audience="class"), set())
        self.assertEqual(self._ids(audience="class", classroom_id="abc"), set())

    def test_everyone_audience_is_unchanged(self):
        self.assertEqual(self._ids(audience="everyone"), {self.member.id, self.outsider.id})

    def test_member_helper_rejects_bad_id(self):
        from .audience import classroom_member_ids
        self.assertEqual(classroom_member_ids("abc"), set())
        self.assertEqual(classroom_member_ids(None), set())
