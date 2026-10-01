# post/tests_show_fewer_why.py
# Feed feedback controls - PART 2: "Show fewer like this" (FeedFeedback negative
# ranking signal) and "Why am I seeing this" (GET /post/<id>/why/).
#
#   PureLogicTests      decay / bump / points / horizon + feed_explain.build_reasons (no DB)
#   FeedFeedbackModel   constraints
#   LoaderTests         load_feedback_effective / _penalties (decay, caps, switches)
#   ServiceTests        record_feed_feedback / apply_show_fewer / prune
#   RankingTests        feed_mix.build_pool_ids: penalty pushes down, following untouched
#   ShowFewerAPITests   POST /post/<id>/show-fewer/
#   FeedbackManageTests GET /post/feedback/, DELETE /post/feedback/<id>/, undo
#   FeedEffectTests     end to end through GET /post/feed/
#   WhyAPITests         GET /post/<id>/why/ (one test per reason + mirroring the ranking)
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_explain, feed_mix
from .models import FeedFeedback, Post, PostHide, PostLike, UserInterest
from .services import (
    ShowFewerError,
    apply_show_fewer,
    prune_stale_feedback,
    record_feed_feedback,
)

User = get_user_model()


def _boost():
    return 0.0, 0.0


def make_post(user, content, days_old=0, **kw):
    kw.setdefault("visibility", "public")
    kw.setdefault("moderation_status", "approved")
    kw.setdefault("post_type", "text")
    kw.setdefault("category", "tech")
    post = Post.objects.create(user=user, content=content, **kw)
    if days_old:
        Post.objects.filter(pk=post.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        post.refresh_from_db()
    return post


def backdate(row, days):
    """FeedFeedback.updated_at is auto_now -> only queryset.update() can move it."""
    FeedFeedback.objects.filter(pk=row.pk).update(updated_at=timezone.now() - timedelta(days=days))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.x = User.objects.create_user(username="xavier", password="x")
        self.y = User.objects.create_user(username="yara", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.following_ids = {self.friend.id}

    def pools(self, **kw):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        return feed_mix.build_pool_ids(self.me, base, self.following_ids, _boost, **kw)

    def contents(self, ids):
        by_id = dict(Post.objects.filter(id__in=ids).values_list("id", "content"))
        return [by_id[i] for i in ids]

    def fb(self, kind, key, weight=1.0):
        return FeedFeedback.objects.create(user=self.me, kind=kind, key=str(key), weight=weight)


# --------------------------------------------------------------------------
# Pure logic - no database
# --------------------------------------------------------------------------
class PureLogicTests(SimpleTestCase):
    def test_decay_halves_every_half_life(self):
        self.assertAlmostEqual(feed_mix.decay_weight(2.0, 0, 30), 2.0)
        self.assertAlmostEqual(feed_mix.decay_weight(2.0, 30, 30), 1.0)
        self.assertAlmostEqual(feed_mix.decay_weight(2.0, 60, 30), 0.5)

    def test_decay_permanent_when_half_life_is_zero_or_none(self):
        self.assertEqual(feed_mix.decay_weight(2.0, 10_000, 0), 2.0)
        self.assertEqual(feed_mix.decay_weight(2.0, 10_000, None), 2.0)

    def test_decay_edge_cases(self):
        self.assertEqual(feed_mix.decay_weight(0, 5, 30), 0.0)
        self.assertEqual(feed_mix.decay_weight(-1, 5, 30), 0.0)
        self.assertAlmostEqual(feed_mix.decay_weight(1.0, -5, 30), 1.0)  # clock skew -> age 0

    def test_bump_adds_step_and_caps(self):
        self.assertEqual(feed_mix.bump_weight(0.0, 1.0, 3.0), 1.0)
        self.assertEqual(feed_mix.bump_weight(2.5, 1.0, 3.0), 3.0)
        self.assertEqual(feed_mix.bump_weight(3.0, 1.0, 3.0), 3.0)

    def test_points_scale_with_weight(self):
        pts = feed_mix.DEFAULT_FEEDBACK["points"]
        self.assertEqual(feed_mix.feedback_points("category", 1.0, pts), 15.0)
        self.assertEqual(feed_mix.feedback_points("author", 2.0, pts), 50.0)
        self.assertEqual(feed_mix.feedback_points("hashtag", 0.5, pts), 4.0)
        self.assertEqual(feed_mix.feedback_points("nope", 1.0, pts), 0.0)

    def test_category_penalty_mirrors_interest_bonus(self):
        self.assertEqual(feed_mix.DEFAULT_FEEDBACK["points"]["category"], feed_mix.POINTS_INTEREST)

    def test_horizon(self):
        cfg = dict(feed_mix.DEFAULT_FEEDBACK)
        horizon = feed_mix.feedback_horizon_days(cfg)
        # 30-day half-life, from max_weight 3.0 down to min_effective 0.05 = ~5.9 half-lives
        self.assertAlmostEqual(horizon, 30 * 5.906, delta=0.1)
        cfg["half_life_days"] = 0
        self.assertIsNone(feed_mix.feedback_horizon_days(cfg))


class BuildReasonsTests(SimpleTestCase):
    def sig(self, **kw):
        base = dict(author_id="u1", author_username="ann", category="tech", category_label="Technology")
        base.update(kw)
        return feed_explain.PostSignals(**base)

    def codes(self, reasons):
        return [r["code"] for r in reasons]

    def test_own_post(self):
        source, reasons = feed_explain.build_reasons(self.sig(is_own=True))
        self.assertIsNone(source)
        self.assertEqual(self.codes(reasons), ["own_post"])

    def test_following_only_reports_following(self):
        source, reasons = feed_explain.build_reasons(
            self.sig(following=True, interest_category=True, trending=True, discovery=True))
        self.assertEqual(source, "following")
        self.assertEqual(self.codes(reasons), ["following"])
        self.assertIn("@ann", reasons[0]["text"])
        self.assertEqual(reasons[0]["meta"], {"user_id": "u1", "username": "ann"})

    def test_trending_only_reports_trending(self):
        source, reasons = feed_explain.build_reasons(
            self.sig(trending=True, discovery=True, interest_category=True, liked_category=True))
        self.assertEqual(source, "trending")
        self.assertEqual(self.codes(reasons), ["trending"])

    def test_recommended_lists_every_matching_signal_in_order(self):
        via = [{"id": "f1", "username": "bob"}, {"id": "f2", "username": "cy"}]
        source, reasons = feed_explain.build_reasons(self.sig(
            discovery=True, interest_category=True, liked_category=True, friend_of_follow=True, fof_via=via))
        self.assertEqual(source, "recommended")
        self.assertEqual(self.codes(reasons), ["interest_category", "liked_category", "friend_of_follow"])
        self.assertEqual(reasons[0]["meta"], {"category": "tech", "label": "Technology"})
        self.assertIn("@bob, @cy", reasons[2]["text"])
        self.assertEqual(reasons[2]["meta"]["via"], via)

    def test_recommended_without_personal_signal_is_popular(self):
        source, reasons = feed_explain.build_reasons(self.sig(discovery=True))
        self.assertEqual(source, "recommended")
        self.assertEqual(self.codes(reasons), ["popular"])

    def test_friend_of_follow_without_names(self):
        _, reasons = feed_explain.build_reasons(self.sig(discovery=True, friend_of_follow=True))
        self.assertEqual(self.codes(reasons), ["friend_of_follow"])
        self.assertIn("People you follow also follow @ann", reasons[0]["text"])

    def test_not_in_feed(self):
        source, reasons = feed_explain.build_reasons(self.sig())
        self.assertIsNone(source)
        self.assertEqual(self.codes(reasons), ["not_in_feed"])


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
class FeedFeedbackModelTests(Base):
    def test_unique_per_user_kind_key(self):
        self.fb("category", "tech")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.fb("category", "tech")
        self.fb("hashtag", "tech")  # same key, other kind -> fine

    def test_weight_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.fb("category", "tech", weight=0)

    def test_cascades_with_user(self):
        self.fb("category", "tech")
        self.me.delete()
        self.assertEqual(FeedFeedback.objects.count(), 0)


# --------------------------------------------------------------------------
# Loaders: decay, caps, switches
# --------------------------------------------------------------------------
class LoaderTests(Base):
    def test_effective_weight_is_decayed_to_now(self):
        row = self.fb("category", "tech", weight=2.0)
        backdate(row, 30)
        eff = feed_mix.load_feedback_effective(self.me)
        self.assertAlmostEqual(eff["category"]["tech"], 1.0, places=2)

    def test_fresh_row_is_full_strength(self):
        self.fb("author", self.x.id, weight=2.0)
        self.assertAlmostEqual(feed_mix.load_feedback_effective(self.me)["author"][str(self.x.id)], 2.0, places=3)

    def test_decayed_rows_are_dropped(self):
        old = self.fb("category", "news", weight=1.0)
        backdate(old, 400)  # far beyond the horizon
        weak = self.fb("hashtag", "py", weight=1.0)
        backdate(weak, 30 * 5)  # 1.0 * 0.5**5 = 0.03 < min_effective 0.05
        eff = feed_mix.load_feedback_effective(self.me)
        self.assertEqual(eff, {"category": {}, "hashtag": {}, "author": {}})

    @override_settings(FEED_FEEDBACK={"half_life_days": 0})
    def test_zero_half_life_means_permanent(self):
        row = self.fb("category", "tech", weight=2.0)
        backdate(row, 1000)
        self.assertEqual(feed_mix.load_feedback_effective(self.me)["category"], {"tech": 2.0})

    @override_settings(FEED_FEEDBACK={"enabled": False})
    def test_switch_off_ignores_everything(self):
        self.fb("category", "tech")
        self.assertEqual(feed_mix.load_feedback_effective(self.me), {"category": {}, "hashtag": {}, "author": {}})
        self.assertIsNone(feed_mix.penalty_expression(feed_mix.load_feedback_penalties(self.me)))

    def test_penalties_use_per_kind_points(self):
        self.fb("category", "tech", 1.0)
        self.fb("hashtag", "py", 2.0)
        self.fb("author", self.x.id, 1.0)
        pen = feed_mix.load_feedback_penalties(self.me)
        self.assertAlmostEqual(pen["category"]["tech"], 15.0, places=2)
        self.assertAlmostEqual(pen["hashtag"]["py"], 16.0, places=2)
        self.assertAlmostEqual(pen["author"][str(self.x.id)], 25.0, places=2)

    @override_settings(FEED_FEEDBACK={"points": {"category": 40.0}})
    def test_partial_points_override_keeps_other_defaults(self):
        self.fb("category", "tech")
        self.fb("author", self.x.id)
        pen = feed_mix.load_feedback_penalties(self.me)
        self.assertAlmostEqual(pen["category"]["tech"], 40.0, places=2)
        self.assertAlmostEqual(pen["author"][str(self.x.id)], 25.0, places=2)

    @override_settings(FEED_FEEDBACK={"load_cap": {"hashtag": 2}})
    def test_load_cap_keeps_the_strongest(self):
        self.fb("hashtag", "a", 1.0)
        self.fb("hashtag", "b", 3.0)
        self.fb("hashtag", "c", 2.0)
        self.assertEqual(list(feed_mix.load_feedback_effective(self.me)["hashtag"]), ["b", "c"])

    def test_other_users_rows_do_not_leak(self):
        FeedFeedback.objects.create(user=self.friend, kind="category", key="tech")
        self.assertEqual(feed_mix.load_feedback_effective(self.me)["category"], {})

    def test_penalty_expression_none_without_rows(self):
        self.assertIsNone(feed_mix.penalty_expression(feed_mix.load_feedback_penalties(self.me)))

    def test_category_penalty_skipped_when_category_is_filtered(self):
        self.fb("category", "tech")
        self.assertIsNone(feed_mix.penalty_expression(feed_mix.load_feedback_penalties(self.me), category_filtered=True))
        self.assertIsNotNone(feed_mix.penalty_expression(feed_mix.load_feedback_penalties(self.me)))


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------
class ServiceTests(Base):
    def test_taps_accumulate_and_cap(self):
        weights = [record_feed_feedback(self.me, "category", "tech").weight for _ in range(5)]
        self.assertEqual(weights, [1.0, 2.0, 3.0, 3.0, 3.0])
        self.assertEqual(FeedFeedback.objects.count(), 1)

    def test_tap_after_decay_starts_from_what_is_left(self):
        row = self.fb("category", "tech", weight=2.0)
        backdate(row, 30)  # decays to ~1.0
        row = record_feed_feedback(self.me, "category", "tech")
        self.assertAlmostEqual(row.weight, 2.0, places=2)  # ~1.0 + step 1.0, NOT 3.0

    def test_tap_refreshes_updated_at(self):
        row = self.fb("category", "tech")
        backdate(row, 10)
        row = record_feed_feedback(self.me, "category", "tech")
        row.refresh_from_db()
        self.assertLess((timezone.now() - row.updated_at).total_seconds(), 60)

    def test_prune_removes_only_dead_rows(self):
        dead = self.fb("category", "news")
        backdate(dead, 400)
        alive = self.fb("category", "tech")
        self.assertEqual(prune_stale_feedback(self.me), 1)
        self.assertEqual(list(FeedFeedback.objects.values_list("pk", flat=True)), [alive.pk])

    @override_settings(FEED_FEEDBACK={"half_life_days": 0})
    def test_prune_is_a_noop_for_permanent_feedback(self):
        row = self.fb("category", "tech")
        backdate(row, 5000)
        self.assertEqual(prune_stale_feedback(self.me), 0)

    def test_apply_show_fewer_hides_and_records(self):
        post = make_post(self.x, "p", hashtags=["python", "django"])
        hide, created, rows = apply_show_fewer(
            self.me, post, [("category", None), ("hashtag", "#Python"), ("author", None)], "not_relevant")
        self.assertTrue(created)
        self.assertEqual(hide.reason, "not_relevant")
        self.assertEqual([(r.kind, r.key) for r in rows],
                         [("category", "tech"), ("hashtag", "python"), ("author", str(self.x.id))])
        self.assertTrue(PostHide.objects.filter(user=self.me, post=post).exists())

    def test_apply_show_fewer_is_all_or_nothing(self):
        post = make_post(self.x, "p", hashtags=["python"])
        with self.assertRaises(ShowFewerError):
            apply_show_fewer(self.me, post, [("category", None), ("hashtag", "rust")], "not_interested")
        self.assertFalse(PostHide.objects.exists())
        self.assertFalse(FeedFeedback.objects.exists())

    def test_apply_show_fewer_requires_a_target_and_dedupes(self):
        post = make_post(self.x, "p")
        with self.assertRaises(ShowFewerError):
            apply_show_fewer(self.me, post, [], "not_interested")
        _, _, rows = apply_show_fewer(self.me, post, [("category", None), ("category", "junk")], "not_interested")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].weight, 1.0)  # one request = one step, even with a duplicate target


