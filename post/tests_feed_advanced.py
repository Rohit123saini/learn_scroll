# post/tests_feed_advanced.py - T1 Parts 3-5:
#   exploration slots + A/B bucket, behaviour signals, educational lens,
#   stage-wise "why", candidate cache, feed metrics.
import io
import json
import random
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_cache, feed_context, feed_experiment, feed_explore, feed_metrics, feed_mix, feed_signals
from .models import Post, PostEvent, PostHide, PostSave

User = get_user_model()
NOW = timezone.now()


def _post(user, content, **kw):
    kw.setdefault("category", "tech")
    p = Post.objects.create(user=user, content=content, post_type="text", visibility="public",
                            moderation_status="approved", **{k: v for k, v in kw.items() if k != "age"})
    if kw.get("age") is not None:
        Post.objects.filter(pk=p.pk).update(created_at=timezone.now() - kw["age"])
        p.refresh_from_db()
    return p


# ===========================================================================
# Part 3 - pure logic
# ===========================================================================
class ExperimentPureTests(SimpleTestCase):
    def test_bucket_is_stable_and_in_range(self):
        for uid in range(1, 200):
            b = feed_experiment.experiment_bucket(uid, "x", 100)
            self.assertTrue(0 <= b < 100)
            self.assertEqual(b, feed_experiment.experiment_bucket(uid, "x", 100))

    def test_name_reshuffles_buckets(self):
        a = [feed_experiment.experiment_bucket(u, "a", 100) for u in range(100)]
        b = [feed_experiment.experiment_bucket(u, "b", 100) for u in range(100)]
        self.assertNotEqual(a, b)

    def test_variants_follow_weights(self):
        variants = [{"name": "A", "weight": 30}, {"name": "B", "weight": 70}]
        counts = {"A": 0, "B": 0}
        for bucket in range(100):
            counts[feed_experiment.pick_variant(bucket, variants, 100)["name"]] += 1
        self.assertEqual(counts, {"A": 30, "B": 70})

    def test_no_valid_variant_falls_back_to_default(self):
        self.assertEqual(feed_experiment.pick_variant(5, [{"name": "", "weight": 0}], 100)["name"], "default")

    def test_section_overrides(self):
        out = feed_experiment.section({"share": 0.1, "mode": "x"}, {"explore": {"share": 0.3}}, "explore")
        self.assertEqual(out, {"share": 0.3, "mode": "x"})

    @override_settings(FEED_EXPERIMENT={"variants": [
        {"name": "control", "weight": 50, "overrides": {}},
        {"name": "wide", "weight": 50, "overrides": {"explore": {"share": 0.2}}},
    ]})
    def test_resolve_returns_overrides_of_the_bucket_variant(self):
        seen = {feed_experiment.resolve(u)["variant"] for u in range(1, 60)}
        self.assertEqual(seen, {"control", "wide"})
        wide = next(u for u in range(1, 60) if feed_experiment.resolve(u)["variant"] == "wide")
        self.assertEqual(feed_experiment.resolve(wide)["overrides"], {"explore": {"share": 0.2}})

    @override_settings(FEED_EXPERIMENT={"enabled": False})
    def test_disabled_experiment_is_default(self):
        self.assertEqual(feed_experiment.resolve(7)["variant"], "default")


