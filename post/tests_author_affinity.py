# post/tests_author_affinity.py
# Feed ranking - PART 2: author affinity. Authors the caller likes / comments on
# rank higher (feed_mix.load_author_affinity + affinity_expression), in all three
# Home pools; following only re-orders inside its recent/unseen bands; an active
# "show fewer" on the author wins; `author_affinity` is a "why" reason.
#
#   PureTests       points + build_reasons (no DB)
#   LoaderTests     weights, decay, window, exclusions, caps, switches
#   SqlTests        expression == points
#   RankingTests    trending / recommended / following / show-fewer wins / switch off
#   WhyAPITests     GET /post/<id>/why/ -> author_affinity
#   FeedAPITests    end to end through GET /post/feed/
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_explain, feed_mix
from .models import FeedFeedback, Post, PostComment, PostLike, UserInterest
from .views import _video_and_velocity_boost

User = get_user_model()
CFG = feed_mix.DEFAULT_AFFINITY


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


def like(user, post, days_ago=0, reaction="like"):
    obj = PostLike.objects.create(user=user, post=post, reaction_type=reaction)
    if days_ago:
        PostLike.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return obj


def comment(user, post, days_ago=0, **kw):
    obj = PostComment.objects.create(user=user, post=post, content="c", **kw)
    if days_ago:
        PostComment.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return obj


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.x = User.objects.create_user(username="xavier", password="x")
        self.y = User.objects.create_user(username="yara", password="x")
        self.following_ids = set()

    def pools(self, following=None):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        return feed_mix.build_pool_ids(self.me, base, following if following is not None else self.following_ids,
                                       _video_and_velocity_boost)

    def order(self, source, following=None):
        ids = self.pools(following)[source]
        by_id = dict(Post.objects.filter(id__in=ids).values_list("id", "content"))
        return [by_id[i] for i in ids]

    def aff(self, **kw):
        return feed_mix.load_author_affinity(self.me, **kw)


# --------------------------------------------------------------------------
class PureTests(SimpleTestCase):
    def test_points_scale_and_cap(self):
        self.assertEqual(feed_mix.affinity_points(0, CFG), 0.0)
        self.assertEqual(feed_mix.affinity_points(3, CFG), 6.0)
        self.assertEqual(feed_mix.affinity_points(10, CFG), 20.0)
        self.assertEqual(feed_mix.affinity_points(1000, CFG), 20.0)
        self.assertEqual(feed_mix.affinity_points(-4, CFG), 0.0)

    def test_cap_stays_below_one_show_fewer_tap_on_an_author(self):
        self.assertLess(CFG["max_points"], feed_mix.DEFAULT_FEEDBACK["points"]["author"])

    def sig(self, **kw):
        base = dict(author_id="u1", author_username="ann", category="tech", category_label="Technology")
        base.update(kw)
        return feed_explain.PostSignals(**base)

    def codes(self, reasons):
        return [r["code"] for r in reasons]

    def test_following_plus_affinity(self):
        source, reasons = feed_explain.build_reasons(
            self.sig(following=True, author_affinity=True, affinity_likes=4, affinity_comments=2))
        self.assertEqual(source, "following")
        self.assertEqual(self.codes(reasons), ["following", "author_affinity"])
        self.assertEqual(reasons[1]["meta"], {"user_id": "u1", "username": "ann", "likes": 4, "comments": 2})
        self.assertEqual(reasons[1]["text"], "You've recently liked 4 of and commented on 2 of @ann's posts.")

    def test_trending_plus_affinity(self):
        source, reasons = feed_explain.build_reasons(self.sig(trending=True, discovery=True, author_affinity=True,
                                                              affinity_likes=1))
        self.assertEqual((source, self.codes(reasons)), ("trending", ["trending", "author_affinity"]))

    def test_recommended_affinity_leads_and_replaces_popular(self):
        source, reasons = feed_explain.build_reasons(self.sig(
            discovery=True, author_affinity=True, affinity_comments=1, interest_category=True))
        self.assertEqual((source, self.codes(reasons)), ("recommended", ["author_affinity", "interest_category"]))
        _, only = feed_explain.build_reasons(self.sig(discovery=True, author_affinity=True, affinity_likes=1))
        self.assertEqual(self.codes(only), ["author_affinity"])

    def test_no_affinity_changes_nothing(self):
        _, reasons = feed_explain.build_reasons(self.sig(discovery=True))
        self.assertEqual(self.codes(reasons), ["popular"])
        _, own = feed_explain.build_reasons(self.sig(is_own=True, author_affinity=True))
        self.assertEqual(self.codes(own), ["own_post"])


