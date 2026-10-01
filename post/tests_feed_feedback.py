# post/tests_feed_feedback.py
# Feed feedback controls - PART 1: "Not interested" (PostHide) and "Mute this
# account" (MutedAccount) + their effect on Home / Explore / Hashtag feeds.
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from unittest import skipUnless

from user_profile.models import Follow

from .models import MutedAccount, Post, PostHide
from .services import exclude_hidden_and_muted

User = get_user_model()


def make_post(user, content, **kw):
    kw.setdefault("visibility", "public")
    kw.setdefault("moderation_status", "approved")
    kw.setdefault("post_type", "text")
    kw.setdefault("category", "tech")
    return Post.objects.create(user=user, content=content, **kw)


class FeedbackBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)

    def feed_contents(self, name="home-feed", **params):
        """Walk every page of a feed, return the set of post contents."""
        params.setdefault("page_size", 50)
        r = self.client.get(reverse(name), params)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        out = {x["content"] for x in r.data["results"]}
        while r.data.get("next"):
            r = self.client.get(r.data["next"])
            self.assertEqual(r.status_code, 200)
            out |= {x["content"] for x in r.data["results"]}
        return out


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username="a", password="x")
        self.b = User.objects.create_user(username="b", password="x")
        self.post = make_post(self.b, "p")

    def test_post_hide_is_unique_per_user_and_post(self):
        PostHide.objects.create(user=self.a, post=self.post)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PostHide.objects.create(user=self.a, post=self.post)

    def test_muted_account_unique_and_no_self_mute(self):
        MutedAccount.objects.create(user=self.a, muted_user=self.b)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MutedAccount.objects.create(user=self.a, muted_user=self.b)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MutedAccount.objects.create(user=self.a, muted_user=self.a)

    def test_deleting_post_or_user_cascades(self):
        PostHide.objects.create(user=self.a, post=self.post)
        MutedAccount.objects.create(user=self.a, muted_user=self.b)
        self.b.delete()
        self.assertEqual(PostHide.objects.count(), 0)
        self.assertEqual(MutedAccount.objects.count(), 0)


class ExcludeHelperTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.other = User.objects.create_user(username="other", password="x")

    def test_hidden_post_muted_author_and_reposts(self):
        keep = make_post(self.author, "keep")
        hidden = make_post(self.author, "hidden")
        repost_of_hidden = make_post(self.other, "rp-hidden", original_post=hidden)
        muted_post = make_post(self.other, "muted")
        repost_of_muted = make_post(self.author, "rp-muted", original_post=muted_post)
        PostHide.objects.create(user=self.me, post=hidden)
        MutedAccount.objects.create(user=self.me, muted_user=self.other)

        got = set(exclude_hidden_and_muted(Post.objects.all(), self.me).values_list("content", flat=True))
        self.assertEqual(got, {"keep"})
        self.assertNotIn(repost_of_hidden.content, got)
        self.assertNotIn(repost_of_muted.content, got)
        self.assertIsNotNone(keep)

    def test_other_users_lists_do_not_leak(self):
        p = make_post(self.author, "x")
        someone = User.objects.create_user(username="someone", password="x")
        PostHide.objects.create(user=someone, post=p)
        MutedAccount.objects.create(user=someone, muted_user=self.author)
        self.assertEqual(exclude_hidden_and_muted(Post.objects.all(), self.me).count(), 1)

    def test_no_user_returns_queryset_unchanged(self):
        make_post(self.author, "x")
        self.assertEqual(exclude_hidden_and_muted(Post.objects.all(), None).count(), 1)