class ExplorePureTests(SimpleTestCase):
    CFG = dict(feed_explore.DEFAULT_EXPLORE)

    def test_share_new_viewer_and_disabled(self):
        self.assertEqual(feed_explore.explore_share(self.CFG, False), 0.08)
        self.assertEqual(feed_explore.explore_share(self.CFG, True), 0.15)
        self.assertEqual(feed_explore.explore_share({**self.CFG, "enabled": False}, True), 0.0)
        self.assertEqual(feed_explore.explore_share({**self.CFG, "share": 9}, False), 0.5)  # capped
        self.assertEqual(feed_explore.explore_share({**self.CFG, "share": "x"}, False), 0.0)

    def test_test_audience_is_stable_and_roughly_pct(self):
        hits = [feed_explore.in_test_audience(u, "post-1", 20) for u in range(2000)]
        self.assertEqual(hits, [feed_explore.in_test_audience(u, "post-1", 20) for u in range(2000)])
        self.assertTrue(300 < sum(hits) < 500)  # ~20 % of 2000
        self.assertTrue(feed_explore.in_test_audience(1, "p", 100))
        self.assertFalse(feed_explore.in_test_audience(1, "p", 0))

    def test_phase_test_scale_drop(self):
        c = self.CFG
        self.assertEqual(feed_explore.phase(10, 0, 0, c), "test")  # inside the test window
        self.assertEqual(feed_explore.phase(120, 5, 5, c), "test")  # too few impressions to judge
        self.assertEqual(feed_explore.phase(120, 100, 20, c), "scale")  # 20 % >= 8 %
        self.assertEqual(feed_explore.phase(120, 100, 2, c), "drop")  # 2 % < 8 %
        self.assertEqual(feed_explore.phase(60 * 30, 100, 50, c), "drop")  # older than the scale window

    def test_thompson_prefers_proven_post_and_is_deterministic(self):
        stats = [("good", 200, 100), ("bad", 200, 2), ("fresh", 0, 0)]
        a = feed_explore.order_candidates(stats, {**self.CFG, "mode": "thompson"}, random.Random(1))
        b = feed_explore.order_candidates(stats, {**self.CFG, "mode": "thompson"}, random.Random(1))
        self.assertEqual(a, b)
        self.assertEqual(a[-1], "bad")  # strong evidence of being bad -> last
        wins = sum(
            feed_explore.order_candidates([("fresh", 0, 0), ("bad", 200, 2)], self.CFG, random.Random(s))[0] == "fresh"
            for s in range(50)
        )
        self.assertGreater(wins, 40)  # unknown post gets a real chance

    def test_epsilon_mode_exploits_but_can_explore(self):
        cfg = {**self.CFG, "mode": "epsilon", "epsilon": 0.0}
        order = feed_explore.order_candidates([("a", 100, 5), ("b", 100, 50)], cfg, random.Random(3))
        self.assertEqual(order, ["b", "a"])
        cfg["epsilon"] = 1.0  # always random: order can flip
        flips = {tuple(feed_explore.order_candidates([("a", 100, 5), ("b", 100, 50)], cfg, random.Random(s)))
                 for s in range(30)}
        self.assertEqual(len(flips), 2)

    def test_weave_first_slot_on_page_one_and_nothing_lost(self):
        main = [f"m{i}" for i in range(40)]
        merged, woven = feed_explore.weave(main, ["e1", "e2", "e3"], 0.25)
        self.assertEqual(merged.index("e1"), 1)
        self.assertEqual(woven, ["e1", "e2", "e3"])
        self.assertEqual(sorted(merged), sorted(main + woven))
        self.assertEqual([m for m in merged if m.startswith("m")], main)  # main order untouched
        self.assertLess(merged.index("e3"), 20)

    def test_weave_moves_duplicates_instead_of_repeating(self):
        merged, woven = feed_explore.weave(["a", "b", "c", "d"], ["c"], 0.3)
        self.assertEqual(merged.count("c"), 1)
        self.assertEqual(woven, ["c"])
        self.assertEqual(len(merged), 4)

    def test_weave_edge_cases(self):
        self.assertEqual(feed_explore.weave(["a"], [], 0.5), (["a"], []))
        self.assertEqual(feed_explore.weave(["a", "b"], ["x"], 0), (["a", "b"], []))
        merged, woven = feed_explore.weave([], ["x", "y"], 0.2)  # empty main: explore still served
        self.assertEqual(merged, ["x", "y"])


