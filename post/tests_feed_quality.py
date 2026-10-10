# post/tests_feed_quality.py - T1 item 6: quality / safety gates.
import unittest

from . import feed_quality as fq

PF = fq.PostFacts


def verdict(**kw):
    cfg = kw.pop("cfg", None)
    base = dict(post_type="text", text="a perfectly normal post about learning calculus today", media_count=0)
    base.update(kw)
    return fq.assess(PF(**base), cfg)


class AssessPureTests(unittest.TestCase):
    def test_normal_post_is_ok(self):
        self.assertTrue(verdict().ok)

    def test_reported_heavy_dropped_and_soft_demoted(self):
        self.assertEqual(verdict(reported_count=10).level, fq.VERDICT_DROP)
        self.assertIn(fq.REASON_REPORTED_HEAVY, verdict(reported_count=10).reasons)
        v = verdict(reported_count=3)
        self.assertEqual((v.level, v.reasons), (fq.VERDICT_DEMOTE, [fq.REASON_REPORTED]))
        self.assertTrue(verdict(reported_count=2).ok)

    def test_spam_rules(self):
        self.assertEqual(verdict(text="buy now http://a.co http://b.co http://c.co http://d.co").level, fq.VERDICT_DROP)
        self.assertEqual(verdict(text="heyyyyyyyyyyyyy everyone look here please").level, fq.VERDICT_DROP)
        self.assertEqual(verdict(text="BUY THIS COURSE NOW BEST PRICE EVER").level, fq.VERDICT_DROP)
        self.assertEqual(verdict(hashtag_count=16).level, fq.VERDICT_DROP)
        self.assertEqual(verdict(text="win win win win win win win win win free").level, fq.VERDICT_DROP)

    def test_short_caps_and_normal_links_are_not_spam(self):
        self.assertTrue(verdict(text="IIT JEE 2027 tips and tricks for physics").ok)
        self.assertTrue(verdict(text="Check https://example.com for the syllabus of this course").ok)
        self.assertTrue(verdict(text="NEET UG notes ok and some more lowercase words here").ok)

    def test_spam_action_demote(self):
        v = verdict(hashtag_count=40, cfg={"spam_action": "demote"})
        self.assertEqual(v.level, fq.VERDICT_DEMOTE)

    def test_repeated_text(self):
        self.assertTrue(verdict(duplicate_count=2).ok)
        v = verdict(duplicate_count=3)
        self.assertEqual((v.level, v.reasons), (fq.VERDICT_DROP, [fq.REASON_REPEATED_TEXT]))

    def test_tiny_text_only_for_plain_text_without_media(self):
        v = verdict(text="hi")
        self.assertEqual((v.level, v.reasons), (fq.VERDICT_DEMOTE, [fq.REASON_TINY_TEXT]))
        self.assertTrue(verdict(text="hi", media_count=1).ok)
        self.assertTrue(verdict(text="hi", post_type="image").ok)
        self.assertTrue(verdict(text="hi", post_type="poll").ok)
        self.assertTrue(verdict(text="hi", cfg={"min_text_chars": 0}).ok)

    def test_drop_beats_demote(self):
        v = verdict(text="hi", reported_count=10)
        self.assertEqual(v.level, fq.VERDICT_DROP)
        self.assertEqual(set(v.reasons), {fq.REASON_REPORTED_HEAVY, fq.REASON_TINY_TEXT})

    def test_disabled_gate_is_noop(self):
        self.assertTrue(verdict(text="hi", reported_count=99, cfg={"enabled": False}).ok)

    def test_normalize_text_ignores_case_punctuation_links(self):
        self.assertEqual(fq.normalize_text("Hello,  WORLD!! https://x.io"), fq.normalize_text("hello world"))
        self.assertEqual(fq.normalize_text(None), "")


class ApplyVerdictsTests(unittest.TestCase):
    def test_drop_removed_demote_to_tail_order_kept(self):
        V = fq.Verdict
        verdicts = {2: V(fq.VERDICT_DROP), 1: V(fq.VERDICT_DEMOTE), 4: V(fq.VERDICT_DEMOTE)}
        ids, stats = fq.apply_verdicts([1, 2, 3, 4, 5], verdicts)
        self.assertEqual(ids, [3, 5, 1, 4])
        self.assertEqual(stats, {"dropped": 1, "demoted": 2})

    def test_duplicate_counts(self):
        rows = [("a", "Same text!"), ("a", "same   text"), ("b", "same text"), ("a", ""), ("a", None)]
        self.assertEqual(fq.duplicate_counts(rows), {("a", "same text"): 2, ("b", "same text"): 1})


# ---- DB-backed (needs the Django test runner) -------------------------------------
try:
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    from django.db import connection
    from django.test import override_settings
    from django.test.utils import CaptureQueriesContext
    from django.urls import reverse
    from django.utils import timezone
    from rest_framework.test import APITestCase

    from user_profile.models import Follow

    from .models import Post
