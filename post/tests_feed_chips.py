# post/tests_feed_chips.py  (NOT RUN - written from reading the code)
# H12: GET /post/feed/?subject= / ?category= / ?exam= (Home chips) + the snapshot / cursor / cache key that keeps
# one chip's scrolling session from mixing with another's.
import datetime
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from user_profile.models import Follow, UserPreference

from . import feed_chips, feed_snapshot
from .models import Post

User = get_user_model()


# ---------------------------------------------------------------- pure
class ParseTests(SimpleTestCase):
    def test_no_params_is_no_filter(self):
        f = feed_chips.parse({})
        self.assertFalse(f.active)
        self.assertEqual(f.token, "")
        self.assertEqual(f.key, "")

    def test_subject_alias_and_case(self):
        self.assertEqual(feed_chips.parse({"subject": " Mathematics "}).subject, "maths")
        self.assertEqual(feed_chips.parse({"subject": "PHY"}).subject, "physics")
        self.assertEqual(feed_chips.parse({"subject": "Data   Structures"}).subject, "data structures")

    def test_exam_alias(self):
        self.assertEqual(feed_chips.parse({"exam": "Boards"}).exam, "board")
        self.assertEqual(feed_chips.parse({"exam": "jee"}).exam, "jee")

    def test_category_label_or_key(self):
        self.assertEqual(feed_chips.parse({"category": "Education"}).category, "education")
        self.assertEqual(feed_chips.parse({"category": "technology"}).category, "tech")

    def test_bad_values_are_400(self):
        for params, field in (({"category": "nope"}, "category"), ({"exam": "gate2"}, "exam"),
                              ({"subject": "x" * 41}, "subject"), ({"subject": "a<b>"}, "subject")):
            with self.assertRaises(ValidationError) as cm:
                feed_chips.parse(params)
            self.assertIn(field, cm.exception.detail)

    def test_token_is_stable_and_distinct(self):
        a = feed_chips.parse({"subject": "physics"})
        self.assertEqual(a.token, feed_chips.parse({"subject": "Physics"}).token)
        self.assertNotEqual(a.token, feed_chips.parse({"subject": "maths"}).token)
        self.assertNotEqual(a.token, feed_chips.parse({"exam": "jee"}).token)
        self.assertNotEqual(a.token, feed_chips.parse({"subject": "physics", "exam": "jee"}).token)
        self.assertTrue(a.token.startswith("f"))

    def test_cursor_roundtrip_carries_the_token(self):
        sid = feed_snapshot.new_snapshot_id()
        tok = feed_chips.parse({"subject": "physics"}).token
        state = feed_snapshot.decode_cursor(feed_snapshot.encode_cursor(sid, {}, None, filter_token=tok))
        self.assertEqual(state["filter"], tok)
        # old cursors (no `f`) mean "no chip"
        self.assertEqual(feed_snapshot.decode_cursor(feed_snapshot.encode_cursor(sid, {}, None))["filter"], "")

    def test_snapshot_cache_key_is_scoped(self):
        sid = feed_snapshot.new_snapshot_id()
        self.assertEqual(feed_snapshot.cache_key(1, sid), f"feed:snap:1:{sid}")  # unfiltered key unchanged
        self.assertNotEqual(feed_snapshot.cache_key(1, sid, "fabc"), feed_snapshot.cache_key(1, sid))