class NotInterestedAPITests(FeedbackBase):
    def url(self, post):
        return reverse("post-not-interested", kwargs={"post_id": post.id})

    def test_requires_auth(self):
        p = make_post(self.stranger, "x")
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url(p)).status_code, 401)

    def test_hide_is_idempotent_and_updates_reason(self):
        p = make_post(self.stranger, "x")
        r = self.client.post(self.url(p), {}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["data"]["reason"], "not_interested")
        r = self.client.post(self.url(p), {"reason": "seen_too_often"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PostHide.objects.filter(user=self.me, post=p).count(), 1)
        self.assertEqual(PostHide.objects.get(user=self.me, post=p).reason, "seen_too_often")

    def test_invalid_reason_400(self):
        p = make_post(self.stranger, "x")
        r = self.client.post(self.url(p), {"reason": "because"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.data["success"])

    def test_cannot_hide_own_post(self):
        mine = make_post(self.me, "mine")
        self.assertEqual(self.client.post(self.url(mine)).status_code, 400)

    def test_unknown_deleted_private_and_unfollowed_connections_posts_are_404(self):
        import uuid
        self.assertEqual(self.client.post(reverse("post-not-interested", kwargs={"post_id": uuid.uuid4()})).status_code, 404)
        gone = make_post(self.stranger, "gone", is_deleted=True)
        self.assertEqual(self.client.post(self.url(gone)).status_code, 404)
        priv = make_post(self.stranger, "priv", visibility="private")
        self.assertEqual(self.client.post(self.url(priv)).status_code, 404)
        conn = make_post(self.stranger, "conn", visibility="connections")
        self.assertEqual(self.client.post(self.url(conn)).status_code, 404)
        ok = make_post(self.friend, "conn-ok", visibility="connections")
        self.assertEqual(self.client.post(self.url(ok)).status_code, 201)

    def test_undo_restores_and_is_idempotent(self):
        p = make_post(self.stranger, "x")
        self.client.post(self.url(p))
        r = self.client.delete(self.url(p))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["removed"])
        self.assertFalse(PostHide.objects.exists())
        r = self.client.delete(self.url(p))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["removed"])

    def test_list_only_returns_my_hides(self):
        p1, p2 = make_post(self.stranger, "a"), make_post(self.stranger, "b")
        self.client.post(self.url(p1))
        other = User.objects.create_user(username="o", password="x")
        PostHide.objects.create(user=other, post=p2)
        r = self.client.get(reverse("post-not-interested-list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([str(x["post_id"]) for x in r.data["results"]], [str(p1.id)])


class MuteAPITests(FeedbackBase):
    url = property(lambda self: reverse("post-muted-accounts"))

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_mute_list_unmute(self):
        r = self.client.post(self.url, {"user_id": str(self.stranger.id)}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.post(self.url, {"user_id": str(self.stranger.id)}, format="json").status_code, 200)
        self.assertEqual(MutedAccount.objects.filter(user=self.me).count(), 1)
        lst = self.client.get(self.url)
        self.assertEqual([x["user"]["username"] for x in lst.data["results"]], ["stranger"])
        d = self.client.delete(reverse("post-unmute-account", kwargs={"user_id": self.stranger.id}))
        self.assertEqual(d.status_code, 200)
        self.assertTrue(d.data["removed"])
        self.assertFalse(MutedAccount.objects.exists())
        d = self.client.delete(reverse("post-unmute-account", kwargs={"user_id": self.stranger.id}))
        self.assertFalse(d.data["removed"])

    def test_validation(self):
        import uuid
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"user_id": "nope"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"user_id": str(self.me.id)}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"user_id": str(uuid.uuid4())}, format="json").status_code, 404)

    def test_mute_keeps_follow_relationship(self):
        self.client.post(self.url, {"user_id": str(self.friend.id)}, format="json")
        self.assertTrue(Follow.objects.filter(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED).exists())

    def test_list_only_shows_my_mutes(self):
        other = User.objects.create_user(username="o", password="x")
        MutedAccount.objects.create(user=other, muted_user=self.stranger)
        self.assertEqual(self.client.get(self.url).data["results"], [])