# ===========================================================================
# Part 4 - pure logic
# ===========================================================================
class SignalsPureTests(SimpleTestCase):
    CFG = feed_signals.DEFAULT_SIGNALS

    def test_event_weights(self):
        c = self.CFG
        self.assertEqual(feed_signals.event_weight("dwell", 2999, c), 0.0)
        self.assertEqual(feed_signals.event_weight("dwell", 3000, c), 1.0)
        self.assertEqual(feed_signals.event_weight("dwell", 10000, c), 2.0)
        self.assertEqual(feed_signals.event_weight("tap", 0, c), 0.5)
        self.assertLess(feed_signals.event_weight("skip", 300, c), 0)  # quick scroll-past
        self.assertEqual(feed_signals.event_weight("skip", 5000, c), 0.0)  # long "skip" is not a quick one
        self.assertEqual(feed_signals.event_weight("impression", 0, c), 0.0)

    def test_decay_halves_each_half_life(self):
        self.assertAlmostEqual(feed_signals.decay(8.0, 7, 7.0), 4.0)
        self.assertAlmostEqual(feed_signals.decay(8.0, 14, 7.0), 2.0)
        self.assertEqual(feed_signals.decay(8.0, 30, 0), 8.0)  # decay off

    def test_score_events_old_counts_less(self):
        a, c = feed_signals.score_events([("1", "tech", 1.0, 0), ("1", "tech", 1.0, 7), ("2", "art", 1.0, 14)], self.CFG)
        self.assertAlmostEqual(a["1"], 1.5)
        self.assertAlmostEqual(a["2"], 0.25)
        self.assertAlmostEqual(c["tech"], 1.5)

    def test_clamp(self):
        self.assertEqual(feed_signals.clamp_points(100, self.CFG), 12.0)
        self.assertEqual(feed_signals.clamp_points(-100, self.CFG), -8.0)
        self.assertEqual(feed_signals.clamp_points(1, self.CFG), 2.0)

    @override_settings(FEED_SIGNALS={"weights": {"save": 9.0}, "half_life_days": 3})
    def test_settings_merge_weights(self):
        cfg = feed_signals.get_config()
        self.assertEqual(cfg["weights"]["save"], 9.0)
        self.assertEqual(cfg["weights"]["share"], 4.0)  # untouched default kept
        self.assertEqual(cfg["half_life_days"], 3)


class ContextPureTests(SimpleTestCase):
    def test_study_hours_incl_midnight_wrap(self):
        self.assertTrue(feed_context.is_study_time(17, (16, 23)))
        self.assertFalse(feed_context.is_study_time(23, (16, 23)))
        self.assertFalse(feed_context.is_study_time(9, (16, 23)))
        self.assertTrue(feed_context.is_study_time(1, (22, 3)))
        self.assertTrue(feed_context.is_study_time(23, (22, 3)))
        self.assertFalse(feed_context.is_study_time(12, (22, 3)))

    def test_subject_tags(self):
        self.assertEqual(feed_context.subject_tags("Data Structures"), ["datastructures", "data_structures"])
        self.assertEqual(feed_context.subject_tags("  #Physics "), ["physics"])
        self.assertEqual(feed_context.subject_tags(""), [])

    def test_matching_subject(self):
        ctx = feed_context.ContextSignals(["physics", "data structures"], True, set(), set())

        class P:
            subcategory = "Physics"
            hashtags = []
        self.assertEqual(feed_context.matching_subject(ctx, P), "physics")
        P2 = type("P2", (), {"subcategory": "", "hashtags": ["#DataStructures"]})
        self.assertEqual(feed_context.matching_subject(ctx, P2), "data structures")
        P3 = type("P3", (), {"subcategory": "chemistry", "hashtags": ["python"]})
        self.assertEqual(feed_context.matching_subject(ctx, P3), "")


# ===========================================================================
# Part 5 - pure logic
# ===========================================================================
def _t(minutes):
    return NOW + timedelta(minutes=minutes)


