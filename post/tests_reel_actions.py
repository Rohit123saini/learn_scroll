# post/tests_reel_actions.py
# Reels v2 backend bits (the Flutter side is in lib/post/screens/reels_screen.dart):
#   * POST /post/<id>/share-link/ - records one PostShare, answers with the DEEP LINK (never the media URL)
#   * POST /post/<id>/share/      - "Send in chat": the message meta now carries shared_post_type
# Own-videos-last-tier tests live in tests_reels.py (OwnLastTierTests).
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from user_profile.models import BlockUser

from .models import Post, PostMedia, PostShare

User = get_user_model()


class ShareBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.client.force_authenticate(self.me)

    def video(self, user=None, **kw):
        kw.setdefault("post_type", "video")
        kw.setdefault("visibility", "public")
        kw.setdefault("moderation_status", "approved")
        kw.setdefault("category", "tech")
        post = Post.objects.create(user=user or self.author, content="cap", **kw)
        PostMedia.objects.bulk_create([PostMedia(
            post=post, media_type="video", duration_seconds=20, width=1080, height=1920,
            file="posts/x.mp4", file_name="x.mp4", file_size_bytes=1, mime_type="video/mp4",
        )])
        return post

    def link(self, post):
        return self.client.post(reverse("post-share-link", args=[post.id]), {}, format="json")


@override_settings(APP_WEB_BASE_URL="https://learnscroll.example/")
class ShareLinkTests(ShareBase):
    def test_returns_deep_link_for_a_video_and_never_the_media_url(self):
        post = self.video()
        r = self.link(post)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["url"], f"https://learnscroll.example/p/{post.id}?v=reel")
        self.assertNotIn("x.mp4", r.data["data"]["url"])

    def test_non_video_link_has_no_reel_hint(self):
        post = self.video(post_type="image")
        self.assertEqual(self.link(post).data["data"]["url"], f"https://learnscroll.example/p/{post.id}")

    def test_counts_one_share_per_user_and_is_idempotent(self):
        post = self.video()
        self.assertEqual(self.link(post).data["data"]["shares_count"], 1)
        self.assertEqual(self.link(post).data["data"]["shares_count"], 1)
        self.assertEqual(PostShare.objects.filter(post=post, user=self.me).count(), 1)
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        self.assertEqual(self.link(post).data["data"]["shares_count"], 2)

    def test_requires_login(self):
        post = self.video()
        self.client.force_authenticate(None)
        self.assertEqual(self.link(post).status_code, 401)

    def test_private_post_of_someone_else_is_refused_and_not_counted(self):
        post = self.video(visibility="private")
        self.assertEqual(self.link(post).status_code, 403)
        self.assertFalse(PostShare.objects.filter(post=post).exists())

    def test_blocked_author_looks_like_not_found(self):
        post = self.video()
        BlockUser.objects.create(blocker=self.author, blocked=self.me)
        self.assertEqual(self.link(post).status_code, 404)

    def test_deleted_or_archived_is_404(self):
        self.assertEqual(self.link(self.video(is_deleted=True)).status_code, 404)
        self.assertEqual(self.link(self.video(is_archived=True)).status_code, 404)

    def test_owner_can_share_their_own_private_video(self):
        post = self.video(user=self.me, visibility="private")
        self.assertEqual(self.link(post).status_code, 200)


class SendInChatTests(ShareBase):
    def _conversation(self, *users):
        from message.models import Conversation, ConversationParticipant, ConversationType

        conv = Conversation.objects.create(type=ConversationType.PRIVATE)
        for u in users:
            ConversationParticipant.objects.create(conversation=conv, user=u)
        return conv

    def test_message_meta_carries_post_id_and_type(self):
        post = self.video()
        conv = self._conversation(self.me, self.author)
        fake = mock.Mock(id="11111111-1111-1111-1111-111111111111", conversation_id=conv.id)
        with mock.patch("message.services.create_message_and_broadcast", return_value=fake) as send:
            r = self.client.post(reverse("post-share", args=[post.id]), {"conversation_id": str(conv.id)}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        meta = send.call_args.kwargs["meta"]
        self.assertEqual(meta["shared_post_id"], str(post.id))
        self.assertEqual(meta["shared_post_type"], "video")
        self.assertTrue(PostShare.objects.filter(post=post, user=self.me).exists())

    def test_not_a_participant_is_403_and_private_post_is_403(self):
        post = self.video()
        conv = self._conversation(self.author)
        r = self.client.post(reverse("post-share", args=[post.id]), {"conversation_id": str(conv.id)}, format="json")
        self.assertEqual(r.status_code, 403)
        private = self.video(visibility="private")
        conv2 = self._conversation(self.me, self.author)
        r = self.client.post(reverse("post-share", args=[private.id]), {"conversation_id": str(conv2.id)}, format="json")
        self.assertEqual(r.status_code, 403)