# --------------------------------------------------------------------------
class LoaderTests(Base):
    def test_like_is_1_comment_is_3(self):
        p = make_post(self.x, "p")
        like(self.me, p)
        e = self.aff()[str(self.x.id)]
        self.assertAlmostEqual(e["score"], 1.0, places=3)
        self.assertAlmostEqual(e["points"], 2.0, places=2)
        comment(self.me, p)
        e = self.aff()[str(self.x.id)]
        self.assertAlmostEqual(e["score"], 4.0, places=3)
        self.assertEqual((e["likes"], e["comments"]), (1, 1))

    def test_replies_count_as_comments(self):
        p = make_post(self.x, "p")
        top = comment(self.y, p)
        comment(self.me, p, parent=top)
        self.assertEqual(self.aff()[str(self.x.id)]["comments"], 1)

    def test_decay_halves_every_14_days(self):
        like(self.me, make_post(self.x, "p"), days_ago=14)
        self.assertAlmostEqual(self.aff()[str(self.x.id)]["score"], 0.5, places=2)

    def test_outside_the_window_is_ignored(self):
        like(self.me, make_post(self.x, "p"), days_ago=45)
        comment(self.me, make_post(self.y, "q"), days_ago=45)
        self.assertEqual(self.aff(), {})

    def test_wrong_reaction_deleted_and_hidden_comments_do_not_count(self):
        like(self.me, make_post(self.x, "p"), reaction="wrong")
        p = make_post(self.y, "q")
        comment(self.me, p, is_deleted=True)
        comment(self.me, p, is_hidden=True)
        self.assertEqual(self.aff(), {})
        like(self.me, make_post(self.x, "r"), reaction="imp")  # every other reaction counts as a like
        self.assertEqual(self.aff()[str(self.x.id)]["likes"], 1)

    def test_own_posts_do_not_count(self):
        mine = make_post(self.me, "mine")
        like(self.me, mine)
        comment(self.me, mine)
        self.assertEqual(self.aff(), {})

    def test_points_are_capped(self):
        for i in range(12):
            like(self.me, make_post(self.x, f"p{i}"))
        self.assertEqual(self.aff()[str(self.x.id)]["points"], 20.0)
        self.assertGreater(self.aff()[str(self.x.id)]["score"], 10)  # score itself is not capped

    def test_other_users_interactions_do_not_leak(self):
        like(self.friend, make_post(self.x, "p"))
        comment(self.friend, make_post(self.x, "q"))
        self.assertEqual(self.aff(), {})

    def test_strongest_first_and_max_authors(self):
        like(self.me, make_post(self.x, "x1"))
        comment(self.me, make_post(self.y, "y1"))
        self.assertEqual(list(self.aff()), [str(self.y.id), str(self.x.id)])
        with override_settings(FEED_AUTHOR_AFFINITY={"max_authors": 1}):
            self.assertEqual(list(self.aff()), [str(self.y.id)])

    def test_active_show_fewer_author_row_removes_the_affinity(self):
        like(self.me, make_post(self.x, "x1"))
        like(self.me, make_post(self.y, "y1"))
        FeedFeedback.objects.create(user=self.me, kind="author", key=str(self.y.id), weight=1.0)
        self.assertEqual(list(self.aff()), [str(self.x.id)])
        # a decayed-away show-fewer row no longer suppresses it
        FeedFeedback.objects.filter(kind="author").update(updated_at=timezone.now() - timedelta(days=400))
        self.assertEqual(set(self.aff()), {str(self.x.id), str(self.y.id)})

    def test_switch_off_and_zero_window(self):
        like(self.me, make_post(self.x, "x1"))
        with override_settings(FEED_AUTHOR_AFFINITY={"enabled": False}):
            self.assertEqual(self.aff(), {})
        with override_settings(FEED_AUTHOR_AFFINITY={"window_days": 0}):
            self.assertEqual(self.aff(), {})

    def test_custom_weights(self):
        like(self.me, make_post(self.x, "x1"))
        with override_settings(FEED_AUTHOR_AFFINITY={"like_weight": 4.0, "points_per_unit": 1.0}):
            self.assertAlmostEqual(self.aff()[str(self.x.id)]["points"], 4.0, places=2)

    def test_no_decay_when_half_life_is_zero(self):
        like(self.me, make_post(self.x, "x1"), days_ago=29)
        with override_settings(FEED_AUTHOR_AFFINITY={"half_life_days": 0}):
            self.assertAlmostEqual(self.aff()[str(self.x.id)]["score"], 1.0, places=3)

    def test_expression_none_when_empty(self):
        self.assertIsNone(feed_mix.affinity_expression({}))
        self.assertIsNone(feed_mix.affinity_expression({"not-a-uuid": {"points": 5.0}}))