# --------------------------------------------------------------------------
# Ranking (feed_mix.build_pool_ids)
# --------------------------------------------------------------------------
class RankingTests(Base):
    def test_trending_author_penalty_pushes_down_but_keeps(self):
        make_post(self.x, "x-old")
        make_post(self.y, "y-new")
        self.assertEqual(self.contents(self.pools()["trending"]), ["y-new", "x-old"])
        self.fb("author", self.y.id)
        self.assertEqual(self.contents(self.pools()["trending"]), ["x-old", "y-new"])

    def test_recommended_category_penalty(self):
        make_post(self.x, "news-old", days_old=12, category="news")
        make_post(self.y, "tech-new", days_old=10, category="tech")
        pools = self.pools()
        self.assertEqual(pools["trending"], [])  # both older than the 7-day trending window
        self.assertEqual(self.contents(pools["recommended"]), ["tech-new", "news-old"])
        self.fb("category", "tech")
        self.assertEqual(self.contents(self.pools()["recommended"]), ["news-old", "tech-new"])

    def test_category_penalty_cancels_the_interest_bonus(self):
        # tech is an explicit interest (+15) and was tapped once (-15): back to the neutral order.
        UserInterest.objects.create(user=self.me, category="tech")
        make_post(self.x, "news-new", days_old=10, category="news")
        make_post(self.y, "tech-old", days_old=12, category="tech")
        self.assertEqual(self.contents(self.pools()["recommended"]), ["tech-old", "news-new"])
        self.fb("category", "tech", 1.0)
        self.assertEqual(self.contents(self.pools()["recommended"]), ["news-new", "tech-old"])

    def test_hashtag_penalty_matches_whole_tags_only(self):
        make_post(self.x, "c-none", days_old=12, hashtags=[])
        make_post(self.x, "b-pythonista", days_old=11, hashtags=["pythonista"])
        make_post(self.x, "a-python", days_old=10, hashtags=["python", "x"])
        self.assertEqual(self.contents(self.pools()["recommended"]), ["a-python", "b-pythonista", "c-none"])
        self.fb("hashtag", "python")
        self.assertEqual(self.contents(self.pools()["recommended"]), ["b-pythonista", "c-none", "a-python"])

    def test_several_disliked_hashtags_count_once_the_strongest(self):
        make_post(self.x, "b-one-tag", days_old=10, hashtags=["c"])  # newer -> first without penalties
        make_post(self.y, "a-two-tags", days_old=11, hashtags=["a", "b"])
        self.assertEqual(self.contents(self.pools()["recommended"]), ["b-one-tag", "a-two-tags"])
        self.fb("hashtag", "a", 1.0)  # 8 points
        self.fb("hashtag", "b", 1.0)  # 8 points
        self.fb("hashtag", "c", 1.5)  # 12 points
        # a-two-tags: strongest single tag = 8 (NOT 8 + 8 = 16); b-one-tag: 12.
        # -> the post with two disliked tags is punished LESS than the one with one strong tag.
        self.assertEqual(self.contents(self.pools()["recommended"]), ["a-two-tags", "b-one-tag"])

    def test_following_pool_is_never_touched(self):
        make_post(self.friend, "f-tech-old", days_old=1, category="tech")
        make_post(self.friend, "f-news-new", category="news")
        before = self.contents(self.pools()["following"])
        self.assertEqual(before, ["f-news-new", "f-tech-old"])
        self.fb("category", "news", 3.0)
        self.fb("author", self.friend.id, 3.0)
        self.assertEqual(self.contents(self.pools()["following"]), before)

    def test_penalty_never_removes_a_post(self):
        make_post(self.x, "x1")
        make_post(self.x, "x2", days_old=10)
        self.fb("author", self.x.id, 3.0)
        pools = self.pools()
        self.assertEqual(sorted(self.contents(pools["trending"] + pools["recommended"])), ["x1", "x2"])

    def test_decayed_feedback_stops_mattering(self):
        make_post(self.x, "x-old")
        make_post(self.y, "y-new")
        row = self.fb("author", self.y.id)
        self.assertEqual(self.contents(self.pools()["trending"]), ["x-old", "y-new"])
        backdate(row, 400)
        self.assertEqual(self.contents(self.pools()["trending"]), ["y-new", "x-old"])

    @override_settings(FEED_FEEDBACK={"enabled": False})
    def test_master_switch(self):
        make_post(self.x, "x-old")
        make_post(self.y, "y-new")
        self.fb("author", self.y.id)
        self.assertEqual(self.contents(self.pools()["trending"]), ["y-new", "x-old"])

    def test_category_filter_request_still_works(self):
        make_post(self.y, "t2")
        make_post(self.x, "t1", hashtags=["py"])  # newer -> first without penalties
        make_post(self.y, "n1", category="news")
        self.assertEqual(self.contents(self.pools(category="tech")["trending"]), ["t1", "t2"])
        self.fb("category", "tech")  # constant inside ?category=tech -> skipped
        self.fb("hashtag", "py")  # still applies
        pools = self.pools(category="tech")
        self.assertEqual(self.contents(pools["trending"]), ["t2", "t1"])

    def test_other_users_feedback_does_not_change_my_pools(self):
        make_post(self.x, "x-old")
        make_post(self.y, "y-new")
        FeedFeedback.objects.create(user=self.friend, kind="author", key=str(self.y.id), weight=3.0)
        self.assertEqual(self.contents(self.pools()["trending"]), ["y-new", "x-old"])

    def test_trending_rank_positions(self):
        a = make_post(self.x, "a")
        b = make_post(self.y, "b")
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        rank = lambda p: feed_mix.trending_rank(self.me, base, self.following_ids, _boost, p)
        self.assertEqual((rank(b), rank(a)), (0, 1))
        self.fb("author", self.y.id)
        self.assertEqual((rank(b), rank(a)), (1, 0))

    def test_trending_rank_none_when_not_eligible(self):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        rank = lambda p: feed_mix.trending_rank(self.me, base, self.following_ids, _boost, p)
        self.assertIsNone(rank(make_post(self.friend, "followed")))  # following has its own pool
        self.assertIsNone(rank(make_post(self.x, "too-old", days_old=10)))  # outside the 7-day window


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
class ApiBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)

    def show_fewer(self, post, targets, **extra):
        return self.client.post(
            reverse("post-show-fewer", kwargs={"post_id": post.id}), {"targets": targets, **extra}, format="json")

    def why(self, post):
        return self.client.get(reverse("post-why", kwargs={"post_id": post.id}))

    def feed_order(self, **params):
        params.setdefault("page_size", 50)
        r = self.client.get(reverse("home-feed"), params)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return [x["content"] for x in r.data["results"]]


