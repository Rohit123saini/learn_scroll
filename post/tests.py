# post/tests.py
#
# Checklist item 64: "Tests likho — post create/delete, like idempotency,
# comment threading, story expiry." See post_app.md §11/§16.
#
# Merged from two independent drafts of this file plus a bug fix found
# while reconciling them: `PostSaveToggleAPIView` (views.py) returns 201
# on the *save* branch and 200 on the *unsave* branch (it's create-or-
# delete, not a single idempotent toggle status code) — an earlier draft
# asserted 200 on both, which would have failed against the real view.
#
# Uses reverse() with the url `name=` values from urls.py rather than
# hardcoded paths, so these don't silently stop testing anything if the
# root urls.py ever mounts this app under a different prefix than
# `post/`.
#
# Follow (user_profile) and the real AUTH_USER_MODEL fields
# (posts_count, is_private, profile_photo) are external dependencies —
# these tests exercise this app's own models/views; HomeFeedView's
# following-based branch lives closer to user_profile's own test surface.
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Post, PostComment, PostLike, PostSave, Story
from .tasks import expire_old_stories, hard_delete_ancient_stories

User = get_user_model()


def make_user(username, **extra):
    """Adjust the field names here if your User.objects.create_user()
    signature differs (e.g. requires phone number instead of email)."""
    return User.objects.create_user(username=username, password="testpass123", **extra)