class SqlTests(Base):
    def test_expression_equals_points(self):
        like(self.me, make_post(self.x, "x1"))
        comment(self.me, make_post(self.y, "y1"))
        aff = self.aff()
        other = make_post(self.friend, "other")
        expr = feed_mix.affinity_expression(aff)
        got = {p.user_id: p.b for p in Post.objects.annotate(b=expr).filter(content__in=["x1", "y1", "other"])}
        self.assertAlmostEqual(got[self.x.id], aff[str(self.x.id)]["points"], places=4)
        self.assertAlmostEqual(got[self.y.id], aff[str(self.y.id)]["points"], places=4)
        self.assertEqual(got[other.user_id], 0.0)


# --------------------------------------------------------------------------
class RankingTests(Base):
    def test_trending_liked_author_moves_up(self):
        liked_before = make_post(self.x, "x-liked", days_old=0)
        make_post(self.x, "x-new")
        make_post(self.y, "y-newest")  # newest, nothing else going for it
        like(self.me, liked_before)
        order = self.order("trending")
        self.assertLess(order.index("x-new"), order.index("y-newest"))
        self.assertEqual(order[0], "x-liked")  # +1 like (3 engagement) + affinity

    def test_without_interactions_order_is_pure_recency(self):
        make_post(self.x, "x-old")
        make_post(self.y, "y-new")
        self.assertEqual(self.order("trending"), ["y-new", "x-old"])

    def test_recommended_pool(self):
        seed = make_post(self.x, "x-seed", days_old=20)
        make_post(self.x, "x-old", days_old=12)
        make_post(self.y, "y-new", days_old=10)
        self.assertEqual(self.order("recommended")[:2], ["y-new", "x-old"])
        comment(self.me, seed)
        order = self.order("recommended")
        self.assertLess(order.index("x-old"), order.index("y-new"))

    def test_comment_beats_like(self):
        a = make_post(self.x, "x-seed", days_old=20)
        b = make_post(self.y, "y-seed", days_old=21)
        make_post(self.x, "x-post", days_old=11)  # x is newer: wins on recency alone ...
        make_post(self.y, "y-post", days_old=12)
        like(self.me, a)  # ... x +2 points
        comment(self.me, b)  # ... y +6 points
        order = self.order("recommended")
        self.assertLess(order.index("y-post"), order.index("x-post"))

    def test_following_pool_reorders_inside_the_recent_band(self):
        friend2 = User.objects.create_user(username="friend2", password="x")
        following = {self.friend.id, friend2.id}
        seed = make_post(self.friend, "f1-seed", days_old=2)
        make_post(self.friend, "f1", days_old=1)
        make_post(friend2, "f2")  # newer, both recent
        self.assertEqual(self.order("following", following)[:2], ["f2", "f1"])
        comment(self.me, seed)
        self.assertEqual(self.order("following", following)[0], "f1")

    def test_following_never_lifts_an_old_post_over_a_recent_one(self):
        seed = make_post(self.friend, "f-seed", days_old=20)
        make_post(self.friend, "f-old", days_old=10)  # NOT in the recent (<7 days) band
        friend2 = User.objects.create_user(username="friend2", password="x")
        make_post(friend2, "f2-recent")
        for _ in range(6):
            comment(self.me, seed)  # max affinity for friend
        order = self.order("following", {self.friend.id, friend2.id})
        self.assertEqual(order[0], "f2-recent")
        self.assertLess(order.index("f2-recent"), order.index("f-old"))

    def test_show_fewer_on_the_author_wins_over_affinity(self):
        seed = make_post(self.y, "y-seed", days_old=20)
        make_post(self.y, "y-post", days_old=12)
        make_post(self.x, "x-post", days_old=13)
        for _ in range(6):
            comment(self.me, seed)
        self.assertEqual(self.order("recommended")[0], "y-post")  # affinity lifts y
        FeedFeedback.objects.create(user=self.me, kind="author", key=str(self.y.id), weight=1.0)
        order = self.order("recommended")
        self.assertLess(order.index("x-post"), order.index("y-post"))  # y is punished (-25), no affinity offsets it
        self.assertNotIn(str(self.y.id), feed_mix.load_author_affinity(self.me))

    def test_switch_off_restores_plain_order(self):
        comment(self.me, make_post(self.x, "x-seed"))  # (a comment doesn't change likes_count)
        make_post(self.y, "y-newest")
        self.assertEqual(self.order("trending")[0], "x-seed")
        with override_settings(FEED_AUTHOR_AFFINITY={"enabled": False}):
            self.assertEqual(self.order("trending")[0], "y-newest")

    def test_never_removes_a_post(self):
        like(self.me, make_post(self.x, "x1"))
        make_post(self.x, "x2")
        make_post(self.y, "y1")
        self.assertEqual(sorted(self.order("trending")), ["x1", "x2", "y1"])

    def test_trending_rank_uses_the_same_bonus(self):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        seed = make_post(self.x, "x-old")
        newer = make_post(self.y, "y-new")
        rank = lambda p: feed_mix.trending_rank(self.me, base, set(), _boost, p)
        self.assertEqual((rank(newer), rank(seed)), (0, 1))
        comment(self.me, seed)  # x-old: engagement 5 + 6 affinity
        self.assertEqual((rank(newer), rank(seed)), (1, 0))


