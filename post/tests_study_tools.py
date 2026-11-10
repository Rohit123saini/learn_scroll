# post/tests_study_tools.py  (NOT RUN - written from reading the code)
# H13: Study tools of a feed card - revision deck (PostSave collection "revision"), topic follow, lazy state.
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from . import study_tools
from .models import Post, PostSave, TopicFollow

User = get_user_model()


class PureTests(SimpleTestCase):
    def test_topic_key(self):
        self.assertEqual(study_tools.topic_key("Data Structures"), "datastructures")
        self.assertEqual(study_tools.topic_key("#Physics "), "physics")
        self.assertEqual(study_tools.topic_key(""), "")
        self.assertEqual(study_tools.topic_key("a<b"), "")

    def test_topics_of_post_order_dedupe_and_cap(self):
        class P:
            topics = ["Kinematics", "Newton's Laws", "kinematics"]
            subject = "Physics"
            hashtags = ["physics", "jee", "neet", "boards", "class11", "extra"]

        out = study_tools.topics_of_post(P())
        keys = [t["key"] for t in out]
        self.assertEqual(keys[0], "kinematics")
        self.assertNotIn("newton'slaws", keys)  # invalid spelling is dropped, not stored
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(len(keys), study_tools.MAX_TOPICS_PER_POST)
        self.assertIn("physics", keys)
        self.assertEqual(out[0]["label"], "Kinematics")  # label keeps the author's spelling


class StudyToolsApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.client.force_authenticate(self.me)
        self.post = Post.objects.create(
            user=self.author, content="notes", post_type="text", visibility="public", moderation_status="approved",
            kind="resource", subject="Physics", topics=["Kinematics"], hashtags=["jee"])

    def _state(self):
        r = self.client.get(reverse("post-study-tools", args=[self.post.id]))
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def _rev(self, desired):
        return self.client.post(reverse("post-study-revision", args=[self.post.id]), {"desired": desired}, format="json")

    def test_state_lists_topics_and_defaults(self):
        s = self._state()
        self.assertFalse(s["in_revision_deck"])
        self.assertFalse(s["is_saved"])
        self.assertEqual([t["key"] for t in s["topics"]], ["kinematics", "physics", "jee"])
        self.assertTrue(all(t["following"] is False for t in s["topics"]))

    def test_revision_add_remove_is_idempotent(self):
        self.assertTrue(self._rev("add").data["in_revision_deck"])
        self.assertTrue(self._rev("add").data["in_revision_deck"])  # replay
        self.assertEqual(PostSave.objects.filter(user=self.me, post=self.post).count(), 1)
        self.assertEqual(PostSave.objects.get(user=self.me, post=self.post).collection_name, "revision")
        self.assertTrue(self._state()["in_revision_deck"])
        self.assertFalse(self._rev("remove").data["in_revision_deck"])
        self.assertFalse(self._rev("remove").data["in_revision_deck"])  # replay
        # removing from the deck keeps the post saved (default collection) - never un-saves by accident
        self.assertEqual(PostSave.objects.get(user=self.me, post=self.post).collection_name, "default")

    def test_add_moves_an_already_saved_post(self):
        PostSave.objects.create(user=self.me, post=self.post, collection_name="default")
        self._rev("add")
        self.assertEqual(PostSave.objects.filter(user=self.me, post=self.post).count(), 1)
        self.assertEqual(PostSave.objects.get(user=self.me, post=self.post).collection_name, "revision")

    def test_deck_is_listed_by_the_saved_endpoint(self):
        self._rev("add")
        r = self.client.get(reverse("saved-posts-list"), {"collection_name": "revision"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["id"] for x in r.data["results"]], [str(self.post.id)])

    def test_bad_desired_is_400(self):
        self.assertEqual(self._rev("maybe").status_code, 400)

    def test_deck_is_per_user(self):
        self._rev("add")
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        self.assertFalse(self._state()["in_revision_deck"])

    def test_hidden_post_is_404(self):
        Post.objects.filter(pk=self.post.pk).update(visibility="private")
        self.assertEqual(self.client.get(reverse("post-study-tools", args=[self.post.id])).status_code, 404)
        self.assertEqual(self._rev("add").status_code, 404)

    def test_topic_follow_unfollow(self):
        url = reverse("topic-follow")
        r = self.client.post(url, {"topic": "Kinematics", "desired": "follow"}, format="json")
        self.assertEqual((r.status_code, r.data["topic"], r.data["following"]), (200, "kinematics", True))
        self.client.post(url, {"topic": "kinematics"}, format="json")  # default desired = follow, idempotent
        self.assertEqual(TopicFollow.objects.filter(user=self.me).count(), 1)
        flags = {t["key"]: t["following"] for t in self._state()["topics"]}
        self.assertTrue(flags["kinematics"])
        self.assertFalse(flags["physics"])
        self.assertEqual(self.client.get(reverse("topics-following")).data["topics"], ["kinematics"])
        r = self.client.post(url, {"topic": "kinematics", "desired": "unfollow"}, format="json")
        self.assertFalse(r.data["following"])
        self.assertEqual(TopicFollow.objects.count(), 0)

    def test_topic_follow_validation_and_cap(self):
        url = reverse("topic-follow")
        self.assertEqual(self.client.post(url, {"topic": "a<b"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"topic": "x", "desired": "maybe"}, format="json").status_code, 400)
        for i in range(study_tools.MAX_FOLLOWED_TOPICS):
            TopicFollow.objects.create(user=self.me, topic=f"t{i}")
        r = self.client.post(url, {"topic": "onemore"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (400, "too_many_topics"))
        # unfollowing still works at the cap
        self.assertEqual(self.client.post(url, {"topic": "t1", "desired": "unfollow"}, format="json").status_code, 200)