# ---------------------------------------------------------------- HTTP
class FeedChipsApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.teacher = User.objects.create_user(username="teacher", password="x")
        Follow.objects.create(follower=self.me, following=self.teacher, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")

        def mk(content, **kw):
            kw.setdefault("category", "general")
            return Post.objects.create(
                user=self.teacher, content=content, post_type="text", visibility="public",
                moderation_status="approved", **kw)

        self.phys_sub = mk("sub physics", category="education", subcategory="Physics")
        self.phys_tag = mk("tag physics", hashtags=["physics", "kinematics"])
        self.math_alias = mk("maths via alias tag", hashtags=["mathematics"])
        self.chem = mk("chem", category="education", subcategory="Chemistry")
        self.jee = mk("jee", category="education", hashtags=["jeemains"])
        self.neet = mk("neet", hashtags=["neet"])
        self.meme = mk("meme", category="entertainment")
        self.many_physics = [mk(f"p{i}", hashtags=["physics"]) for i in range(25)]
        old = timezone.now() - datetime.timedelta(days=10)
        for n, p in enumerate(Post.objects.order_by("created_at")):
            Post.objects.filter(pk=p.pk).update(created_at=old + datetime.timedelta(minutes=n))

    def _get(self, url=None, **params):
        if url is None:
            r = self.client.get(self.url, params)
        else:  # a `next` URL already carries every query param
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r

    @staticmethod
    def _ids(r):
        return {x["id"] for x in r.data["results"]}

    # ---- filtering -------------------------------------------------------
    def test_no_chip_returns_everything(self):
        r = self._get(page_size=50)
        self.assertEqual(r.data["count"], 7 + 25)

    def test_subject_matches_subcategory_and_hashtag(self):
        r = self._get(subject="physics", page_size=50)
        ids = self._ids(r)
        self.assertIn(str(self.phys_sub.id), ids)
        self.assertIn(str(self.phys_tag.id), ids)
        self.assertNotIn(str(self.chem.id), ids)
        self.assertNotIn(str(self.meme.id), ids)
        self.assertEqual(r.data["count"], 2 + 25)

    def test_subject_alias_hits_alias_hashtag(self):
        ids = self._ids(self._get(subject="maths", page_size=50))
        self.assertEqual(ids, {str(self.math_alias.id)})

    def test_exam_chip(self):
        self.assertEqual(self._ids(self._get(exam="jee", page_size=50)), {str(self.jee.id)})
        self.assertEqual(self._ids(self._get(exam="neet", page_size=50)), {str(self.neet.id)})

    def test_category_chip(self):
        ids = self._ids(self._get(category="education", page_size=50))
        self.assertEqual(ids, {str(p.id) for p in (self.phys_sub, self.chem, self.jee)})

    def test_chips_combine_with_and(self):
        ids = self._ids(self._get(category="education", subject="physics", page_size=50))
        self.assertEqual(ids, {str(self.phys_sub.id)})

    def test_bad_chip_value_is_400(self):
        for params in ({"category": "nope"}, {"exam": "gate2"}):
            self.assertEqual(self.client.get(self.url, params).status_code, 400)

    def test_exam_mode_still_applies_on_top_of_a_chip(self):
        UserPreference.objects.update_or_create(user=self.me, defaults={"exam_mode": True})
        # the meme is not study content -> still hidden even for the broadest chip combination
        ids = self._ids(self._get(category="entertainment", page_size=50))
        self.assertEqual(ids, set())

    # ---- paging / cursor / cache keys -----------------------------------
    def test_chip_session_pages_without_leaking_other_posts(self):
        r = self._get(subject="physics", page_size=10)
        seen = self._ids(r)
        n = 0
        while r.data["next"]:
            n += 1
            self.assertLess(n, 10)
            r = self._get(r.data["next"])
            seen |= self._ids(r)
        self.assertEqual(len(seen), 27)
        self.assertNotIn(str(self.meme.id), seen)

    def test_next_url_keeps_the_chip_and_cursor_carries_its_token(self):
        r = self._get(subject="physics", page_size=10)
        q = parse_qs(urlparse(r.data["next"]).query)
        self.assertEqual(q["subject"], ["physics"])
        state = feed_snapshot.decode_cursor(q["cursor"][0])
        self.assertEqual(state["filter"], feed_chips.parse({"subject": "physics"}).token)

    def test_cursor_of_another_chip_is_404(self):
        r = self._get(subject="physics", page_size=10)
        cursor = parse_qs(urlparse(r.data["next"]).query)["cursor"][0]
        self.assertEqual(self.client.get(self.url, {"subject": "maths", "cursor": cursor}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"cursor": cursor}).status_code, 404)  # chip dropped
        # and an unfiltered cursor is not accepted by a chip request either
        plain = self._get(page_size=10)
        plain_cursor = parse_qs(urlparse(plain.data["next"]).query)["cursor"][0]
        self.assertEqual(self.client.get(self.url, {"subject": "physics", "cursor": plain_cursor}).status_code, 404)

    def test_snapshot_is_stored_under_the_chip_scope_only(self):
        r = self._get(subject="physics", page_size=10)
        state = feed_snapshot.decode_cursor(parse_qs(urlparse(r.data["next"]).query)["cursor"][0])
        tok = feed_chips.parse({"subject": "physics"}).token
        self.assertIsNotNone(feed_snapshot.load(self.me.pk, state["snapshot_id"], scope=tok))
        self.assertIsNone(feed_snapshot.load(self.me.pk, state["snapshot_id"]))

    def test_candidate_cache_is_not_shared_between_chips(self):
        # page 1 of each chip within the 30 s candidate-cache window must not reuse another chip's pools
        a = self._get(subject="physics", page_size=50)
        b = self._get(exam="jee", page_size=50)
        c = self._get(page_size=50)
        self.assertEqual(a.data["count"], 27)
        self.assertEqual(b.data["count"], 1)
        self.assertEqual(c.data["count"], 32)
