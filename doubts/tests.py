"""
Integration tests for the Doubts hub (needs the DB; the pure logic is in
tests_hub.py). Covers the two systems that need the lightest fixtures
(classroom_chat + feed_post); campus / tuition / testseries adapters follow
the identical base-class flow and are exercised through the same endpoints.

    python manage.py test doubts
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password="x")


def _client(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


def _group(teacher, *students, name="Physics 12"):
    from message.models import Conversation, ConversationType, Group, GroupMember

    conv = Conversation.objects.create(type=ConversationType.GROUP)
    group = Group.objects.create(conversation=conv, name=name, created_by=teacher)
    GroupMember.objects.create(group=group, user=teacher, role=GroupMember.Role.ADMIN)
    for s in students:
        GroupMember.objects.create(group=group, user=s, role=GroupMember.Role.MEMBER)
    return group


@mock.patch("message.views.DoubtQuestionViewSet._broadcast", staticmethod(lambda *a, **k: None))
class HubClassroomChatTests(TestCase):
    """classroom_chat = message.DoubtQuestion + DoubtAnswer thread (T1.3): any member except the
    asker answers, a teacher answer is auto-verified, asker/teacher marks ONE best answer."""

    def setUp(self):
        cache.clear()
        self.teacher, self.student, self.mate, self.other = _user("t"), _user("s"), _user("mate"), _user("outsider")
        self.group = _group(self.teacher, self.student, self.mate)

    def _ask(self, **extra):
        body = {"source": "classroom_chat", "target_id": str(self.group.id), "text": "What is torque?"}
        body.update(extra)
        return _client(self.student).post("/doubt-hub/ask/", body, format="json")

    def _answer(self, who, did, text="Torque = r x F"):
        return _client(who).post(f"/doubt-hub/{did}/answer/", {"text": text}, format="json")

    def test_requires_auth(self):
        self.assertEqual(_client().get("/doubt-hub/").status_code, 401)

    def test_ask_returns_unified_item_and_lists(self):
        res = self._ask()
        self.assertEqual(res.status_code, 201, res.content)
        item = res.json()
        self.assertTrue(item["id"].startswith("classroom_chat:"))
        self.assertEqual(item["status"], "open")
        self.assertTrue(item["is_mine"])
        self.assertEqual(item["parent"]["name"], "Physics 12")
        self.assertFalse(item["capabilities"]["answer"])  # asker cannot answer own doubt

        listing = _client(self.student).get("/doubt-hub/").json()
        self.assertEqual([i["id"] for i in listing["results"]], [item["id"]])
        self.assertEqual(listing["errors"], [])

    def test_teacher_queue_and_teacher_answer_is_verified(self):
        did = self._ask().json()["id"]
        t = _client(self.teacher)
        todo = t.get("/doubt-hub/?role=to_answer").json()["results"]
        self.assertEqual([i["id"] for i in todo], [did])

        res = self._answer(self.teacher, did)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["status"], "answered")          # answered, not yet "best"
        self.assertEqual(body["answers_count"], 1)
        self.assertTrue(body["thread"][0]["is_staff_reply"])
        self.assertTrue(body["thread"][0]["is_verified"])
        self.assertEqual(t.get("/doubt-hub/?role=to_answer").json()["results"], [])

    def test_asker_cannot_answer_own_doubt(self):
        did = self._ask().json()["id"]
        self.assertEqual(self._answer(self.student, did).status_code, 400)

    def test_classmate_can_answer_then_asker_marks_best_resolves(self):
        did = self._ask().json()["id"]
        res = self._answer(self.mate, did, "Think of a wrench")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertFalse(res.json()["thread"][0]["is_staff_reply"])
        answer_id = res.json()["thread"][0]["id"]

        no = _client(self.mate).post(f"/doubt-hub/{did}/mark_best/", {"answer_id": answer_id}, format="json")
        self.assertEqual(no.status_code, 403)                  # only asker or teacher
        ok = _client(self.student).post(f"/doubt-hub/{did}/mark_best/", {"answer_id": answer_id}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["status"], "resolved")
        self.assertTrue(ok.json()["thread"][0]["is_best"])

    def test_only_teacher_can_verify(self):
        did = self._ask().json()["id"]
        answer_id = self._answer(self.mate, did).json()["thread"][0]["id"]
        self.assertEqual(
            _client(self.mate).post(f"/doubt-hub/{did}/verify/", {"answer_id": answer_id}, format="json").status_code, 403)
        ok = _client(self.teacher).post(f"/doubt-hub/{did}/verify/", {"answer_id": answer_id}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertTrue(ok.json()["thread"][0]["is_verified"])

    def test_mark_best_rejects_foreign_answer_id(self):
        did = self._ask().json()["id"]
        self._answer(self.mate, did)
        res = _client(self.student).post(
            f"/doubt-hub/{did}/mark_best/", {"answer_id": "00000000-0000-0000-0000-000000000000"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_non_member_gets_404_not_a_leak(self):
        did = self._ask().json()["id"]
        o = _client(self.other)
        self.assertEqual(o.get(f"/doubt-hub/{did}/").status_code, 404)
        self.assertEqual(o.get("/doubt-hub/").json()["results"], [])

    def test_anonymous_author_hidden_until_reveal(self):
        did = self._ask(is_anonymous=True).json()["id"]
        t = _client(self.teacher)
        self.assertIsNone(t.get(f"/doubt-hub/{did}/").json()["author"])
        self.assertIsNotNone(_client(self.student).get(f"/doubt-hub/{did}/").json()["author"])
        self.assertEqual(t.post(f"/doubt-hub/{did}/reveal/", {}, format="json").status_code, 200)
        self.assertEqual(t.get(f"/doubt-hub/{did}/").json()["author"]["username"], "s")

    def test_upvote_toggle(self):
        did = self._ask().json()["id"]
        c = _client(self.teacher)
        self.assertEqual(c.post(f"/doubt-hub/{did}/upvote/", {}, format="json").json()["upvotes_count"], 1)
        self.assertEqual(c.post(f"/doubt-hub/{did}/unupvote/", {}, format="json").json()["upvotes_count"], 0)

    def test_summary_counts_and_cache_invalidation(self):
        did = self._ask().json()["id"]
        t = _client(self.teacher)
        s = t.get("/doubt-hub/summary/").json()
        self.assertEqual(s["by_status"]["open"], 1)
        self.assertEqual(s["to_answer"]["by_source"]["classroom_chat"], 1)
        self._answer(self.teacher, did)
        s = t.get("/doubt-hub/summary/").json()  # cache dropped by the action
        self.assertEqual((s["by_status"]["open"], s["by_status"]["answered"]), (0, 1))

    def test_validation(self):
        c = _client(self.student)
        self.assertEqual(c.post("/doubt-hub/ask/", {"source": "nope", "text": "x"}, format="json").status_code, 400)
        self.assertEqual(c.post("/doubt-hub/ask/", {"source": "classroom_chat", "text": "  "}, format="json").status_code, 400)
        self.assertEqual(c.get("/doubt-hub/?status=bogus").status_code, 400)
        self.assertEqual(c.get("/doubt-hub/bogus:1/").status_code, 400)
        self.assertEqual(c.get("/doubt-hub/classroom_chat:not-a-uuid/").status_code, 404)
        did = self._ask().json()["id"]
        self.assertEqual(c.post(f"/doubt-hub/{did}/explode/", {}, format="json").status_code, 400)
        self.assertEqual(c.post(f"/doubt-hub/{did}/resolve/", {}, format="json").status_code, 400)  # unsupported here

    def test_pagination_cursor(self):
        for i in range(5):
            self._ask(text=f"q{i}")
        c = _client(self.student)
        p1 = c.get("/doubt-hub/?limit=2").json()
        self.assertEqual(len(p1["results"]), 2)
        self.assertIsNotNone(p1["next_cursor"])
        p2 = c.get(f"/doubt-hub/?limit=2&cursor={p1['next_cursor']}").json()
        p3 = c.get(f"/doubt-hub/?limit=2&cursor={p2['next_cursor']}").json()
        ids = [i["id"] for p in (p1, p2, p3) for i in p["results"]]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertIsNone(p3["next_cursor"])


class HubFeedPostTests(TestCase):
    def setUp(self):
        cache.clear()
        from post.models import Post

        self.asker, self.helper, self.stranger = _user("asker"), _user("helper"), _user("stranger")
        self.post = Post.objects.create(user=self.asker, content="Why is the sky blue?", post_type="doubt",
                                        category="general", visibility="public")
        self.hid = f"feed_post:{self.post.id}"

    def test_public_doubt_visible_and_to_answer_for_others(self):
        res = _client(self.helper).get("/doubt-hub/?role=to_answer&source=feed_post").json()
        self.assertEqual([i["id"] for i in res["results"]], [self.hid])

    def test_answer_then_mark_best_resolves(self):
        res = _client(self.helper).post(f"/doubt-hub/{self.hid}/answer/", {"text": "Rayleigh scattering"}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status"], "answered")
        answer_id = res.json()["thread"][0]["id"]

        # only the asker may pin
        no = _client(self.helper).post(f"/doubt-hub/{self.hid}/mark_best/", {"answer_id": answer_id}, format="json")
        self.assertEqual(no.status_code, 403)
        ok = _client(self.asker).post(f"/doubt-hub/{self.hid}/mark_best/", {"answer_id": answer_id}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(_client(self.asker).get(f"/doubt-hub/{self.hid}/").json()["status"], "resolved")

    def test_feed_doubts_cannot_be_asked_through_hub(self):
        res = _client(self.helper).post("/doubt-hub/ask/", {"source": "feed_post", "text": "x"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "ask_unsupported")

    def test_private_doubt_not_leaked(self):
        from post.models import Post

        priv = Post.objects.create(user=self.asker, content="secret", post_type="doubt",
                                   category="general", visibility="private")
        res = _client(self.helper).get(f"/doubt-hub/feed_post:{priv.id}/")
        self.assertEqual(res.status_code, 404)

    def test_unsupported_action_on_feed_doubt(self):
        res = _client(self.helper).post(f"/doubt-hub/{self.hid}/upvote/", {}, format="json")
        self.assertEqual(res.status_code, 400)


class HubMergeTests(TestCase):
    def test_two_sources_interleave_newest_first_and_source_filter(self):
        from post.models import Post

        cache.clear()
        teacher, student = _user("t2"), _user("s2")
        group = _group(teacher, student)
        from message.models import DoubtQuestion

        d1 = DoubtQuestion.objects.create(group=group, conversation=group.conversation, author=student, text="older")
        p = Post.objects.create(user=student, content="newer", post_type="doubt", category="general", visibility="public")
        c = _client(student)
        ids = [i["id"] for i in c.get("/doubt-hub/").json()["results"]]
        self.assertEqual(ids, [f"feed_post:{p.id}", f"classroom_chat:{d1.id}"])
        only = [i["source"] for i in c.get("/doubt-hub/?source=classroom_chat").json()["results"]]
        self.assertEqual(only, ["classroom_chat"])