# --------------------------------------------------------------------------
class ApiBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)

    def why(self, post):
        r = self.client.get(reverse("post-why", kwargs={"post_id": post.id}))
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r.data["data"]


class WhyAPITests(ApiBase):
    def test_following_author_you_like(self):
        like(self.me, make_post(self.friend, "seed"))
        data = self.why(make_post(self.friend, "p"))
        self.assertEqual([r["code"] for r in data["reasons"]], ["following", "author_affinity"])
        self.assertEqual(data["reasons"][1]["meta"]["likes"], 1)

    def test_trending_author_you_comment_on(self):
        comment(self.me, make_post(self.stranger, "seed"))
        p = make_post(self.stranger, "hot")
        data = self.why(p)
        self.assertEqual(data["feed_source"], "trending")
        self.assertEqual([r["code"] for r in data["reasons"]], ["trending", "author_affinity"])
        self.assertEqual(data["reasons"][1]["meta"]["comments"], 1)

    def test_recommended_leads_with_affinity(self):
        UserInterest.objects.create(user=self.me, category="tech")
        comment(self.me, make_post(self.stranger, "seed", days_old=10))  # (a like would also add liked_category)
        data = self.why(make_post(self.stranger, "old", days_old=11))
        self.assertEqual(data["feed_source"], "recommended")
        self.assertEqual([r["code"] for r in data["reasons"]], ["author_affinity", "interest_category"])

    def test_no_reason_without_interactions_or_after_show_fewer(self):
        p = make_post(self.stranger, "old", days_old=11)
        self.assertEqual([r["code"] for r in self.why(p)["reasons"]], ["popular"])
        comment(self.me, make_post(self.stranger, "seed", days_old=10))
        self.assertEqual([r["code"] for r in self.why(p)["reasons"]], ["author_affinity"])
        FeedFeedback.objects.create(user=self.me, kind="author", key=str(self.stranger.id), weight=1.0)
        self.assertEqual([r["code"] for r in self.why(p)["reasons"]], ["popular"])


class FeedAPITests(ApiBase):
    def order(self):
        r = self.client.get(reverse("home-feed"), {"page_size": 50})
        self.assertEqual(r.status_code, 200)
        return [x["content"] for x in r.data["results"]]

    def test_home_feed_puts_the_liked_authors_post_first(self):
        other = User.objects.create_user(username="other", password="x")
        seed = make_post(self.stranger, "s-seed")
        make_post(other, "o-newest")
        cache.clear()
        self.assertEqual(self.order()[0], "o-newest")
        comment(self.me, seed)
        self.assertEqual(self.order()[0], "s-seed")

    def test_other_users_feed_is_unaffected(self):
        other = User.objects.create_user(username="other", password="x")
        make_post(self.stranger, "s-1")
        make_post(other, "o-newest")
        comment(other, Post.objects.get(content="s-1"))
        self.assertEqual(self.order(), ["o-newest", "s-1"])