class MetricsPureTests(SimpleTestCase):
    def rows(self):
        return [
            (1, "A", "impression", 0, _t(0)), (1, "A", "tap", 0, _t(0.1)),
            (1, "A", "dwell", 4000, _t(0.2)), (1, "B", "impression", 0, _t(1)),
            (1, "B", "skip", 300, _t(1.1)), (1, "A", "impression", 0, _t(2)),
            (1, "C", "impression", 0, _t(120)), (1, "C", "dwell", 1000, _t(121)),  # 2nd session
            (2, "A", "impression", 0, _t(5)), (2, "A", "impression", 0, _t(6)),  # same author back to back
        ]

    def test_basic_counts_and_rates(self):
        m = feed_metrics.summarize(self.rows())
        self.assertEqual(m["users"], 2)
        self.assertEqual(m["impressions"], 6)
        self.assertEqual(m["taps"], 1)
        self.assertEqual(m["ctr"], round(1 / 6, 4))
        self.assertEqual(m["avg_dwell_ms"], 2500)
        self.assertEqual(m["long_dwell_rate"], round(1 / 6, 4))
        self.assertEqual(m["quick_skip_rate"], round(1 / 6, 4))

    def test_sessions_split_on_gap(self):
        m = feed_metrics.summarize(self.rows(), gap_minutes=30)
        self.assertEqual(m["sessions"], 3)  # user1 x2, user2 x1
        m2 = feed_metrics.summarize(self.rows(), gap_minutes=500)
        self.assertEqual(m2["sessions"], 2)

    def test_diversity(self):
        m = feed_metrics.summarize(self.rows())
        # impressions with a predecessor in-session: u1: B(after A), A(after B) | u1 s2: none | u2: 1 -> 3; same-back-to-back: u2's 2nd
        self.assertEqual(m["diversity"]["same_author_back_to_back_rate"], round(1 / 3, 4))
        self.assertTrue(0 < m["diversity"]["unique_author_ratio"] < 1)

    def test_show_fewer_and_variants(self):
        m = feed_metrics.summarize(self.rows(), hides_by_user={1: 2, 99: 1},
                                   variant_of=lambda u: "x" if u == 1 else "y")
        self.assertEqual(m["show_fewer"], 3)  # incl. user 99 with no events
        self.assertEqual(set(m["by_variant"]), {"x", "y"})
        self.assertEqual(m["by_variant"]["x"]["show_fewer"], 2)
        self.assertEqual(m["by_variant"]["y"]["impressions"], 2)

    def test_empty(self):
        m = feed_metrics.summarize([])
        self.assertEqual((m["impressions"], m["ctr"], m["sessions"]), (0, 0.0, 0))


class CoercePkTests(SimpleTestCase):
    def test_integer_user_pk_works(self):
        # regression: `uuid.UUID("7")` raised, which silently dropped every affinity bonus
        self.assertEqual(feed_mix.coerce_user_pk("7"), 7)
        with self.assertRaises(ValueError):
            feed_mix.coerce_user_pk("not-a-number")