class ShowFewerAPITests(ApiBase):
    def test_requires_auth(self):
        p = make_post(self.stranger, "x")
        self.client.force_authenticate(None)
        self.assertEqual(self.show_fewer(p, [{"kind": "category"}]).status_code, 401)

    def test_hides_and_dampens_all_three_targets(self):
        p = make_post(self.stranger, "x", hashtags=["python", "django"])
        r = self.show_fewer(p, [{"kind": "category"}, {"kind": "hashtag", "key": "python"}, {"kind": "author"}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["success"])
        self.assertEqual(r.data["data"]["hidden"]["post_id"], str(p.id))
        self.assertEqual(r.data["data"]["hidden"]["reason"], "not_interested")
        fb = r.data["data"]["feedback"]
        self.assertEqual([(f["kind"], f["key"], f["label"], f["strength"]) for f in fb], [
            ("category", "tech", "Technology", 1.0),
            ("hashtag", "python", "#python", 1.0),
            ("author", str(self.stranger.id), "@stranger", 1.0),
        ])
        self.assertTrue(PostHide.objects.filter(user=self.me, post=p).exists())
        self.assertEqual(FeedFeedback.objects.filter(user=self.me).count(), 3)

    def test_second_tap_is_200_and_adds_a_step(self):
        p = make_post(self.stranger, "x")
        self.assertEqual(self.show_fewer(p, [{"kind": "category"}]).status_code, 201)
        r = self.show_fewer(p, [{"kind": "category"}], reason="seen_too_often")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["feedback"][0]["strength"], 2.0)
        self.assertEqual(PostHide.objects.get(user=self.me, post=p).reason, "seen_too_often")
        self.assertEqual(PostHide.objects.count(), 1)

    def test_hashtag_key_is_normalised(self):
        p = make_post(self.stranger, "x", hashtags=["python"])
        r = self.show_fewer(p, [{"kind": "hashtag", "key": "#Python "}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["data"]["feedback"][0]["key"], "python")

    def test_category_and_author_keys_come_from_the_post(self):
        p = make_post(self.stranger, "x", category="tech")
        r = self.show_fewer(p, [{"kind": "category", "key": "news"}, {"kind": "author", "key": str(self.me.id)}])
        self.assertEqual(r.status_code, 201)
        self.assertEqual(set(FeedFeedback.objects.values_list("key", flat=True)), {"tech", str(self.stranger.id)})

    def test_duplicate_targets_count_once(self):
        p = make_post(self.stranger, "x")
        r = self.show_fewer(p, [{"kind": "category"}, {"kind": "category"}])
        self.assertEqual(len(r.data["data"]["feedback"]), 1)
        self.assertEqual(r.data["data"]["feedback"][0]["strength"], 1.0)

    def test_validation_errors(self):
        p = make_post(self.stranger, "x", hashtags=["python"])
        url = reverse("post-show-fewer", kwargs={"post_id": p.id})
        for body in (
            {},                                                            # no targets
            {"targets": []},                                               # empty targets
            {"targets": [{"kind": "mood"}]},                               # unknown kind
            {"targets": [{"kind": "hashtag"}]},                            # hashtag without key
            {"targets": [{"kind": "hashtag", "key": "rust"}]},             # not a tag of this post
            {"targets": [{"kind": "category"}], "reason": "because"},      # bad reason
            {"targets": [{"kind": "category"}] * 6},                       # too many
        ):
            r = self.client.post(url, body, format="json")
            self.assertEqual(r.status_code, 400, body)
            self.assertFalse(r.data["success"])
            self.assertTrue(r.data["message"])
        # nothing was written by any of them
        self.assertFalse(PostHide.objects.exists())
        self.assertFalse(FeedFeedback.objects.exists())

    def test_one_bad_target_rejects_the_whole_request(self):
        p = make_post(self.stranger, "x", hashtags=["python"])
        r = self.show_fewer(p, [{"kind": "category"}, {"kind": "hashtag", "key": "rust"}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PostHide.objects.exists())
        self.assertFalse(FeedFeedback.objects.exists())

    def test_cannot_use_on_own_post(self):
        mine = make_post(self.me, "mine")
        self.assertEqual(self.show_fewer(mine, [{"kind": "category"}]).status_code, 400)
        self.assertFalse(FeedFeedback.objects.exists())

    def test_hidden_deleted_private_and_unfollowed_connections_posts_are_404(self):
        import uuid
        url = lambda pid: reverse("post-show-fewer", kwargs={"post_id": pid})
        body = {"targets": [{"kind": "category"}]}
        self.assertEqual(self.client.post(url(uuid.uuid4()), body, format="json").status_code, 404)
        gone = make_post(self.stranger, "gone", is_deleted=True)
        priv = make_post(self.stranger, "priv", visibility="private")
        conn = make_post(self.stranger, "conn", visibility="connections")
        for p in (gone, priv, conn):
            self.assertEqual(self.client.post(url(p.id), body, format="json").status_code, 404)
        ok = make_post(self.friend, "conn-ok", visibility="connections")
        self.assertEqual(self.client.post(url(ok.id), body, format="json").status_code, 201)
        self.assertEqual(FeedFeedback.objects.count(), 1)


class FeedbackManageTests(ApiBase):
    def test_list_shows_only_my_rows_with_labels(self):
        p = make_post(self.stranger, "x", hashtags=["python"])
        self.show_fewer(p, [{"kind": "category"}, {"kind": "hashtag", "key": "python"}, {"kind": "author"}])
        FeedFeedback.objects.create(user=self.friend, kind="category", key="news")
        r = self.client.get(reverse("post-feedback-list"))
        self.assertEqual(r.status_code, 200)
        got = {(x["kind"], x["label"]) for x in r.data["results"]}
        self.assertEqual(got, {("category", "Technology"), ("hashtag", "#python"), ("author", "@stranger")})
        self.assertTrue(all(x["strength"] == 1.0 for x in r.data["results"]))

    def test_list_kind_filter_and_decayed_rows_hidden(self):
        p = make_post(self.stranger, "x", hashtags=["python"])
        self.show_fewer(p, [{"kind": "category"}, {"kind": "hashtag", "key": "python"}])
        r = self.client.get(reverse("post-feedback-list"), {"kind": "hashtag"})
        self.assertEqual([x["kind"] for x in r.data["results"]], ["hashtag"])
        backdate(FeedFeedback.objects.get(kind="category"), 400)
        r = self.client.get(reverse("post-feedback-list"))
        self.assertEqual([x["kind"] for x in r.data["results"]], ["hashtag"])

    def test_list_strength_reflects_decay(self):
        row = FeedFeedback.objects.create(user=self.me, kind="category", key="tech", weight=2.0)
        backdate(row, 30)
        r = self.client.get(reverse("post-feedback-list"))
        self.assertAlmostEqual(r.data["results"][0]["strength"], 1.0, places=1)

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("post-feedback-list")).status_code, 401)

    def test_delete_is_idempotent_and_owner_only(self):
        mine = FeedFeedback.objects.create(user=self.me, kind="category", key="tech")
        theirs = FeedFeedback.objects.create(user=self.friend, kind="category", key="tech")
        url = lambda row: reverse("post-feedback-delete", kwargs={"feedback_id": row.id})
        r = self.client.delete(url(mine))
        self.assertEqual((r.status_code, r.data["removed"]), (200, True))
        r = self.client.delete(url(mine))
        self.assertEqual((r.status_code, r.data["removed"]), (200, False))
        r = self.client.delete(url(theirs))
        self.assertEqual((r.status_code, r.data["removed"]), (200, False))
        self.assertTrue(FeedFeedback.objects.filter(pk=theirs.pk).exists())

    def test_undo_flow_restores_the_feed(self):
        make_post(self.stranger, "s-old")
        p = make_post(self.stranger, "s-new")
        before = self.feed_order()
        self.assertEqual(before, ["s-new", "s-old"])
        r = self.show_fewer(p, [{"kind": "category"}, {"kind": "author"}])
        self.assertEqual(self.feed_order(), ["s-old"])
        # Undo snackbar: restore the post + drop every feedback item of the response.
        self.client.delete(reverse("post-not-interested", kwargs={"post_id": p.id}))
        for item in r.data["data"]["feedback"]:
            self.client.delete(reverse("post-feedback-delete", kwargs={"feedback_id": item["id"]}))
        self.assertFalse(FeedFeedback.objects.exists())
        self.assertEqual(self.feed_order(), before)


class FeedEffectTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.x = User.objects.create_user(username="xavier", password="x")
        self.y = User.objects.create_user(username="yara", password="x")
        self.z = User.objects.create_user(username="zed", password="x")

    def test_show_fewer_ranks_that_author_last_in_the_next_session(self):
        make_post(self.x, "x1")
        make_post(self.z, "z1")
        make_post(self.y, "y1")
        y0 = make_post(self.y, "y0")  # newest
        self.assertEqual(self.feed_order(), ["y0", "y1", "z1", "x1"])
        self.show_fewer(y0, [{"kind": "author"}])
        # y0 is hidden at once; y1 is still shown (a penalty never removes) but now last.
        self.assertEqual(self.feed_order(), ["z1", "x1", "y1"])

    def test_show_fewer_never_touches_following_posts(self):
        f_tech = make_post(self.friend, "f-tech", category="tech")
        make_post(self.friend, "f-news", category="news")
        other = make_post(self.x, "x-tech", category="tech")
        before = self.feed_order()
        self.show_fewer(other, [{"kind": "category"}, {"kind": "author"}])
        after = self.feed_order()
        self.assertIn("f-tech", after)
        self.assertEqual([c for c in after if c.startswith("f-")], [c for c in before if c.startswith("f-")])
        self.assertIsNotNone(f_tech)

    def test_feedback_is_private_to_the_user(self):
        p = make_post(self.x, "x1")
        self.show_fewer(p, [{"kind": "author"}])
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        self.assertEqual(self.feed_order(), ["x1"])