class PostCreateTests(APITestCase):
    def setUp(self):
        self.user = make_user("author1")
        self.client.force_authenticate(self.user)
        self.url = reverse("post-create")

    def test_create_text_post(self):
        resp = self.client.post(
            self.url, {"content": "hello world", "category": "tech", "post_type": "text"}, format="multipart"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Post.objects.count(), 1)
        post = Post.objects.first()
        self.assertEqual(post.user, self.user)
        self.assertEqual(post.content, "hello world")

    def test_text_post_without_content_is_rejected(self):
        resp = self.client.post(self.url, {"post_type": "text", "category": "general"}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Post.objects.count(), 0)

    def test_hashtags_are_auto_extracted_from_content(self):
        # Exercises the fix documented in services.py's module docstring:
        # this must go through the plain Post.hashtags JSONField, NOT a
        # removed attach_hashtags()/Hashtag-model path.
        resp = self.client.post(
            self.url,
            {"content": "loving #django and #drf today", "post_type": "text", "category": "tech"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        post = Post.objects.first()
        self.assertEqual(set(post.hashtags), {"django", "drf"})

    def test_posts_count_incremented_exactly_once_by_signal(self):
        self.user.refresh_from_db()
        before = self.user.posts_count
        self.client.post(self.url, {"content": "hi", "category": "general", "post_type": "text"}, format="multipart")
        self.user.refresh_from_db()
        self.assertEqual(self.user.posts_count, before + 1)


class PostDeleteTests(APITestCase):
    def setUp(self):
        self.author = make_user("author2")
        self.other = make_user("stranger1")
        self.post = Post.objects.create(user=self.author, content="to be deleted", category="general")
        self.author.refresh_from_db()

    def test_author_can_delete_own_post(self):
        self.client.force_authenticate(self.author)
        before = self.author.posts_count
        url = reverse("post-delete", args=[self.post.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
        self.assertIsNotNone(self.post.deleted_at)
        self.author.refresh_from_db()
        self.assertEqual(self.author.posts_count, before - 1)

    def test_non_author_non_staff_cannot_delete(self):
        self.client.force_authenticate(self.other)
        url = reverse("post-delete", args=[self.post.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)

    def test_staff_can_delete_others_post(self):
        staff = make_user("staffuser")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        self.client.force_authenticate(staff)
        url = reverse("post-delete", args=[self.post.id])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_full_cycle_create_then_delete_via_api_nets_zero(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post(
            reverse("post-create"), {"content": "x", "category": "general", "post_type": "text"}, format="multipart"
        )
        post_id = create_resp.data["data"]["id"]
        self.author.refresh_from_db()
        after_create = self.author.posts_count

        del_resp = self.client.delete(reverse("post-delete", args=[post_id]))
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.author.refresh_from_db()
        self.assertEqual(self.author.posts_count, after_create - 1)


class ReactionIdempotencyTests(APITestCase):
    """Like/unlike/change-reaction toggle on a Post. Reacting twice with
    the same type unlikes; reacting with a different type changes it;
    the counter never drifts from the actual row count."""

    def setUp(self):
        self.author = make_user("author3")
        self.liker = make_user("liker1")
        self.post = Post.objects.create(user=self.author, content="react to me", category="general")
        self.client.force_authenticate(self.liker)
        self.url = reverse("post-reaction", args=[self.post.id])

    def test_like_then_same_reaction_again_unlikes_then_relikes(self):
        r1 = self.client.post(self.url, {"reaction": "like"}, format="json")
        r2 = self.client.post(self.url, {"reaction": "like"}, format="json")
        r3 = self.client.post(self.url, {"reaction": "like"}, format="json")

        self.assertEqual(r1.data["status"], "liked")
        self.assertEqual(r2.data["status"], "unliked")
        self.assertEqual(r3.data["status"], "liked")
        self.assertEqual(PostLike.objects.filter(post=self.post, user=self.liker).count(), 1)
        self.post.refresh_from_db()
        self.assertEqual(self.post.like_count, 1)
        self.assertEqual(self.post.likes_count, 1)

    def test_changing_reaction_type_does_not_duplicate_row(self):
        self.client.post(self.url, {"reaction": "like"}, format="json")
        resp = self.client.post(self.url, {"reaction": "wrong"}, format="json")
        self.assertEqual(resp.data["status"], "changed")
        self.assertEqual(PostLike.objects.filter(post=self.post, user=self.liker).count(), 1)
        self.post.refresh_from_db()
        self.assertEqual(self.post.wrong_count, 1)
        self.assertEqual(self.post.like_count, 0)
        self.assertEqual(self.post.likes_count, 1)

    def test_counter_always_matches_actual_row_count(self):
        for _ in range(3):
            self.client.post(self.url, {"reaction": "like"}, format="json")
        self.post.refresh_from_db()
        actual_rows = PostLike.objects.filter(post=self.post, user=self.liker).count()
        self.assertEqual(self.post.likes_count, actual_rows)


class CommentThreadingTests(APITestCase):
    def setUp(self):
        self.author = make_user("author4")
        self.commenter = make_user("commenter1")
        self.post = Post.objects.create(user=self.author, content="comment on me", category="general")
        self.client.force_authenticate(self.commenter)
        self.create_url = reverse("comment-create")

    def test_top_level_comment_increments_post_comments_count(self):
        resp = self.client.post(self.create_url, {"post_id": str(self.post.id), "content": "nice post"}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 1)
        self.assertIsNone(resp.data["parent"])

    def test_reply_increments_parent_replies_count_not_post_comments_count(self):
        parent_resp = self.client.post(self.create_url, {"post_id": str(self.post.id), "content": "top level"}, format="multipart")
        parent_id = parent_resp.data["id"]

        reply_author = make_user("replier1")
        self.client.force_authenticate(reply_author)
        reply_resp = self.client.post(self.create_url, {"parent_id": parent_id, "content": "a reply"}, format="multipart")
        self.assertEqual(reply_resp.status_code, status.HTTP_201_CREATED, reply_resp.data)

        parent = PostComment.objects.get(id=parent_id)
        self.post.refresh_from_db()
        self.assertEqual(parent.replies_count, 1)
        self.assertEqual(self.post.comments_count, 1)  # unchanged by the reply

    def test_deleting_top_level_comment_decrements_post_comments_count(self):
        create_resp = self.client.post(self.create_url, {"post_id": str(self.post.id), "content": "temp"}, format="multipart")
        comment_id = create_resp.data["id"]
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 1)

        del_resp = self.client.delete(reverse("comment-delete", args=[comment_id]))
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)
        comment = PostComment.objects.get(id=comment_id)
        self.post.refresh_from_db()
        self.assertTrue(comment.is_deleted)
        self.assertEqual(self.post.comments_count, 0)

    def test_replies_endpoint_returns_only_that_parents_replies(self):
        parent = PostComment.objects.create(post=self.post, user=self.author, content="top")
        other_parent = PostComment.objects.create(post=self.post, user=self.author, content="other top")
        PostComment.objects.create(post=self.post, user=self.commenter, content="reply 1", parent=parent)
        PostComment.objects.create(post=self.post, user=self.commenter, content="reply to other", parent=other_parent)

        resp = self.client.get(reverse("comment-replies", args=[parent.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["content"], "reply 1")

    def test_comment_on_disabled_post_is_rejected(self):
        Post.objects.filter(pk=self.post.pk).update(is_comments_disabled=True)
        resp = self.client.post(self.create_url, {"post_id": str(self.post.id), "content": "nope"}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class SavePostTests(APITestCase):
    def setUp(self):
        self.author = make_user("author5")
        self.saver = make_user("saver1")
        self.post = Post.objects.create(user=self.author, content="save me", category="general")
        self.client.force_authenticate(self.saver)
        self.url = reverse("post-save-toggle", args=[self.post.id])

    def test_save_then_unsave_toggle(self):
        # PostSaveToggleAPIView (views.py) is create-or-delete, not a
        # single idempotent status: 201 on save, 200 on unsave.
        resp1 = self.client.post(self.url)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp1.data["is_saved"])
        self.assertTrue(PostSave.objects.filter(post=self.post, user=self.saver).exists())
        self.post.refresh_from_db()
        self.assertEqual(self.post.saves_count, 1)

        resp2 = self.client.post(self.url)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertFalse(resp2.data["is_saved"])
        self.assertFalse(PostSave.objects.filter(post=self.post, user=self.saver).exists())
        self.post.refresh_from_db()
        self.assertEqual(self.post.saves_count, 0)

    def test_saved_list_only_shows_current_users_saves(self):
        self.client.post(self.url)
        other_user = make_user("saver2")
        self.client.force_authenticate(other_user)

        resp = self.client.get(reverse("saved-posts-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 0)


class HashtagDiscoveryTests(APITestCase):
    """Covers the checklist's "Hashtag" responsibility — this used to be
    write-only metadata on Post.hashtags with no way to query it back
    out. Also covers the case-normalization fix in
    PostCreateSerializer.create() (serializers.py): without it,
    "#Django" and "#django" would be two different strings and this
    lookup would silently miss posts."""

    def setUp(self):
        self.author = make_user("tagger1")
        self.other_author = make_user("tagger2")
        self.client.force_authenticate(self.author)

    def test_hashtags_normalized_to_lowercase_on_create(self):
        resp = self.client.post(
            reverse("post-create"),
            {"content": "post about #Django and #DRF", "post_type": "text", "category": "tech"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        post = Post.objects.first()
        self.assertEqual(set(post.hashtags), {"django", "drf"})

    def test_hashtag_endpoint_finds_posts_regardless_of_original_casing(self):
        self.client.post(
            reverse("post-create"),
            {"content": "loving #Flutter today", "post_type": "text", "category": "tech"},
            format="multipart",
        )
        resp = self.client.get(reverse("hashtag-posts", args=["FLUTTER"]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(rows), 1)

    def test_hashtag_endpoint_excludes_private_and_deleted_posts(self):
        self.client.post(
            reverse("post-create"),
            {"content": "#secret stuff", "post_type": "text", "category": "tech", "visibility": "private"},
            format="multipart",
        )
        resp = self.client.get(reverse("hashtag-posts", args=["secret"]))
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(rows), 0)

    def test_trending_hashtags_counts_across_authors(self):
        self.client.post(
            reverse("post-create"),
            {"content": "#python is great", "post_type": "text", "category": "tech"},
            format="multipart",
        )
        self.client.force_authenticate(self.other_author)
        self.client.post(
            reverse("post-create"),
            {"content": "#python again", "post_type": "text", "category": "tech"},
            format="multipart",
        )

        resp = self.client.get(reverse("trending-hashtags"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_tag = {row["hashtag"]: row["count"] for row in resp.data["results"]}
        self.assertEqual(by_tag.get("python"), 2)


class ExploreFeedTests(APITestCase):
    """Covers the checklist's "Explore-content" responsibility — a
    discovery surface independent of who the requester follows, distinct
    from HomeFeedView's public-post fallback (which only ever appears
    *inside* the following feed when it's otherwise empty)."""

    def setUp(self):
        self.viewer = make_user("explorer1")
        self.followed_author = make_user("followed1")
        self.stranger_author = make_user("stranger2")
        self.client.force_authenticate(self.viewer)

    def test_explore_excludes_own_and_followed_posts(self):
        from user_profile.models import Follow

        Follow.objects.create(follower=self.viewer, following=self.followed_author, status=Follow.Status.ACCEPTED)
        Post.objects.create(user=self.viewer, content="my own post", category="general", visibility="public")
        Post.objects.create(user=self.followed_author, content="from someone I follow", category="general", visibility="public")
        stranger_post = Post.objects.create(user=self.stranger_author, content="from a stranger", category="general", visibility="public")

        resp = self.client.get(reverse("post-explore"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        ids = [row["id"] for row in rows]
        self.assertEqual(ids, [str(stranger_post.id)])

    def test_explore_excludes_private_posts(self):
        Post.objects.create(user=self.stranger_author, content="private stuff", category="general", visibility="private")
        resp = self.client.get(reverse("post-explore"))
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(rows), 0)

    def test_explore_category_filter(self):
        Post.objects.create(user=self.stranger_author, content="tech post", category="tech", visibility="public")
        Post.objects.create(user=self.stranger_author, content="art post", category="art", visibility="public")

        resp = self.client.get(reverse("post-explore"), {"category": "tech"})
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "tech")


class StoryExpiryTests(APITestCase):
    def setUp(self):
        self.user = make_user("storyuser1")

    def _make_story(self, expires_at, is_deleted=False, deleted_at=None):
        story = Story.objects.create(user=self.user, media="stories/2026/01/01/test.jpg", media_type="image")
        Story.objects.filter(id=story.id).update(expires_at=expires_at, is_deleted=is_deleted, deleted_at=deleted_at)
        story.refresh_from_db()
        return story

    def test_is_expired_property(self):
        fresh = self._make_story(timezone.now() + timedelta(hours=1))
        stale = self._make_story(timezone.now() - timedelta(minutes=1))
        self.assertFalse(fresh.is_expired)
        self.assertTrue(stale.is_expired)

    def test_expire_old_stories_task_soft_deletes_only_expired(self):
        expired = self._make_story(timezone.now() - timedelta(hours=1))
        active = self._make_story(timezone.now() + timedelta(hours=1))

        count = expire_old_stories()

        expired.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertTrue(expired.is_deleted)
        self.assertIsNotNone(expired.deleted_at)
        self.assertFalse(active.is_deleted)

    def test_hard_delete_ancient_stories_purges_old_soft_deletes_only(self):
        old_deleted = self._make_story(
            timezone.now() - timedelta(days=40), is_deleted=True, deleted_at=timezone.now() - timedelta(days=31)
        )
        recent_deleted = self._make_story(
            timezone.now() - timedelta(days=2), is_deleted=True, deleted_at=timezone.now() - timedelta(days=1)
        )

        count = hard_delete_ancient_stories(days=30)

        self.assertEqual(count, 1)
        self.assertFalse(Story.objects.filter(id=old_deleted.id).exists())
        self.assertTrue(Story.objects.filter(id=recent_deleted.id).exists())

    def test_expired_story_excluded_from_list(self):
        self.client.force_authenticate(self.user)
        self._make_story(timezone.now() - timedelta(hours=1))  # expired, still is_deleted=False
        active = self._make_story(timezone.now() + timedelta(hours=1))

        resp = self.client.get(reverse("story-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        ids = [row["id"] for row in rows]
        self.assertIn(str(active.id), ids)
        self.assertEqual(len(rows), 1)

    def test_story_view_dedup_per_viewer(self):
        story = self._make_story(timezone.now() + timedelta(hours=1))
        viewer = make_user("viewer1")
        self.client.force_authenticate(viewer)
        url = reverse("story-view", args=[story.id])

        r1 = self.client.post(url)
        r2 = self.client.post(url)
        self.assertEqual(r1.data["views_count"], 1)
        self.assertEqual(r2.data["views_count"], 1)  # deduped, not 2