class FeedEffectTests(FeedbackBase):
    def test_home_feed_drops_hidden_post_and_muted_account(self):
        make_post(self.friend, "friend-keep")
        friend_hide = make_post(self.friend, "friend-hide")
        make_post(self.stranger, "stranger-keep")
        m = User.objects.create_user(username="m", password="x")
        make_post(m, "muted-post")
        before = self.feed_contents()
        self.assertEqual(before, {"friend-keep", "friend-hide", "stranger-keep", "muted-post"})

        self.client.post(reverse("post-not-interested", kwargs={"post_id": friend_hide.id}))
        self.client.post(reverse("post-muted-accounts"), {"user_id": str(m.id)}, format="json")
        self.assertEqual(self.feed_contents(), {"friend-keep", "stranger-keep"})

    def test_muting_a_followed_account_hides_it_from_home_feed(self):
        make_post(self.friend, "from-friend")
        make_post(self.stranger, "from-stranger")
        self.client.post(reverse("post-muted-accounts"), {"user_id": str(self.friend.id)}, format="json")
        self.assertEqual(self.feed_contents(), {"from-stranger"})
        self.assertEqual(self.feed_contents(source="following"), {"from-stranger"})  # discovery fill only

    def test_undo_and_unmute_bring_posts_back(self):
        p = make_post(self.stranger, "s")
        self.client.post(reverse("post-not-interested", kwargs={"post_id": p.id}))
        self.assertEqual(self.feed_contents(), set())
        self.client.delete(reverse("post-not-interested", kwargs={"post_id": p.id}))
        self.assertEqual(self.feed_contents(), {"s"})
        self.client.post(reverse("post-muted-accounts"), {"user_id": str(self.stranger.id)}, format="json")
        self.assertEqual(self.feed_contents(), set())
        self.client.delete(reverse("post-unmute-account", kwargs={"user_id": self.stranger.id}))
        self.assertEqual(self.feed_contents(), {"s"})

    def test_hidden_post_disappears_from_an_already_frozen_snapshot(self):
        posts = [make_post(self.stranger, f"s{i}") for i in range(12)]
        p1 = self.client.get(reverse("home-feed"), {"page_size": 5})
        self.assertIsNotNone(p1.data["next"])
        shown = {x["content"] for x in p1.data["results"]}
        rest = [p for p in posts if p.content not in shown]
        self.client.post(reverse("post-not-interested", kwargs={"post_id": rest[0].id}))
        got = shown | {x["content"] for x in self.client.get(p1.data["next"]).data["results"]}
        r = self.client.get(p1.data["next"])
        while r.data["next"]:
            r = self.client.get(r.data["next"])
            got |= {x["content"] for x in r.data["results"]}
        self.assertNotIn(rest[0].content, got)
        self.assertEqual(len(got), 11)

    def test_explore_feed_respects_hide_and_mute(self):
        a = make_post(self.stranger, "keep")
        b = make_post(self.stranger, "hide")
        m = User.objects.create_user(username="m", password="x")
        make_post(m, "muted")
        self.client.post(reverse("post-not-interested", kwargs={"post_id": b.id}))
        self.client.post(reverse("post-muted-accounts"), {"user_id": str(m.id)}, format="json")
        self.assertEqual(self.feed_contents("post-explore"), {"keep"})
        self.assertIsNotNone(a)

    @skipUnless(connection.vendor == "postgresql", "hashtags__contains needs PostgreSQL")
    def test_hashtag_feed_respects_hide_and_mute(self):
        keep = make_post(self.stranger, "keep", hashtags=["py"])
        hide = make_post(self.stranger, "hide", hashtags=["py"])
        m = User.objects.create_user(username="m", password="x")
        make_post(m, "muted", hashtags=["py"])
        self.client.post(reverse("post-not-interested", kwargs={"post_id": hide.id}))
        self.client.post(reverse("post-muted-accounts"), {"user_id": str(m.id)}, format="json")
        r = self.client.get(reverse("hashtag-posts", kwargs={"tag": "py"}))
        self.assertEqual({x["content"] for x in r.data["results"]}, {"keep"})
        self.assertIsNotNone(keep)

    def test_other_users_feed_is_unaffected(self):
        p = make_post(self.stranger, "s")
        self.client.post(reverse("post-not-interested", kwargs={"post_id": p.id}))
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        self.assertEqual(self.feed_contents(), {"s"})