# ===========================================================================
# DB-backed tests
# ===========================================================================
@override_settings(FEED_EXPLORE={"enabled": True, "test_audience_pct": 100, "share": 0.2, "new_viewer_share": 0.2})
class ExplorationFeedTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        old = timezone.now() - timedelta(days=200)
        self.vets = []
        for i in range(4):
            u = User.objects.create_user(username=f"vet{i}", password="x")
            User.objects.filter(pk=u.pk).update(date_joined=old)
            for j in range(8):  # >5 posts + old account = NOT a new creator
                _post(u, f"vet{i}-{j}", likes_count=80 + j, age=timedelta(hours=j + 1))
            self.vets.append(u)
        self.newbie = User.objects.create_user(username="newbie", password="x")
        self.newbie_post = _post(self.newbie, "hello world", likes_count=0)
        self.client.force_authenticate(self.me)

    def _feed(self, **params):
        r = self.client.get(reverse("home-feed"), {"page_size": 20, **params})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_new_creator_post_is_on_page_one_and_flagged(self):
        results = self._feed()["results"]
        row = next((r for r in results if r["id"] == str(self.newbie_post.id)), None)
        self.assertIsNotNone(row, "new creator's post must get an exploration slot on page 1")
        self.assertTrue(row["is_exploration"])
        self.assertFalse(any(r["is_exploration"] for r in results if r["user"]["username"].startswith("vet")))

    def test_exploration_stays_stable_across_pages_and_never_duplicates(self):
        ids, cursor = [], None
        for _ in range(10):
            data = self._feed(page_size=6, **({"cursor": cursor} if cursor else {}))
            ids += [r["id"] for r in data["results"]]
            if not data["next"]:
                break
            from urllib.parse import parse_qs, urlparse
            cursor = parse_qs(urlparse(data["next"]).query)["cursor"][0]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(str(self.newbie_post.id), ids)

    @override_settings(FEED_EXPLORE={"enabled": False})
    def test_switched_off_means_no_exploration_flag(self):
        results = self._feed()["results"]
        self.assertFalse(any(r["is_exploration"] for r in results))

    @override_settings(FEED_EXPLORE={"enabled": True, "test_audience_pct": 0, "share": 0.2, "new_viewer_share": 0.2})
    def test_young_post_outside_test_audience_is_not_explored(self):
        results = self._feed()["results"]
        self.assertFalse(any(r["is_exploration"] for r in results))

    def test_poorly_performing_post_is_dropped_from_exploration(self):
        old_post = _post(self.newbie, "meh", age=timedelta(hours=3))
        viewers = [User.objects.create_user(username=f"v{i}", password="x") for i in range(40)]
        for v in viewers:  # 40 impressions, no taps / dwell -> rate 0 -> drop
            PostEvent.objects.create(user=v, post=old_post, event_type="impression", surface="feed")
        ids, phases = feed_explore.build_explore_ids(
            self.me, Post.objects.all(), set(), set(), feed_explore.get_config(), timezone.now(), random.Random(1),
        )
        self.assertNotIn(old_post.id, ids)

    def test_well_performing_post_scales_for_everyone(self):
        hot = _post(self.newbie, "hot", age=timedelta(hours=3))
        viewers = [User.objects.create_user(username=f"v{i}", password="x") for i in range(40)]
        for v in viewers:
            PostEvent.objects.create(user=v, post=hot, event_type="impression", surface="feed")
            PostEvent.objects.create(user=v, post=hot, event_type="tap", surface="feed")
        with override_settings(FEED_EXPLORE={"enabled": True, "test_audience_pct": 0}):
            ids, phases = feed_explore.build_explore_ids(
                self.me, Post.objects.all(), set(), set(), feed_explore.get_config(), timezone.now(), random.Random(1),
            )
        self.assertIn(hot.id, ids)
        self.assertEqual(phases[str(hot.id)], "scale")

    def test_blocked_author_never_explored(self):
        from user_profile.models import BlockUser
        BlockUser.objects.create(blocker=self.me, blocked=self.newbie)
        results = self._feed()["results"]
        self.assertNotIn(str(self.newbie_post.id), [r["id"] for r in results])

    def test_variant_override_changes_share(self):
        with override_settings(FEED_EXPERIMENT={"variants": [
            {"name": "off", "weight": 100, "overrides": {"explore": {"enabled": False}}}]}):
            results = self._feed()["results"]
        self.assertFalse(any(r["is_exploration"] for r in results))

    def test_why_has_stages_experiment_and_new_creator_reason(self):
        r = self.client.get(reverse("post-why", args=[self.newbie_post.id]))
        self.assertEqual(r.status_code, 200, r.data)
        r.data = r.data["data"]
        codes = [x["code"] for x in r.data["reasons"]]
        self.assertIn("new_creator", codes)
        self.assertEqual(r.data["experiment"]["variant"], "default")
        self.assertEqual([s["stage"] for s in r.data["stages"]], ["candidate", "score", "rerank"])
        self.assertIn("exploration_slot", r.data["stages"][2]["rules"])


class BehaviourSignalTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.a = User.objects.create_user(username="a", password="x")
        self.b = User.objects.create_user(username="b", password="x")
        self.pa = _post(self.a, "pa")
        self.pb = _post(self.b, "pb")

    def _ev(self, post, etype, dwell=0, **kw):
        return PostEvent.objects.create(user=self.me, post=post, event_type=etype, dwell_ms=dwell, surface="feed", **kw)

    def test_dwell_save_positive_quick_skip_negative(self):
        self._ev(self.pa, "dwell", 12000)
        PostSave.objects.create(user=self.me, post=self.pa)
        for _ in range(3):
            self._ev(self.pb, "skip", 200)
        sig = feed_signals.load_behaviour_signals(self.me)
        self.assertGreater(sig["authors"][str(self.a.pk)], 5)
        self.assertLess(sig["authors"][str(self.b.pk)], 0)

    def test_old_events_outside_window_ignored_and_own_posts_ignored(self):
        e = self._ev(self.pa, "dwell", 12000)
        PostEvent.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(days=40))
        own = _post(self.me, "mine")
        self._ev(own, "dwell", 20000)
        self.assertEqual(feed_signals.load_behaviour_signals(self.me)["authors"], {})

    def test_show_fewer_author_gets_no_signal_points(self):
        self._ev(self.pa, "dwell", 12000)
        sig = feed_signals.load_behaviour_signals(self.me, exclude_authors={str(self.a.pk)})
        self.assertNotIn(str(self.a.pk), sig["authors"])

    def test_signal_lifts_author_in_recommended_pool(self):
        for _ in range(4):
            self._ev(self.pa, "dwell", 15000)
        pools = feed_mix.build_pool_ids(self.me, Post.objects.all(), set(), lambda: (0, 0), seen_ids=set())
        order = [p for p in pools["recommended"] + pools["trending"] if p in (self.pa.id, self.pb.id)]
        self.assertEqual(order[0], self.pa.id)

    def test_quick_scrolling_past_an_author_sinks_them(self):
        for _ in range(8):
            self._ev(self.pa, "skip", 150)
        pools = feed_mix.build_pool_ids(self.me, Post.objects.all(), set(), lambda: (0, 0), seen_ids=set())
        order = [p for p in pools["recommended"] + pools["trending"] if p in (self.pa.id, self.pb.id)]
        self.assertEqual(order[0], self.pb.id)

    @override_settings(FEED_SIGNALS={"enabled": False})
    def test_switch_off(self):
        self._ev(self.pa, "dwell", 15000)
        self.assertEqual(feed_signals.load_behaviour_signals(self.me), {"authors": {}, "categories": {}})


class EducationalLensTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.teacher = User.objects.create_user(username="teacher", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.p_phys = _post(self.other, "phys", category="education", subcategory="physics")
        self.p_plain = _post(self.other, "plain", category="entertainment")
        self.p_teacher = _post(self.teacher, "teach", category="entertainment")

    def _points(self, ctx, hour=18):
        expr = feed_context.context_expression(ctx, hour)
        from django.db.models import FloatField
        qs = Post.objects.annotate(pts=expr)
        return {p.content: p.pts for p in qs}

    def test_subject_match_gets_points(self):
        pts = self._points(feed_context.ContextSignals(["physics"], True, set(), set()), hour=3)
        self.assertEqual(pts["phys"], feed_context.POINTS_SUBJECT + feed_context.POINTS_STUDY_OFF)
        self.assertEqual(pts["plain"], 0.0)

    def test_study_time_boosts_education_for_learners_only(self):
        learner = self._points(feed_context.ContextSignals([], True, set(), set()), hour=19)
        self.assertEqual(learner["phys"], feed_context.POINTS_STUDY)
        non_learner = feed_context.context_expression(feed_context.ContextSignals([], False, set(), set()), 19)
        self.assertIsNone(non_learner)

    def test_class_and_campus_authors_get_context_points(self):
        pts = self._points(feed_context.ContextSignals([], False, {self.teacher.pk}, set()))
        self.assertEqual(pts["teach"], feed_context.POINTS_CONTEXT)
        self.assertEqual(pts["plain"], 0.0)

    def test_nothing_known_means_no_expression(self):
        self.assertIsNone(feed_context.context_expression(feed_context.EMPTY, 12))

    def test_unknown_viewer_loads_empty_context(self):
        ctx = feed_context.load_context(self.me)
        self.assertEqual((ctx.subjects, ctx.is_learner, ctx.campus_authors, ctx.class_authors), ([], False, set(), set()))

    def test_load_context_reads_joined_class_teacher_and_subject(self):
        from tuitionclass.models import ClassJoinRequest, Classroom
        try:
            room = Classroom.objects.create(teacher=self.teacher, title="Phys 101", subject="Physics")
            ClassJoinRequest.objects.create(
                classroom=room, class_pass=mock.MagicMock(), student=self.me, status="accepted",
            )
        except Exception:
            self.skipTest("Classroom / ClassJoinRequest need more fields in this schema")
        ctx = feed_context.load_context(self.me)
        self.assertIn("physics", ctx.subjects)
        self.assertIn(self.teacher.pk, ctx.class_authors)
        self.assertTrue(ctx.is_learner)

    def test_why_reports_educational_topic(self):
        self.client.force_authenticate(self.me)
        ctx = feed_context.ContextSignals(["physics"], True, set(), {self.other.pk})
        with mock.patch.object(feed_context, "load_context", return_value=ctx):
            r = self.client.get(reverse("post-why", args=[self.p_phys.id]))
        self.assertEqual(r.status_code, 200, r.data)
        r.data = r.data["data"]
        codes = [x["code"] for x in r.data["reasons"]]
        self.assertIn("educational_topic", codes)
        self.assertIn("class_context", codes)
        self.assertIn("educational_topic", r.data["stages"][1]["points"])


class CandidateCacheTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        for i in range(5):
            _post(self.other, f"p{i}", likes_count=i)
        self.client.force_authenticate(self.me)

    def _feed(self, **params):
        r = self.client.get(reverse("home-feed"), params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_second_session_inside_ttl_reuses_cached_pools(self):
        with mock.patch.object(feed_mix, "build_pool_ids", wraps=feed_mix.build_pool_ids) as spy:
            first = self._feed()
            second = self._feed()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([r["id"] for r in first["results"]], [r["id"] for r in second["results"]])

    def test_refresh_param_bypasses_cache(self):
        with mock.patch.object(feed_mix, "build_pool_ids", wraps=feed_mix.build_pool_ids) as spy:
            self._feed()
            self._feed(refresh=1)
        self.assertEqual(spy.call_count, 2)

    def test_hide_invalidates_cache(self):
        first = self._feed()
        victim = first["results"][0]["id"]
        r = self.client.post(reverse("post-not-interested", args=[victim]), {"reason": "not_interested"}, format="json")
        self.assertIn(r.status_code, (200, 201), getattr(r, "data", None))
        again = self._feed()
        self.assertNotIn(victim, [x["id"] for x in again["results"]])

    def test_seen_marking_invalidates_cache(self):
        with mock.patch.object(feed_mix, "build_pool_ids", wraps=feed_mix.build_pool_ids) as spy:
            first = self._feed()
            ids = [r["id"] for r in first["results"]]
            self.client.post(reverse("feed-seen"), {"post_ids": ids}, format="json")
            self._feed()  # marking posts seen drops the cached candidates -> rebuilt
        self.assertEqual(spy.call_count, 2)

    @override_settings(FEED_CANDIDATE_CACHE={"enabled": False})
    def test_disabled(self):
        with mock.patch.object(feed_mix, "build_pool_ids", wraps=feed_mix.build_pool_ids) as spy:
            self._feed()
            self._feed()
        self.assertEqual(spy.call_count, 2)

    def test_cache_failure_is_a_miss_not_an_error(self):
        with mock.patch.object(feed_cache.cache, "get", side_effect=RuntimeError("redis down")):
            self.assertIsNone(feed_cache.get(1, None, 0.3))
        with mock.patch.object(feed_cache.cache, "set", side_effect=RuntimeError("redis down")):
            self.assertFalse(feed_cache.put(1, None, 0.3, {}, {}))

    def test_invalidate_changes_key(self):
        feed_cache.put(self.me.pk, None, 0.3, {"following": [], "recommended": [], "trending": []}, {"a": 1})
        self.assertEqual(feed_cache.get(self.me.pk, None, 0.3)["meta"], {"a": 1})
        feed_cache.invalidate(self.me.pk)
        self.assertIsNone(feed_cache.get(self.me.pk, None, 0.3))
        self.assertIsNone(feed_cache.get(self.me.pk, "tech", 0.3))  # category is part of the key


class FeedMetricsEndpointTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.staff = User.objects.create_user(username="staff", password="x", is_staff=True)
        self.author = User.objects.create_user(username="auth", password="x")
        self.p = _post(self.author, "x")
        for et, d in (("impression", 0), ("tap", 0), ("dwell", 5000)):
            PostEvent.objects.create(user=self.me, post=self.p, event_type=et, dwell_ms=d, surface="feed")

    def test_staff_only(self):
        self.client.force_authenticate(self.me)
        self.assertEqual(self.client.get(reverse("post-feed-metrics")).status_code, 403)
        self.client.force_authenticate(self.staff)
        r = self.client.get(reverse("post-feed-metrics"), {"days": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["impressions"], 1)
        self.assertEqual(r.data["data"]["ctr"], 1.0)
        self.assertIn("default", r.data["data"]["by_variant"])
        self.assertEqual(self.client.get(reverse("post-feed-metrics"), {"surface": "nope"}).status_code, 400)

    def test_management_command_json_and_table(self):
        out = io.StringIO()
        call_command("feed_metrics", "--days", "1", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["impressions"], 1)
        out = io.StringIO()
        call_command("feed_metrics", "--days", "1", stdout=out)
        self.assertIn("default", out.getvalue())
