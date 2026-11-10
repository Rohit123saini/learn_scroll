# message/tests_similar_doubts.py
"""
T1.5 (D5) — `GET /message/groups/<group_id>/doubts/similar/?q=...`

Pins:
  * normalize/clamp helpers.
  * Python fallback ranking (sqlite / non-ASCII) — chalta hai kisi bhi DB pe.
  * Endpoint: membership (403), missing q (400), short q (200 + empty),
    group scoping, soft-deleted exclusion, answered-first, limit clamp,
    anonymous author hidden, `similarity` field.
  * Postgres-only: pg_trgm typo-tolerant match (skip hota hai non-PG pe).
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from . import doubt_similarity as ds
from .models import (
    Conversation, ConversationParticipant, ConversationType, DoubtQuestion,
    Group, GroupMember,
)

User = get_user_model()


class HelperTests(SimpleTestCase):
    def test_normalize_collapses_whitespace_and_truncates(self):
        self.assertEqual(ds.normalize_query("  how   does\n photosynthesis  work "), "how does photosynthesis work")
        self.assertEqual(ds.normalize_query(None), "")
        self.assertEqual(len(ds.normalize_query("a" * 1000)), ds.MAX_QUERY_CHARS)

    def test_clamp_limit(self):
        self.assertEqual(ds.clamp_limit(None), ds.DEFAULT_LIMIT)
        self.assertEqual(ds.clamp_limit("abc"), ds.DEFAULT_LIMIT)
        self.assertEqual(ds.clamp_limit("0"), 1)
        self.assertEqual(ds.clamp_limit("999"), ds.MAX_LIMIT)
        self.assertEqual(ds.clamp_limit("3"), 3)

    def test_tokens_drop_stopwords_and_short(self):
        self.assertEqual(ds._tokens("What is the Photosynthesis, a process?"), ["photosynthesis", "process"])


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = User.objects.create_user(username="teach", password="x")
        self.student = User.objects.create_user(username="stud", password="x")
        self.outsider = User.objects.create_user(username="out", password="x")
        self.group = self._make_group("Physics", [self.teacher, self.student], admin=self.teacher)
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def _make_group(self, name, members, admin=None):
        conv = Conversation.objects.create(type=ConversationType.GROUP)
        group = Group.objects.create(conversation=conv, name=name, created_by=members[0])
        for u in members:
            ConversationParticipant.objects.create(conversation=conv, user=u)
            GroupMember.objects.create(
                group=group, user=u,
                role=GroupMember.Role.ADMIN if u == admin else GroupMember.Role.MEMBER,
            )
        return group

    def _doubt(self, text, group=None, author=None, **kw):
        group = group or self.group
        return DoubtQuestion.objects.create(
            group=group, conversation=group.conversation, author=author or self.teacher, text=text, **kw,
        )

    def _url(self, group=None):
        return reverse("group-doubt-similar", kwargs={"group_id": (group or self.group).id})

    def _get(self, q, **params):
        params = {"q": q, **params}
        return self.client.get(self._url(), params)


class SimilarEndpointTests(_Base):
    def test_requires_membership(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self._get("how does photosynthesis work").status_code, 403)

    def test_unauthenticated_rejected(self):
        res = APIClient().get(self._url(), {"q": "photosynthesis"})
        self.assertIn(res.status_code, (401, 403))

    def test_missing_q_is_400(self):
        res = self.client.get(self._url())
        self.assertEqual(res.status_code, 400)

    def test_short_query_returns_empty_200(self):
        self._doubt("photosynthesis light reaction")
        res = self._get("photo")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 0)
        self.assertEqual(res.data["results"], [])
        self.assertEqual(res.data["min_chars"], ds.MIN_QUERY_CHARS)

    def test_finds_similar_and_includes_score(self):
        d = self._doubt("How does photosynthesis work in the light reaction?")
        self._doubt("Newton third law example please")
        res = self._get("photosynthesis light reaction")
        self.assertEqual(res.status_code, 200)
        ids = [r["id"] for r in res.data["results"]]
        self.assertEqual(ids, [str(d.id)])
        self.assertGreater(res.data["results"][0]["similarity"], 0.3)
        self.assertEqual(res.data["count"], 1)

    def test_scoped_to_group(self):
        other_group = self._make_group("Chem", [self.teacher, self.student], admin=self.teacher)
        self._doubt("photosynthesis light reaction explained", group=other_group)
        self.assertEqual(self._get("photosynthesis light reaction").data["count"], 0)

    def test_soft_deleted_excluded(self):
        d = self._doubt("photosynthesis light reaction explained")
        d.soft_delete()
        self.assertEqual(self._get("photosynthesis light reaction").data["count"], 0)

    def test_answered_ranks_above_equally_similar_unanswered(self):
        unanswered = self._doubt("photosynthesis light reaction explained")
        answered = self._doubt("photosynthesis light reaction explained", is_answered=True, answer_text="chlorophyll")
        res = self._get("photosynthesis light reaction explained")
        ids = [r["id"] for r in res.data["results"]]
        self.assertEqual(ids[0], str(answered.id))
        self.assertIn(str(unanswered.id), ids)

    def test_limit_is_respected(self):
        for i in range(4):
            self._doubt(f"photosynthesis light reaction variant {i}")
        res = self._get("photosynthesis light reaction", limit=2)
        self.assertEqual(res.data["count"], 2)

    def test_anonymous_author_stays_hidden(self):
        self._doubt("photosynthesis light reaction explained", author=self.teacher, is_anonymous=True)
        row = self._get("photosynthesis light reaction").data["results"][0]
        self.assertIsNone(row["author"])
        self.assertFalse(row["is_mine"])

    def test_own_anonymous_doubt_shows_me_as_author(self):
        self._doubt("photosynthesis light reaction explained", author=self.student, is_anonymous=True)
        row = self._get("photosynthesis light reaction").data["results"][0]
        self.assertTrue(row["is_mine"])
        self.assertIsNotNone(row["author"])

    def test_hinglish_query_uses_fallback_tokens(self):
        d = self._doubt("Newton ka teesra niyam kya hota hai example ke saath")
        res = self._get("newton teesra niyam example")
        self.assertEqual([r["id"] for r in res.data["results"]], [str(d.id)])


class FallbackRankingTests(_Base):
    def test_requires_two_shared_tokens_for_multi_word_query(self):
        self._doubt("photosynthesis only mentioned once here")
        self.assertEqual(ds._fallback_search(self.group, "photosynthesis light reaction", 5), [])

    def test_unrelated_returns_nothing(self):
        self._doubt("Newton third law example please")
        self.assertEqual(ds._fallback_search(self.group, "photosynthesis light reaction", 5), [])


class PostgresTrigramTests(_Base):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("pg_trgm needs PostgreSQL")
        super().setUp()

    def test_typo_tolerant_match(self):
        d = self._doubt("Explain the light reaction of photosynthesis in detail")
        res = self._get("photosyntesis light reaction")  # typo: missing 'h'
        self.assertEqual([r["id"] for r in res.data["results"]], [str(d.id)])

    def test_short_query_matches_inside_long_doubt(self):
        d = self._doubt(
            "Teacher please explain why the rate of photosynthesis drops at very high temperature "
            "because enzymes denature and the whole light and dark reaction slows down"
        )
        res = self._get("photosynthesis temperature")
        self.assertIn(str(d.id), [r["id"] for r in res.data["results"]])

    def test_unrelated_not_returned(self):
        self._doubt("Newton third law example please")
        self.assertEqual(self._get("photosynthesis light reaction").data["count"], 0)

    def test_index_exists(self):
        with connection.cursor() as c:
            c.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", ["doubt_text_trgm_gin"])
            row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertIn("gin_trgm_ops", row[0])