class WhyAPITests(ApiBase):
    def codes(self, r):
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return [x["code"] for x in r.data["data"]["reasons"]]

    def test_requires_auth(self):
        p = make_post(self.stranger, "x")
        self.client.force_authenticate(None)
        self.assertEqual(self.why(p).status_code, 401)

    def test_404_for_unknown_deleted_private_and_unfollowed_connections(self):
        import uuid
        self.assertEqual(self.client.get(reverse("post-why", kwargs={"post_id": uuid.uuid4()})).status_code, 404)
        for kw in ({"is_deleted": True}, {"visibility": "private"}, {"visibility": "connections"}):
            self.assertEqual(self.why(make_post(self.stranger, "x", **kw)).status_code, 404, kw)

    def test_own_post(self):
        r = self.why(make_post(self.me, "mine"))
        self.assertEqual(self.codes(r), ["own_post"])
        self.assertIsNone(r.data["data"]["feed_source"])

    def test_following(self):
        r = self.why(make_post(self.friend, "f"))
        self.assertEqual(self.codes(r), ["following"])
        self.assertEqual(r.data["data"]["feed_source"], "following")
        self.assertIn("@friend", r.data["data"]["reasons"][0]["text"])

    def test_following_for_connections_only_post(self):
        self.assertEqual(self.codes(self.why(make_post(self.friend, "f", visibility="connections"))), ["following"])

    def test_trending(self):
        r = self.why(make_post(self.stranger, "hot"))
        self.assertEqual(self.codes(r), ["trending"])
        self.assertEqual(r.data["data"]["feed_source"], "trending")

    def test_trending_reports_only_trending_even_if_interest_matches(self):
        UserInterest.objects.create(user=self.me, category="tech")
        self.assertEqual(self.codes(self.why(make_post(self.stranger, "hot", category="tech"))), ["trending"])

    def test_interest_category(self):
        UserInterest.objects.create(user=self.me, category="tech")
        r = self.why(make_post(self.stranger, "p", days_old=10, category="tech"))
        self.assertEqual(self.codes(r), ["interest_category"])
        self.assertEqual(r.data["data"]["feed_source"], "recommended")
        self.assertEqual(r.data["data"]["reasons"][0]["meta"], {"category": "tech", "label": "Technology"})

    def test_liked_category(self):
        liked = make_post(self.friend, "liked-one", days_old=3, category="news")
        PostLike.objects.create(post=liked, user=self.me, reaction_type="like")
        r = self.why(make_post(self.stranger, "p", days_old=10, category="news"))
        self.assertEqual(self.codes(r), ["liked_category"])

    def test_interest_and_liked_together(self):
        UserInterest.objects.create(user=self.me, category="news")
        liked = make_post(self.friend, "liked-one", days_old=3, category="news")
        PostLike.objects.create(post=liked, user=self.me, reaction_type="like")
        r = self.why(make_post(self.stranger, "p", days_old=10, category="news"))
        self.assertEqual(self.codes(r), ["interest_category", "liked_category"])

    def test_friend_of_follow(self):
        Follow.objects.create(follower=self.friend, following=self.stranger, status=Follow.Status.ACCEPTED)
        r = self.why(make_post(self.stranger, "p", days_old=10))
        self.assertEqual(self.codes(r), ["friend_of_follow"])
        via = r.data["data"]["reasons"][0]["meta"]["via"]
        self.assertEqual([v["username"] for v in via], ["friend"])
        self.assertIn("@friend", r.data["data"]["reasons"][0]["text"])

    def test_popular_when_no_personal_signal(self):
        r = self.why(make_post(self.stranger, "p", days_old=10))
        self.assertEqual(self.codes(r), ["popular"])
        self.assertEqual(r.data["data"]["feed_source"], "recommended")

    def test_not_in_feed_for_private_account(self):
        self.stranger.is_private = True
        self.stranger.save()
        r = self.why(make_post(self.stranger, "p"))
        self.assertEqual(self.codes(r), ["not_in_feed"])
        self.assertIsNone(r.data["data"]["feed_source"])

    def test_not_in_feed_for_blocked_author(self):
        from user_profile.models import BlockUser
        BlockUser.objects.create(blocker=self.me, blocked=self.stranger)
        self.assertEqual(self.codes(self.why(make_post(self.stranger, "p"))), ["not_in_feed"])

    # -- the explanation follows the ranking, not a guess ---------------------
    @override_settings(FEED_MIX_LIMITS={"trending_pool_cap": 1})
    def test_trending_means_inside_the_pool_cap(self):
        old = make_post(self.stranger, "old")
        new = make_post(self.stranger, "new")
        self.assertEqual(self.codes(self.why(new)), ["trending"])  # rank 0 < cap 1
        self.assertEqual(self.codes(self.why(old)), ["popular"])  # rank 1 -> recommended pool

    @override_settings(FEED_MIX_LIMITS={"trending_pool_cap": 1})
    def test_show_fewer_penalty_changes_the_trending_explanation(self):
        other = User.objects.create_user(username="other", password="x")
        old = make_post(other, "old")
        new = make_post(self.stranger, "new")
        FeedFeedback.objects.create(user=self.me, kind="author", key=str(self.stranger.id), weight=1.0)
        self.assertEqual(self.codes(self.why(old)), ["trending"])  # the penalised post fell behind it
        self.assertEqual(self.codes(self.why(new)), ["popular"])

    def test_dampened_lists_matching_show_fewer_rows(self):
        first = make_post(self.stranger, "first", hashtags=["python"])
        self.show_fewer(first, [{"kind": "category"}, {"kind": "hashtag", "key": "python"}, {"kind": "author"}])
        again = make_post(self.stranger, "again", hashtags=["python", "django"])
        r = self.why(again)
        got = {(d["kind"], d["key"], d["strength"]) for d in r.data["data"]["dampened"]}
        self.assertEqual(got, {("category", "tech", 1.0), ("hashtag", "python", 1.0),
                               ("author", str(self.stranger.id), 1.0)})
        unrelated = make_post(self.friend, "unrelated", category="news")
        self.assertEqual(self.why(unrelated).data["data"]["dampened"], [])

    def test_payload_shape(self):
        p = make_post(self.stranger, "p", days_old=10)
        data = self.why(p).data["data"]
        self.assertEqual(set(data), {"post_id", "feed_source", "reasons", "dampened"})
        self.assertEqual(data["post_id"], str(p.id))
        self.assertEqual(set(data["reasons"][0]), {"code", "text", "meta"})