except Exception:  # pragma: no cover
    APITestCase = None

if APITestCase is not None:
    User = get_user_model()
    ON = {"enabled": True}

    def mk(user, content, **kw):
        kw.setdefault("post_type", "text")
        kw.setdefault("visibility", "public")
        kw.setdefault("moderation_status", "approved")
        return Post.objects.create(user=user, content=content, **kw)

    # exploration stays off here: these tests are about the gates, not about new-creator slots
    NO_EXPLORE = {"enabled": False}

    @override_settings(FEED_EXPLORE=NO_EXPLORE)
    class QualityEndpointTests(APITestCase):
        def setUp(self):
            cache.clear()
            self.me = User.objects.create_user(username="q_me", password="x")
            self.good = User.objects.create_user(username="q_good", password="x")
            self.spammer = User.objects.create_user(username="q_spam", password="x")
            self.client.force_authenticate(self.me)

        def _contents(self, **params):
            params.setdefault("page_size", 50)
            r = self.client.get(reverse("home-feed"), params)
            self.assertEqual(r.status_code, 200, r.data)
            return [x["content"] for x in r.data["results"]]

        def test_spam_dropped_tiny_demoted_good_kept_in_discovery(self):
            now = timezone.now()
            mk(self.good, "A genuinely useful explanation of integration by parts", created_at=now - timedelta(minutes=5))
            mk(self.good, "ok", created_at=now - timedelta(minutes=1))  # tiny -> demoted
            mk(self.spammer, "BUY CHEAP COURSES NOW AT BEST PRICE EVER", created_at=now)  # spam -> dropped
            with override_settings(FEED_QUALITY=ON):
                got = self._contents()
            self.assertNotIn("BUY CHEAP COURSES NOW AT BEST PRICE EVER", got)
            self.assertEqual(got[0], "A genuinely useful explanation of integration by parts")
            self.assertEqual(got[-1], "ok")

        def test_repeated_text_dropped(self):
            now = timezone.now()
            for i in range(4):
                mk(self.spammer, "Join my telegram group for free notes everyday", created_at=now - timedelta(minutes=i))
            mk(self.good, "Notes on thermodynamics: first law explained slowly", created_at=now)
            with override_settings(FEED_QUALITY=ON):
                got = self._contents()
            self.assertEqual(got, ["Notes on thermodynamics: first law explained slowly"])

        def test_following_is_not_quality_gated_but_heavy_reports_are(self):
            Follow.objects.create(follower=self.me, following=self.spammer, status=Follow.Status.ACCEPTED)
            mk(self.spammer, "ok")  # tiny, but followed -> stays
            mk(self.spammer, "A followed author's heavily reported post lives here", reported_count=10)
            with override_settings(FEED_QUALITY=ON):
                got = self._contents(source="following")
            self.assertEqual(got, ["ok"])

        def test_gates_off_changes_nothing(self):
            mk(self.spammer, "BUY CHEAP COURSES NOW AT BEST PRICE EVER")
            with override_settings(FEED_QUALITY={"enabled": False}):
                self.assertEqual(len(self._contents()), 1)

        def test_filter_pools_never_touches_following(self):
            p = mk(self.good, "x")
            pools = {"following": [p.id], "recommended": [], "trending": []}
            out, stats = fq.filter_pools(pools, {"enabled": True})
            self.assertEqual(out["following"], [p.id])

        def test_exploration_does_not_give_spam_a_free_test_audience(self):
            from django.test import override_settings as _os

            newbie = User.objects.create_user(username="q_newbie", password="x")
            mk(newbie, "BUY CHEAP COURSES NOW AT BEST PRICE EVER")
            mk(self.good, "A genuinely useful explanation of integration by parts")
            with _os(FEED_QUALITY=ON, FEED_EXPLORE={"enabled": True}):
                got = self._contents()
            self.assertNotIn("BUY CHEAP COURSES NOW AT BEST PRICE EVER", got)

        def test_filter_ids_keeps_clean_and_drops_spam(self):
            clean = mk(self.good, "A genuinely useful explanation of integration by parts")
            spam = mk(self.spammer, "BUY CHEAP COURSES NOW AT BEST PRICE EVER")
            self.assertEqual(fq.filter_ids([clean.id, spam.id], {"enabled": True}), [clean.id])
            self.assertEqual(fq.filter_ids([clean.id, spam.id], {"enabled": False}), [clean.id, spam.id])

        def test_why_has_a_quality_stage_for_discovery_posts(self):
            p = mk(self.good, "A genuinely useful explanation of integration by parts", likes_count=5)
            with override_settings(FEED_QUALITY=ON):
                r = self.client.get(reverse("post-why", args=[p.id]))
            self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
            stages = {s["stage"]: s for s in r.data["stages"]}
            if r.data["feed_source"] in ("recommended", "trending"):
                self.assertEqual(stages["quality"]["verdict"], "ok")
